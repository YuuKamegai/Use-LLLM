"""汎用チャット・設定用の独立FastAPIサーフェス。"""

from __future__ import annotations

import asyncio
import base64
import html
import json
import secrets
from collections.abc import Callable
from contextlib import asynccontextmanager
from dataclasses import replace
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

from use_lllm.core.azure_openai import (
    AzureOpenAIClient,
    AzureOpenAIConfig,
    normalize_azure_openai_endpoint,
)
from use_lllm.core.config import ConfigurationError, OllamaConfig
from use_lllm.core.endpoints import (
    PROVIDER_AZURE_OPENAI,
    PROVIDER_OLLAMA,
    Endpoint,
    EndpointRegistry,
)
from use_lllm.core.knowledge import KnowledgeStore
from use_lllm.core.mcp_client import MCPConnectionError
from use_lllm.core.mcp_oauth import oauth_callbacks
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
from use_lllm.core.setup_store import SETUP_VERSION, SetupStore
from use_lllm.general.agent_loop import DEFAULT_GENERAL_SESSION_TITLE, GeneralAgentLoop
from use_lllm.general.security import (
    CSRF_PLACEHOLDER,
    LocalAPISecurityMiddleware,
)

STATIC_ROOT = Path(__file__).with_name("static")
VENDOR_ROOT = STATIC_ROOT.parent.parent / "static" / "vendor"
MAX_SESSION_PNG_BYTES = 20 * 1024 * 1024


def _static_ui_ready() -> bool:
    required_files = (
        STATIC_ROOT / "index.html",
        STATIC_ROOT / "app.js",
        STATIC_ROOT / "styles.css",
        STATIC_ROOT / "pca-plot.js",
        STATIC_ROOT / "eic-plot.js",
        STATIC_ROOT / "markdown.js",
        STATIC_ROOT / "artifact-renderer.js",
        VENDOR_ROOT / "plotly.min.js",
        VENDOR_ROOT / "katex.min.js",
        VENDOR_ROOT / "katex.min.css",
        VENDOR_ROOT / "fonts" / "KaTeX_Main-Regular.woff2",
    )
    return all(path.is_file() for path in required_files)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EndpointBody(StrictModel):
    name: str
    base_url: str
    trust: str = "loopback"
    default_model: str | None = None
    provider: str = PROVIDER_OLLAMA
    api_key: str | None = None
    context_window: int | None = Field(default=None, ge=2048)


class MCPServerBody(StrictModel):
    name: str
    command: str = ""
    args: list[str] = Field(default_factory=list)
    cwd: str | None = None
    env: dict[str, str] | None = None
    autostart: bool = False
    read_only_auto: bool = False
    transport: str = "stdio"
    url: str | None = None
    headers: dict[str, str] | None = None
    auth_mode: str = "none"


class SessionBody(StrictModel):
    title: str = DEFAULT_GENERAL_SESSION_TITLE


class ChatBody(StrictModel):
    message: str
    attachment_ids: list[str] = Field(default_factory=list)


class ToolPreferencesBody(StrictModel):
    disabled_tools: list[str] = Field(default_factory=list)


class KnowledgeUploadBody(StrictModel):
    name: str
    content_base64: str
    mime_type: str = "application/octet-stream"


class ClaudeImportBody(StrictModel):
    config: dict[str, Any]


class ResourceReadBody(StrictModel):
    server: str
    uri: str


class PromptGetBody(StrictModel):
    server: str
    name: str
    arguments: dict[str, str] = Field(default_factory=dict)


class ApprovalBody(StrictModel):
    approved: bool


class SetupModelBody(StrictModel):
    model: str


RegistryFactory = Callable[[tuple[MCPServerSpec, ...]], MCPRegistry]
OllamaFactory = Callable[[OllamaConfig], OllamaClient]
AzureFactory = Callable[[AzureOpenAIConfig], AzureOpenAIClient]


