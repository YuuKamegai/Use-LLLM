from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest

from use_lllm.core.context_memory import ContextMemoryManager, parse_summary_json
from use_lllm.core.sessions import SessionStore


class FakeSummarizer:
    def __init__(self) -> None:
        self.calls = []

    async def chat(self, messages, **kwargs):
        self.calls.append({"messages": messages, **kwargs})
        return SimpleNamespace(
            content=json.dumps(
                {
                    "objective": "PCAを再解析する",
                    "facts": ["ARFを読み込んだ"],
                    "decisions": [],
                    "constraints": ["最新条件を維持する"],
                    "open_questions": [],
                    "dataset_refs": ["test.arf"],
                },
                ensure_ascii=False,
            ),
            model="fake-summary",
        )


class ContextMemoryTests(unittest.IsolatedAsyncioTestCase):
    def test_markdown_fenced_summary_is_normalized(self) -> None:
        parsed = parse_summary_json(
            '```json\n{"summary":"ARFを読み込み、PCAを続行する。"}\n```'
        )
        self.assertEqual(parsed["objective"], "ARFを読み込み、PCAを続行する。")
        self.assertEqual(parsed["facts"], ["ARFを読み込み、PCAを続行する。"])

    async def test_large_history_is_incrementally_summarized_and_recent_turns_remain(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            store = SessionStore(Path(raw) / "state")
            session = store.create_session("chat", surface="general")
            for index in range(6):
                store.add_message(
                    session["id"], "user", f"user-{index}:" + "あ" * 280
                )
                store.add_message(
                    session["id"], "assistant", f"assistant-{index}:" + "い" * 280
                )
            ollama = FakeSummarizer()
            memory = ContextMemoryManager(
                ollama,
                store,
                context_window=2048,
                input_ratio=0.3,
                recent_user_turns=3,
            )

            assembled = await memory.assemble(session["id"], "system", [])

            self.assertTrue(assembled.summary_created)
            self.assertGreaterEqual(len(ollama.calls), 1)
            self.assertIn("format_schema", ollama.calls[0])
            encoded = json.dumps(assembled.messages, ensure_ascii=False)
            self.assertIn("セッション会話要約", encoded)
            self.assertNotIn("user-0", encoded)
            self.assertIn("user-3", encoded)
            self.assertIn("user-5", encoded)
            self.assertIsNotNone(store.latest_conversation_summary(session["id"]))

    async def test_raw_tool_result_stays_in_db_but_prompt_uses_compact_ledger(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            store = SessionStore(Path(raw) / "state")
            session = store.create_session("chat", surface="general")
            invocation = store.start_tool_invocation(
                session["id"],
                "ms-data-parser",
                "ms-data-parser::arf_parser",
                {"file_path": "C:/data/test.arf"},
            )
            raw_result = "PCA_RESULT " + "x" * 12000
            message_id = store.add_message(
                session["id"],
                "tool",
                raw_result,
                {"tool_name": "ms-data-parser::arf_parser", "is_error": False},
            )
            store.complete_tool_invocation(
                invocation,
                result_message_id=message_id,
                is_error=False,
                result_text=raw_result,
            )
            memory = ContextMemoryManager(FakeSummarizer(), store)

            assembled = await memory.assemble(session["id"], "system", [])

            self.assertEqual(
                store.list_messages(session["id"])[0]["content"], raw_result
            )
            encoded = json.dumps(assembled.messages, ensure_ascii=False)
            self.assertIn("MCPツール実行台帳", encoded)
            self.assertIn("tool result compacted", encoded)
            self.assertLess(len(encoded), len(raw_result))


if __name__ == "__main__":
    unittest.main()
