"""汎用チャット単体 loopback ASGI アプリ。"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.responses import RedirectResponse

from use_lllm.general.api import create_general_app


def create_app(*, general_app: FastAPI | None = None) -> FastAPI:
    app = FastAPI(title="Use-LLLM WebUI", version="0.1.0")
    app.mount("/general", general_app or create_general_app(), name="general")

    @app.get("/", include_in_schema=False)
    async def _root() -> RedirectResponse:
        return RedirectResponse(url="/general/")

    return app


app = create_app()
