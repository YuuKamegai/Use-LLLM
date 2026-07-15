"""Token-budgeted prompt assembly with durable incremental session summaries."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
import os
from typing import Any, Protocol

from jsonschema import Draft202012Validator

from use_lllm.core.sessions import SessionStore, compact_tool_result


SUMMARY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "objective": {"type": "string"},
        "facts": {"type": "array", "items": {"type": "string"}},
        "decisions": {"type": "array", "items": {"type": "string"}},
        "constraints": {"type": "array", "items": {"type": "string"}},
        "open_questions": {"type": "array", "items": {"type": "string"}},
        "dataset_refs": {"type": "array", "items": {"type": "string"}},
    },
    "required": [
        "objective",
        "facts",
        "decisions",
        "constraints",
        "open_questions",
        "dataset_refs",
    ],
    "additionalProperties": False,
}


class SummarizingOllama(Protocol):
    async def chat(self, messages: list[dict[str, Any]], **kwargs: Any) -> Any: ...


@dataclass(frozen=True, slots=True)
class ContextAssembly:
    messages: list[dict[str, Any]]
    estimated_tokens: int
    summary_created: bool


def estimate_tokens(value: Any) -> int:
    serialized = (
        value
        if isinstance(value, str)
        else json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    )
    return max(1, math.ceil(len(serialized.encode("utf-8")) / 3))


def parse_summary_json(content: str) -> dict[str, Any]:
    text = content.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end < start:
        raise ValueError("summary response does not contain a JSON object")
    value = json.loads(text[start : end + 1])
    if not isinstance(value, dict):
        raise ValueError("summary is not an object")
    required = set(SUMMARY_SCHEMA["required"])
    if not required.issubset(value):
        narrative = str(value.get("summary") or value.get("objective") or "").strip()
        value = {
            "objective": narrative,
            "facts": [narrative] if narrative else [],
            "decisions": [],
            "constraints": [],
            "open_questions": [],
            "dataset_refs": [],
        }
    Draft202012Validator(SUMMARY_SCHEMA).validate(value)
    return value


class ContextMemoryManager:
    def __init__(
        self,
        ollama: SummarizingOllama,
        sessions: SessionStore,
        *,
        context_window: int | None = None,
        input_ratio: float = 0.65,
        recent_user_turns: int = 3,
    ) -> None:
        env_window = os.environ.get("USE_LLLM_CONTEXT_WINDOW")
        configured = context_window or int(env_window or "32768")
        if configured < 2048:
            raise ValueError("context_windowは2048以上にしてください。")
        if not 0.3 <= input_ratio <= 0.9:
            raise ValueError("input_ratioは0.3から0.9の範囲にしてください。")
        self.ollama = ollama
        self.sessions = sessions
        self.context_window = configured
        self.input_ratio = input_ratio
        self.recent_user_turns = max(1, recent_user_turns)
        self._context_resolved = context_window is not None or env_window is not None

    async def _resolve_context_window(self) -> None:
        if self._context_resolved:
            return
        self._context_resolved = True
        resolver = getattr(self.ollama, "context_window", None)
        if resolver is None:
            return
        try:
            value = await resolver()
        except Exception:
            return
        if isinstance(value, int) and value >= 2048:
            self.context_window = value

    @staticmethod
    def _stored_message(stored: dict[str, Any]) -> dict[str, Any]:
        content = str(stored["content"])
        if stored["role"] == "tool":
            content = compact_tool_result(content, 900)
        item: dict[str, Any] = {"role": stored["role"], "content": content}
        metadata = stored.get("metadata", {})
        if metadata.get("tool_calls"):
            item["tool_calls"] = metadata["tool_calls"]
        if metadata.get("tool_name"):
            item["tool_name"] = metadata["tool_name"]
        return item

    def _tool_memory(self, session_id: str) -> dict[str, Any] | None:
        completed = [
            item
            for item in self.sessions.list_tool_invocations(
                session_id, include_replays=False
            )
            if item["status"] == "complete" and not item.get("is_error")
        ]
        if not completed:
            return None
        return {
            "successful_invocations": [
                {
                    "id": item["id"],
                    "tool": item["tool_name"],
                    "arguments": item["arguments"],
                    "result": compact_tool_result(item["result_summary"], 480),
                }
                for item in completed[-12:]
            ]
        }

    def _recent_boundary(self, messages: list[dict[str, Any]]) -> int:
        user_ids = [int(item["id"]) for item in messages if item["role"] == "user"]
        if len(user_ids) <= self.recent_user_turns:
            return 0
        return user_ids[-self.recent_user_turns]

    async def _summarize(
        self,
        session_id: str,
        prior: dict[str, Any] | None,
        candidates: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        if not candidates:
            return prior
        compact = [self._stored_message(item) for item in candidates]
        source = {
            "prior": prior["summary"] if prior else None,
            "messages": compact,
        }
        source_json = json.dumps(source, ensure_ascii=False, sort_keys=True)
        source_hash = hashlib.sha256(source_json.encode("utf-8")).hexdigest()
        prompt = (
            "会話の古い部分を、後続の判断に必要な事実だけ残して要約してください。"
            "Markdownコードフェンスや説明文を付けず、次のキーをすべて持つJSONオブジェクトだけを返してください: "
            '{"objective":"","facts":[],"decisions":[],"constraints":[],'
            '"open_questions":[],"dataset_refs":[]}。'
            "各配列の要素は文字列にしてください。MCP実行の成否や解析状態を推測で補完せず、"
            "観測された内容だけを書いてください。\n\n"
            + source_json
        )
        try:
            response = await self.ollama.chat(
                [
                    {
                        "role": "system",
                        "content": "あなたは会話記憶を圧縮する記録係です。",
                    },
                    {"role": "user", "content": prompt},
                ],
                format_schema=SUMMARY_SCHEMA,
                temperature=0.1,
                think=False,
                num_predict=1400,
            )
            summary = parse_summary_json(response.content)
            model = str(getattr(response, "model", "unknown"))
        except Exception as exc:
            summary = {
                "objective": prior["summary"].get("objective", "") if prior else "",
                "facts": [
                    f"{item['role']}: {compact_tool_result(str(item['content']), 480)}"
                    for item in candidates[-8:]
                ],
                "decisions": [],
                "constraints": [],
                "open_questions": [],
                "dataset_refs": [],
                "degraded_reason": str(exc),
            }
            model = "deterministic-fallback"
        first_id = int(prior["first_message_id"]) if prior else int(candidates[0]["id"])
        last_id = int(candidates[-1]["id"])
        self.sessions.add_conversation_summary(
            session_id,
            first_id,
            last_id,
            summary,
            source_hash=source_hash,
            model=model,
            estimated_tokens=estimate_tokens(summary),
        )
        self.sessions.append_event(
            session_id,
            "conversation_compacted",
            {
                "first_message_id": first_id,
                "last_message_id": last_id,
                "model": model,
            },
        )
        return self.sessions.latest_conversation_summary(session_id)

    async def assemble(
        self,
        session_id: str,
        system_content: str,
        tools: list[dict[str, Any]],
    ) -> ContextAssembly:
        await self._resolve_context_window()
        stored = self.sessions.list_messages(session_id)
        prior = self.sessions.latest_conversation_summary(session_id)
        covered = int(prior["last_message_id"]) if prior else 0
        raw = [item for item in stored if int(item["id"]) > covered]
        summary_created = False

        def render(summary: dict[str, Any] | None) -> list[dict[str, Any]]:
            result: list[dict[str, Any]] = [
                {"role": "system", "content": system_content}
            ]
            if summary:
                result.append(
                    {
                        "role": "system",
                        "content": "セッション会話要約:\n"
                        + json.dumps(summary["summary"], ensure_ascii=False),
                    }
                )
            tool_memory = self._tool_memory(session_id)
            if tool_memory:
                result.append(
                    {
                        "role": "system",
                        "content": "MCPツール実行台帳（解析状態の正本）:\n"
                        + json.dumps(tool_memory, ensure_ascii=False),
                    }
                )
            result.extend(self._stored_message(item) for item in raw)
            return result

        messages = render(prior)
        budget = max(1024, int(self.context_window * self.input_ratio) - estimate_tokens(tools))
        while estimate_tokens(messages) > budget:
            boundary = self._recent_boundary(raw)
            candidates = [
                item for item in raw if boundary and int(item["id"]) < boundary
            ]
            if not candidates:
                break
            chunk: list[dict[str, Any]] = []
            chunk_tokens = 0
            chunk_budget = max(512, int(self.context_window * 0.35))
            for item in candidates:
                item_tokens = estimate_tokens(self._stored_message(item))
                if chunk and chunk_tokens + item_tokens > chunk_budget:
                    break
                chunk.append(item)
                chunk_tokens += item_tokens
            updated = await self._summarize(session_id, prior, chunk)
            if updated is prior or updated is None:
                break
            summary_created = True
            prior = updated
            covered = int(prior["last_message_id"])
            raw = [item for item in stored if int(item["id"]) > covered]
            messages = render(prior)

        return ContextAssembly(messages, estimate_tokens(messages), summary_created)
