"""機微な引数値を残さない共有ツール監査イベント。"""

from __future__ import annotations

from typing import Any

from use_lllm.core.policy import ToolDecision
from use_lllm.core.sessions import SessionStore


class AuditLogger:
    def __init__(self, sessions: SessionStore) -> None:
        self._sessions = sessions

    @staticmethod
    def _tool_parts(qualified_name: str) -> tuple[str, str]:
        if "::" not in qualified_name:
            return "", qualified_name
        server, tool = qualified_name.split("::", 1)
        return server, tool

    def record_tool_decision(
        self,
        session_id: str,
        qualified_name: str,
        arguments: dict[str, Any],
        decision: ToolDecision,
        *,
        status: str = "complete",
    ) -> int:
        server, tool = self._tool_parts(qualified_name)
        return self._sessions.append_event(
            session_id,
            "general_tool_decision",
            {
                "server": server,
                "tool": tool,
                "qualified_name": qualified_name,
                "argument_keys": sorted(str(key) for key in arguments),
                "argument_count": len(arguments),
                "decision": decision.to_dict(),
            },
            status=status,
        )

    def record_tool_result(
        self,
        session_id: str,
        qualified_name: str,
        *,
        is_error: bool,
        content_blocks: int,
        has_structured_content: bool = False,
        parent_event_id: int | None = None,
    ) -> int:
        server, tool = self._tool_parts(qualified_name)
        return self._sessions.append_event(
            session_id,
            "general_tool_result",
            {
                "server": server,
                "tool": tool,
                "qualified_name": qualified_name,
                "is_error": is_error,
                "content_blocks": content_blocks,
                "has_structured_content": has_structured_content,
            },
            status="failed" if is_error else "complete",
            parent_event_id=parent_event_id,
        )
