import asyncio

import pytest

from use_lllm.core.mcp_client import MCPConnectionError
from use_lllm.core.mcp_registry import MCPRegistry
from use_lllm.core.policy import ToolPolicyError
from use_lllm.core.settings_store import MCPServerSpec
from use_lllm.web.contracts import WebResult
from use_lllm.web.registry import WebRegistry


class Service:
    def __init__(self):
        self.calls = []

    async def search(self, query, max_results=5):
        self.calls.append(query)
        return WebResult({"status": "ok", "results": [], "error": None})

    async def read_page(self, url):
        return WebResult({"status": "ok", "text": "Evidence", "error": None})


def test_enabled_exposes_exactly_two_tools_and_auto_allows():
    service = Service()
    registry = WebRegistry(MCPRegistry([]), search=service, pages=service, enabled=True)
    assert registry.tool_names() == ["web::search", "web::read_page"]
    assert registry.decide("web::search").allowed
    assert not registry.decide("web::search").approval_required
    assert registry.get_tool("read_page").qualified_name == "web::read_page"
    result = asyncio.run(registry.call_tool("web::search", {"query": "q"}))
    assert not result.is_error and service.calls == ["q"]
    assert registry.connection_generation("s", "web") == 1
    assert not registry.may_write_files("web::read_page")


def test_disabled_hides_and_rejects_direct_calls():
    service = Service()
    registry = WebRegistry(MCPRegistry([]), search=service, pages=service, enabled=False)
    assert registry.tool_names() == []
    with pytest.raises((MCPConnectionError, ToolPolicyError)):
        asyncio.run(registry.call_tool("web::search", {"query": "q"}, approved=True))
    assert not service.calls


def test_respects_excluded_tools_and_unambiguous_alias():
    registry = WebRegistry(MCPRegistry([]), search=Service(), pages=Service(), enabled=True)
    offered = registry.ollama_tools(excluded={"web::search"})
    assert [t["function"]["name"] for t in offered] == ["web::read_page"]


def test_collision_rejected_even_when_disconnected():
    with pytest.raises(ValueError, match="web"):
        WebRegistry(
            MCPRegistry([MCPServerSpec("web", "python")]),
            search=Service(),
            pages=Service(),
            enabled=True,
        )


def test_external_annotations_do_not_gain_auto_approval():
    from contextlib import asynccontextmanager

    from tests.test_mcp_registry import FakeSession, FakeTool

    @asynccontextmanager
    async def factory(spec):
        yield FakeSession(
            [
                FakeTool(
                    "search",
                    {"type": "object"},
                    annotations={"readOnlyHint": True, "openWorldHint": True},
                )
            ]
        )

    async def check():
        delegate = MCPRegistry(
            [MCPServerSpec("external", "python", allow_network=True)], session_factory=factory
        )
        await delegate.connect("external")
        registry = WebRegistry(delegate, search=Service(), pages=Service(), enabled=True)
        assert not registry.decide("external::search").allowed
        with pytest.raises(MCPConnectionError):
            registry.get_tool("search")
        assert registry.resources() == delegate.resources()
        await registry.close_all()

    asyncio.run(check())


def test_unknown_recovery_candidates_are_false_not_exceptions():
    registry = WebRegistry(MCPRegistry([]), search=Service(), pages=Service(), enabled=True)
    assert registry.is_replay_safe("srv::not_registered") is False
    assert registry.may_write_files("srv::not_registered") is False