class GeneralRuntime:
    def __init__(
        self,
        sessions: SessionStore,
        settings_path: Path,
        *,
        registry_factory: RegistryFactory,
        ollama_factory: OllamaFactory,
        azure_factory: AzureFactory,
    ) -> None:
        self.sessions = sessions
        self.knowledge = KnowledgeStore(sessions.base_directory)
        self.setup = SetupStore(sessions.base_directory)
        self.settings_path = settings_path
        self.registry_factory = registry_factory
        self.ollama_factory = ollama_factory
        self.azure_factory = azure_factory
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
        selected = endpoints.selected()
        if selected.provider == PROVIDER_AZURE_OPENAI:
            model_client = self.azure_factory(selected.to_azure_openai_config())
        else:
            model_client = self.ollama_factory(selected.to_ollama_config())
        registry = self.registry_factory(settings.mcp_servers)
        if persist:
            save_settings(self.settings_path, settings)
        self.settings = settings
        self.endpoints = endpoints
        self.ollama = model_client
        self.registry = registry
        self.agent = GeneralAgentLoop(model_client, self.sessions, registry)
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

    async def select_model(self, model: str) -> None:
        name = model.strip()
        if not name or len(name) > 200 or any(char.isspace() for char in name):
            raise ValueError("モデル名が不正です。")
        selected = self.settings.selected_endpoint
        endpoints = tuple(
            replace(item, default_model=name) if item.name == selected else item
            for item in self.settings.endpoints
        )
        await self.apply(Settings(endpoints, selected, self.settings.mcp_servers))

    async def test_endpoint(self, name: str) -> dict[str, Any]:
        endpoint = self.endpoints.get(name)
        client = (
            self.azure_factory(endpoint.to_azure_openai_config())
            if endpoint.provider == PROVIDER_AZURE_OPENAI
            else self.ollama_factory(endpoint.to_ollama_config())
        )
        tester = getattr(client, "test_connection", None)
        return await tester() if tester is not None else await client.health()

    def public_settings(self) -> dict[str, Any]:
        return {
            "endpoints": [
                {
                    "name": item.name,
                    "base_url": item.base_url,
                    "trust": item.trust,
                    "default_model": item.default_model,
                    "provider": item.provider,
                    "api_key_configured": bool(item.api_key),
                    "context_window": item.context_window,
                }
                for item in self.settings.endpoints
            ],
            "selected_endpoint": self.settings.selected_endpoint,
            "selected_remote": self.endpoints.selected().is_remote,
            "mcp_servers": [
                {
                    "name": item.name,
                    "command": item.command,
                    "args": list(item.args),
                    "cwd": item.cwd,
                    "env_keys": sorted(key for key, _value in item.env),
                    "transport": item.transport,
                    "url": item.url,
                    "header_keys": sorted(key for key, _value in item.headers),
                    "auth_mode": item.auth_mode,
                    "autostart": item.autostart,
                    "read_only_auto": item.read_only_auto,
                }
                for item in self.settings.mcp_servers
            ],
            "mcp_statuses": self.registry.statuses(),
        }


def _endpoint(body: EndpointBody, existing: Endpoint | None = None) -> Endpoint:
    base_url = body.base_url.strip().rstrip("/")
    api_key = (body.api_key or "").strip()
    if body.provider != PROVIDER_AZURE_OPENAI:
        api_key = ""
    elif (
        not api_key
        and existing is not None
        and existing.provider == PROVIDER_AZURE_OPENAI
        and normalize_azure_openai_endpoint(existing.base_url)
        == normalize_azure_openai_endpoint(base_url)
    ):
        api_key = existing.api_key or ""
    return Endpoint(
        name=body.name.strip(),
        base_url=base_url,
        trust=body.trust,
        default_model=(body.default_model or "").strip() or None,
        provider=body.provider,
        api_key=api_key or None,
        context_window=(body.context_window if body.provider == PROVIDER_AZURE_OPENAI else None),
    )


def _server(body: MCPServerBody, existing: MCPServerSpec | None = None) -> MCPServerSpec:
    env = (
        tuple(sorted(body.env.items()))
        if body.env is not None
        else existing.env
        if existing is not None
        else ()
    )
    headers = (
        tuple(sorted(body.headers.items()))
        if body.headers is not None
        else existing.headers
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
        transport=body.transport,
        url=(body.url or "").strip() or None,
        headers=headers,
        auth_mode=body.auth_mode,
    )


