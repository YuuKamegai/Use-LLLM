from __future__ import annotations

import unittest

from use_lllm.core.policy import (
    ToolSafety,
    classify_server_tool,
    classify_tool,
    decide_server_tool,
)


class ClassifyServerToolTests(unittest.TestCase):
    def test_builtin_profile_uses_name_based_classification(self) -> None:
        self.assertEqual(
            classify_server_tool("ms-data-parser", "arf_parser"),
            ToolSafety.READ_ONLY,
        )
        self.assertEqual(
            classify_server_tool("ms-data-parser", "write_report"),
            ToolSafety.LOCAL_WRITE,
        )
        self.assertEqual(
            classify_server_tool("ms-data-parser", "eic_plot_chromatograms"),
            ToolSafety.READ_ONLY,
        )
        self.assertEqual(
            classify_server_tool("ms-data-parser", "save_eic_figure"),
            ToolSafety.LOCAL_WRITE,
        )

    def test_unknown_server_unknown_tool_is_unknown(self) -> None:
        self.assertEqual(classify_server_tool("other", "do_thing"), ToolSafety.UNKNOWN)

    def test_read_only_hint_annotation_marks_read_only(self) -> None:
        safety = classify_server_tool("other", "peek", annotations={"readOnlyHint": True})
        self.assertEqual(safety, ToolSafety.READ_ONLY)

    def test_non_true_read_only_hint_is_unknown(self) -> None:
        self.assertEqual(
            classify_server_tool("other", "peek", annotations={"readOnlyHint": False}),
            ToolSafety.UNKNOWN,
        )


class DecideServerToolTests(unittest.TestCase):
    def test_read_only_auto_allows_without_approval(self) -> None:
        decision = decide_server_tool("ms-data-parser", "arf_parser", read_only_auto=True)
        self.assertTrue(decision.allowed)
        self.assertFalse(decision.approval_required)
        self.assertEqual(decision.tool_name, "ms-data-parser::arf_parser")

    def test_read_only_without_auto_requires_approval(self) -> None:
        decision = decide_server_tool("ms-data-parser", "arf_parser", read_only_auto=False)
        self.assertFalse(decision.allowed)
        self.assertTrue(decision.approval_required)

        approved = decide_server_tool(
            "ms-data-parser", "arf_parser", read_only_auto=False, approved=True
        )
        self.assertTrue(approved.allowed)

    def test_unknown_tool_never_auto_runs(self) -> None:
        decision = decide_server_tool("other", "danger", read_only_auto=True)
        self.assertFalse(decision.allowed)
        self.assertTrue(decision.approval_required)

        approved = decide_server_tool("other", "danger", read_only_auto=True, approved=True)
        self.assertTrue(approved.allowed)

    def test_annotated_read_only_with_auto_allows(self) -> None:
        decision = decide_server_tool(
            "other",
            "peek",
            annotations={"readOnlyHint": True},
            read_only_auto=True,
        )
        self.assertTrue(decision.allowed)
        self.assertFalse(decision.approval_required)

    def test_write_requires_approval_then_allows(self) -> None:
        blocked = decide_server_tool("ms-data-parser", "write_report")
        self.assertFalse(blocked.allowed)
        approved = decide_server_tool("ms-data-parser", "write_report", approved=True)
        self.assertTrue(approved.allowed)

    def test_external_network_blocked_offline_allowed_literature(self) -> None:
        offline = decide_server_tool(
            "ms-data-parser",
            "paper_search",
            approved=True,
            network_mode="offline",
        )
        self.assertFalse(offline.allowed)
        literature = decide_server_tool(
            "ms-data-parser",
            "paper_search",
            approved=True,
            network_mode="literature-only",
        )
        self.assertTrue(literature.allowed)


class ToolNameSyncTests(unittest.TestCase):
    """ms-data-parser の実ツール名と分類表が一致していること。

    旧名が残ると classify_tool が UNKNOWN を返し、read-only 解析が毎回
    承認待ちで止まってプロットの描画まで到達できない。
    """

    def test_current_read_only_tools_are_classified(self) -> None:
        for name in (
            "eic_parser",
            "eic_plot_chromatograms",
            "eic_plot_compounds",
            "eic_rank_by_max_intensity",
            "eic_search_by_mz_range",
            "eic_search_by_rt_range",
            "arf_plot_volcano",
            "dcl_parser",
            "dcl_find_msms",
            "sample_search",
            "pai2_inspect_peak",
        ):
            self.assertEqual(classify_tool(name), ToolSafety.READ_ONLY, name)

    def test_removed_server_tools_are_no_longer_classified(self) -> None:
        for name in (
            "eicaef_parser",
            "eicaef_plot_chromatograms",
            "eicaef_top_peak_tops",
            "eicaef_search_by_mz_range",
            "eicaef_search_by_rt_range",
            "arf_re_pca",
            "pai2_get_top_metabolites",
            "pai2_inspect_metabolite_details",
            "pai2_update_analysis_filter",
        ):
            self.assertEqual(classify_tool(name), ToolSafety.UNKNOWN, name)

    def test_figure_savers_stay_local_write(self) -> None:
        for name in ("save_pca_figure", "save_volcano_figure", "save_eic_figure"):
            self.assertEqual(classify_tool(name), ToolSafety.LOCAL_WRITE, name)

    def test_paper_search_stays_network_gated(self) -> None:
        self.assertEqual(classify_tool("paper_search"), ToolSafety.EXTERNAL_NETWORK)


if __name__ == "__main__":
    unittest.main()
