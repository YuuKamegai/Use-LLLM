"""Explicit, replay-safe state dependencies for known stateful MCP tools."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class ToolStateRule:
    requires: tuple[str, ...] = ()
    provides: tuple[str, ...] = ()
    replay_safe: bool = False


RULES: dict[str, ToolStateRule] = {
    "ms-data-parser::load_dataset": ToolStateRule(
        provides=("arf_dataset", "pca_result"), replay_safe=True
    ),
    "ms-data-parser::arf_parser": ToolStateRule(
        provides=("arf_dataset", "pca_result"), replay_safe=True
    ),
    "ms-data-parser::arf_exclude": ToolStateRule(
        requires=("arf_dataset",), provides=("arf_dataset",), replay_safe=True
    ),
    "ms-data-parser::arf_preprocess": ToolStateRule(
        requires=("arf_dataset",),
        provides=("preprocessed_matrix",),
        replay_safe=True,
    ),
    "ms-data-parser::arf_pca_preprocessed": ToolStateRule(
        requires=("preprocessed_matrix",),
        provides=("pca_result",),
        replay_safe=True,
    ),
    "ms-data-parser::arf_list_tags": ToolStateRule(requires=("arf_dataset",)),
    "ms-data-parser::arf_list_classes": ToolStateRule(requires=("arf_dataset",)),
    "ms-data-parser::arf_list_sample_roles": ToolStateRule(requires=("arf_dataset",)),
    # arf_differential は群指定引数に依存するため replay_safe にしない。無引数の
    # 再実行では同じ差次的結果を再現できず、別条件の図を「同じ結果」として
    # 保存・解釈してしまうため。
    "ms-data-parser::arf_differential": ToolStateRule(
        requires=("arf_dataset",), provides=("differential_result",)
    ),
    "ms-data-parser::arf_plot_volcano": ToolStateRule(
        requires=("differential_result",)
    ),
    "ms-data-parser::save_volcano_figure": ToolStateRule(
        requires=("differential_result",)
    ),
    "ms-data-parser::save_pca_figure": ToolStateRule(requires=("pca_result",)),
    "ms-data-parser::eic_plot_chromatograms": ToolStateRule(
        provides=("eic_plot",), replay_safe=True
    ),
    "ms-data-parser::eic_plot_compounds": ToolStateRule(
        provides=("eic_plot",), replay_safe=True
    ),
    "ms-data-parser::save_eic_figure": ToolStateRule(requires=("eic_plot",)),
}


class StateRestoreBlocked(RuntimeError):
    pass


def rule_for(tool_name: str) -> ToolStateRule:
    return RULES.get(tool_name, ToolStateRule())


def indicates_missing_state(tool_name: str, result_text: str) -> bool:
    if not rule_for(tool_name).requires or len(result_text) > 800:
        return False
    return any(
        marker in result_text
        for marker in (
            "先に eic_plot_chromatograms",
            "先に arf_parser",
            "先に arf_differential",
            "PCA結果がありません",
            "データを読み込んでください",
        )
    )


def build_replay_plan(target_tool: str, invocations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return the minimal successful replay-safe chain required by target_tool."""

    target_rule = rule_for(target_tool)
    if not target_rule.requires:
        return []
    candidates = [
        item
        for item in invocations
        if item.get("status") == "complete"
        and not item.get("is_error")
        and item.get("replay_of_id") is None
        and item.get("tool_name") in RULES
    ]
    selected: dict[int, dict[str, Any]] = {}
    resolving: set[tuple[str, int]] = set()

    def restore_state(state: str, before_id: int) -> None:
        marker = (state, before_id)
        if marker in resolving:
            raise StateRestoreBlocked(f"MCP状態依存が循環しています: {state}")
        resolving.add(marker)
        providers = [
            item
            for item in candidates
            if int(item["id"]) < before_id and state in rule_for(str(item["tool_name"])).provides
        ]
        if not providers:
            resolving.remove(marker)
            raise StateRestoreBlocked(f"復元可能なMCP状態がありません: {state}")
        provider = providers[-1]
        provider_rule = rule_for(str(provider["tool_name"]))
        if not provider_rule.replay_safe:
            resolving.remove(marker)
            raise StateRestoreBlocked(
                f"安全に再実行できないMCPツールが必要です: {provider['tool_name']}"
            )
        for requirement in provider_rule.requires:
            restore_state(requirement, int(provider["id"]))
        selected[int(provider["id"])] = provider
        resolving.remove(marker)

    boundary = max((int(item["id"]) for item in candidates), default=0) + 1
    for requirement in target_rule.requires:
        restore_state(requirement, boundary)
    return [selected[key] for key in sorted(selected)]
