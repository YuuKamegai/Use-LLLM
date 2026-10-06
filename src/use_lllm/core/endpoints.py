"""名前付きOllamaエンドポイントと、明示的なLAN信頼境界。"""

from __future__ import annotations

from dataclasses import dataclass

from use_lllm.core.azure_openai import AzureOpenAIConfig
from use_lllm.core.config import (
    DEFAULT_OLLAMA_NUM_CTX,
    ConfigurationError,
    OllamaConfig,
    is_loopback_url,
)

TRUST_LOOPBACK = "loopback"
TRUST_LAN_ALLOWED = "lan_allowed"
TRUST_CLOUD_ALLOWED = "cloud_allowed"
PROVIDER_OLLAMA = "ollama"
PROVIDER_AZURE_OPENAI = "azure_openai"
_TRUSTS = frozenset({TRUST_LOOPBACK, TRUST_LAN_ALLOWED, TRUST_CLOUD_ALLOWED})
_PROVIDERS = frozenset({PROVIDER_OLLAMA, PROVIDER_AZURE_OPENAI})


@dataclass(frozen=True, slots=True)
class Endpoint:
    name: str
    base_url: str
    trust: str = TRUST_LOOPBACK
    default_model: str | None = None
    provider: str = PROVIDER_OLLAMA
    api_key: str | None = None
    context_window: int | None = None

    def validate(self) -> None:
        if not self.name.strip():
            raise ConfigurationError("エンドポイント名が空です。")
        if not self.base_url.startswith(("http://", "https://")):
            raise ConfigurationError(
                f"エンドポイント {self.name} のURLはhttp(s)を指定してください。"
            )
        if self.trust not in _TRUSTS:
            raise ConfigurationError(f"エンドポイント {self.name} のtrustが不正です: {self.trust}")
        if self.provider not in _PROVIDERS:
            raise ConfigurationError(f"未対応のAI providerです: {self.provider}")
        if self.provider == PROVIDER_OLLAMA and self.trust == TRUST_CLOUD_ALLOWED:
            raise ConfigurationError("Ollamaではcloud_allowedを指定できません。")
        if self.provider == PROVIDER_AZURE_OPENAI:
            if self.trust != TRUST_CLOUD_ALLOWED:
                raise ConfigurationError("Azure OpenAIではクラウド送信の明示許可が必要です。")
            self.to_azure_openai_config().validate()
        elif self.trust == TRUST_LOOPBACK and not is_loopback_url(self.base_url):
            raise ConfigurationError(
                f"エンドポイント {self.name} はloopback指定ですが非loopback URLです。"
            )

    @property
    def allow_lan(self) -> bool:
        return self.trust == TRUST_LAN_ALLOWED

    @property
    def is_remote(self) -> bool:
        return self.trust != TRUST_LOOPBACK

    def to_ollama_config(
        self, *, model: str | None = None, timeout_seconds: float = 300.0
    ) -> OllamaConfig:
        chosen = (model or self.default_model or "").strip()
        return OllamaConfig(
            base_url=self.base_url.rstrip("/"),
            model=chosen,
            timeout_seconds=timeout_seconds,
            allow_lan=self.allow_lan,
            num_ctx=self.context_window or DEFAULT_OLLAMA_NUM_CTX,
        )

    def to_azure_openai_config(self, *, timeout_seconds: float = 300.0) -> AzureOpenAIConfig:
        return AzureOpenAIConfig(
            endpoint=self.base_url,
            api_key=self.api_key or "",
            deployment=(self.default_model or "").strip(),
            timeout_seconds=timeout_seconds,
            context_window=self.context_window,
        )


class EndpointRegistry:
    def __init__(self, endpoints: list[Endpoint], selected: str) -> None:
        if not endpoints:
            raise ConfigurationError("エンドポイントが1つも登録されていません。")
        for endpoint in endpoints:
            endpoint.validate()
        order = [endpoint.name for endpoint in endpoints]
        if len(order) != len(set(order)):
            raise ConfigurationError("エンドポイント名が重複しています。")
        if selected not in order:
            raise ConfigurationError(f"選択中エンドポイントが存在しません: {selected}")
        self._endpoints = {endpoint.name: endpoint for endpoint in endpoints}
        self._order = order
        self._selected = selected

    def names(self) -> list[str]:
        return list(self._order)

    def get(self, name: str) -> Endpoint:
        try:
            return self._endpoints[name]
        except KeyError as exc:
            raise ConfigurationError(f"未登録のエンドポイントです: {name}") from exc

    def selected(self) -> Endpoint:
        return self._endpoints[self._selected]

    def selected_name(self) -> str:
        return self._selected

    def select(self, name: str) -> None:
        if name not in self._endpoints:
            raise ConfigurationError(f"未登録のエンドポイントです: {name}")
        self._selected = name
