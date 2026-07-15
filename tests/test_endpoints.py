from __future__ import annotations

import unittest

from use_lllm.core.config import ConfigurationError
from use_lllm.core.endpoints import (
    Endpoint,
    EndpointRegistry,
    TRUST_LAN_ALLOWED,
    TRUST_LOOPBACK,
)


class EndpointTests(unittest.TestCase):
    def test_loopback_endpoint_validates(self) -> None:
        Endpoint(name="local", base_url="http://127.0.0.1:11434").validate()

    def test_loopback_trust_rejects_non_loopback_url(self) -> None:
        endpoint = Endpoint(name="bad", base_url="http://10.0.0.5:11434", trust=TRUST_LOOPBACK)
        with self.assertRaisesRegex(ConfigurationError, "loopback"):
            endpoint.validate()

    def test_lan_allowed_endpoint_validates_non_loopback(self) -> None:
        Endpoint(name="lab", base_url="http://10.242.145.97:11434", trust=TRUST_LAN_ALLOWED).validate()

    def test_invalid_trust_rejected(self) -> None:
        with self.assertRaisesRegex(ConfigurationError, "trust"):
            Endpoint(name="x", base_url="http://127.0.0.1:11434", trust="public").validate()

    def test_spoofed_loopback_host_rejected(self) -> None:
        endpoint = Endpoint(name="spoof", base_url="http://127.0.0.1.evil.com:11434", trust=TRUST_LOOPBACK)
        with self.assertRaisesRegex(ConfigurationError, "loopback"):
            endpoint.validate()

    def test_to_ollama_config_carries_allow_lan_and_model(self) -> None:
        endpoint = Endpoint(
            name="lab", base_url="http://10.242.145.97:11434/", trust=TRUST_LAN_ALLOWED, default_model="qwen3:14b"
        )
        config = endpoint.to_ollama_config()
        self.assertTrue(config.allow_lan)
        self.assertEqual(config.base_url, "http://10.242.145.97:11434")
        self.assertEqual(config.model, "qwen3:14b")
        config.validate()  # lan_allowed なので通る


class EndpointRegistryTests(unittest.TestCase):
    def _registry(self) -> EndpointRegistry:
        return EndpointRegistry(
            [
                Endpoint(name="local", base_url="http://127.0.0.1:11434", default_model="qwen3:14b"),
                Endpoint(name="lab", base_url="http://10.242.145.97:11434", trust=TRUST_LAN_ALLOWED),
            ],
            selected="local",
        )

    def test_names_preserve_order(self) -> None:
        self.assertEqual(self._registry().names(), ["local", "lab"])

    def test_selected_returns_current(self) -> None:
        self.assertEqual(self._registry().selected().name, "local")

    def test_select_switches(self) -> None:
        registry = self._registry()
        registry.select("lab")
        self.assertEqual(registry.selected_name(), "lab")

    def test_select_unknown_raises(self) -> None:
        with self.assertRaisesRegex(ConfigurationError, "未登録"):
            self._registry().select("ghost")

    def test_duplicate_names_rejected(self) -> None:
        with self.assertRaisesRegex(ConfigurationError, "重複"):
            EndpointRegistry(
                [
                    Endpoint(name="dup", base_url="http://127.0.0.1:11434"),
                    Endpoint(name="dup", base_url="http://localhost:11434"),
                ],
                selected="dup",
            )

    def test_selected_must_exist(self) -> None:
        with self.assertRaisesRegex(ConfigurationError, "選択"):
            EndpointRegistry([Endpoint(name="local", base_url="http://127.0.0.1:11434")], selected="nope")


if __name__ == "__main__":
    unittest.main()
