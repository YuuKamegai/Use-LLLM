"""Azure OpenAI v1 Chat Completions client with Ollama-compatible responses."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any, AsyncIterator
from urllib.parse import urlsplit, urlunsplit

import httpx

from use_lllm.core.config import ConfigurationError
from use_lllm.core.ollama import OllamaError, OllamaResponse


class AzureOpenAIError(OllamaError):
    """Raised when Azure OpenAI cannot accept or complete a request."""


AZURE_OPENAI_HOST_SUFFIXES = ("openai.azure.com", "services.ai.azure.com")


@dataclass(frozen=True, slots=True)
class AzureModelContextProfile:
    context_window: int
    max_input_tokens: int
    max_output_tokens: int


_GPT_54_MINI_PROFILE = AzureModelContextProfile(400_000, 272_000, 128_000)
_GPT_54_PROFILE = AzureModelContextProfile(1_050_000, 922_000, 128_000)


def azure_model_context_profile(model: str) -> AzureModelContextProfile | None:
    """Resolve context limits from an Azure response model or deployment alias."""

    normalized = model.strip().lower()
    if "gpt-5.4-mini" in normalized or "gpt-5.4-nano" in normalized:
        return _GPT_54_MINI_PROFILE
    if "gpt-5.4-pro" in normalized or re.search(r"(?:^|[^0-9])gpt-5\.4(?:$|[^0-9])", normalized):
        return _GPT_54_PROFILE
    return None


def _supported_azure_openai_host(hostname: str) -> bool:
    for suffix in AZURE_OPENAI_HOST_SUFFIXES:
        marker = f".{suffix}"
        if not hostname.endswith(marker):
            continue
        resource = hostname[: -len(marker)]
        return bool(
            resource
            and "." not in resource
            and not resource.startswith("-")
            and not resource.endswith("-")
            and all(
                character.isascii() and (character.isalnum() or character == "-")
                for character in resource
            )
        )
    return False


def normalize_azure_openai_endpoint(value: str) -> str:
    """Normalize a portal resource endpoint to the stable Azure OpenAI v1 base URL."""

    raw = value.strip().rstrip("/")
    parsed = urlsplit(raw)
    hostname = (parsed.hostname or "").lower()
    try:
        port = parsed.port
    except ValueError as exc:
        raise ConfigurationError("Azure OpenAI endpointのport指定が不正です。") from exc
    if (
        parsed.scheme != "https"
        or not parsed.netloc
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or hostname.endswith(".")
        or not _supported_azure_openai_host(hostname)
        or port not in {None, 443}
    ):
        raise ConfigurationError(
            "Azure OpenAI endpointには公式のhttps://<resource>.openai.azure.com "
            "またはhttps://<resource>.services.ai.azure.comを指定してください。"
        )
    path = parsed.path.rstrip("/").lower()
    if path not in {"", "/openai", "/openai/v1"}:
        raise ConfigurationError("Azure OpenAI endpointのpathは/openai/v1のみ指定できます。")
    return urlunsplit(("https", hostname, "/openai/v1", "", ""))


@dataclass(frozen=True, slots=True)
class AzureOpenAIConfig:
    endpoint: str
    api_key: str
    deployment: str
    timeout_seconds: float = 300.0
    context_window: int | None = None

    def validate(self) -> None:
        normalize_azure_openai_endpoint(self.endpoint)
        if not self.api_key.strip():
            raise ConfigurationError("Azure OpenAI API keyが空です。")
        if not self.deployment.strip():
            raise ConfigurationError("Azure OpenAI deployment名が空です。")
        if self.timeout_seconds <= 0:
            raise ConfigurationError("Azure OpenAI timeoutは0より大きくしてください。")
        if self.context_window is not None and self.context_window < 2048:
            raise ConfigurationError("Azure OpenAI context windowは2048以上にしてください。")

    @property
    def base_url(self) -> str:
        return normalize_azure_openai_endpoint(self.endpoint)


def _safe_tool_name(name: str) -> str:
    base = re.sub(r"[^A-Za-z0-9_-]", "_", name).strip("_") or "tool"
    digest = hashlib.sha256(name.encode("utf-8")).hexdigest()[:10]
    suffix = f"_{digest}"
    return f"{base[: 64 - len(suffix)]}{suffix}"


class AzureOpenAIClient:
    """Adapt Azure's OpenAI-compatible API to the existing local MCP agent loop."""

    def __init__(
        self,
        config: AzureOpenAIConfig,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        config.validate()
        self.config = config
        self._transport = transport
        self._response_model: str | None = None

    @property
    def _chat_url(self) -> str:
        return f"{self.config.base_url}/chat/completions"

    @property
    def _headers(self) -> dict[str, str]:
        return {"api-key": self.config.api_key, "Content-Type": "application/json"}

    @staticmethod
    def _tool_maps(
        tools: list[dict[str, Any]] | None,
        messages: list[dict[str, Any]],
    ) -> tuple[dict[str, str], dict[str, str]]:
        names: list[str] = []
        for item in tools or []:
            function = item.get("function") or {}
            name = str(function.get("name") or "")
            if name:
                names.append(name)
        for item in messages:
            for call in item.get("tool_calls") or []:
                if not isinstance(call, dict):
                    continue
                function = call.get("function") or {}
                name = str(function.get("name") or "")
                if name:
                    names.append(name)
        original_to_safe = {name: _safe_tool_name(name) for name in dict.fromkeys(names)}
        safe_to_original = {safe: original for original, safe in original_to_safe.items()}
        return original_to_safe, safe_to_original

    @staticmethod
    def _normalize_tools(
        tools: list[dict[str, Any]] | None, name_map: dict[str, str]
    ) -> list[dict[str, Any]] | None:
        if not tools:
            return None
        normalized: list[dict[str, Any]] = []
        for item in tools:
            function = dict(item.get("function") or {})
            original = str(function.get("name") or "")
            function["name"] = name_map.get(original, _safe_tool_name(original))
            normalized.append({"type": "function", "function": function})
        return normalized

    @staticmethod
    def _normalize_messages(
        messages: list[dict[str, Any]], name_map: dict[str, str]
    ) -> list[dict[str, Any]]:
        normalized: list[dict[str, Any]] = []
        pending_tool_ids: list[str] = []
        for message_index, item in enumerate(messages):
            role = str(item.get("role") or "user")
            content = item.get("content")
            if role == "assistant" and item.get("tool_calls"):
                calls: list[dict[str, Any]] = []
                for call_index, call in enumerate(item.get("tool_calls") or []):
                    if not isinstance(call, dict):
                        continue
                    function = call.get("function") or {}
                    original = str(function.get("name") or "")
                    arguments = function.get("arguments") or "{}"
                    if not isinstance(arguments, str):
                        arguments = json.dumps(arguments, ensure_ascii=False)
                    call_id = str(call.get("id") or f"call_{message_index}_{call_index}")
                    pending_tool_ids.append(call_id)
                    calls.append(
                        {
                            "id": call_id,
                            "type": "function",
                            "function": {
                                "name": name_map.get(original, _safe_tool_name(original)),
                                "arguments": arguments,
                            },
                        }
                    )
                normalized.append(
                    {"role": "assistant", "content": content or None, "tool_calls": calls}
                )
                continue
            if role == "tool":
                if pending_tool_ids:
                    normalized.append(
                        {
                            "role": "tool",
                            "tool_call_id": pending_tool_ids.pop(0),
                            "content": str(content or ""),
                        }
                    )
                else:
                    normalized.append(
                        {
                            "role": "system",
                            "content": "MCP tool result: " + str(content or ""),
                        }
                    )
                continue
            normalized.append({"role": role, "content": content or ""})
        return normalized

    def _payload(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None,
        format_schema: dict[str, Any] | str | None,
        model: str | None,
        temperature: float,
        num_predict: int | None,
        stream: bool,
    ) -> tuple[dict[str, Any], dict[str, str]]:
        name_map, reverse_map = self._tool_maps(tools, messages)
        payload: dict[str, Any] = {
            "model": model or self.config.deployment,
            "messages": self._normalize_messages(messages, name_map),
            "stream": stream,
        }
        # Azure deployments (especially reasoning models) do not all accept a
        # temperature override. Keep the cross-deployment default here.
        _ = temperature
        normalized_tools = self._normalize_tools(tools, name_map)
        if normalized_tools:
            payload["tools"] = normalized_tools
        if isinstance(format_schema, dict):
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": "use_lllm_response",
                    "strict": True,
                    "schema": format_schema,
                },
            }
        elif isinstance(format_schema, str):
            payload["response_format"] = {"type": format_schema}
        if num_predict is not None:
            payload["max_completion_tokens"] = num_predict
        return payload, reverse_map

    async def _error(self, response: httpx.Response) -> AzureOpenAIError:
        await response.aread()
        request_id = response.headers.get("x-request-id") or response.headers.get("apim-request-id")
        detail = response.text[:1000]
        try:
            value = response.json()
            error = value.get("error", {}) if isinstance(value, dict) else {}
            detail = str(error.get("message") or detail)
        except (json.JSONDecodeError, ValueError):
            pass
        suffix = f" (request_id={request_id})" if request_id else ""
        return AzureOpenAIError(
            f"Azure OpenAI APIがHTTP {response.status_code}を返しました: {detail}{suffix}"
        )

    async def _post_json(self, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(self.config.timeout_seconds), transport=self._transport
            ) as client:
                response = await client.post(self._chat_url, headers=self._headers, json=payload)
                if response.is_error:
                    raise await self._error(response)
                value = response.json()
        except AzureOpenAIError:
            raise
        except httpx.ConnectError as exc:
            raise AzureOpenAIError(
                "Azure OpenAIへ接続できません。endpointを確認してください。"
            ) from exc
        except httpx.TimeoutException as exc:
            raise AzureOpenAIError("Azure OpenAIの応答がタイムアウトしました。") from exc
        except (httpx.HTTPError, json.JSONDecodeError) as exc:
            raise AzureOpenAIError(f"Azure OpenAI APIの応答を読み取れません: {exc}") from exc
        if not isinstance(value, dict):
            raise AzureOpenAIError("Azure OpenAI APIの応答形式が不正です。")
        return value

    @staticmethod
    def _content(value: Any) -> str:
        if isinstance(value, str):
            return value
        if isinstance(value, list):
            return "".join(str(item.get("text") or "") for item in value if isinstance(item, dict))
        return ""

    @staticmethod
    def _normalize_response_calls(calls: Any, reverse_map: dict[str, str]) -> list[dict[str, Any]]:
        normalized: list[dict[str, Any]] = []
        for call in calls if isinstance(calls, list) else []:
            if not isinstance(call, dict):
                continue
            function = call.get("function") or {}
            safe_name = str(function.get("name") or "")
            normalized.append(
                {
                    "id": str(call.get("id") or ""),
                    "type": "function",
                    "function": {
                        "name": reverse_map.get(safe_name, safe_name),
                        "arguments": function.get("arguments") or "{}",
                    },
                }
            )
        return normalized

    async def health(self) -> dict[str, Any]:
        return {
            "status": "configured",
            "provider": "azure_openai",
            "base_url": self.config.base_url,
            "selected_model": self.config.deployment,
            "models": [self.config.deployment],
        }

    async def test_connection(self) -> dict[str, Any]:
        response = await self.chat(
            [{"role": "user", "content": "Reply with OK."}],
            temperature=0,
        )
        return {
            "status": "ready",
            "provider": "azure_openai",
            "base_url": self.config.base_url,
            "selected_model": self.config.deployment,
            "response_received": bool(response.content or response.tool_calls),
        }

    async def context_window_details(self) -> dict[str, int | str] | None:
        profile = azure_model_context_profile(self._response_model or self.config.deployment)
        if self.config.context_window is not None:
            details: dict[str, int | str] = {
                "context_window": self.config.context_window,
                "source": "azure_configured",
            }
            if profile is not None and profile.context_window == self.config.context_window:
                details["max_input_tokens"] = profile.max_input_tokens
                details["max_output_tokens"] = profile.max_output_tokens
            return details
        if profile is None:
            return None
        return {
            "context_window": profile.context_window,
            "max_input_tokens": profile.max_input_tokens,
            "max_output_tokens": profile.max_output_tokens,
            "source": "azure_model_profile",
        }

    async def context_window(self) -> int | None:
        details = await self.context_window_details()
        return int(details["context_window"]) if details is not None else None

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
        _ = think
        payload, reverse_map = self._payload(
            messages,
            tools=tools,
            format_schema=format_schema,
            model=model,
            temperature=temperature,
            num_predict=num_predict,
            stream=False,
        )
        data = await self._post_json(payload)
        choices = data.get("choices") or []
        if not choices or not isinstance(choices[0], dict):
            raise AzureOpenAIError("Azure OpenAI応答にchoicesがありません。")
        message = choices[0].get("message") or {}
        if not isinstance(message, dict):
            raise AzureOpenAIError("Azure OpenAI応答のmessageが不正です。")
        usage = data.get("usage") or {}
        response_model = str(data.get("model") or model or self.config.deployment)
        self._response_model = response_model
        return OllamaResponse(
            message={
                "role": "assistant",
                "content": self._content(message.get("content")),
                "tool_calls": self._normalize_response_calls(
                    message.get("tool_calls"), reverse_map
                ),
            },
            model=response_model,
            total_duration=None,
            prompt_eval_count=(
                int(usage["prompt_tokens"]) if isinstance(usage.get("prompt_tokens"), int) else None
            ),
            eval_count=(
                int(usage["completion_tokens"])
                if isinstance(usage.get("completion_tokens"), int)
                else None
            ),
        )

    async def stream_chat(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
        temperature: float = 0.2,
    ) -> AsyncIterator[dict[str, Any]]:
        payload, reverse_map = self._payload(
            messages,
            tools=tools,
            format_schema=None,
            model=model,
            temperature=temperature,
            num_predict=None,
            stream=True,
        )
        payload["stream_options"] = {"include_usage": True}
        tool_parts: dict[int, dict[str, str]] = {}
        response_model = model or self.config.deployment
        prompt_eval_count: int | None = None
        eval_count: int | None = None
        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(self.config.timeout_seconds), transport=self._transport
            ) as client:
                async with client.stream(
                    "POST", self._chat_url, headers=self._headers, json=payload
                ) as response:
                    if response.is_error:
                        raise await self._error(response)
                    async for line in response.aiter_lines():
                        if not line.startswith("data:"):
                            continue
                        raw = line[5:].strip()
                        if not raw or raw == "[DONE]":
                            continue
                        item = json.loads(raw)
                        if not isinstance(item, dict):
                            continue
                        response_model = str(item.get("model") or response_model)
                        usage = item.get("usage") or {}
                        if isinstance(usage, dict):
                            if isinstance(usage.get("prompt_tokens"), int):
                                prompt_eval_count = int(usage["prompt_tokens"])
                            if isinstance(usage.get("completion_tokens"), int):
                                eval_count = int(usage["completion_tokens"])
                        choices = item.get("choices") or []
                        if not choices or not isinstance(choices[0], dict):
                            continue
                        delta = choices[0].get("delta") or {}
                        if not isinstance(delta, dict):
                            continue
                        content = self._content(delta.get("content"))
                        if content:
                            yield {
                                "model": response_model,
                                "message": {"role": "assistant", "content": content},
                            }
                        for call in delta.get("tool_calls") or []:
                            if not isinstance(call, dict):
                                continue
                            index = int(call.get("index", 0))
                            part = tool_parts.setdefault(
                                index, {"id": "", "name": "", "arguments": ""}
                            )
                            if call.get("id"):
                                part["id"] = str(call["id"])
                            function = call.get("function") or {}
                            part["name"] += str(function.get("name") or "")
                            part["arguments"] += str(function.get("arguments") or "")
        except AzureOpenAIError:
            raise
        except httpx.ConnectError as exc:
            raise AzureOpenAIError(
                "Azure OpenAIへ接続できません。endpointを確認してください。"
            ) from exc
        except httpx.TimeoutException as exc:
            raise AzureOpenAIError(
                "Azure OpenAIのストリーミング応答がタイムアウトしました。"
            ) from exc
        except (httpx.HTTPError, json.JSONDecodeError) as exc:
            raise AzureOpenAIError(f"Azure OpenAIストリームを読み取れません: {exc}") from exc

        self._response_model = response_model
        calls = [
            {
                "id": part["id"],
                "type": "function",
                "function": {
                    "name": reverse_map.get(part["name"], part["name"]),
                    "arguments": part["arguments"] or "{}",
                },
            }
            for _index, part in sorted(tool_parts.items())
        ]
        yield {
            "model": response_model,
            "message": {"role": "assistant", "content": "", "tool_calls": calls},
            "prompt_eval_count": prompt_eval_count,
            "eval_count": eval_count,
        }
