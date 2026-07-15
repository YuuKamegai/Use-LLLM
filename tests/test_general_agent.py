from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from use_lllm.core.mcp_client import MCPToolResult
from use_lllm.core.ollama import OllamaResponse
from use_lllm.core.policy import ToolDecision, ToolSafety
from use_lllm.core.sessions import SessionStore
from use_lllm.general.agent_loop import GeneralAgentLoop


def response(content: str = "", tool: str | None = None, arguments=None):
    message = {"role": "assistant", "content": content}
    if tool:
        message["tool_calls"] = [
            {"function": {"name": tool, "arguments": arguments or {}}}
        ]
    return OllamaResponse(message, "fake", None, None, None)


class FakeOllama:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    async def chat(self, messages, **kwargs):
        self.calls.append({"messages": messages, **kwargs})
        if not self.responses:
            raise AssertionError("unexpected Ollama call")
        return self.responses.pop(0)


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


class GeneralAgentTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.store = SessionStore(Path(self.temp.name) / "state")
        self.session = self.store.create_session("chat", surface="general")

    async def asyncTearDown(self) -> None:
        self.temp.cleanup()

    async def test_plain_chat_is_persisted(self) -> None:
        ollama = FakeOllama([response("こんにちは。")])
        loop = GeneralAgentLoop(ollama, self.store, FakeRegistry({}))

        result = await loop.chat(self.session["id"], "こんにちは")

        self.assertEqual(result["status"], "complete")
        self.assertEqual(result["content"], "こんにちは。")
        messages = self.store.list_messages(self.session["id"])
        self.assertEqual([item["role"] for item in messages], ["user", "assistant"])

    async def test_read_only_auto_tool_runs_then_model_answers(self) -> None:
        ollama = FakeOllama(
            [response(tool="srv::peek", arguments={"x": 1}), response("完了")]
        )
        registry = FakeRegistry(
            {"srv::peek": (ToolSafety.READ_ONLY, True)}
        )
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
        registry = FakeRegistry(
            {"srv::danger": (ToolSafety.UNKNOWN, False)}
        )
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
        ollama = FakeOllama(
            [response(tool="srv::danger"), response("拒否を了解しました")]
        )
        registry = FakeRegistry(
            {"srv::danger": (ToolSafety.UNKNOWN, False)}
        )
        loop = GeneralAgentLoop(ollama, self.store, registry)
        pending = await loop.chat(self.session["id"], "実行して")

        result = await loop.resolve_approval(
            self.session["id"], pending["approval"]["event_id"], approved=False
        )
        self.assertEqual(result["content"], "拒否を了解しました")
        self.assertEqual(registry.calls, [])

    async def test_step_limit_stops_repeated_tool_calls(self) -> None:
        ollama = FakeOllama(
            [response(tool="srv::peek"), response(tool="srv::peek")]
        )
        registry = FakeRegistry(
            {"srv::peek": (ToolSafety.READ_ONLY, True)}
        )
        loop = GeneralAgentLoop(ollama, self.store, registry, max_steps=2)

        result = await loop.chat(self.session["id"], "繰り返して")
        self.assertEqual(result["status"], "step_limit")
        self.assertEqual(len(registry.calls), 2)

    async def test_reconnect_replays_parser_before_re_pca(self) -> None:
        parser = "ms-data-parser::arf_parser"
        re_pca = "ms-data-parser::arf_re_pca"
        ollama = FakeOllama(
            [
                response(tool=parser, arguments={"file_path": "C:/data/test.arf"}),
                response("loaded"),
                response(tool=re_pca, arguments={"min_intensity": 10}),
                response("re-pca complete"),
            ]
        )
        registry = FakeRegistry(
            {
                parser: (ToolSafety.READ_ONLY, True),
                re_pca: (ToolSafety.READ_ONLY, True),
            }
        )
        loop = GeneralAgentLoop(ollama, self.store, registry)

        await loop.chat(self.session["id"], "ARFを読み込んで")
        registry.generation = 2
        result = await loop.chat(self.session["id"], "PCAを再実行して")

        self.assertEqual(result["content"], "re-pca complete")
        self.assertEqual(
            [item[0] for item in registry.calls],
            [parser, parser, re_pca],
        )
        ledger = self.store.list_tool_invocations(self.session["id"])
        replays = [item for item in ledger if item["replay_of_id"] is not None]
        self.assertEqual(len(replays), 1)
        self.assertEqual(replays[0]["tool_name"], parser)


if __name__ == "__main__":
    unittest.main()
