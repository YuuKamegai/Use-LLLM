"""ツール結果契約リーダーの回帰テスト。サーバ固有の知識をここに入れない。"""

from __future__ import annotations

import json
import unittest

from use_lllm.core.tool_result_contract import read_missing_state


def envelope(state="preprocessed_matrix", tools=("arf_preprocess",), message="先に実行"):
    return json.dumps(
        {
            "error": {
                "code": "missing_state",
                "state": state,
                "required_tools": list(tools),
                "message": message,
            }
        },
        ensure_ascii=False,
    )


class ReadMissingStateTests(unittest.TestCase):
    def test_reads_a_well_formed_envelope(self) -> None:
        found = read_missing_state(envelope())
        self.assertIsNotNone(found)
        self.assertEqual(found.state, "preprocessed_matrix")
        self.assertEqual(found.required_tools, ("arf_preprocess",))
        self.assertEqual(found.message, "先に実行")

    def test_preserves_alternative_order(self) -> None:
        found = read_missing_state(envelope(tools=("a", "b")))
        self.assertEqual(found.required_tools, ("a", "b"))

    def test_reads_an_envelope_embedded_in_surrounding_text(self) -> None:
        """FastMCP は例外を "Error executing tool <name>: " で前置する。"""
        raw = "Error executing tool arf_plot_volcano: " + envelope()
        found = read_missing_state(raw)
        self.assertIsNotNone(found)
        self.assertEqual(found.state, "preprocessed_matrix")

    def test_plain_text_is_not_an_envelope(self) -> None:
        self.assertIsNone(read_missing_state("前処理後の行列がありません。"))

    def test_unrelated_json_is_not_an_envelope(self) -> None:
        self.assertIsNone(read_missing_state('{"status": "success", "rows": []}'))

    def test_unknown_error_code_is_ignored(self) -> None:
        raw = json.dumps({"error": {"code": "rate_limited", "message": "後で"}})
        self.assertIsNone(read_missing_state(raw))

    def test_envelope_without_required_tools_is_rejected(self) -> None:
        """復旧の手掛かりが無いものは契約違反。エンベロープとして扱わない。"""
        raw = json.dumps(
            {
                "error": {
                    "code": "missing_state",
                    "state": "x",
                    "message": "m",
                    "required_tools": [],
                }
            }
        )
        self.assertIsNone(read_missing_state(raw))

    def test_envelope_with_non_string_tools_is_rejected(self) -> None:
        raw = json.dumps(
            {
                "error": {
                    "code": "missing_state",
                    "state": "x",
                    "message": "m",
                    "required_tools": [1, 2],
                }
            }
        )
        self.assertIsNone(read_missing_state(raw))

    def test_empty_and_none_input_are_safe(self) -> None:
        self.assertIsNone(read_missing_state(""))
        self.assertIsNone(read_missing_state(None))

    def test_non_string_input_never_raises(self) -> None:
        """全サーバの全ツール結果が通る経路なので、例外を出したら全呼び出しが壊れる。"""
        for value in (123, 3.14, True, {"a": 1}, [1, 2, 3], object()):
            with self.subTest(value=value):
                self.assertIsNone(read_missing_state(value))

    def test_json_array_is_not_an_envelope(self) -> None:
        self.assertIsNone(read_missing_state("[1, 2, 3]"))


if __name__ == "__main__":
    unittest.main()