def _raise_http(exc: Exception) -> None:
    if isinstance(exc, (SessionNotFound, KeyError, FileNotFoundError)):
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
    azure_factory: AzureFactory = AzureOpenAIClient,
) -> FastAPI:
    sessions = session_store or SessionStore()
    runtime = GeneralRuntime(
        sessions,
        settings_path or sessions.base_directory / "settings.json",
        registry_factory=registry_factory,
        ollama_factory=ollama_factory,
        azure_factory=azure_factory,
    )

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        try:
            yield
        finally:
            close_all = getattr(runtime.registry, "close_all", None)
            if close_all is not None:
                await close_all()

    csrf_token = secrets.token_urlsafe(32)
    app = FastAPI(title="Use-LLLM General", version="0.1.0", lifespan=lifespan)
    app.add_middleware(LocalAPISecurityMiddleware, csrf_token=csrf_token)
    app.state.runtime = runtime
    app.state.sessions = sessions
    app.state.knowledge = runtime.knowledge
    app.state.setup = runtime.setup
    app.state.local_api_csrf_token = csrf_token

    if STATIC_ROOT.is_dir():
        app.mount("/static", StaticFiles(directory=STATIC_ROOT), name="general-static")
    if VENDOR_ROOT.is_dir():
        app.mount("/vendor", StaticFiles(directory=VENDOR_ROOT), name="general-vendor")

    @app.get("/", response_class=HTMLResponse)
    async def index():
        path = STATIC_ROOT / "index.html"
        if path.is_file():
            content = path.read_text(encoding="utf-8").replace(
                CSRF_PLACEHOLDER, html.escape(csrf_token, quote=True)
            )
            return HTMLResponse(
                content,
                headers={
                    "Cache-Control": "no-store",
                    "Referrer-Policy": "no-referrer",
                    "X-Frame-Options": "DENY",
                    "X-Content-Type-Options": "nosniff",
                },
            )
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
            model_status = await runtime.ollama.health()
        except Exception as exc:
            model_status = {"status": "error", "error": str(exc)}
        return {
            "model": model_status,
            "ollama": model_status,
            "mcp": runtime.registry.statuses(),
            "tools": runtime.registry.tool_names(),
            "storage": {"database": str(sessions.database_path)},
        }

    @app.get("/api/setup")
    async def setup_status() -> dict[str, Any]:
        try:
            ollama = await runtime.ollama.health()
        except Exception as exc:
            ollama = {"status": "unavailable", "error": str(exc), "models": []}
        return {
            "completed": runtime.setup.is_complete(),
            "setup_version": SETUP_VERSION,
            "state": runtime.setup.load(),
            "ollama": ollama,
            "selected_endpoint": runtime.settings.selected_endpoint,
            "selected_model": runtime.endpoints.selected().default_model,
            "ollama_download_url": "https://ollama.com/download/windows",
            "mcp_optional": True,
        }

    @app.post("/api/setup/model")
    async def select_setup_model(body: SetupModelBody) -> dict[str, Any]:
        try:
            await runtime.select_model(body.model)
            return await setup_status()
        except Exception as exc:
            _raise_http(exc)

    @app.post("/api/setup/models/pull")
    async def pull_setup_model(body: SetupModelBody) -> StreamingResponse:
        async def generate():
            try:
                async for item in runtime.ollama.pull_model(body.model):
                    yield json.dumps(item, ensure_ascii=False) + "\n"
                await runtime.select_model(body.model)
                yield (
                    json.dumps(
                        {"status": "ready", "model": body.model.strip(), "done": True},
                        ensure_ascii=False,
                    )
                    + "\n"
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                yield json.dumps({"error": str(exc), "done": True}, ensure_ascii=False) + "\n"

        return StreamingResponse(generate(), media_type="application/x-ndjson")

    @app.post("/api/setup/complete")
    async def complete_setup(body: SetupModelBody) -> dict[str, Any]:
        try:
            await runtime.select_model(body.model)
            health = await runtime.ollama.health()
            if health.get("status") != "ready":
                raise ValueError("選択したOllamaモデルを利用できません。")
            state = runtime.setup.mark_complete(
                endpoint=runtime.settings.selected_endpoint,
                model=body.model.strip(),
            )
            return {"completed": True, "state": state, "ollama": health}
        except Exception as exc:
            _raise_http(exc)

    @app.post("/api/setup/connections")
    async def continue_setup_in_connections() -> dict[str, Any]:
        state = runtime.setup.mark_complete(
            endpoint=runtime.settings.selected_endpoint,
            model="connection-settings",
        )
        return {"completed": True, "state": state}

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
            existing = next((item for item in current if item.name == name), None)
            if existing is None:
                raise KeyError(name)
            if body.name != name and body.name in {item.name for item in current}:
                raise ValueError(f"エンドポイント名が重複しています: {body.name}")
            updated = tuple(
                _endpoint(body, existing) if item.name == name else item for item in current
            )
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

    @app.post("/api/endpoints/{name}/test")
    async def test_endpoint(name: str) -> dict[str, Any]:
        try:
            return await runtime.test_endpoint(name)
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

    @app.get("/api/mcp-oauth/callback", response_class=HTMLResponse)
    async def mcp_oauth_callback(code: str, state: str | None = None) -> HTMLResponse:
        delivered = oauth_callbacks.deliver(code, state)
        message = "認証が完了しました。このタブを閉じてUse-LLLMへ戻ってください。"
        if not delivered:
            message = "待機中のMCP OAuth接続が見つかりません。"
        return HTMLResponse(f"<h1>Use-LLLM</h1><p>{message}</p>")

    @app.post("/api/mcp-servers/import-claude")
    async def import_claude(body: ClaudeImportBody) -> dict[str, Any]:
        try:
            raw = body.config.get("mcpServers", {})
            if not isinstance(raw, dict):
                raise ValueError("Claude設定のmcpServersがobjectではありません。")
            existing = {item.name for item in runtime.settings.mcp_servers}
            additions: list[MCPServerSpec] = []
            for name, item in raw.items():
                if name in existing or not isinstance(item, dict):
                    continue
                transport = str(
                    item.get("transport") or ("streamable_http" if item.get("url") else "stdio")
                )
                additions.append(
                    MCPServerSpec(
                        name=str(name),
                        command=str(item.get("command", "")),
                        args=tuple(str(value) for value in item.get("args", [])),
                        cwd=item.get("cwd"),
                        env=tuple(sorted((str(k), str(v)) for k, v in item.get("env", {}).items())),
                        transport=transport,
                        url=item.get("url"),
                        headers=tuple(
                            sorted((str(k), str(v)) for k, v in item.get("headers", {}).items())
                        ),
                    )
                )
            await runtime.apply(
                Settings(
                    runtime.settings.endpoints,
                    runtime.settings.selected_endpoint,
                    runtime.settings.mcp_servers + tuple(additions),
                )
            )
            return {
                "imported": [item.name for item in additions],
                "settings": runtime.public_settings(),
            }
        except Exception as exc:
            _raise_http(exc)

    @app.get("/api/mcp-servers/{name}/diagnostics")
    async def diagnose_server(name: str) -> dict[str, Any]:
        try:
            spec = runtime.registry.spec(name)
            spec.validate()
            status = runtime.registry.status(name)
            return {
                "ok": status["status"] == "connected",
                "transport": spec.transport,
                "target": spec.command if spec.transport == "stdio" else spec.url,
                "auth_mode": spec.auth_mode,
                "status": status,
            }
        except Exception as exc:
            _raise_http(exc)

    @app.get("/api/tools")
    async def list_tools() -> dict[str, Any]:
        return {"tools": runtime.registry.tool_names()}

    @app.get("/api/resources")
    async def list_resources() -> dict[str, Any]:
        return {"resources": runtime.registry.resources()}

    @app.post("/api/resources/read")
    async def read_resource(body: ResourceReadBody) -> dict[str, Any]:
        try:
            return await runtime.registry.read_resource(body.server, body.uri)
        except Exception as exc:
            _raise_http(exc)

    @app.get("/api/prompts")
    async def list_prompts() -> dict[str, Any]:
        return {"prompts": runtime.registry.prompts()}

    @app.post("/api/prompts/get")
    async def get_prompt(body: PromptGetBody) -> dict[str, Any]:
        try:
            return await runtime.registry.get_prompt(body.server, body.name, body.arguments)
        except Exception as exc:
            _raise_http(exc)

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
        return sessions.create_session(
            body.title.strip() or DEFAULT_GENERAL_SESSION_TITLE, surface="general"
        )

    @app.get("/api/sessions/{session_id}")
    async def get_session(session_id: str) -> dict[str, Any]:
        try:
            return general_session(session_id)
        except Exception as exc:
            _raise_http(exc)

    @app.get("/api/sessions/{session_id}/artifacts/{name}")
    async def get_session_artifact(session_id: str, name: str) -> FileResponse:
        try:
            general_session(session_id)
            path = sessions.artifact_path(session_id, name)
            if (
                not path.is_file()
                or path.suffix.lower() != ".png"
                or not 0 < path.stat().st_size <= MAX_SESSION_PNG_BYTES
            ):
                raise FileNotFoundError(name)
            with path.open("rb") as stream:
                if stream.read(8) != b"\x89PNG\r\n\x1a\n":
                    raise FileNotFoundError(name)
            return FileResponse(
                path,
                media_type="image/png",
                filename=path.name,
                content_disposition_type="inline",
                headers={"Cache-Control": "private, max-age=31536000, immutable"},
            )
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

    @app.put("/api/sessions/{session_id}/tools")
    async def set_session_tools(session_id: str, body: ToolPreferencesBody) -> dict[str, Any]:
        try:
            general_session(session_id)
            known = set(runtime.registry.tool_names())
            invalid = sorted(set(body.disabled_tools) - known)
            if invalid:
                raise ValueError("未登録のMCPツールです: " + ", ".join(invalid))
            return sessions.update_session(
                session_id, state_patch={"disabled_tools": sorted(set(body.disabled_tools))}
            )
        except Exception as exc:
            _raise_http(exc)

    @app.get("/api/knowledge")
    async def list_knowledge() -> dict[str, Any]:
        return {"sources": runtime.knowledge.list()}

    @app.get("/api/knowledge/search")
    async def search_knowledge(q: str = Query(default="", max_length=500)) -> dict[str, Any]:
        try:
            return {"sources": runtime.knowledge.search(q)}
        except Exception as exc:
            _raise_http(exc)

    @app.post("/api/knowledge", status_code=201)
    async def add_knowledge(body: KnowledgeUploadBody) -> dict[str, Any]:
        try:
            data = base64.b64decode(body.content_base64, validate=True)
            return runtime.knowledge.add_bytes(body.name, data, body.mime_type)
        except Exception as exc:
            _raise_http(
                exc if isinstance(exc, ValueError) else ValueError("Base64データが不正です。")
            )

    @app.delete("/api/knowledge/{source_id}")
    async def delete_knowledge(source_id: str) -> dict[str, bool]:
        try:
            runtime.knowledge.delete(source_id)
            return {"deleted": True}
        except Exception as exc:
            _raise_http(exc)

    @app.post("/api/sessions/{session_id}/chat")
    async def chat(session_id: str, body: ChatBody) -> dict[str, Any]:
        try:
            general_session(session_id)
            attachment_context = runtime.knowledge.attachment_context(body.attachment_ids)
            metadata = {"attachment_ids": body.attachment_ids}
            if attachment_context:
                metadata["attachment_context"] = attachment_context
            return await runtime.agent.chat(session_id, body.message, metadata=metadata)
        except Exception as exc:
            _raise_http(exc)

    @app.post("/api/sessions/{session_id}/chat/stream")
    async def chat_stream(session_id: str, body: ChatBody) -> StreamingResponse:
        general_session(session_id)

        async def generate():
            try:
                attachment_context = runtime.knowledge.attachment_context(body.attachment_ids)
                metadata = {"attachment_ids": body.attachment_ids}
                if attachment_context:
                    metadata["attachment_context"] = attachment_context
                async for item in runtime.agent.chat_stream(
                    session_id, body.message, metadata=metadata
                ):
                    yield f"data: {json.dumps(item, ensure_ascii=False)}\n\n"
            except asyncio.CancelledError:
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
