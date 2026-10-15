"""Shared Web tool results, without provider-specific assumptions."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from use_lllm.core.mcp_client import MCPToolResult


@dataclass(frozen=True)
class WebResult:
    payload: dict[str, Any]
    is_error: bool = False


class WebError(Exception):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def failure(code: str, message: str, **fields: Any) -> WebResult:
    return WebResult(
        {
            **fields,
            "status": "error",
            "error": {"code": code, "message": message},
            "retrieved_at": utc_now(),
        },
        True,
    )


def to_mcp_result(name: str, result: WebResult) -> MCPToolResult:
    return MCPToolResult(
        name,
        result.is_error,
        ({"type": "text", "text": json.dumps(result.payload, ensure_ascii=False)},),
        result.payload,
    )
