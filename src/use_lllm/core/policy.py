"""Explicit MCP tool safety classes and approval requirements."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class ToolSafety(StrEnum):
    READ_ONLY = "read_only"
    LOCAL_WRITE = "local_write"
    EXTERNAL_NETWORK = "external_network"
    KNOWLEDGE_MUTATION = "knowledge_mutation"
    UNKNOWN = "unknown"


class ToolPolicyError(PermissionError):
    pass


@dataclass(frozen=True, slots=True)
class ToolDecision:
    tool_name: str
    safety: ToolSafety
    allowed: bool
    approval_required: bool
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool_name": self.tool_name,
            "safety": self.safety.value,
            "allowed": self.allowed,
            "approval_required": self.approval_required,
            "reason": self.reason,
        }


def classify_server_tool(
    tool_name: str,
    *,
    annotations: Mapping[str, Any] | None = None,
) -> ToolSafety:
    """MCP 標準 annotations だけからツールの安全クラスを決める。

    サーバ固有のツール名リストは持たない。汎用 MCP クライアントとして、
    どのローカルサーバに対しても同じ規則で判定する。

    判定順が重要。openWorldHint を先に見ないと、外部を読むだけのツール
    （readOnlyHint も真になる）が READ_ONLY に落ちて network_mode のゲートを
    迂回する。

    未設定フィールドは model_dump(by_alias=True) の結果で None として入るため、
    必ず `is True` / `is False` で判定する。truthiness では None を False と
    区別できず、宣言していない項目を「宣言した」と誤読する。
    """
    if annotations is None:
        return ToolSafety.UNKNOWN
    if annotations.get("openWorldHint") is True:
        return ToolSafety.EXTERNAL_NETWORK
    if annotations.get("readOnlyHint") is True:
        return ToolSafety.READ_ONLY
    if annotations.get("destructiveHint") is True:
        return ToolSafety.KNOWLEDGE_MUTATION
    if annotations.get("readOnlyHint") is False:
        return ToolSafety.LOCAL_WRITE
    return ToolSafety.UNKNOWN


def is_replay_safe(annotations: Mapping[str, Any] | None) -> bool:
    """同じ引数での再実行が安全か。状態復旧のリプレイ可否に使う。

    MCP 仕様は idempotentHint を「readOnlyHint == false のときだけ意味を持つ」と
    定義するため、readOnlyHint との OR で拾う。
    """
    if annotations is None:
        return False
    return annotations.get("readOnlyHint") is True or annotations.get("idempotentHint") is True


def decide_server_tool(
    server_name: str,
    tool_name: str,
    *,
    annotations: Mapping[str, Any] | None = None,
    read_only_auto: bool = False,
    approved: bool = False,
    network_mode: str = "offline",
) -> ToolDecision:
    """任意サーバーのツール1件について実行可否と承認要否を決める。"""

    safety = classify_server_tool(tool_name, annotations=annotations)
    qualified = f"{server_name}::{tool_name}"
    if safety == ToolSafety.UNKNOWN:
        if approved:
            return ToolDecision(qualified, safety, True, True, "承認済みです。")
        return ToolDecision(qualified, safety, False, True, "未知のツールは承認が必要です。")
    if safety == ToolSafety.READ_ONLY:
        if read_only_auto:
            return ToolDecision(qualified, safety, True, False, "read-only自動実行が有効です。")
        if approved:
            return ToolDecision(qualified, safety, True, True, "承認済みです。")
        return ToolDecision(
            qualified,
            safety,
            False,
            True,
            "read-only自動化が無効のため承認が必要です。",
        )
    if safety == ToolSafety.EXTERNAL_NETWORK and network_mode != "literature-only":
        return ToolDecision(qualified, safety, False, True, "ネットワークモードがofflineです。")
    if not approved:
        return ToolDecision(qualified, safety, False, True, "明示的な承認が必要です。")
    return ToolDecision(qualified, safety, True, True, "承認済みです。")


def enforce_tool(
    tool_name: str,
    *,
    annotations: Mapping[str, Any] | None = None,
    approved: bool = False,
    network_mode: str = "offline",
    server_name: str = "mcp",
) -> ToolDecision:
    """許可されないツール呼び出しを ToolPolicyError で止める。

    annotations を渡せないほど早い段階では呼ばないこと（annotations=None は
    UNKNOWN＝承認必須になる）。
    """
    decision = decide_server_tool(
        server_name,
        tool_name,
        annotations=annotations,
        approved=approved,
        network_mode=network_mode,
    )
    if not decision.allowed:
        raise ToolPolicyError(decision.reason)
    return decision
