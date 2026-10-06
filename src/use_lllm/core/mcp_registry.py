"""複数のローカルstdio MCPサーバーを名前空間付きで管理する。"""

from __future__ import annotations

import asyncio
import os
import re
import secrets
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass
from typing import Any, Protocol

from mcp import ClientSession, StdioServerParameters
from mcp.client.auth import OAuthClientProvider
from mcp.client.sse import sse_client
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamablehttp_client
from mcp.shared.auth import OAuthClientMetadata

from use_lllm.core.mcp_client import (
    MCPClient,
    MCPConnectionError,
    MCPServerSnapshot,
    MCPToolResult,
    ToolDescription,
    list_all_tools,
)
from use_lllm.core.mcp_oauth import MemoryTokenStorage, oauth_callbacks, oauth_storages
from use_lllm.core.policy import (
    ToolDecision,
    ToolPolicyError,
    decide_server_tool,
)
from use_lllm.core.policy import (
    is_replay_safe as policy_is_replay_safe,
)
from use_lllm.core.policy import (
    writes_outside_server as policy_writes_outside_server,
)
from use_lllm.core.settings_store import MCPServerSpec


class RegistrySession(Protocol):
    async def initialize(self) -> Any: ...

    async def list_tools(self, cursor: str | None = None) -> Any: ...

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> Any: ...


SessionFactory = Callable[[MCPServerSpec], AbstractAsyncContextManager[RegistrySession]]


@asynccontextmanager
async def stdio_session(spec: MCPServerSpec) -> AsyncIterator[ClientSession]:
    """MCPServerSpecから1つのstdioセッションを開く。"""

    spec.validate()
    parameters = StdioServerParameters(
        command=spec.command,
        args=list(spec.args),
        cwd=spec.cwd,
        env=spec.env_dict() or None,
    )
    try:
        async with stdio_client(parameters) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                yield session
    except MCPConnectionError:
        raise
    except Exception as exc:
        raise MCPConnectionError(f"MCPサーバー {spec.name} とのstdio通信に失敗しました。") from exc


def _oauth_provider(spec: MCPServerSpec) -> OAuthClientProvider | None:
    if spec.auth_mode != "oauth":
        return None
    storage = oauth_storages.setdefault(spec.name, MemoryTokenStorage())
    web_port = int(os.environ.get("USE_LLLM_WEB_PORT", "8765"))
    return OAuthClientProvider(
        spec.url or "",
        OAuthClientMetadata(
            redirect_uris=[f"http://127.0.0.1:{web_port}/general/api/mcp-oauth/callback"],
            client_name="Use-LLLM",
        ),
        storage,
        redirect_handler=oauth_callbacks.open_browser,
        callback_handler=oauth_callbacks.wait,
    )


@asynccontextmanager
async def mcp_session(spec: MCPServerSpec) -> AsyncIterator[ClientSession]:
    """Open a stdio, Streamable HTTP, or legacy SSE MCP session."""

    if spec.transport == "stdio":
        async with stdio_session(spec) as session:
            yield session
        return
    spec.validate()
    kwargs = {"headers": spec.headers_dict() or None, "auth": _oauth_provider(spec)}
    try:
        transport = streamablehttp_client if spec.transport == "streamable_http" else sse_client
        async with transport(spec.url or "", **kwargs) as streams:
            read_stream, write_stream = streams[0], streams[1]
            async with ClientSession(read_stream, write_stream) as session:
                yield session
    except MCPConnectionError:
        raise
    except Exception as exc:
        raise MCPConnectionError(
            f"MCPサーバー {spec.name} との{spec.transport}通信に失敗しました。"
        ) from exc


def _instructions_text(initialized: Any) -> str | None:
    value = getattr(initialized, "instructions", None)
    if not isinstance(value, str):
        return None
    return value.strip() or None


