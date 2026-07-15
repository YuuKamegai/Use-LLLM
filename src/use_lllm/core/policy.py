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


READ_ONLY_TOOLS = frozenset(
    {
        "list_reports", "read_report", "list_data_files", "load_dataset",
        "pai2_parser", "pai2_get_top_metabolites", "pai2_inspect_metabolite_details",
        "verify_peak_annotation", "arf_list_tags", "arf_list_classes",
        "arf_list_sample_roles", "arf_exclude", "arf_preprocess",
        "arf_pca_preprocessed", "arf_parser", "arf_re_pca", "arf_differential",
        "arf2_parser", "arf2_annotate_identities", "eicaef_parser",
        "eicaef_top_peak_tops", "eicaef_search_by_mz_range",
        "eicaef_search_by_rt_range", "knowledge_coverage", "log_search",
    }
)
LOCAL_WRITE_TOOLS = frozenset(
    {"write_report", "save_pca_figure", "save_volcano_figure", "record_objective", "update_objective", "pai2_update_analysis_filter"}
)
EXTERNAL_NETWORK_TOOLS = frozenset({"paper_search"})
KNOWLEDGE_MUTATION_TOOLS = frozenset(
    {"ingest_stage", "ingest_review_queue", "ingest_promote", "ingest_reject"}
)


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


def classify_tool(tool_name: str) -> ToolSafety:
    if tool_name in READ_ONLY_TOOLS:
        return ToolSafety.READ_ONLY
    if tool_name in LOCAL_WRITE_TOOLS:
        return ToolSafety.LOCAL_WRITE
    if tool_name in EXTERNAL_NETWORK_TOOLS:
        return ToolSafety.EXTERNAL_NETWORK
    if tool_name in KNOWLEDGE_MUTATION_TOOLS:
        return ToolSafety.KNOWLEDGE_MUTATION
    return ToolSafety.UNKNOWN


def decide_tool(tool_name: str, approved: bool = False, network_mode: str = "offline") -> ToolDecision:
    safety = classify_tool(tool_name)
    if safety == ToolSafety.UNKNOWN:
        return ToolDecision(tool_name, safety, False, True, "未知のツールは実行しません。")
    if safety == ToolSafety.READ_ONLY:
        return ToolDecision(tool_name, safety, True, False, "ローカルread-only解析です。")
    if safety == ToolSafety.EXTERNAL_NETWORK and network_mode != "literature-only":
        return ToolDecision(tool_name, safety, False, True, "ネットワークモードがofflineです。")
    if not approved:
        return ToolDecision(tool_name, safety, False, True, "明示的な承認が必要です。")
    return ToolDecision(tool_name, safety, True, True, "承認済みです。")


def enforce_tool(tool_name: str, approved: bool = False, network_mode: str = "offline") -> ToolDecision:
    decision = decide_tool(tool_name, approved, network_mode)
    if not decision.allowed:
        raise ToolPolicyError(decision.reason)
    return decision


BUILTIN_PROFILE_SERVERS = frozenset({"ms-data-parser"})


def classify_server_tool(
    server_name: str,
    tool_name: str,
    *,
    annotations: Mapping[str, Any] | None = None,
) -> ToolSafety:
    """サーバー単位でツールの安全クラスを判定する。

    既知プロファイルは名前ベースで分類し、未知サーバーはMCP annotationsの
    readOnlyHintだけを安全側のヒントとして使う。
    """

    if server_name in BUILTIN_PROFILE_SERVERS:
        return classify_tool(tool_name)
    if annotations is not None and annotations.get("readOnlyHint") is True:
        return ToolSafety.READ_ONLY
    return ToolSafety.UNKNOWN


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

    safety = classify_server_tool(server_name, tool_name, annotations=annotations)
    qualified = f"{server_name}::{tool_name}"
    if safety == ToolSafety.UNKNOWN:
        if approved:
            return ToolDecision(qualified, safety, True, True, "承認済みです。")
        return ToolDecision(
            qualified, safety, False, True, "未知のツールは承認が必要です。"
        )
    if safety == ToolSafety.READ_ONLY:
        if read_only_auto:
            return ToolDecision(
                qualified, safety, True, False, "read-only自動実行が有効です。"
            )
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
        return ToolDecision(
            qualified, safety, False, True, "ネットワークモードがofflineです。"
        )
    if not approved:
        return ToolDecision(
            qualified, safety, False, True, "明示的な承認が必要です。"
        )
    return ToolDecision(qualified, safety, True, True, "承認済みです。")
