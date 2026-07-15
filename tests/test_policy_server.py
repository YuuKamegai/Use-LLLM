from __future__ import annotations

import unittest

from use_lllm.core.policy import (
    ToolSafety,
    classify_server_tool,
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


if __name__ == "__main__":
    unittest.main()
