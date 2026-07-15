"""汎用チャット・設定用の独立FastAPIサーフェス。"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

from use_lllm.core.config import ConfigurationError, OllamaConfig
from use_lllm.core.endpoints import Endpoint, EndpointRegistry
from use_lllm.core.mcp_client import MCPConnectionError
from use_lllm.core.mcp_registry import MCPRegistry
from use_lllm.core.ollama import OllamaClient, OllamaError
from use_lllm.core.policy import ToolPolicyError
from use_lllm.core.sessions import SessionNotFound, SessionStore
from use_lllm.core.settings_store import (
    MCPServerSpec,
    Settings,
    load_settings,
    save_settings,
)
from use_lllm.general.agent_loop import GeneralAgentLoop

STATIC_ROOT = Path(__file__).with_name("static")
VENDOR_ROOT = STATIC_ROOT.parent.parent / "static" / "vendor"


def _static_ui_ready() -> bool:
    required_files = (
        STATIC_ROOT / "index.html",
        STATIC_ROOT / "app.js",
        STATIC_ROOT / "styles.css",
        STATIC_ROOT / "pca-plot.js",
        STATIC_ROOT / "eic-plot.js",
        VENDOR_ROOT / "plotly.min.js",
    )
    return all(path.is_file() for path in required_files)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EndpointBody(StrictModel):
    name: str
    base_url: str
    trust: str = "loopback"
    default_model: str | None = None


class MCPServerBody(StrictModel):
    name: str
    command: str
    args: list[str] = Field(default_factory=list)
    cwd: str | None = None
    env: dict[str, str] | None = None
    autostart: bool = False
    read_only_auto: bool = False


class SessionBody(StrictModel):
    title: str = "新しいチャット"


class ChatBody(StrictModel):
    message: str


class ApprovalBody(StrictModel):
    approved: bool


RegistryFactory = Callable[[tuple[MCPServerSpec, ...]], MCPRegistry]
OllamaFactory = Callable[[OllamaConfig], OllamaClient]


class GeneralRuntime:
    def __init__(
        self,
        sessions: SessionStore,
        settings_path: Path,
        *,
        registry_factory: RegistryFactory,
        ollama_factory: OllamaFactory,
    ) -> None:
        self.sessions = sessions
        self.settings_path = settings_path
        self.registry_factory = registry_factory
        self.ollama_factory = ollama_factory
        self.autostart_attempted = False
        self.settings = load_settings(settings_path)
        self._install(self.settings, persist=False)

    @staticmethod
    def _validate(settings: Settings) -> EndpointRegistry:
        endpoints = EndpointRegistry(list(settings.endpoints), settings.selected_endpoint)
        names = [spec.name for spec in settings.mcp_servers]
        if len(names) != len(set(names)):
            raise ConfigurationError("MCPサーバー名が重複しています。")
        for spec in settings.mcp_servers:
            spec.validate()
        return endpoints

    def _install(self, settings: Settings, *, persist: bool) -> None:
        endpoints = self._validate(settings)
        config = endpoints.selected().to_ollama_config()
        ollama = self.ollama_factory(config)
        registry = self.registry_factory(settings.mcp_servers)
        if persist:
            save_settings(self.settings_path, settings)
        self.settings = settings
        self.endpoints = endpoints
        self.ollama = ollama
        self.registry = registry
        self.agent = GeneralAgentLoop(ollama, self.sessions, registry)
        self.autostart_attempted = False

    async def apply(self, settings: Settings) -> None:
        old_registry = self.registry
        self._install(settings, persist=True)
        close_all = getattr(old_registry, "close_all", None)
        if close_all is not None:
            await close_all()

    async def ensure_autostart(self) -> None:
        if not self.autostart_attempted:
            self.autostart_attempted = True
            await self.registry.connect_autostart()

    def public_settings(self) -> dict[str, Any]:
        return {
            "endpoints": [
                {
                    "name": item.name,
                    "base_url": item.base_url,
                    "trust": item.trust,
                    "default_model": item.default_model,
                }
                for item in self.settings.endpoints
            ],
            "selected_endpoint": self.settings.selected_endpoint,
            "selected_remote": self.endpoints.selected().allow_lan,
            "mcp_servers": [
                {
                    "name": item.name,
                    "command": item.command,
                    "args": list(item.args),
                    "cwd": item.cwd,
                    "env_keys": sorted(key for key, _value in item.env),
                    "autostart": item.autostart,
                    "read_only_auto": item.read_only_auto,
                }
                for item in self.settings.mcp_servers
            ],
            "mcp_statuses": self.registry.statuses(),
        }


def _endpoint(body: EndpointBody) -> Endpoint:
    return Endpoint(
        name=body.name.strip(),
        base_url=body.base_url.strip().rstrip("/"),
        trust=body.trust,
        default_model=(body.default_model or "").strip() or None,
    )


def _server(body: MCPServerBody, existing: MCPServerSpec | None = None) -> MCPServerSpec:
    env = (
        tuple(sorted(body.env.items()))
        if body.env is not None
        else existing.env
        if existing is not None
        else ()
    )
    return MCPServerSpec(
        name=body.name.strip(),
        command=body.command.strip(),
        args=tuple(body.args),
        cwd=(body.cwd or "").strip() or None,
        env=env,
        autostart=body.autostart,
        read_only_auto=body.read_only_auto,
    )


def _raise_http(exc: Exception) -> None:
    if isinstance(exc, (SessionNotFound, KeyError)):
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if isinstance(exc, ToolPolicyError):
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    if isinstance(exc, (ConfigurationError, ValueError)):
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if isinstance(exc, (MCPConnectionError, OllamaError)):
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    raise exc


def create_general_app(
    *,
    session_store: SessionStore | None = None,
    settings_path: Path | None = None,
    registry_factory: RegistryFactory = MCPRegistry,
    ollama_factory: OllamaFactory = OllamaClient,
) -> FastAPI:
    sessions = session_store or SessionStore()
    runtime = GeneralRuntime(
        sessions,
        settings_path or sessions.base_directory / "settings.json",
        registry_factory=registry_factory,
        ollama_factory=ollama_factory,
    )

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        try:
            yield
        finally:
            close_all = getattr(runtime.registry, "close_all", None)
            if close_all is not None:
                await close_all()

    app = FastAPI(title="Use-LLLM General", version="0.1.0", lifespan=lifespan)
    app.state.runtime = runtime
    app.state.sessions = sessions

    if STATIC_ROOT.is_dir():
        app.mount("/static", StaticFiles(directory=STATIC_ROOT), name="general-static")
    if VENDOR_ROOT.is_dir():
        app.mount("/vendor", StaticFiles(directory=VENDOR_ROOT), name="general-vendor")

    @app.get("/", response_class=HTMLResponse)
    async def index():
        path = STATIC_ROOT / "index.html"
        if path.is_file():
            return FileResponse(path)
        return HTMLResponse("<h1>Use-LLLM General</h1>")

    @app.get("/api/health")
    async def health() -> dict[str, Any]:
        await runtime.ensure_autostart()
        return {
            "status": "ok",
            "binding": "loopback-only",
            "selected_endpoint": runtime.settings.selected_endpoint,
        }

    @app.get("/api/launcher-health")
    async def launcher_health() -> dict[str, Any]:
        return {
            "application": "use-lllm-webui",
            "surface": "general",
            "static_ready": _static_ui_ready(),
        }

    @app.get("/api/status")
    async def status() -> dict[str, Any]:
        await runtime.ensure_autostart()
        try:
            ollama = await runtime.ollama.health()
        except Exception as exc:
            ollama = {"status": "error", "error": str(exc)}
        return {
            "ollama": ollama,
            "mcp": runtime.registry.statuses(),
            "tools": runtime.registry.tool_names(),
            "storage": {"database": str(sessions.database_path)},
        }

    @app.get("/api/settings")
    async def get_settings() -> dict[str, Any]:
        return runtime.public_settings()

    @app.post("/api/endpoints", status_code=201)
    async def add_endpoint(body: EndpointBody) -> dict[str, Any]:
        try:
            if body.name in {item.name for item in runtime.settings.endpoints}:
                raise ValueError(f"エンドポイント名が重複しています: {body.name}")
            settings = Settings(
                runtime.settings.endpoints + (_endpoint(body),),
                runtime.settings.selected_endpoint,
                runtime.settings.mcp_servers,
            )
            await runtime.apply(settings)
            return runtime.public_settings()
        except Exception as exc:
            _raise_http(exc)

    @app.put("/api/endpoints/{name}")
    async def edit_endpoint(name: str, body: EndpointBody) -> dict[str, Any]:
        try:
            current = list(runtime.settings.endpoints)
            if name not in {item.name for item in current}:
                raise KeyError(name)
            if body.name != name and body.name in {item.name for item in current}:
                raise ValueError(f"エンドポイント名が重複しています: {body.name}")
            updated = tuple(_endpoint(body) if item.name == name else item for item in current)
            selected = (
                body.name
                if runtime.settings.selected_endpoint == name
                else runtime.settings.selected_endpoint
            )
            await runtime.apply(Settings(updated, selected, runtime.settings.mcp_servers))
            return runtime.public_settings()
        except Exception as exc:
            _raise_http(exc)

    @app.delete("/api/endpoints/{name}")
    async def delete_endpoint(name: str) -> dict[str, Any]:
        try:
            remaining = tuple(item for item in runtime.settings.endpoints if item.name != name)
            if len(remaining) == len(runtime.settings.endpoints):
                raise KeyError(name)
            if not remaining:
                raise ValueError("最後のOllamaエンドポイントは削除できません。")
            selected = runtime.settings.selected_endpoint
            if selected == name:
                selected = remaining[0].name
            await runtime.apply(Settings(remaining, selected, runtime.settings.mcp_servers))
            return runtime.public_settings()
        except Exception as exc:
            _raise_http(exc)

    @app.post("/api/endpoints/{name}/select")
    async def select_endpoint(name: str) -> dict[str, Any]:
        try:
            runtime.endpoints.get(name)
            await runtime.apply(
                Settings(runtime.settings.endpoints, name, runtime.settings.mcp_servers)
            )
            return runtime.public_settings()
        except Exception as exc:
            _raise_http(exc)

    @app.post("/api/mcp-servers", status_code=201)
    async def add_server(body: MCPServerBody) -> dict[str, Any]:
        try:
            if body.name in {item.name for item in runtime.settings.mcp_servers}:
                raise ValueError(f"MCPサーバー名が重複しています: {body.name}")
            await runtime.apply(
                Settings(
                    runtime.settings.endpoints,
                    runtime.settings.selected_endpoint,
                    runtime.settings.mcp_servers + (_server(body),),
                )
            )
            return runtime.public_settings()
        except Exception as exc:
            _raise_http(exc)

    @app.put("/api/mcp-servers/{name}")
    async def edit_server(name: str, body: MCPServerBody) -> dict[str, Any]:
        try:
            existing = next(
                (item for item in runtime.settings.mcp_servers if item.name == name),
                None,
            )
            if existing is None:
                raise KeyError(name)
            if body.name != name and body.name in {
                item.name for item in runtime.settings.mcp_servers
            }:
                raise ValueError(f"MCPサーバー名が重複しています: {body.name}")
            updated = tuple(
                _server(body, existing) if item.name == name else item
                for item in runtime.settings.mcp_servers
            )
            await runtime.apply(
                Settings(
                    runtime.settings.endpoints,
                    runtime.settings.selected_endpoint,
                    updated,
                )
            )
            return runtime.public_settings()
        except Exception as exc:
            _raise_http(exc)

    @app.delete("/api/mcp-servers/{name}")
    async def delete_server(name: str) -> dict[str, Any]:
        try:
            updated = tuple(item for item in runtime.settings.mcp_servers if item.name != name)
            if len(updated) == len(runtime.settings.mcp_servers):
                raise KeyError(name)
            await runtime.apply(
                Settings(
                    runtime.settings.endpoints,
                    runtime.settings.selected_endpoint,
                    updated,
                )
            )
            return runtime.public_settings()
        except Exception as exc:
            _raise_http(exc)

    @app.post("/api/mcp-servers/{name}/connect")
    async def connect_server(name: str) -> dict[str, Any]:
        try:
            return await runtime.registry.connect(name)
        except Exception as exc:
            _raise_http(exc)

    @app.post("/api/mcp-servers/{name}/disconnect")
    async def disconnect_server(name: str) -> dict[str, Any]:
        try:
            return await runtime.registry.disconnect(name)
        except Exception as exc:
            _raise_http(exc)

    @app.get("/api/tools")
    async def list_tools() -> dict[str, Any]:
        return {"tools": runtime.registry.tool_names()}

    def general_session(session_id: str) -> dict[str, Any]:
        session = sessions.get_session(session_id)
        if session.get("surface") != "general":
            raise SessionNotFound(session_id)
        return session

    @app.get("/api/sessions")
    async def list_sessions() -> list[dict[str, Any]]:
        return sessions.list_sessions("general")

    @app.post("/api/sessions", status_code=201)
    async def create_session(body: SessionBody) -> dict[str, Any]:
        return sessions.create_session(body.title.strip() or "新しいチャット", surface="general")

    @app.get("/api/sessions/{session_id}")
    async def get_session(session_id: str) -> dict[str, Any]:
        try:
            return general_session(session_id)
        except Exception as exc:
            _raise_http(exc)

    @app.delete("/api/sessions/{session_id}")
    async def delete_session(session_id: str) -> dict[str, Any]:
        try:
            general_session(session_id)
            close_session = getattr(runtime.registry, "close_session", None)
            if close_session is not None:
                await close_session(session_id)
            return sessions.delete_session(session_id)
        except Exception as exc:
            _raise_http(exc)

    @app.post("/api/sessions/{session_id}/chat")
    async def chat(session_id: str, body: ChatBody) -> dict[str, Any]:
        try:
            general_session(session_id)
            return await runtime.agent.chat(session_id, body.message)
        except Exception as exc:
            _raise_http(exc)

    @app.post("/api/sessions/{session_id}/chat/stream")
    async def chat_stream(session_id: str, body: ChatBody) -> StreamingResponse:
        general_session(session_id)

        async def generate():
            try:
                async for item in runtime.agent.chat_stream(session_id, body.message):
                    yield f"data: {json.dumps(item, ensure_ascii=False)}\n\n"
            except asyncio.CancelledError:
                sessions.append_event(session_id, "general_chat_cancelled", {}, status="cancelled")
                raise
            except Exception as exc:
                yield f"data: {json.dumps({'type': 'error', 'error': str(exc)}, ensure_ascii=False)}\n\n"

        return StreamingResponse(generate(), media_type="text/event-stream")

    @app.post("/api/sessions/{session_id}/approvals/{event_id}")
    async def resolve_approval(
        session_id: str, event_id: int, body: ApprovalBody
    ) -> dict[str, Any]:
        try:
            general_session(session_id)
            return await runtime.agent.resolve_approval(
                session_id, event_id, approved=body.approved
            )
        except Exception as exc:
            _raise_http(exc)

    return app
