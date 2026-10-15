import asyncio
import json

import httpx
import pytest

from tests.test_general_agent import FakeOllama, response
from tests.test_web_registry import Service
from use_lllm.core.azure_openai import AzureOpenAIClient, AzureOpenAIConfig
from use_lllm.core.mcp_registry import MCPRegistry
from use_lllm.core.sessions import SessionStore
from use_lllm.general.agent_loop import GeneralAgentLoop
from use_lllm.web.budget import consume_web_budget, reset_web_budget
from use_lllm.web.registry import WebRegistry


def test_budget_search_four_page_six(tmp_path):
    store = SessionStore(tmp_path)
    sid = store.create_session("test", surface="general")["id"]
    reset_web_budget(store, sid)
    for name, limit in [("web::search", 4), ("web::read_page", 6)]:
        for _ in range(limit):
            assert consume_web_budget(store, sid, name) is None
        failure = consume_web_budget(store, sid, name)
        assert failure.is_error and failure.payload["limit_scope"] == "turn"
    assert consume_web_budget(store, sid, "other::search") is None
    reset_web_budget(store, sid)
    assert consume_web_budget(store, sid, "web::search") is None


class StreamingModel(FakeOllama):
    async def stream_chat(self, messages, **kwargs):
        reply = await self.chat(messages, **kwargs)
        yield {"model": "fake", "message": reply.message}


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("provider", ["ollama", "azure"])
def test_fact_check_flow_for_both_providers(tmp_path, stream, provider):
    store = SessionStore(tmp_path)
    sid = store.create_session(provider, surface="general")["id"]
    service = Service()
    registry = WebRegistry(MCPRegistry([]), search=service, pages=service, enabled=True)
    model = StreamingModel(
        [
            response(tool="web::search", arguments={"query": "latest public facts"}),
            response(tool="web::read_page", arguments={"url": "https://example.org/"}),
            response("確認しました。[根拠](https://example.org/)"),
        ]
    )
    calls = model.calls
    if provider == "azure":
        replies = list(model.responses)

        def api_reply(request):
            payload = json.loads(request.content)
            calls.append(payload)
            message = dict(replies.pop(0).message)
            if message.get("tool_calls"):
                original = message["tool_calls"][0]["function"]
                safe = next(
                    t["function"]["name"]
                    for t in payload["tools"]
                    if t["function"]["name"].startswith(original["name"].replace("::", "__") + "_")
                )
                message["tool_calls"] = [
                    {
                        "id": "call_1",
                        "index": 0,
                        "type": "function",
                        "function": {"name": safe, "arguments": json.dumps(original["arguments"])},
                    }
                ]
            if payload["stream"]:
                content = (
                    "data: "
                    + json.dumps({"model": "fake", "choices": [{"delta": message}]})
                    + "\n\ndata: [DONE]\n\n"
                )
                return httpx.Response(
                    200, text=content, headers={"content-type": "text/event-stream"}
                )
            return httpx.Response(200, json={"model": "fake", "choices": [{"message": message}]})

        model = AzureOpenAIClient(
            AzureOpenAIConfig("https://example.openai.azure.com", "fake-key", "fake"),
            transport=httpx.MockTransport(api_reply),
        )
    agent = GeneralAgentLoop(model, store, registry)

    async def check():
        if stream:
            events = [event async for event in agent.chat_stream(sid, "最新情報を確認して")]
            assert [e["tool"] for e in events if e["type"] == "tool_started"] == [
                "web::search",
                "web::read_page",
            ]
            assert not any(e["type"] == "approval_required" for e in events)
        else:
            result = await agent.chat(sid, "最新情報を確認して")
            assert result["status"] == "complete"
        assert service.calls == ["latest public facts"]
        messages = store.list_messages(sid)
        assert "https://example.org/" in messages[-1]["content"]
        assert store.get_session(sid)["state"]["web_budget"] == {"search": 1, "read_page": 1}
        assert "一次資料" in calls[0]["messages"][0]["content"]

    asyncio.run(check())