@dataclass(frozen=True, slots=True)
class RegisteredTool:
    server_name: str
    description: ToolDescription

    @property
    def qualified_name(self) -> str:
        return f"{self.server_name}::{self.description.name}"

    def to_ollama_tool(self) -> dict[str, Any]:
        description = self.description.description or self.description.title or ""
        return {
            "type": "function",
            "function": {
                "name": self.qualified_name,
                "description": f"[{self.server_name}] {description}".strip(),
                "parameters": self.description.input_schema,
            },
        }


@dataclass(slots=True)
class ManagedRequest:
    tool_name: str
    arguments: dict[str, Any]
    future: asyncio.Future[Any]


@dataclass(slots=True)
class ManagedConnection:
    queue: asyncio.Queue[ManagedRequest | None]
    task: asyncio.Task[None]
    ready: asyncio.Future[None]
    generation: int


class MCPRegistry:
    """MCPサーバーの論理接続状態と取得済みtool schemaを保持する。"""

    def __init__(
        self,
        specs: list[MCPServerSpec] | tuple[MCPServerSpec, ...],
        *,
        session_factory: SessionFactory | None = None,
        startup_timeout_seconds: float = 30.0,
        call_timeout_seconds: float = 300.0,
    ) -> None:
        names = [spec.name for spec in specs]
        if len(names) != len(set(names)):
            raise ValueError("MCPサーバー名が重複しています。")
        for spec in specs:
            spec.validate()
        self._specs = {spec.name: spec for spec in specs}
        self._order = names
        self._session_factory = session_factory or mcp_session
        self._startup_timeout = startup_timeout_seconds
        self._call_timeout = call_timeout_seconds
        self._snapshots: dict[str, MCPServerSnapshot] = {}
        self._errors: dict[str, str] = {}
        self._managed: dict[tuple[str, str], ManagedConnection] = {}
        self._managed_guard = asyncio.Lock()

    def server_names(self) -> list[str]:
        return list(self._order)

    def spec(self, name: str) -> MCPServerSpec:
        try:
            return self._specs[name]
        except KeyError as exc:
            raise MCPConnectionError(f"未登録のMCPサーバーです: {name}") from exc

    async def connect(self, name: str) -> dict[str, Any]:
        spec = self.spec(name)
        try:
            async with asyncio.timeout(self._startup_timeout):
                async with self._session_factory(spec) as session:
                    initialized = await session.initialize()
                    tools = await list_all_tools(session)
                    resources = await self._list_capability(session, "list_resources", "resources")
                    templates = await self._list_capability(
                        session, "list_resource_templates", "resourceTemplates"
                    )
                    prompts = await self._list_capability(session, "list_prompts", "prompts")
            snapshot = MCPServerSnapshot(
                server_name=str(initialized.serverInfo.name),
                server_version=str(initialized.serverInfo.version),
                protocol_version=str(initialized.protocolVersion),
                tools=tools,
                resources=resources,
                resource_templates=templates,
                prompts=prompts,
                instructions=_instructions_text(initialized),
            )
            self._snapshots[name] = snapshot
            self._errors.pop(name, None)
            return self.status(name)
        except TimeoutError as exc:
            message = f"MCPサーバー {name} の接続がタイムアウトしました。"
            self._errors[name] = message
            raise MCPConnectionError(message) from exc
        except Exception as exc:
            self._errors[name] = str(exc)
            raise

    async def connect_autostart(self) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        for name in self._order:
            if self._specs[name].autostart:
                try:
                    results.append(await self.connect(name))
                except Exception:
                    results.append(self.status(name))
        return results

    @staticmethod
    async def _list_capability(
        session: RegistrySession, method_name: str, field_name: str
    ) -> tuple[dict[str, Any], ...]:
        method = getattr(session, method_name, None)
        if method is None:
            return ()
        result: list[dict[str, Any]] = []
        cursor: str | None = None
        seen: set[str] = set()
        try:
            while True:
                page = await method(cursor=cursor)
                values = getattr(page, field_name, ()) or ()
                result.extend(
                    item if isinstance(item, dict) else item.model_dump(mode="json", by_alias=True)
                    for item in values
                )
                cursor = getattr(page, "nextCursor", None)
                if cursor is None:
                    break
                if cursor in seen:
                    raise MCPConnectionError(
                        f"{method_name} が同じカーソルを繰り返しました: {cursor}"
                    )
                seen.add(cursor)
        except Exception:
            return ()
        return tuple(result)

    async def disconnect(self, name: str) -> dict[str, Any]:
        self.spec(name)
        await self._close_matching(lambda key: key[1] == name)
        self._snapshots.pop(name, None)
        self._errors.pop(name, None)
        return self.status(name)

    async def _open_managed(self, session_id: str, server_name: str) -> ManagedConnection:
        key = (session_id, server_name)
        existing = self._managed.get(key)
        if existing is not None and not existing.task.done():
            return existing
        if existing is not None:
            self._managed.pop(key, None)
        async with self._managed_guard:
            existing = self._managed.get(key)
            if existing is not None and not existing.task.done():
                return existing
            if existing is not None:
                self._managed.pop(key, None)
            spec = self.spec(server_name)
            loop = asyncio.get_running_loop()
            queue: asyncio.Queue[ManagedRequest | None] = asyncio.Queue()
            ready: asyncio.Future[None] = loop.create_future()
            generation = secrets.randbits(62) or 1
            task = asyncio.create_task(
                self._managed_worker(spec, queue, ready),
                name=f"mcp:{session_id}:{server_name}",
            )
            managed = ManagedConnection(
                queue=queue,
                task=task,
                ready=ready,
                generation=generation,
            )
            self._managed[key] = managed
            try:
                async with asyncio.timeout(self._startup_timeout):
                    await ready
            except Exception:
                self._managed.pop(key, None)
                task.cancel()
                try:
                    await task
                except BaseException:
                    pass
                raise
            return managed

    async def _managed_worker(
        self,
        spec: MCPServerSpec,
        queue: asyncio.Queue[ManagedRequest | None],
        ready: asyncio.Future[None],
    ) -> None:
        failure: BaseException | None = None
        try:
            async with self._session_factory(spec) as session:
                await session.initialize()
                if not ready.done():
                    ready.set_result(None)
                while True:
                    request = await queue.get()
                    if request is None:
                        return
                    try:
                        result = await session.call_tool(request.tool_name, request.arguments)
                    except BaseException as exc:
                        failure = exc
                        if not request.future.done():
                            request.future.set_exception(exc)
                        return
                    if not request.future.done():
                        request.future.set_result(result)
        except BaseException as exc:
            failure = exc
            if not ready.done():
                ready.set_exception(exc)
        finally:
            if not ready.done():
                ready.set_exception(
                    MCPConnectionError(f"MCPサーバー {spec.name} を起動できませんでした。")
                )
            pending_error = failure or MCPConnectionError(
                f"MCPサーバー {spec.name} の接続が終了しました。"
            )
            while True:
                try:
                    pending = queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
                if pending is not None and not pending.future.done():
                    pending.future.set_exception(pending_error)

    async def _close_key(self, key: tuple[str, str]) -> None:
        managed = self._managed.pop(key, None)
        if managed is None:
            return
        if not managed.task.done():
            await managed.queue.put(None)
        await managed.task

    async def _close_matching(self, predicate: Callable[[tuple[str, str]], bool]) -> None:
        for key in [item for item in self._managed if predicate(item)]:
            await self._close_key(key)

    async def close_session(self, session_id: str) -> None:
        await self._close_matching(lambda key: key[0] == session_id)

    async def close_all(self) -> None:
        await self._close_matching(lambda _key: True)

    def connection_generation(self, session_id: str, server_name: str) -> int:
        managed = self._managed.get((session_id, server_name))
        return managed.generation if managed is not None else 0

    async def ensure_session(self, session_id: str, server_name: str) -> int:
        managed = await self._open_managed(session_id, server_name)
        return managed.generation

    def status(self, name: str) -> dict[str, Any]:
        spec = self.spec(name)
        snapshot = self._snapshots.get(name)
        return {
            "name": name,
            "status": "connected"
            if snapshot is not None
            else "error"
            if name in self._errors
            else "disconnected",
            "read_only_auto": spec.read_only_auto,
            "tool_count": snapshot.tool_count if snapshot is not None else 0,
            "resource_count": len(snapshot.resources) if snapshot is not None else 0,
            "prompt_count": len(snapshot.prompts) if snapshot is not None else 0,
            "transport": spec.transport,
            "server": snapshot.server_name if snapshot is not None else None,
            "version": snapshot.server_version if snapshot is not None else None,
            "error": self._errors.get(name),
        }

    def statuses(self) -> list[dict[str, Any]]:
        return [self.status(name) for name in self._order]

    def tools(self) -> list[RegisteredTool]:
        values: list[RegisteredTool] = []
        for name in self._order:
            snapshot = self._snapshots.get(name)
            if snapshot is not None:
                values.extend(RegisteredTool(name, tool) for tool in snapshot.tools)
        return values

    def server_instructions(self) -> dict[str, str]:
        """接続中サーバのうち instructions を返したものだけを、サーバ名ごとに返す。"""

        return {
            name: snapshot.instructions
            for name in self._order
            if (snapshot := self._snapshots.get(name)) is not None and snapshot.instructions
        }

    def tool_names(self) -> list[str]:
        return [tool.qualified_name for tool in self.tools()]

    def ollama_tools(
        self,
        query: str | None = None,
        *,
        limit: int | None = None,
        excluded: set[str] | None = None,
    ) -> list[dict[str, Any]]:
        """接続中のtoolを、現在の発話に近い順に並べて**全件**渡す。

        明示名を最優先に並べ替えるが、既定では1件も落とさない（limit=None）。
        以前は既定で24件に切り詰めていたため、どのtool名にもマッチしない発話
        （「9w vs 24M」等）では全件スコア0となり登録順の先頭24件しか渡らず、
        後続手順に必要なtoolがモデルから見えなくなって解析が途中で止まっていた。
        絞り込みは会話からは回復できないので、コンテキスト量とのトレードオフは
        呼び出し側が limit を明示したときだけ受け入れる。

        UIと監査では従来どおり全件を維持する。ユーザーが明示的に無効化した
        tool（excluded）は引き続き渡さない。
        """

        if limit is not None and limit <= 0:
            raise ValueError("tool候補上限は1以上にしてください。")
        tools = [tool for tool in self.tools() if tool.qualified_name not in (excluded or set())]
        if not query:
            return [tool.to_ollama_tool() for tool in tools[:limit]]
        folded = query.casefold()
        query_tokens = {
            token for token in re.findall(r"[^\W_]+", folded, flags=re.UNICODE) if len(token) >= 2
        }

        synonyms = {
            "検索": ("search", "find", "query", "list"),
            "読む": ("read", "get", "fetch"),
            "一覧": ("list", "search"),
            "保存": ("save", "write", "create"),
            "解析": ("analyze", "analysis", "parse"),
        }

        def score(item: RegisteredTool) -> int:
            qualified = item.qualified_name.casefold()
            name = item.description.name.casefold()
            description = (item.description.description or "").casefold()
            value = 0
            if qualified in folded:
                value += 200
            if name in folded:
                value += 150
            for part in re.split(r"[_\-.]+", name):
                if len(part) >= 3 and part in folded:
                    value += 20
            for token in query_tokens:
                if token in description:
                    value += 2
                if any(word in description or word in name for word in synonyms.get(token, ())):
                    value += 12
            return value

        ranked = sorted(enumerate(tools), key=lambda pair: (-score(pair[1]), pair[0]))
        return [item.to_ollama_tool() for _index, item in ranked[:limit]]

    def resources(self) -> list[dict[str, Any]]:
        values: list[dict[str, Any]] = []
        for name in self._order:
            snapshot = self._snapshots.get(name)
            if snapshot:
                values.extend({"server": name, **item} for item in snapshot.resources)
                values.extend(
                    {"server": name, "template": True, **item}
                    for item in snapshot.resource_templates
                )
        return values

    def prompts(self) -> list[dict[str, Any]]:
        values: list[dict[str, Any]] = []
        for name in self._order:
            snapshot = self._snapshots.get(name)
            if snapshot:
                values.extend({"server": name, **item} for item in snapshot.prompts)
        return values

    async def read_resource(self, server_name: str, uri: str) -> dict[str, Any]:
        spec = self.spec(server_name)
        async with self._session_factory(spec) as session:
            await session.initialize()
            result = await session.read_resource(uri)
        return result.model_dump(mode="json", by_alias=True)

    async def get_prompt(
        self, server_name: str, name: str, arguments: dict[str, str] | None = None
    ) -> dict[str, Any]:
        spec = self.spec(server_name)
        async with self._session_factory(spec) as session:
            await session.initialize()
            result = await session.get_prompt(name, arguments or {})
        return result.model_dump(mode="json", by_alias=True)

    def get_tool(self, qualified_name: str) -> RegisteredTool:
        try:
            server_name, tool_name = qualified_name.split("::", 1)
        except ValueError as exc:
            raise MCPConnectionError(
                f"MCPツール名はserver::tool形式で指定してください: {qualified_name}"
            ) from exc
        snapshot = self._snapshots.get(server_name)
        if snapshot is None:
            raise MCPConnectionError(f"MCPサーバーが接続されていません: {server_name}")
        for tool in snapshot.tools:
            if tool.name == tool_name:
                return RegisteredTool(server_name, tool)
        raise MCPConnectionError(f"接続中MCPに未知のツールです: {qualified_name}")

    def is_replay_safe(self, qualified_name: str) -> bool:
        """サーバが宣言した annotations から、同じ引数での再実行が安全かを判定する。"""
        try:
            tool = self.get_tool(qualified_name)
        except MCPConnectionError:
            return False
        return policy_is_replay_safe(tool.description.annotations)

    def may_write_files(self, qualified_name: str) -> bool:
        """そのツールがサーバ外へ書くと宣言しているか（成果物回収の対象判定）。"""
        try:
            tool = self.get_tool(qualified_name)
        except MCPConnectionError:
            return False
        return policy_writes_outside_server(tool.description.annotations)

    def decide(
        self,
        qualified_name: str,
        *,
        approved: bool = False,
        network_mode: str = "offline",
    ) -> ToolDecision:
        tool = self.get_tool(qualified_name)
        spec = self.spec(tool.server_name)
        return decide_server_tool(
            tool.server_name,
            tool.description.name,
            annotations=tool.description.annotations,
            read_only_auto=spec.read_only_auto,
            approved=approved,
            network_mode=network_mode,
        )

    async def call_tool(
        self,
        qualified_name: str,
        arguments: dict[str, Any] | None = None,
        *,
        approved: bool = False,
        network_mode: str = "offline",
        session_id: str | None = None,
    ) -> MCPToolResult:
        tool = self.get_tool(qualified_name)
        decision = self.decide(qualified_name, approved=approved, network_mode=network_mode)
        if not decision.allowed:
            raise ToolPolicyError(decision.reason)
        values = arguments or {}
        MCPClient._validate_arguments(tool.description, values)
        spec = self.spec(tool.server_name)
        try:
            async with asyncio.timeout(self._call_timeout):
                if session_id is None:
                    async with self._session_factory(spec) as session:
                        await session.initialize()
                        result = await session.call_tool(tool.description.name, values)
                else:
                    managed = await self._open_managed(session_id, tool.server_name)
                    future: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
                    await managed.queue.put(ManagedRequest(tool.description.name, values, future))
                    result = await future
            serialized = MCPClient._serialize_result(tool.description.name, result)
            return MCPToolResult(
                tool_name=qualified_name,
                is_error=serialized.is_error,
                content=serialized.content,
                structured_content=serialized.structured_content,
            )
        except TimeoutError as exc:
            if session_id is not None:
                await self._close_key((session_id, tool.server_name))
            raise MCPConnectionError(
                f"MCPツール {qualified_name} がタイムアウトしました。"
            ) from exc
        except Exception:
            if session_id is not None:
                await self._close_key((session_id, tool.server_name))
            raise
