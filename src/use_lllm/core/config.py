"""Application configuration loaded without importing the MCP server."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
from urllib.parse import urlsplit


DEFAULT_MCP_COMMAND = Path(r"C:\Python314\python.exe")
DEFAULT_MCP_SERVER_SCRIPT = Path(r"C:\Users\yuu18\Lipidmix_with_LLM\server.py")
DEFAULT_OLLAMA_URL = "http://127.0.0.1:11434"
DEFAULT_OLLAMA_MODEL = "qwen3:14b"

LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})


def is_loopback_url(url: str) -> bool:
    """URLのホスト名がloopbackか厳密に判定する(部分一致のなりすましを防ぐ)。"""
    try:
        host = urlsplit(url).hostname
    except ValueError:
        return False
    return host is not None and host.lower() in LOOPBACK_HOSTS


class ConfigurationError(ValueError):
    """Raised when a local MCP process cannot be configured safely."""


@dataclass(frozen=True, slots=True)
class MCPServerConfig:
    command: Path
    server_script: Path
    startup_timeout_seconds: float = 30.0

    @classmethod
    def from_env(cls) -> "MCPServerConfig":
        command = Path(
            os.environ.get("USE_LLLM_MCP_COMMAND", str(DEFAULT_MCP_COMMAND))
        ).expanduser()
        server_script = Path(
            os.environ.get(
                "USE_LLLM_MCP_SERVER_SCRIPT", str(DEFAULT_MCP_SERVER_SCRIPT)
            )
        ).expanduser()
        timeout_text = os.environ.get("USE_LLLM_MCP_STARTUP_TIMEOUT", "30")
        try:
            timeout = float(timeout_text)
        except ValueError as exc:
            raise ConfigurationError(
                "USE_LLLM_MCP_STARTUP_TIMEOUT は数値で指定してください"
            ) from exc
        config = cls(
            command=command,
            server_script=server_script,
            startup_timeout_seconds=timeout,
        )
        config.validate()
        return config

    @property
    def working_directory(self) -> Path:
        return self.server_script.parent

    def validate(self) -> None:
        if self.startup_timeout_seconds <= 0:
            raise ConfigurationError("MCP起動タイムアウトは0より大きくしてください")
        if not self.command.is_file():
            raise ConfigurationError(
                f"MCP起動コマンドが見つかりません: {self.command}"
            )
        if not self.server_script.is_file():
            raise ConfigurationError(
                f"MCPサーバースクリプトが見つかりません: {self.server_script}"
            )


@dataclass(frozen=True, slots=True)
class OllamaConfig:
    base_url: str = DEFAULT_OLLAMA_URL
    model: str = DEFAULT_OLLAMA_MODEL
    timeout_seconds: float = 300.0
    allow_lan: bool = False

    @classmethod
    def from_env(cls) -> "OllamaConfig":
        base_url = os.environ.get("USE_LLLM_OLLAMA_URL", DEFAULT_OLLAMA_URL).rstrip("/")
        model = os.environ.get("USE_LLLM_OLLAMA_MODEL", DEFAULT_OLLAMA_MODEL).strip()
        try:
            timeout = float(os.environ.get("USE_LLLM_OLLAMA_TIMEOUT", "300"))
        except ValueError as exc:
            raise ConfigurationError("USE_LLLM_OLLAMA_TIMEOUTは数値で指定してください") from exc
        config = cls(base_url=base_url, model=model, timeout_seconds=timeout)
        config.validate()
        return config

    def validate(self) -> None:
        if not self.base_url.startswith(("http://", "https://")):
            raise ConfigurationError("Ollama URLはhttp(s)を指定してください。")
        if not self.allow_lan and not is_loopback_url(self.base_url):
            raise ConfigurationError("Ollama URLは既定ではloopbackだけを許可します。")
        if not self.model:
            raise ConfigurationError("Ollamaモデル名が空です。")
        if self.timeout_seconds <= 0:
            raise ConfigurationError("Ollamaタイムアウトは0より大きくしてください。")
