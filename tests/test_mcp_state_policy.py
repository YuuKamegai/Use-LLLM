from __future__ import annotations

import unittest

from use_lllm.core.mcp_state_policy import (
    StateRestoreBlocked,
    build_replay_plan,
    indicates_missing_state,
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
    def test_re_pca_restores_parser_and_all_subsequent_exclusions(self) -> None:
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

        plan = build_replay_plan("ms-data-parser::arf_re_pca", history)

        self.assertEqual([item["id"] for item in plan], [1, 2, 3])

    def test_missing_required_state_is_reported(self) -> None:
        with self.assertRaisesRegex(StateRestoreBlocked, "arf_dataset"):
            build_replay_plan("ms-data-parser::arf_re_pca", [])

    def test_unknown_tool_needs_no_automatic_replay(self) -> None:
        self.assertEqual(build_replay_plan("other::unknown", []), [])

    def test_save_eic_replays_latest_plot_payload(self) -> None:
        history = [invocation(
            4,
            "ms-data-parser::eicaef_plot_chromatograms",
            arguments={"spot_id": 12, "file_ids": [1, 2]},
        )]

        plan = build_replay_plan("ms-data-parser::save_eic_figure", history)

        self.assertEqual([item["id"] for item in plan], [4])

    def test_missing_eic_plot_message_is_treated_as_state_error(self) -> None:
        self.assertTrue(
            indicates_missing_state(
                "ms-data-parser::save_eic_figure",
                "先に eicaef_plot_chromatograms を実行してください（EICプロット情報がありません）。",
            )
        )

    def test_short_prerequisite_message_is_treated_as_state_error(self) -> None:
        self.assertTrue(
            indicates_missing_state(
                "ms-data-parser::arf_re_pca",
                "先に arf_parser を実行してデータを読み込んでください。",
            )
        )


if __name__ == "__main__":
    unittest.main()
