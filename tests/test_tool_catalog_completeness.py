"""接続中のMCPツールを間引かずにモデルへ渡すことの回帰テスト。

背景: ollama_tools は既定で上限24件まで絞り込み、順位付けは「直近のユーザー発言」
だけを使ったキーワードスコアだった。ms-data-parser は40ツールを公開するため、
「9w vs 24M」のようにどのツール名にもマッチしない発言では全件スコア0になり、
登録順の先頭24件しか渡らない。実セッションでは arf_preprocess が脱落し、モデルが
「今の利用可能ツール一覧には arf_preprocess が見えていません」と回答して解析が
そこで止まった。順位付けは残してよいが、脱落させてはいけない。
"""

from __future__ import annotations

import unittest
from contextlib import asynccontextmanager
from dataclasses import dataclass
from types import SimpleNamespace

from use_lllm.core.mcp_registry import MCPRegistry
from use_lllm.core.settings_store import MCPServerSpec


@dataclass
class FakeTool:
    name: str
    inputSchema: dict
    title: str | None = None
    description: str | None = None
    annotations: dict | None = None


@dataclass
class FakePage:
    tools: list[FakeTool]
    nextCursor: str | None = None


class FakeSession:
    def __init__(self, tools: list[FakeTool]) -> None:
        self.tools = tools

    async def initialize(self):
        return SimpleNamespace(
            serverInfo=SimpleNamespace(name="ms-data-parser", version="1.0"),
            protocolVersion="2025-06-18",
        )

    async def list_tools(self, cursor=None):
        return FakePage(self.tools)


# 実 ms-data-parser が公開する40ツール。上限24件だと、どれが落ちるかは登録順まかせで
# 16件が黙って消える（実セッションで消えたのは arf_preprocess だった）。
MS_DATA_PARSER_TOOLS = [
    "record_objective",
    "update_objective",
    "knowledge_coverage",
    "paper_search",
    "log_search",
    "write_report",
    "read_report",
    "list_reports",
    "ingest_stage",
    "ingest_review_queue",
    "ingest_promote",
    "ingest_reject",
    "pai2_parser",
    "pai2_inspect_peak",
    "verify_peak_annotation",
    "dcl_parser",
    "dcl_find_msms",
    "arf_list_tags",
    "arf_list_classes",
    "arf_list_sample_roles",
    "arf_exclude",
    "arf_preprocess",
    "arf_pca_preprocessed",
    "arf_parser",
    "arf_differential",
    "arf_plot_volcano",
    "sample_search",
    "arf2_parser",
    "arf2_annotate_identities",
    "eic_parser",
    "eic_search_by_mz_range",
    "eic_search_by_rt_range",
    "eic_rank_by_max_intensity",
    "eic_plot_chromatograms",
    "eic_plot_compounds",
    "save_figure",
    "arf_plot_species",
    "arf_pca_species",
    "plot_pca_loadings",
    "list_data_files",
    "load_dataset",
]


class ToolCatalogCompletenessTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        session = FakeSession([FakeTool(name, {"type": "object"}) for name in MS_DATA_PARSER_TOOLS])

        @asynccontextmanager
        async def factory(spec):
            yield session

        self.registry = MCPRegistry(
            [MCPServerSpec("ms-data-parser", "python")], session_factory=factory
        )
        await self.registry.connect("ms-data-parser")

    def exposed_names(self, *args, **kwargs) -> set[str]:
        return {item["function"]["name"] for item in self.registry.ollama_tools(*args, **kwargs)}

    def test_every_connected_tool_is_exposed_without_a_query(self) -> None:
        self.assertEqual(len(self.exposed_names()), len(MS_DATA_PARSER_TOOLS))

    def test_no_tool_is_dropped_by_an_unmatched_query(self) -> None:
        """実セッションで arf_preprocess を脱落させた発言をそのまま使う。"""
        exposed = self.exposed_names(query="9w vs 24M")
        self.assertIn("ms-data-parser::arf_preprocess", exposed)
        self.assertEqual(len(exposed), len(MS_DATA_PARSER_TOOLS))

    def test_ranking_still_puts_an_explicitly_named_tool_first(self) -> None:
        """脱落させないだけで、順位付け自体は維持する。"""
        ranked = self.registry.ollama_tools(query="arf_preprocess を実行して")
        self.assertEqual(ranked[0]["function"]["name"], "ms-data-parser::arf_preprocess")

    def test_excluded_tools_are_still_withheld(self) -> None:
        """ユーザーが明示的に無効化したツールは従来どおり渡さない。"""
        exposed = self.exposed_names(query="9w vs 24M", excluded={"ms-data-parser::arf_preprocess"})
        self.assertNotIn("ms-data-parser::arf_preprocess", exposed)
        self.assertEqual(len(exposed), len(MS_DATA_PARSER_TOOLS) - 1)

    def test_an_explicit_limit_is_still_honoured(self) -> None:
        """上限は既定で無効なだけで、呼び出し側が明示すれば効く。"""
        self.assertEqual(len(self.exposed_names(limit=5)), 5)


if __name__ == "__main__":
    unittest.main()
