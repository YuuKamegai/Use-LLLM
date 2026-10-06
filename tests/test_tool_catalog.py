from __future__ import annotations

import json
import unittest

from use_lllm.core.tool_catalog import (
    DESCRIBE_TOOL_NAME,
    MAX_SUMMARY_CHARS,
    ToolCatalog,
    summarize_description,
)


def tool(name: str, description: str) -> dict:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {"type": "object", "properties": {"path": {"type": "string"}}},
        },
    }


LONG = "[srv] mzTab を読み込む。\n\n呼び出し方:\n  - path: 絶対パス\n\n成功すると状態を保存する。"


class ToolCatalogTests(unittest.TestCase):
    def test_summary_keeps_first_paragraph_only(self) -> None:
        self.assertEqual(summarize_description(LONG), "[srv] mzTab を読み込む。")

    def test_summary_is_capped(self) -> None:
        summary = summarize_description("あ" * 1000)
        self.assertEqual(len(summary), MAX_SUMMARY_CHARS)
        self.assertTrue(summary.endswith("…"))

    def test_catalog_shortens_descriptions_but_keeps_every_tool_and_schema(self) -> None:
        source = [tool("srv::load", LONG), tool("srv::peek", "[srv] peek")]
        catalog = ToolCatalog.build(source)

        names = [item["function"]["name"] for item in catalog.tools]
        self.assertEqual(names, ["srv::load", "srv::peek", DESCRIBE_TOOL_NAME])
        self.assertEqual(catalog.tools[0]["function"]["description"], "[srv] mzTab を読み込む。")
        self.assertEqual(
            catalog.tools[0]["function"]["parameters"], source[0]["function"]["parameters"]
        )
        # 元の定義は変更しない（レジストリのキャッシュを汚さない）。
        self.assertEqual(source[0]["function"]["description"], LONG)

    def test_describe_tool_is_omitted_when_nothing_was_shortened(self) -> None:
        catalog = ToolCatalog.build([tool("srv::peek", "[srv] peek")])
        self.assertEqual([item["function"]["name"] for item in catalog.tools], ["srv::peek"])

    def test_describe_returns_full_description_and_schema(self) -> None:
        catalog = ToolCatalog.build([tool("srv::load", LONG)])

        content, is_error = catalog.describe({"name": "srv::load"})

        self.assertFalse(is_error)
        payload = json.loads(content)
        self.assertEqual(payload["description"], LONG)
        self.assertIn("path", payload["parameters"]["properties"])

    def test_describe_accepts_bare_tool_name(self) -> None:
        catalog = ToolCatalog.build([tool("srv::load", LONG)])
        content, is_error = catalog.describe({"name": "load"})
        self.assertFalse(is_error)
        self.assertEqual(json.loads(content)["name"], "srv::load")

    def test_describe_unknown_tool_is_error(self) -> None:
        catalog = ToolCatalog.build([tool("srv::load", LONG)])
        content, is_error = catalog.describe({"name": "srv::missing"})
        self.assertTrue(is_error)
        self.assertIn("srv::missing", content)


if __name__ == "__main__":
    unittest.main()
