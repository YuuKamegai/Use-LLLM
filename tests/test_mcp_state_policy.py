from __future__ import annotations

import unittest

from use_lllm.core.mcp_state_policy import (
    StateRestoreBlocked,
    build_replay_plan,
    indicates_missing_state,
    rule_for,
)


def invocation(identifier, tool, *, arguments=None):
    return {
        "id": identifier,
        "tool_name": tool,
        "arguments": arguments or {},
        "status": "complete",
        "is_error": False,
        "replay_of_id": None,
    }


class MCPStatePolicyTests(unittest.TestCase):
    def test_differential_restores_parser_and_all_subsequent_exclusions(self) -> None:
        history = [
            invocation(
                1,
                "ms-data-parser::arf_parser",
                arguments={"file_path": "C:/data/test.arf"},
            ),
            invocation(
                2,
                "ms-data-parser::arf_exclude",
                arguments={"exclude_samples": ["A"]},
            ),
            invocation(
                3,
                "ms-data-parser::arf_exclude",
                arguments={"exclude_samples": ["B"]},
            ),
        ]

        plan = build_replay_plan("ms-data-parser::arf_differential", history)

        self.assertEqual([item["id"] for item in plan], [1, 2, 3])

    def test_missing_required_state_is_reported(self) -> None:
        with self.assertRaisesRegex(StateRestoreBlocked, "arf_dataset"):
            build_replay_plan("ms-data-parser::arf_differential", [])

    def test_unknown_tool_needs_no_automatic_replay(self) -> None:
        self.assertEqual(build_replay_plan("other::unknown", []), [])

    def test_save_eic_replays_latest_plot_payload(self) -> None:
        history = [invocation(
            4,
            "ms-data-parser::eic_plot_chromatograms",
            arguments={"spot_id": 12, "file_ids": [1, 2]},
        )]

        plan = build_replay_plan("ms-data-parser::save_eic_figure", history)

        self.assertEqual([item["id"] for item in plan], [4])

    def test_missing_eic_plot_message_is_treated_as_state_error(self) -> None:
        self.assertTrue(
            indicates_missing_state(
                "ms-data-parser::save_eic_figure",
                "先に eic_plot_chromatograms または eic_plot_compounds を実行してください。",
            )
        )

    def test_short_prerequisite_message_is_treated_as_state_error(self) -> None:
        self.assertTrue(
            indicates_missing_state(
                "ms-data-parser::arf_differential",
                "先に arf_parser を実行してデータを読み込んでください。",
            )
        )

    def test_eic_plot_tools_provide_eic_plot_state(self) -> None:
        for name in (
            "ms-data-parser::eic_plot_chromatograms",
            "ms-data-parser::eic_plot_compounds",
        ):
            rule = rule_for(name)
            self.assertEqual(rule.provides, ("eic_plot",), name)
            self.assertTrue(rule.replay_safe, name)
        self.assertEqual(
            rule_for("ms-data-parser::eicaef_plot_chromatograms").provides, ()
        )

    def test_volcano_tools_require_differential_result(self) -> None:
        differential = rule_for("ms-data-parser::arf_differential")
        self.assertEqual(differential.provides, ("differential_result",))
        # 群指定引数に依存するため無引数の再実行では同じ結果を再現できない。
        self.assertFalse(differential.replay_safe)
        self.assertEqual(
            rule_for("ms-data-parser::arf_plot_volcano").requires,
            ("differential_result",),
        )
        self.assertEqual(
            rule_for("ms-data-parser::save_volcano_figure").requires,
            ("differential_result",),
        )

    def test_volcano_state_cannot_be_auto_restored(self) -> None:
        history = [invocation(1, "ms-data-parser::arf_differential")]
        with self.assertRaisesRegex(StateRestoreBlocked, "arf_differential"):
            build_replay_plan("ms-data-parser::arf_plot_volcano", history)

    def test_removed_tool_rules_are_gone(self) -> None:
        self.assertEqual(rule_for("ms-data-parser::arf_re_pca").provides, ())

    def test_volcano_prerequisite_message_is_treated_as_state_error(self) -> None:
        self.assertTrue(
            indicates_missing_state(
                "ms-data-parser::arf_plot_volcano",
                "先に arf_differential（2群比較）を実行してください"
                "（直近の2群差次的解析の volcano データがありません）。",
            )
        )


if __name__ == "__main__":
    unittest.main()
