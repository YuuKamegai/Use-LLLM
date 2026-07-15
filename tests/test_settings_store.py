from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from use_lllm.core.config import ConfigurationError
from use_lllm.core.endpoints import TRUST_LAN_ALLOWED, Endpoint
from use_lllm.core.settings_store import (
    MS_DATA_PARSER,
    MCPServerSpec,
    Settings,
    default_settings,
    load_settings,
    save_settings,
    settings_from_json,
    settings_to_json,
)


class MCPServerSpecTests(unittest.TestCase):
    def test_validate_rejects_empty_name(self) -> None:
        with self.assertRaisesRegex(ConfigurationError, "名"):
            MCPServerSpec(name="", command="python").validate()

    def test_validate_rejects_empty_command(self) -> None:
        with self.assertRaisesRegex(ConfigurationError, "command"):
            MCPServerSpec(name="srv", command="").validate()

    def test_env_dict_round_trips_pairs(self) -> None:
        spec = MCPServerSpec(name="srv", command="python", env=(("A", "1"), ("B", "2")))
        self.assertEqual(spec.env_dict(), {"A": "1", "B": "2"})


class DefaultSettingsTests(unittest.TestCase):
    def test_default_has_local_loopback_and_ms_data_parser(self) -> None:
        settings = default_settings()
        self.assertEqual(settings.selected_endpoint, "local")
        self.assertEqual([e.name for e in settings.endpoints], ["local"])
        self.assertTrue(settings.endpoints[0].base_url.startswith("http://127.0.0.1"))
        names = [s.name for s in settings.mcp_servers]
        self.assertIn(MS_DATA_PARSER, names)

    def test_ms_data_parser_defaults_to_read_only_auto(self) -> None:
        spec = next(s for s in default_settings().mcp_servers if s.name == MS_DATA_PARSER)
        self.assertTrue(spec.read_only_auto)


class RoundTripTests(unittest.TestCase):
    def _settings(self) -> Settings:
        return Settings(
            endpoints=(
                Endpoint(
                    name="local", base_url="http://127.0.0.1:11434", default_model="qwen3:14b"
                ),
                Endpoint(
                    name="lab", base_url="http://10.242.145.97:11434", trust=TRUST_LAN_ALLOWED
                ),
            ),
            selected_endpoint="lab",
            mcp_servers=(
                MCPServerSpec(
                    name="ms-data-parser",
                    command="python",
                    args=("server.py",),
                    read_only_auto=True,
                ),
                MCPServerSpec(name="other", command="node", args=("mcp.js",), env=(("K", "V"),)),
            ),
        )

    def test_json_round_trip_is_equal(self) -> None:
        settings = self._settings()
        self.assertEqual(settings_from_json(settings_to_json(settings)), settings)

    def test_save_then_load_is_equal(self) -> None:
        settings = self._settings()
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "nested" / "settings.json"
            save_settings(path, settings)
            self.assertEqual(load_settings(path), settings)

    def test_missing_file_returns_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "absent.json"
            self.assertEqual(load_settings(path), default_settings())

    def test_malformed_json_raises(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "bad.json"
            path.write_text("{ not json", encoding="utf-8")
            with self.assertRaisesRegex(ConfigurationError, "設定"):
                load_settings(path)


if __name__ == "__main__":
    unittest.main()
