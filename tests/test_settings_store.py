from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from use_lllm.core.config import ConfigurationError
from use_lllm.core.endpoints import (
    PROVIDER_AZURE_OPENAI,
    TRUST_CLOUD_ALLOWED,
    TRUST_LAN_ALLOWED,
    Endpoint,
)
from use_lllm.core.secret_protection import PROTECTED_SECRET_PREFIX
from use_lllm.core.settings_store import (
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
    def test_default_has_local_loopback_and_no_required_mcp_server(self) -> None:
        settings = default_settings()
        self.assertEqual(settings.selected_endpoint, "local")
        self.assertEqual([e.name for e in settings.endpoints], ["local"])
        self.assertTrue(settings.endpoints[0].base_url.startswith("http://127.0.0.1"))
        self.assertEqual(settings.mcp_servers, ())


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
                Endpoint(
                    name="azure",
                    base_url="https://sample.openai.azure.com",
                    trust=TRUST_CLOUD_ALLOWED,
                    default_model="deployment-a",
                    provider=PROVIDER_AZURE_OPENAI,
                    api_key="secret",
                    context_window=400_000,
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
        payload = settings_to_json(settings)
        azure = next(item for item in payload["endpoints"] if item["name"] == "azure")
        self.assertNotIn("api_key", azure)
        self.assertTrue(azure["api_key_protected"].startswith(PROTECTED_SECRET_PREFIX))
        self.assertEqual(azure["context_window"], 400_000)
        self.assertNotIn("secret", json.dumps(payload))
        self.assertEqual(settings_from_json(payload), settings)

    def test_save_then_load_is_equal(self) -> None:
        settings = self._settings()
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "nested" / "settings.json"
            save_settings(path, settings)
            self.assertNotIn("secret", path.read_text(encoding="utf-8"))
            self.assertEqual(load_settings(path), settings)

    def test_load_migrates_legacy_plaintext_api_key_to_dpapi(self) -> None:
        settings = self._settings()
        legacy = settings_to_json(settings)
        azure = next(item for item in legacy["endpoints"] if item["name"] == "azure")
        azure.pop("api_key_protected")
        azure["api_key"] = "legacy-plaintext-key"
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "settings.json"
            path.write_text(json.dumps(legacy), encoding="utf-8")

            loaded = load_settings(path)
            migrated_text = path.read_text(encoding="utf-8")

        loaded_azure = next(item for item in loaded.endpoints if item.name == "azure")
        self.assertEqual(loaded_azure.api_key, "legacy-plaintext-key")
        self.assertNotIn("legacy-plaintext-key", migrated_text)
        migrated = json.loads(migrated_text)
        migrated_azure = next(item for item in migrated["endpoints"] if item["name"] == "azure")
        self.assertNotIn("api_key", migrated_azure)
        self.assertTrue(migrated_azure["api_key_protected"].startswith(PROTECTED_SECRET_PREFIX))

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
