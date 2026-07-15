"""Minimal loopback-only Ollama chat and tool-calling client."""

from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any, AsyncIterator

import httpx

from use_lllm.core.config import OllamaConfig


class OllamaError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class OllamaResponse:
    message: dict[str, Any]
    model: str
    total_duration: int | None
    prompt_eval_count: int | None
    eval_count: int | None

    @property
    def content(self) -> str:
        return str(self.message.get("content", ""))

    @property
    def tool_calls(self) -> list[dict[str, Any]]:
        calls = self.message.get("tool_calls") or []
        return calls if isinstance(calls, list) else []


class OllamaClient:
    def __init__(self, config: OllamaConfig) -> None:
        config.validate()
        self.config = config

    async def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        timeout = httpx.Timeout(self.config.timeout_seconds)
        try:
            async with httpx.AsyncClient(base_url=self.config.base_url, timeout=timeout) as client:
                response = await client.request(method, path, **kwargs)
                response.raise_for_status()
                data = response.json()
        except httpx.ConnectError as exc:
            raise OllamaError("Ollamaへ接続できません。Ollamaが起動しているか確認してください。") from exc
        except httpx.TimeoutException as exc:
            raise OllamaError("Ollamaの応答がタイムアウトしました。") from exc
        except (httpx.HTTPStatusError, json.JSONDecodeError) as exc:
            raise OllamaError(f"Ollama APIがエラーを返しました: {exc}") from exc
        if not isinstance(data, dict):
            raise OllamaError("Ollama APIの応答形式が不正です。")
        return data

    async def list_models(self) -> list[dict[str, Any]]:
        data = await self._request("GET", "/api/tags")
        models = data.get("models", [])
        return models if isinstance(models, list) else []

    async def health(self) -> dict[str, Any]:
        models = await self.list_models()
        names = [str(model.get("name", "")) for model in models]
        return {
            "status": "ready" if self.config.model in names else "model_missing",
            "base_url": self.config.base_url,
            "selected_model": self.config.model,
            "models": names,
        }

    async def context_window(self) -> int | None:
        """Return the context length of the currently loaded selected model."""

        data = await self._request("GET", "/api/ps")
        models = data.get("models", [])
        if not isinstance(models, list):
            return None
        for model in models:
            if not isinstance(model, dict):
                continue
            name = str(model.get("name") or model.get("model") or "")
            if name == self.config.model:
                value = model.get("context_length")
                if isinstance(value, int) and value > 0:
                    return value
        return None

    async def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        format_schema: dict[str, Any] | str | None = None,
        model: str | None = None,
        temperature: float = 0.2,
        think: bool | str | None = None,
        num_predict: int | None = None,
    ) -> OllamaResponse:
        payload: dict[str, Any] = {
            "model": model or self.config.model,
            "messages": messages,
            "stream": False,
            "options": {"temperature": temperature},
        }
        if num_predict is not None:
            payload["options"]["num_predict"] = num_predict
        if think is not None:
            payload["think"] = think
        if tools:
            payload["tools"] = tools
        if format_schema is not None:
            payload["format"] = format_schema
        data = await self._request("POST", "/api/chat", json=payload)
        message = data.get("message")
        if not isinstance(message, dict):
            raise OllamaError("Ollama応答にmessageがありません。")
        return OllamaResponse(
            message=message,
            model=str(data.get("model", model or self.config.model)),
            total_duration=data.get("total_duration"),
            prompt_eval_count=data.get("prompt_eval_count"),
            eval_count=data.get("eval_count"),
        )

    async def stream_chat(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
        temperature: float = 0.2,
    ) -> AsyncIterator[dict[str, Any]]:
        payload: dict[str, Any] = {
            "model": model or self.config.model,
            "messages": messages,
            "stream": True,
            "options": {"temperature": temperature},
        }
        if tools:
            payload["tools"] = tools
        try:
            async with httpx.AsyncClient(
                base_url=self.config.base_url, timeout=httpx.Timeout(self.config.timeout_seconds)
            ) as client:
                async with client.stream("POST", "/api/chat", json=payload) as response:
                    response.raise_for_status()
                    async for line in response.aiter_lines():
                        if not line:
                            continue
                        value = json.loads(line)
                        if isinstance(value, dict):
                            yield value
        except httpx.ConnectError as exc:
            raise OllamaError("Ollamaへ接続できません。") from exc
        except httpx.TimeoutException as exc:
            raise OllamaError("Ollamaのストリーミング応答がタイムアウトしました。") from exc
        except (httpx.HTTPStatusError, json.JSONDecodeError) as exc:
            raise OllamaError(f"OllamaストリーミングAPIがエラーを返しました: {exc}") from exc

    async def interpret_result(self, objective: str, result_text: str, context: str = "") -> dict[str, Any]:
        schema = {
            "type": "object",
            "properties": {
                "known": {"type": "array", "items": {"type": "string"}},
                "uncertainties": {"type": "array", "items": {"type": "string"}},
                "proposals": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "action": {"type": "string"},
                            "purpose": {"type": "string"},
                            "requires_confirmation": {"type": "boolean"},
                        },
                        "required": ["action", "purpose", "requires_confirmation"],
                    },
                },
                "caveats": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["known", "uncertainties", "proposals", "caveats"],
        }
        system = (
            "あなたはリピドミクス解析の慎重な共同研究者です。観測、統計結果、生物学的解釈、"
            "仮説を混同しません。PCAだけで有意差を主張しません。入力にない数値や文献を作りません。"
            "結果を評価したら次に有用な操作を最大3件提案し、実行せず停止してください。日本語で回答します。"
        )
        response = await self.chat(
            [
                {"role": "system", "content": system},
                {"role": "user", "content": f"解析目的:\n{objective or '未設定'}\n\n前提:\n{context}\n\nツール結果:\n{result_text[:24000]}"},
            ],
            format_schema=schema,
            temperature=0.1,
            think=False,
            num_predict=1200,
        )
        try:
            value = json.loads(response.content)
        except json.JSONDecodeError as exc:
            raise OllamaError("Ollamaの構造化解釈をJSONとして読めません。") from exc
        if not isinstance(value, dict):
            raise OllamaError("Ollamaの構造化解釈がオブジェクトではありません。")
        value["model"] = response.model
        return value

    async def tool_call_smoke(self, model: str | None = None) -> dict[str, Any]:
        tool = {
            "type": "function",
            "function": {
                "name": "list_data_files",
                "description": "指定フォルダのデータファイル一覧を取得する",
                "parameters": {
                    "type": "object",
                    "properties": {"directory": {"type": "string"}, "extension": {"type": "string"}},
                    "required": ["directory", "extension"],
                },
            },
        }
        response = await self.chat(
            [{"role": "user", "content": r"C:\data\NEG の .arf2 一覧を取得してください。"}],
            tools=[tool], model=model, temperature=0,
        )
        return {
            "model": response.model,
            "tool_calls": response.tool_calls,
            "content": response.content,
            "eval_count": response.eval_count,
            "total_duration": response.total_duration,
        }
