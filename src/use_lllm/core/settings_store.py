"""エンドポイントとMCPサーバー仕様をJSON設定ファイルへ永続化する。"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from use_lllm.core.config import (
    DEFAULT_OLLAMA_MODEL,
    DEFAULT_OLLAMA_URL,
    ConfigurationError,
)
from use_lllm.core.endpoints import TRUST_LOOPBACK, Endpoint

MS_DATA_PARSER = "ms-data-parser"


@dataclass(frozen=True, slots=True)
class MCPServerSpec:
    name: str
    command: str = ""
    args: tuple[str, ...] = ()
    cwd: str | None = None
    env: tuple[tuple[str, str], ...] = ()
    autostart: bool = False
    read_only_auto: bool = False
    transport: str = "stdio"
    url: str | None = None
    headers: tuple[tuple[str, str], ...] = ()
    auth_mode: str = "none"

    def validate(self) -> None:
        if not self.name.strip():
            raise ConfigurationError("MCPサーバー名が空です。")
        if self.transport not in {"stdio", "streamable_http", "sse"}:
            raise ConfigurationError(f"未対応のMCP transportです: {self.transport}")
        if self.transport == "stdio" and not self.command.strip():
            raise ConfigurationError(f"MCPサーバー {self.name} のcommandが空です。")
        if self.transport != "stdio":
            if not (self.url or "").strip().lower().startswith(("http://", "https://")):
                raise ConfigurationError(f"MCPサーバー {self.name} のURLが不正です。")
        if self.auth_mode not in {"none", "oauth"}:
            raise ConfigurationError(f"未対応のMCP認証方式です: {self.auth_mode}")

    def env_dict(self) -> dict[str, str]:
        return {key: value for key, value in self.env}

    def headers_dict(self) -> dict[str, str]:
        return {key: value for key, value in self.headers}


@dataclass(frozen=True, slots=True)
class Settings:
    endpoints: tuple[Endpoint, ...]
    selected_endpoint: str
    mcp_servers: tuple[MCPServerSpec, ...]


def default_settings() -> Settings:
    local = Endpoint(
        name="local",
        base_url=DEFAULT_OLLAMA_URL,
        trust=TRUST_LOOPBACK,
        default_model=DEFAULT_OLLAMA_MODEL,
    )
    return Settings(endpoints=(local,), selected_endpoint="local", mcp_servers=())


def _endpoint_to_json(endpoint: Endpoint) -> dict[str, Any]:
    return {
        "name": endpoint.name,
        "base_url": endpoint.base_url,
        "trust": endpoint.trust,
        "default_model": endpoint.default_model,
    }


def _endpoint_from_json(data: dict[str, Any]) -> Endpoint:
    return Endpoint(
        name=str(data["name"]),
        base_url=str(data["base_url"]),
        trust=str(data.get("trust", TRUST_LOOPBACK)),
        default_model=data.get("default_model"),
    )


def _server_to_json(spec: MCPServerSpec) -> dict[str, Any]:
    return {
        "name": spec.name,
        "command": spec.command,
        "args": list(spec.args),
        "cwd": spec.cwd,
        "env": [list(pair) for pair in spec.env],
        "autostart": spec.autostart,
        "read_only_auto": spec.read_only_auto,
        "transport": spec.transport,
        "url": spec.url,
        "headers": [list(pair) for pair in spec.headers],
        "auth_mode": spec.auth_mode,
    }


def _server_from_json(data: dict[str, Any]) -> MCPServerSpec:
    return MCPServerSpec(
        name=str(data["name"]),
        command=str(data.get("command", "")),
        args=tuple(str(item) for item in data.get("args", [])),
        cwd=data.get("cwd"),
        env=tuple((str(k), str(v)) for k, v in data.get("env", [])),
        autostart=bool(data.get("autostart", False)),
        read_only_auto=bool(data.get("read_only_auto", False)),
        transport=str(data.get("transport", "stdio")),
        url=data.get("url"),
        headers=tuple((str(k), str(v)) for k, v in data.get("headers", [])),
        auth_mode=str(data.get("auth_mode", "none")),
    )


def settings_to_json(settings: Settings) -> dict[str, Any]:
    return {
        "endpoints": [_endpoint_to_json(e) for e in settings.endpoints],
        "selected_endpoint": settings.selected_endpoint,
        "mcp_servers": [_server_to_json(s) for s in settings.mcp_servers],
    }


def settings_from_json(data: dict[str, Any]) -> Settings:
    try:
        return Settings(
            endpoints=tuple(_endpoint_from_json(e) for e in data["endpoints"]),
            selected_endpoint=str(data["selected_endpoint"]),
            mcp_servers=tuple(_server_from_json(s) for s in data.get("mcp_servers", [])),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ConfigurationError(f"設定の形式が不正です: {exc}") from exc


def load_settings(path: Path) -> Settings:
    if not path.exists():
        return default_settings()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        raise ConfigurationError(f"設定ファイルを読めません: {path}") from exc
    return settings_from_json(raw)


def save_settings(path: Path, settings: Settings) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(settings_to_json(settings), ensure_ascii=False, indent=2)
    path.write_text(payload, encoding="utf-8")
