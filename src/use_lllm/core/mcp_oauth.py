"""Small loopback OAuth bridge used by HTTP MCP transports."""

from __future__ import annotations

import asyncio
import webbrowser
from collections import deque
from typing import Any


class MemoryTokenStorage:
    def __init__(self) -> None:
        self.tokens: Any = None
        self.client_info: Any = None

    async def get_tokens(self) -> Any:
        return self.tokens

    async def set_tokens(self, tokens: Any) -> None:
        self.tokens = tokens

    async def get_client_info(self) -> Any:
        return self.client_info

    async def set_client_info(self, client_info: Any) -> None:
        self.client_info = client_info


class OAuthCallbackBroker:
    def __init__(self) -> None:
        self._waiters: deque[asyncio.Future[tuple[str, str | None]]] = deque()

    async def open_browser(self, url: str) -> None:
        await asyncio.to_thread(webbrowser.open, url)

    async def wait(self) -> tuple[str, str | None]:
        future = asyncio.get_running_loop().create_future()
        self._waiters.append(future)
        return await future

    def deliver(self, code: str, state: str | None) -> bool:
        while self._waiters:
            future = self._waiters.popleft()
            if not future.done():
                future.set_result((code, state))
                return True
        return False


oauth_callbacks = OAuthCallbackBroker()
oauth_storages: dict[str, MemoryTokenStorage] = {}
