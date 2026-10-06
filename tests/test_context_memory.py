from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

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


class LateLoadedSummarizer(FakeSummarizer):
    def __init__(self) -> None:
        super().__init__()
        self.loaded = False

    async def context_window(self):
        return 8192 if self.loaded else None


class AzureProfileSummarizer(FakeSummarizer):
    async def context_window_details(self):
        return {
            "context_window": 400_000,
            "max_input_tokens": 272_000,
            "max_output_tokens": 128_000,
            "source": "azure_model_profile",
        }


class ContextMemoryTests(unittest.IsolatedAsyncioTestCase):
    def test_markdown_fenced_summary_is_normalized(self) -> None:
        parsed = parse_summary_json('```json\n{"summary":"ARFを読み込み、PCAを続行する。"}\n```')
        self.assertEqual(parsed["objective"], "ARFを読み込み、PCAを続行する。")
        self.assertEqual(parsed["facts"], ["ARFを読み込み、PCAを続行する。"])

    async def test_large_history_is_incrementally_summarized_and_recent_turns_remain(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            store = SessionStore(Path(raw) / "state")
            session = store.create_session("chat", surface="general")
            for index in range(6):
                store.add_message(session["id"], "user", f"user-{index}:" + "あ" * 280)
                store.add_message(session["id"], "assistant", f"assistant-{index}:" + "い" * 280)
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

            self.assertEqual(store.list_messages(session["id"])[0]["content"], raw_result)
            encoded = json.dumps(assembled.messages, ensure_ascii=False)
            self.assertIn("MCPツール実行台帳", encoded)
            self.assertIn("tool result compacted", encoded)
            self.assertLess(len(encoded), len(raw_result))

    async def test_context_usage_combines_ollama_prompt_count_with_response_estimate(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            store = SessionStore(Path(raw) / "state")
            session = store.create_session("chat", surface="general")
            store.add_message(session["id"], "user", "現在の残量を確認")
            memory = ContextMemoryManager(FakeSummarizer(), store, context_window=4096)
            assembled = await memory.assemble(
                session["id"],
                "system",
                [{"type": "function", "function": {"name": "srv::peek"}}],
            )

            usage = await memory.context_usage(
                assembled,
                model="fake",
                response_content="確認しました。",
                response_metadata={"step": 1},
                prompt_eval_count=600,
            )

            self.assertEqual(assembled.context_window, 4096)
            self.assertEqual(assembled.input_budget, int(4096 * 0.65))
            self.assertGreater(assembled.tool_tokens, 0)
            self.assertEqual(usage["prompt_tokens"], 600)
            self.assertGreater(usage["used_tokens"], 600)
            self.assertEqual(usage["accuracy"], "estimated")
            self.assertEqual(usage["measurement_source"], "provider_prompt_plus_response_estimate")

    async def test_azure_profile_uses_260k_budget_and_provider_usage(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            store = SessionStore(Path(raw) / "state")
            session = store.create_session("azure", surface="general")
            store.add_message(session["id"], "user", "Azure context")
            memory = ContextMemoryManager(AzureProfileSummarizer(), store)
            assembled = await memory.assemble(session["id"], "system", [])
            usage = await memory.context_usage(
                assembled,
                model="gpt-5.4-mini-2026-03-17",
                response_content="done",
                prompt_eval_count=9_000,
                completion_eval_count=1_000,
            )

            self.assertEqual(assembled.context_window, 400_000)
            self.assertEqual(assembled.input_budget, 260_000)
            self.assertEqual(usage["max_input_tokens"], 272_000)
            self.assertEqual(usage["max_output_tokens"], 128_000)
            self.assertTrue(usage["context_window_confirmed"])
            self.assertEqual(usage["used_tokens"], 10_000)
            self.assertEqual(usage["accuracy"], "measured")
            self.assertEqual(usage["measurement_source"], "provider_usage")

    async def test_remaining_percent_is_measured_against_conversation_room(self) -> None:
        # ツール定義とシステムプロンプトは要約しても減らない固定費。残り%は
        # 会話に使える分を分母にしないと、開始時点から 0% 近くに見えてしまう。
        with tempfile.TemporaryDirectory() as raw:
            store = SessionStore(Path(raw) / "state")
            session = store.create_session("chat", surface="general")
            store.add_message(session["id"], "user", "残量")
            memory = ContextMemoryManager(FakeSummarizer(), store, context_window=32768)
            tools = [{"type": "function", "function": {"name": "srv::t", "description": "x" * 30_000}}]
            assembled = await memory.assemble(session["id"], "system", tools)

            usage = await memory.context_usage(
                assembled, model="fake", response_content="ok", prompt_eval_count=12_000
            )

            fixed = assembled.fixed_tokens
            self.assertGreater(fixed, assembled.tool_tokens)
            self.assertEqual(usage["fixed_tokens"], fixed)
            self.assertEqual(usage["conversation_budget"], assembled.input_budget - fixed)
            self.assertFalse(usage["fixed_overflow"])
            self.assertEqual(
                usage["remaining_percent"],
                round(usage["remaining_tokens"] / usage["conversation_budget"] * 100),
            )

    async def test_fixed_cost_over_budget_skips_futile_compaction(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            store = SessionStore(Path(raw) / "state")
            session = store.create_session("chat", surface="general")
            for index in range(6):
                store.add_message(session["id"], "user", f"質問{index} " + "あ" * 600)
                store.add_message(session["id"], "assistant", "回答" + "い" * 600)
            summarizer = FakeSummarizer()
            memory = ContextMemoryManager(summarizer, store, context_window=4096)
            tools = [{"type": "function", "function": {"name": "srv::t", "description": "x" * 9_000}}]

            assembled = await memory.assemble(session["id"], "system", tools)
            usage = await memory.context_usage(assembled, model="fake", response_content="ok")

            self.assertEqual(summarizer.calls, [])
            self.assertFalse(assembled.summary_created)
            self.assertTrue(assembled.fixed_overflow)
            self.assertTrue(usage["fixed_overflow"])
            self.assertEqual(usage["conversation_budget"], 0)
            self.assertEqual(usage["remaining_percent"], 0)

    async def test_describe_tool_result_is_not_compacted(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            store = SessionStore(Path(raw) / "state")
            session = store.create_session("chat", surface="general")
            store.add_message(session["id"], "user", "詳細")
            full = "説明" * 2000
            store.add_message(
                session["id"], "tool", full, {"tool_name": "use_lllm::describe_tool", "tool_reference": True}
            )
            memory = ContextMemoryManager(FakeSummarizer(), store, context_window=65536)

            assembled = await memory.assemble(session["id"], "system", [])

            self.assertEqual(assembled.messages[-1]["content"], full)

    async def test_context_window_is_retried_after_model_load(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            store = SessionStore(Path(raw) / "state")
            session = store.create_session("chat", surface="general")
            store.add_message(session["id"], "user", "モデルをロード")
            ollama = LateLoadedSummarizer()
            memory = ContextMemoryManager(ollama, store)

            assembled = await memory.assemble(session["id"], "system", [])
            self.assertEqual(assembled.context_window, 32768)
            ollama.loaded = True

            usage = await memory.context_usage(
                assembled,
                model="fake",
                response_content="ロード完了",
                prompt_eval_count=120,
            )

            self.assertEqual(usage["context_window"], 8192)
            self.assertEqual(usage["context_window_source"], "ollama")


if __name__ == "__main__":
    unittest.main()
