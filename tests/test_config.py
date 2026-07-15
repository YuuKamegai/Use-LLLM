from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from use_lllm.core.config import ConfigurationError, MCPServerConfig, OllamaConfig


class MCPServerConfigTests(unittest.TestCase):
    def test_default_ollama_is_loopback_and_quality_selected_model(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            config = OllamaConfig.from_env()
        self.assertEqual(config.base_url, "http://127.0.0.1:11434")
        self.assertEqual(config.model, "qwen3:14b")

    def test_from_env_reads_existing_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            command = root / "python.exe"
            script = root / "server.py"
            command.touch()
            script.touch()
            env = {
                "USE_LLLM_MCP_COMMAND": str(command),
                "USE_LLLM_MCP_SERVER_SCRIPT": str(script),
                "USE_LLLM_MCP_STARTUP_TIMEOUT": "12.5",
            }
            with patch.dict(os.environ, env, clear=False):
                config = MCPServerConfig.from_env()

        self.assertEqual(config.command, command)
        self.assertEqual(config.server_script, script)
        self.assertEqual(config.startup_timeout_seconds, 12.5)

    def test_validate_rejects_missing_command(self) -> None:
        config = MCPServerConfig(
            command=Path("missing-python.exe"),
            server_script=Path(__file__),
        )
        with self.assertRaisesRegex(ConfigurationError, "起動コマンド"):
            config.validate()


class OllamaConfigTrustTests(unittest.TestCase):
    def test_loopback_still_required_by_default(self) -> None:
        config = OllamaConfig(base_url="http://10.242.145.97:11434", model="qwen3:14b")
        with self.assertRaisesRegex(ConfigurationError, "loopback"):
            config.validate()

    def test_lan_url_allowed_when_allow_lan_true(self) -> None:
        config = OllamaConfig(
            base_url="http://10.242.145.97:11434", model="qwen3:14b", allow_lan=True
        )
        config.validate()  # 例外を投げない

    def test_allow_lan_still_requires_http_scheme(self) -> None:
        config = OllamaConfig(base_url="ftp://10.0.0.1", model="m", allow_lan=True)
        with self.assertRaisesRegex(ConfigurationError, "http"):
            config.validate()

    def test_from_env_defaults_allow_lan_false(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            config = OllamaConfig.from_env()
        self.assertFalse(config.allow_lan)

    def test_spoofed_loopback_host_rejected(self) -> None:
        config = OllamaConfig(base_url="http://localhost.evil.com:11434", model="m")
        with self.assertRaisesRegex(ConfigurationError, "loopback"):
            config.validate()

    def test_genuine_loopback_variants_accepted(self) -> None:
        for url in ("http://127.0.0.1:11434", "http://localhost:11434", "http://[::1]:11434"):
            OllamaConfig(base_url=url, model="m").validate()


if __name__ == "__main__":
    unittest.main()
