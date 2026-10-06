from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from use_lllm.core.mcp_client import MCPToolResult
from use_lllm.core.ollama import OllamaResponse
from use_lllm.core.policy import ToolDecision, ToolSafety
from use_lllm.core.sessions import SessionStore
from use_lllm.core.tool_catalog import DESCRIBE_TOOL_NAME
from use_lllm.general.agent_loop import MAX_SERVER_INSTRUCTIONS_CHARS, GeneralAgentLoop


def response(
    content: str = "",
    tool: str | None = None,
    arguments=None,
    *,
    prompt_eval_count: int | None = None,
    eval_count: int | None = None,
):
    message = {"role": "assistant", "content": content}
    if tool:
        message["tool_calls"] = [{"function": {"name": tool, "arguments": arguments or {}}}]
    return OllamaResponse(message, "fake", None, prompt_eval_count, eval_count)


class FakeOllama:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    async def chat(self, messages, **kwargs):
        self.calls.append({"messages": messages, **kwargs})
        if not self.responses:
            raise AssertionError("unexpected Ollama call")
        return self.responses.pop(0)


class StreamingOllama(FakeOllama):
    async def stream_chat(self, messages, **kwargs):
        self.calls.append({"messages": messages, **kwargs})
        yield {"model": "fake", "message": {"content": "こん"}}
        yield {"model": "fake", "message": {"content": "にちは"}}
        yield {
            "model": "fake",
            "message": {},
            "done": True,
            "prompt_eval_count": 120,
            "eval_count": 8,
        }


class FakeRegistry:
    def __init__(self, decisions):
        self.decisions = decisions
        self.calls = []
        self.generation = 1

    async def ensure_session(self, session_id, server_name):
        return self.generation

    def ollama_tools(self, query=None, *, limit=12):
        return [
            {
                "type": "function",
                "function": {
                    "name": "srv::peek",
                    "description": "peek",
                    "parameters": {"type": "object"},
                },
            }
        ]

    def decide(self, name, *, approved=False, network_mode="offline"):
        safety, auto = self.decisions[name]
        allowed = approved or auto
        return ToolDecision(
            name,
            safety,
            allowed,
            not auto,
            "allowed" if allowed else "approval required",
        )

    def is_replay_safe(self, name):
        return True

    def may_write_files(self, name):
        return True

    async def call_tool(
        self,
        name,
        arguments=None,
        *,
        approved=False,
        network_mode="offline",
        session_id=None,
    ):
        self.calls.append((name, arguments or {}, approved, network_mode))
        return MCPToolResult(
            name,
            False,
            ({"type": "text", "text": f"result:{name}"},),
            None,
        )


class SavedPngRegistry(FakeRegistry):
    def __init__(self, source: Path):
        super().__init__({})
        self.source = source

    async def call_tool(
        self,
        name,
        arguments=None,
        *,
        approved=False,
        network_mode="offline",
        session_id=None,
    ):
        return MCPToolResult(
            name,
            False,
            ({"type": "text", "text": f"PCA図を保存: {self.source}"},),
            None,
        )


class GeneralAgentTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.store = SessionStore(Path(self.temp.name) / "state")
        self.session = self.store.create_session("chat", surface="general")

    async def asyncTearDown(self) -> None:
        self.temp.cleanup()

    async def test_plain_chat_is_persisted(self) -> None:
        ollama = FakeOllama([response("こんにちは。", prompt_eval_count=80, eval_count=6)])
        loop = GeneralAgentLoop(ollama, self.store, FakeRegistry({}))

        result = await loop.chat(self.session["id"], "こんにちは")

        self.assertEqual(result["status"], "complete")
        self.assertEqual(result["content"], "こんにちは。")
        messages = self.store.list_messages(self.session["id"])
        self.assertEqual([item["role"] for item in messages], ["user", "assistant"])
        usage = self.store.get_session(self.session["id"])["state"]["context_usage"]
        self.assertEqual(usage["prompt_tokens"], 80)
        self.assertEqual(usage["measurement_source"], "provider_usage")
        self.assertEqual(usage["completion_tokens"], 6)
        self.assertEqual(usage["accuracy"], "measured")

    async def test_default_title_uses_first_user_message(self) -> None:
        session = self.store.create_session("新しいチャット", surface="general")
        loop = GeneralAgentLoop(FakeOllama([response("了解")]), self.store, FakeRegistry({}))

        await loop.chat(session["id"], "  反応速度式を\n   わかりやすく説明して  ")

        self.assertEqual(
            self.store.get_session(session["id"])["title"],
            "反応速度式を わかりやすく説明して",
        )

    async def test_custom_title_is_preserved(self) -> None:
        loop = GeneralAgentLoop(FakeOllama([response("了解")]), self.store, FakeRegistry({}))

        await loop.chat(self.session["id"], "最初の質問")

        self.assertEqual(self.store.get_session(self.session["id"])["title"], "chat")

    async def test_lipidmix_saved_png_is_copied_into_session_artifacts(self) -> None:
        source = Path(self.temp.name) / "pca-result.png"
        png = b"\x89PNG\r\n\x1a\n" + b"test-image"
        source.write_bytes(png)
        loop = GeneralAgentLoop(FakeOllama([]), self.store, SavedPngRegistry(source))

        captured = await loop._call_and_record(
            self.session["id"],
            "lipidmix::save_pca_figure",
            {},
            approved=True,
            network_mode="offline",
        )

        self.assertEqual(len(captured.content), 2)
        image = captured.content[1]
        self.assertEqual(image["type"], "artifact_image")
        self.assertEqual(image["mimeType"], "image/png")
        self.assertTrue(image["url"].startswith(f"./api/sessions/{self.session['id']}/"))
        self.assertNotIn(str(source), image["url"])
        stored = list((self.store.artifact_root / self.session["id"]).glob("*-pca-result.png"))
        self.assertEqual(len(stored), 1)
        self.assertEqual(stored[0].read_bytes(), png)
        message = self.store.list_messages(self.session["id"])[-1]
        self.assertEqual(message["metadata"]["content_blocks"][1], image)

    async def test_png_capture_rejects_unknown_tools_and_invalid_files(self) -> None:
        source = Path(self.temp.name) / "not-really.png"
        source.write_bytes(b"not a png")
        result = MCPToolResult(
            "lipidmix::save_pca_figure",
            False,
            ({"type": "text", "text": f"保存: {source}"},),
            None,
        )
        loop = GeneralAgentLoop(FakeOllama([]), self.store, FakeRegistry({}))

        invalid = loop._capture_png_artifacts(
            self.session["id"], "lipidmix::save_pca_figure", result
        )
        unknown = loop._capture_png_artifacts(
            self.session["id"], "lipidmix::some_other_tool", result
        )

        self.assertIs(invalid, result)
        self.assertIs(unknown, result)
        self.assertEqual(list((self.store.artifact_root / self.session["id"]).iterdir()), [])

    async def test_default_title_is_not_replaced_by_second_message(self) -> None:
        session = self.store.create_session("新しいチャット", surface="general")
        loop = GeneralAgentLoop(
            FakeOllama([response("最初"), response("次")]), self.store, FakeRegistry({})
        )

        await loop.chat(session["id"], "新しいチャット")
        await loop.chat(session["id"], "二番目の入力")

        self.assertEqual(self.store.get_session(session["id"])["title"], "新しいチャット")

    async def test_stream_chat_forwards_deltas_and_persists_final_answer(self) -> None:
        loop = GeneralAgentLoop(StreamingOllama([]), self.store, FakeRegistry({}))

        events = [item async for item in loop.chat_stream(self.session["id"], "こんにちは")]

        self.assertEqual(
            [item["type"] for item in events], ["status", "delta", "delta", "complete"]
        )
        self.assertEqual("".join(item.get("content", "") for item in events[1:3]), "こんにちは")
        self.assertEqual(self.store.list_messages(self.session["id"])[-1]["content"], "こんにちは")
        usage = self.store.get_session(self.session["id"])["state"]["context_usage"]
        self.assertEqual(usage["prompt_tokens"], 120)
        self.assertEqual(events[-1]["context_usage"], usage)

    async def test_stream_chat_reports_automatic_title(self) -> None:
        session = self.store.create_session("新しいチャット", surface="general")
        loop = GeneralAgentLoop(StreamingOllama([]), self.store, FakeRegistry({}))

        events = [item async for item in loop.chat_stream(session["id"], "速度式を説明して")]

        self.assertEqual(events[0]["session_title"], "速度式を説明して")
        self.assertEqual(self.store.get_session(session["id"])["title"], "速度式を説明して")

    async def test_read_only_auto_tool_runs_then_model_answers(self) -> None:
        ollama = FakeOllama([response(tool="srv::peek", arguments={"x": 1}), response("完了")])
        registry = FakeRegistry({"srv::peek": (ToolSafety.READ_ONLY, True)})
        loop = GeneralAgentLoop(ollama, self.store, registry)

        result = await loop.chat(self.session["id"], "確認して")

        self.assertEqual(result["status"], "complete")
        self.assertEqual(registry.calls[0][:2], ("srv::peek", {"x": 1}))
        self.assertIn("srv::peek", ollama.calls[0]["messages"][0]["content"])
        roles = [item["role"] for item in self.store.list_messages(self.session["id"])]
        self.assertEqual(roles, ["user", "assistant", "tool", "assistant"])

    async def test_tool_waits_for_approval_and_resumes(self) -> None:
        ollama = FakeOllama(
            [response(tool="srv::danger", arguments={"path": "C:/data"}), response("承認後完了")]
        )
        registry = FakeRegistry({"srv::danger": (ToolSafety.UNKNOWN, False)})
        loop = GeneralAgentLoop(ollama, self.store, registry)

        pending = await loop.chat(self.session["id"], "実行して")
        self.assertEqual(pending["status"], "approval_required")
        self.assertEqual(registry.calls, [])

        completed = await loop.resolve_approval(
            self.session["id"], pending["approval"]["event_id"], approved=True
        )
        self.assertEqual(completed["status"], "complete")
        self.assertTrue(registry.calls[0][2])
        self.assertEqual(
            self.store.get_session(self.session["id"])["state"]["pending_approval"],
            None,
        )

    async def test_rejected_tool_is_not_called_and_model_can_answer(self) -> None:
        ollama = FakeOllama([response(tool="srv::danger"), response("拒否を了解しました")])
        registry = FakeRegistry({"srv::danger": (ToolSafety.UNKNOWN, False)})
        loop = GeneralAgentLoop(ollama, self.store, registry)
        pending = await loop.chat(self.session["id"], "実行して")

        result = await loop.resolve_approval(
            self.session["id"], pending["approval"]["event_id"], approved=False
        )
        self.assertEqual(result["content"], "拒否を了解しました")
        self.assertEqual(registry.calls, [])

    async def test_step_limit_stops_repeated_tool_calls(self) -> None:
        ollama = FakeOllama([response(tool="srv::peek"), response(tool="srv::peek")])
        registry = FakeRegistry({"srv::peek": (ToolSafety.READ_ONLY, True)})
        loop = GeneralAgentLoop(ollama, self.store, registry, max_steps=2)

        result = await loop.chat(self.session["id"], "繰り返して")
        self.assertEqual(result["status"], "step_limit")
        self.assertEqual(len(registry.calls), 2)

    async def test_server_instructions_reach_system_prompt_for_offered_servers_only(
        self,
    ) -> None:
        # MCP サーバの instructions（入口の判定規則など）が無いと、ローカルモデルは
        # 「解析済みの出力」と言われても生データ向けツールから探索を始めてしまう。
        ollama = FakeOllama([response("了解")])
        registry = FakeRegistry({})
        registry.server_instructions = lambda: {
            "srv": "ENTRY POINT: outputs -> load_dataset",
            "absent": "このサーバのツールは今回渡していない",
        }
        loop = GeneralAgentLoop(ollama, self.store, registry)

        await loop.chat(self.session["id"], "解析して")

        system = ollama.calls[0]["messages"][0]["content"]
        self.assertIn("ENTRY POINT: outputs -> load_dataset", system)
        self.assertIn("srv", system)
        self.assertNotIn("このサーバのツールは今回渡していない", system)

    async def test_oversized_server_instructions_are_truncated(self) -> None:
        ollama = FakeOllama([response("了解")])
        registry = FakeRegistry({})
        registry.server_instructions = lambda: {"srv": "A" * (MAX_SERVER_INSTRUCTIONS_CHARS + 500)}
        loop = GeneralAgentLoop(ollama, self.store, registry)

        await loop.chat(self.session["id"], "解析して")

        system = ollama.calls[0]["messages"][0]["content"]
        self.assertIn("A" * MAX_SERVER_INSTRUCTIONS_CHARS, system)
        self.assertNotIn("A" * (MAX_SERVER_INSTRUCTIONS_CHARS + 1), system)

    async def test_registry_without_instructions_support_still_works(self) -> None:
        ollama = FakeOllama([response("了解")])
        loop = GeneralAgentLoop(ollama, self.store, FakeRegistry({}))

        result = await loop.chat(self.session["id"], "こんにちは")

        self.assertEqual(result["status"], "complete")

    async def test_model_sees_summarized_descriptions_plus_describe_tool(self) -> None:
        ollama = FakeOllama([response("了解")])
        loop = GeneralAgentLoop(ollama, self.store, LongDescriptionRegistry({}))

        await loop.chat(self.session["id"], "読み込んで")

        offered = {item["function"]["name"]: item["function"] for item in ollama.calls[0]["tools"]}
        self.assertEqual(set(offered), {"srv::load", DESCRIBE_TOOL_NAME})
        self.assertEqual(offered["srv::load"]["description"], "[srv] 読み込む。")

    async def test_describe_tool_answers_locally_with_full_text(self) -> None:
        ollama = FakeOllama(
            [response(tool=DESCRIBE_TOOL_NAME, arguments={"name": "srv::load"}), response("了解")]
        )
        # decisions が空なので、decide や call_tool に渡れば KeyError / 呼び出し記録で分かる。
        registry = LongDescriptionRegistry({})
        loop = GeneralAgentLoop(ollama, self.store, registry)

        result = await loop.chat(self.session["id"], "読み込んで")

        self.assertEqual(result["status"], "complete")
        self.assertEqual(registry.calls, [])
        # 900 文字の結果圧縮を受けず、説明全文が次のプロンプトに届く。
        second_prompt = json.dumps(ollama.calls[1]["messages"], ensure_ascii=False)
        self.assertIn("詳細" * 600, second_prompt)
        self.assertEqual(self.store.list_tool_invocations(self.session["id"]), [])

    async def test_stream_describe_tool_answers_locally(self) -> None:
        ollama = ScriptedStreamingOllama(
            [
                {"tool_calls": [{"function": {"name": DESCRIBE_TOOL_NAME, "arguments": {"name": "load"}}}]},
                {"content": "了解"},
            ]
        )
        registry = LongDescriptionRegistry({})
        loop = GeneralAgentLoop(ollama, self.store, registry)

        events = [item async for item in loop.chat_stream(self.session["id"], "読み込んで")]

        self.assertEqual(events[-1]["type"], "complete")
        self.assertEqual(registry.calls, [])
        self.assertIn(
            {"type": "tool_result", "tool": DESCRIBE_TOOL_NAME, "is_error": False, "step": 1},
            events,
        )


class LongDescriptionRegistry(FakeRegistry):
    def ollama_tools(self, query=None, *, limit=12):
        return [
            {
                "type": "function",
                "function": {
                    "name": "srv::load",
                    "description": "[srv] 読み込む。\n\n" + "詳細" * 600,
                    "parameters": {"type": "object"},
                },
            }
        ]


class ScriptedStreamingOllama(FakeOllama):
    def __init__(self, messages):
        super().__init__([])
        self.messages = list(messages)

    async def stream_chat(self, messages, **kwargs):
        self.calls.append({"messages": messages, **kwargs})
        yield {"model": "fake", "message": self.messages.pop(0)}
        yield {"model": "fake", "message": {}, "done": True}


if __name__ == "__main__":
    unittest.main()
