from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from use_lllm.core.endpoints import TRUST_LAN_ALLOWED, Endpoint, EndpointRegistry
from use_lllm.core.policy import decide_server_tool
from use_lllm.core.settings_store import (
    MCPServerSpec,
    default_settings,
    load_settings,
    save_settings,
)


class FoundationIntegrationTests(unittest.TestCase):
    def test_default_settings_build_a_usable_endpoint_registry(self) -> None:
        settings = default_settings()
        registry = EndpointRegistry(list(settings.endpoints), settings.selected_endpoint)
        config = registry.selected().to_ollama_config()
        config.validate()
        self.assertFalse(config.allow_lan)

    def test_lan_endpoint_round_trips_and_yields_allow_lan_config(self) -> None:
        settings = default_settings()
        lab = Endpoint(
            name="lab",
            base_url="http://10.242.145.97:11434",
            trust=TRUST_LAN_ALLOWED,
            default_model="qwen3:14b",
        )
        mutated = settings.__class__(
            endpoints=settings.endpoints + (lab,),
            selected_endpoint="lab",
            mcp_servers=settings.mcp_servers,
        )
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "settings.json"
            save_settings(path, mutated)
            loaded = load_settings(path)
        registry = EndpointRegistry(list(loaded.endpoints), loaded.selected_endpoint)
        config = registry.selected().to_ollama_config()
        self.assertTrue(config.allow_lan)
        config.validate()

    def test_ms_data_parser_read_only_auto_flows_into_policy(self) -> None:
        spec = MCPServerSpec(name="ms-data-parser", command="python", read_only_auto=True)
        decision = decide_server_tool(spec.name, "arf_parser", read_only_auto=spec.read_only_auto)
        self.assertTrue(decision.allowed)
        self.assertFalse(decision.approval_required)


if __name__ == "__main__":
    unittest.main()
