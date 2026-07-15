"""エンドポイントとMCPサーバー仕様をJSON設定ファイルへ永続化する。"""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any

from use_lllm.core.config import (
    ConfigurationError,
    DEFAULT_MCP_COMMAND,
    DEFAULT_MCP_SERVER_SCRIPT,
    DEFAULT_OLLAMA_MODEL,
    DEFAULT_OLLAMA_URL,
)
from use_lllm.core.endpoints import Endpoint, TRUST_LOOPBACK


MS_DATA_PARSER = "ms-data-parser"


@dataclass(frozen=True, slots=True)
class MCPServerSpec:
    name: str
    command: str
    args: tuple[str, ...] = ()
    cwd: str | None = None
    env: tuple[tuple[str, str], ...] = ()
    autostart: bool = False
    read_only_auto: bool = False

    def validate(self) -> None:
        if not self.name.strip():
            raise ConfigurationError("MCPサーバー名が空です。")
        if not self.command.strip():
            raise ConfigurationError(f"MCPサーバー {self.name} のcommandが空です。")

    def env_dict(self) -> dict[str, str]:
        return {key: value for key, value in self.env}


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
    ms = MCPServerSpec(
        name=MS_DATA_PARSER,
        command=str(DEFAULT_MCP_COMMAND),
        args=(str(DEFAULT_MCP_SERVER_SCRIPT),),
        cwd=str(DEFAULT_MCP_SERVER_SCRIPT.parent),
        autostart=False,
        read_only_auto=True,
    )
    return Settings(endpoints=(local,), selected_endpoint="local", mcp_servers=(ms,))


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
    }


def _server_from_json(data: dict[str, Any]) -> MCPServerSpec:
    return MCPServerSpec(
        name=str(data["name"]),
        command=str(data["command"]),
        args=tuple(str(item) for item in data.get("args", [])),
        cwd=data.get("cwd"),
        env=tuple((str(k), str(v)) for k, v in data.get("env", [])),
        autostart=bool(data.get("autostart", False)),
        read_only_auto=bool(data.get("read_only_auto", False)),
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