def test_disabled_web_tool_cannot_be_called_by_hallucinated_alias(tmp_path):
    store = SessionStore(tmp_path)
    sid = store.create_session("test", surface="general")["id"]
    store.update_session(sid, state_patch={"disabled_tools": ["web::search"]})
    service = Service()
    registry = WebRegistry(MCPRegistry([]), search=service, pages=service, enabled=True)
    agent = GeneralAgentLoop(StreamingModel([]), store, registry)
    result = asyncio.run(
        agent._registry_call(sid, "search", {"query": "q"}, approved=True, network_mode="online")
    )
    assert result.is_error and not service.calls


def test_translation_no_search_and_budget_survives_rebuild(tmp_path):
    store = SessionStore(tmp_path)
    sid = store.create_session("test", surface="general")["id"]
    service = Service()
    registry = WebRegistry(MCPRegistry([]), search=service, pages=service, enabled=True)
    model = StreamingModel([response("こんにちは")])
    asyncio.run(GeneralAgentLoop(model, store, registry).chat(sid, "検索せずHelloを翻訳して"))
    assert not service.calls
    instructions = model.calls[0]["messages"][0]["content"]
    assert "検索しない" in instructions and "確認できなかった" in instructions
    consume_web_budget(store, sid, "web::search")
    rebuilt = GeneralAgentLoop(StreamingModel([]), store, registry)
    assert rebuilt.sessions.get_session(sid)["state"]["web_budget"]["search"] == 1


def test_unqualified_builtin_executes_through_full_loop(tmp_path):
    store = SessionStore(tmp_path)
    sid = store.create_session("alias", surface="general")["id"]
    service = Service()
    registry = WebRegistry(MCPRegistry([]), search=service, pages=service, enabled=True)
    model = StreamingModel([response(tool="search", arguments={"query": "q"}), response("done")])
    result = asyncio.run(GeneralAgentLoop(model, store, registry).chat(sid, "確認して"))
    assert result["status"] == "complete" and service.calls == ["q"]


def test_instructions_follow_availability_and_failed_search(tmp_path):
    from use_lllm.web.contracts import failure
    from use_lllm.web.instructions import web_instructions

    assert web_instructions([]) == ""
    text = web_instructions(["web::read_page"])
    assert "web::search" not in text and "web::read_page" in text

    class Failed(Service):
        async def search(self, **kwargs):
            return failure("search_failed", "Failed")

    store = SessionStore(tmp_path)
    sid = store.create_session("error", surface="general")["id"]
    service = Failed()
    registry = WebRegistry(MCPRegistry([]), search=service, pages=service, enabled=True)
    model = StreamingModel(
        [response(tool="web::search", arguments={"query": "q"}), response("確認できなかった")]
    )
    asyncio.run(GeneralAgentLoop(model, store, registry).chat(sid, "確認して"))
    assert "search_failed" in str(model.calls[-1]["messages"])
    assert "確認できなかった" in store.list_messages(sid)[-1]["content"]


@pytest.mark.parametrize("stream", [False, True])
def test_all_web_failures_cannot_be_presented_as_verified(tmp_path, stream):
    from use_lllm.web.contracts import failure

    class Failed(Service):
        async def search(self, **kwargs):
            return failure("search_failed", "network failed")

    store = SessionStore(tmp_path)
    sid = store.create_session("guard", surface="general")["id"]
    registry = WebRegistry(MCPRegistry([]), search=Failed(), pages=Service(), enabled=True)
    model = StreamingModel(
        [
            response(tool="web::search", arguments={"query": "latest"}),
            response("最新はPython 1.0です。"),
        ]
    )

    async def check():
        agent = GeneralAgentLoop(model, store, registry)
        if stream:
            events = [e async for e in agent.chat_stream(sid, "最新を検索して")]
            assert not any("Python 1.0" in e.get("content", "") for e in events)
        else:
            await agent.chat(sid, "最新を検索して")
        answer = store.list_messages(sid)[-1]["content"]
        assert "確認できなかった" in answer and "Python 1.0" not in answer

    asyncio.run(check())
