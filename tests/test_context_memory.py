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
                prompt_eval_count=40,
            )

            self.assertEqual(assembled.context_window, 4096)
            self.assertEqual(assembled.input_budget, int(4096 * 0.65))
            self.assertGreater(assembled.tool_tokens, 0)
            self.assertEqual(usage["prompt_tokens"], 40)
            self.assertGreater(usage["used_tokens"], 40)
            self.assertEqual(usage["accuracy"], "calibrated")
            self.assertEqual(usage["measurement_source"], "provider_prompt_plus_response_estimate")

    async def test_azure_profile_uses_260k_budget_and_provider_usage(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            store = SessionStore(Path(raw) / "state")
            session = store.create_session("azure", surface="general")
            store.add_message(session["id"], "user", "Azure context " + "x" * 3_000)
            memory = ContextMemoryManager(AzureProfileSummarizer(), store)
            assembled = await memory.assemble(session["id"], "system", [])
            usage = await memory.context_usage(
                assembled,
                model="gpt-5.4-mini-2026-03-17",
                response_content="done",
                prompt_eval_count=800,
                completion_eval_count=1_000,
            )

            self.assertEqual(assembled.context_window, 400_000)
            self.assertEqual(assembled.input_budget, 260_000)
            self.assertEqual(usage["max_input_tokens"], 272_000)
            self.assertEqual(usage["max_output_tokens"], 128_000)
            self.assertTrue(usage["context_window_confirmed"])
            self.assertEqual(usage["prompt_tokens"], 800)
            self.assertEqual(usage["completion_tokens"], 1_000)
            self.assertLess(usage["used_tokens"], 800 + 20)
            self.assertEqual(usage["accuracy"], "calibrated")

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

            # 実測で換算係数が変わるので、固定費も同じ係数で換算し直される。
            fixed = usage["fixed_tokens"]
            self.assertGreater(assembled.fixed_tokens, assembled.tool_tokens)
            self.assertAlmostEqual(
                fixed / assembled.fixed_tokens, usage["token_scale"], places=2
            )
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

    async def test_measured_prompt_calibrates_compaction_to_the_displayed_scale(self) -> None:
        # 表示は実測トークン、要約判定はバイト/3 の概算だと単位がずれ、メーターに
        # 残りがあるうちに要約が走る。実測/概算の比率で判定側も換算する。
        with tempfile.TemporaryDirectory() as raw:
            store = SessionStore(Path(raw) / "state")
            session = store.create_session("chat", surface="general")
            store.add_message(session["id"], "user", "最初" + "あ" * 400)
            summarizer = FakeSummarizer()
            memory = ContextMemoryManager(summarizer, store, context_window=4096)
            first = await memory.assemble(session["id"], "system", [])
            usage = await memory.context_usage(
                first,
                model="fake",
                response_content="了解",
                prompt_eval_count=int(first.estimated_tokens * 0.6),
            )
            store.update_session(session["id"], state_patch={"context_usage": usage})
            self.assertAlmostEqual(usage["token_scale"], 0.6, places=2)

            # 概算では入力予算 2662 を超えるが、実測換算（×0.6）では収まる量。
            for index in range(6):
                store.add_message(session["id"], "assistant", f"回答{index}" + "い" * 260)
                store.add_message(session["id"], "user", f"質問{index}" + "う" * 260)
            assembled = await memory.assemble(session["id"], "system", [])

            self.assertGreater(assembled.estimated_tokens / 0.6, assembled.input_budget)
            self.assertLess(assembled.estimated_tokens, assembled.input_budget)
            self.assertFalse(assembled.summary_created)
            self.assertEqual(summarizer.calls, [])

    async def test_implausible_measurement_does_not_calibrate(self) -> None:
        # KV キャッシュ再利用時に新規評価分しか返らない実装では実測値が極端に小さい。
        # それを信じると要約が遅れてモデル上限を溢れるので、採用しない。
        with tempfile.TemporaryDirectory() as raw:
            store = SessionStore(Path(raw) / "state")
            session = store.create_session("chat", surface="general")
            store.add_message(session["id"], "user", "あ" * 900)
            memory = ContextMemoryManager(FakeSummarizer(), store, context_window=4096)
            assembled = await memory.assemble(session["id"], "system", [])

            usage = await memory.context_usage(
                assembled, model="fake", response_content="ok", prompt_eval_count=12
            )

            self.assertEqual(usage["token_scale"], 1.0)
            self.assertGreater(usage["prompt_tokens"], 900)
            self.assertEqual(usage["measurement_source"], "local_estimate")

    async def test_reasoning_tokens_are_not_counted_as_carried_history(self) -> None:
        # eval_count には思考トークンが含まれるが、履歴に残るのは本文だけ。
        with tempfile.TemporaryDirectory() as raw:
            store = SessionStore(Path(raw) / "state")
            session = store.create_session("chat", surface="general")
            store.add_message(session["id"], "user", "考えて")
            memory = ContextMemoryManager(FakeSummarizer(), store, context_window=32768)
            assembled = await memory.assemble(session["id"], "system", [])

            usage = await memory.context_usage(
                assembled,
                model="fake",
                response_content="答え",
                prompt_eval_count=assembled.estimated_tokens,
                completion_eval_count=5_000,
            )

            self.assertEqual(usage["completion_tokens"], 5_000)
            self.assertLess(usage["used_tokens"], assembled.estimated_tokens + 100)

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
