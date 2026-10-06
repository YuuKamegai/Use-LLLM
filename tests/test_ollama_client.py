from __future__ import annotations

import asyncio
import json
import unittest
from typing import Any
from unittest.mock import patch

import httpx

from use_lllm.core import ollama as ollama_module
from use_lllm.core.config import DEFAULT_OLLAMA_NUM_CTX, ConfigurationError, OllamaConfig
from use_lllm.core.endpoints import Endpoint
from use_lllm.core.ollama import OllamaClient


class _FakeOllama:
    """Ollama HTTP API の最小スタブ。受け取ったリクエスト本文を記録する。"""

    def __init__(self, *, train_ctx: int | None = 262_144, loaded_ctx: int = 4096) -> None:
        self.train_ctx = train_ctx
        self.loaded_ctx = loaded_ctx
        self.chat_payloads: list[dict[str, Any]] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/chat":
            payload = json.loads(request.content)
            self.chat_payloads.append(payload)
            message = {"role": "assistant", "content": "ok"}
            if payload.get("stream"):
                body = json.dumps({"model": payload["model"], "message": message, "done": True})
                return httpx.Response(200, content=body + "\n")
            return httpx.Response(200, json={"model": payload["model"], "message": message})
        if request.url.path == "/api/show":
            info = {"general.architecture": "qwen35moe"}
            if self.train_ctx is not None:
                info["qwen35moe.context_length"] = self.train_ctx
            return httpx.Response(200, json={"model_info": info})
        if request.url.path == "/api/ps":
            model = {"name": "m:1", "context_length": self.loaded_ctx}
            return httpx.Response(200, json={"models": [model]})
        return httpx.Response(404)

    def patch(self) -> Any:
        real = httpx.AsyncClient
        transport = httpx.MockTransport(self.handler)
        return patch.object(
            ollama_module.httpx,
            "AsyncClient",
            lambda **kwargs: real(transport=transport, **kwargs),
        )


def _client(**config: Any) -> OllamaClient:
    return OllamaClient(OllamaConfig(model="m:1", **config))


class OllamaNumCtxTest(unittest.TestCase):
    """num_ctx を送らないと Ollama はモデルを既定の 4096 で読み込み、
    約 26k トークンある MCP ツール定義を黙って切り捨てる。"""

    def test_default_num_ctx_is_large_enough_for_full_tool_catalog(self) -> None:
        self.assertEqual(OllamaConfig().num_ctx, DEFAULT_OLLAMA_NUM_CTX)
        self.assertGreaterEqual(DEFAULT_OLLAMA_NUM_CTX, 65_536)

    def test_chat_sends_num_ctx(self) -> None:
        fake = _FakeOllama()
        with fake.patch():
            asyncio.run(_client(num_ctx=65_536).chat([{"role": "user", "content": "x"}]))
        self.assertEqual(fake.chat_payloads[0]["options"]["num_ctx"], 65_536)

    def test_stream_chat_sends_num_ctx(self) -> None:
        fake = _FakeOllama()

        async def consume() -> None:
            async for _ in _client(num_ctx=65_536).stream_chat([{"role": "user", "content": "x"}]):
                pass

        with fake.patch():
            asyncio.run(consume())
        self.assertEqual(fake.chat_payloads[0]["options"]["num_ctx"], 65_536)

    def test_context_window_is_num_ctx_capped_by_model_limit_not_stale_loaded_value(self) -> None:
        # 別クライアントが 4096 で読み込み済みでも、次の要求で num_ctx へ読み直される。
        # 要約の予算は読み直し後の実効値で計算しないと、毎ターン過剰に要約してしまう。
        fake = _FakeOllama(train_ctx=262_144, loaded_ctx=4096)
        with fake.patch():
            self.assertEqual(asyncio.run(_client(num_ctx=65_536).context_window()), 65_536)

        small = _FakeOllama(train_ctx=32_768)
        with small.patch():
            self.assertEqual(asyncio.run(_client(num_ctx=65_536).context_window()), 32_768)

    def test_context_window_without_model_limit_uses_num_ctx(self) -> None:
        fake = _FakeOllama(train_ctx=None)
        with fake.patch():
            self.assertEqual(asyncio.run(_client(num_ctx=65_536).context_window()), 65_536)

    def test_rejects_too_small_num_ctx(self) -> None:
        with self.assertRaises(ConfigurationError):
            OllamaConfig(num_ctx=1024).validate()

    def test_endpoint_context_window_overrides_default_num_ctx(self) -> None:
        endpoint = Endpoint(
            name="local",
            base_url="http://127.0.0.1:11434",
            default_model="m:1",
            context_window=131_072,
        )
        self.assertEqual(endpoint.to_ollama_config().num_ctx, 131_072)

        unset = Endpoint(name="local", base_url="http://127.0.0.1:11434", default_model="m:1")
        self.assertEqual(unset.to_ollama_config().num_ctx, DEFAULT_OLLAMA_NUM_CTX)


if __name__ == "__main__":
    unittest.main()
