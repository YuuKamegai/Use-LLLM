"""Loopback WebUI request-boundary protections."""

from __future__ import annotations

import hmac
from urllib.parse import urlsplit

from starlette.datastructures import Headers
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

CSRF_HEADER = "X-Use-LLLM-CSRF"
CSRF_META_NAME = "use-lllm-csrf-token"
CSRF_PLACEHOLDER = "__USE_LLLM_CSRF_TOKEN__"
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
_UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


def _authority(value: str) -> tuple[str, int] | None:
    if not value or any(char in value for char in ("/", "\\", "@")):
        return None
    try:
        parsed = urlsplit(f"//{value}")
        hostname = (parsed.hostname or "").lower()
        port = parsed.port or 80
    except ValueError:
        return None
    if hostname not in _LOOPBACK_HOSTS:
        return None
    return hostname, port


def _origin_matches(origin: str, host: str) -> bool:
    try:
        parsed = urlsplit(origin)
        origin_host = (parsed.hostname or "").lower()
        origin_port = parsed.port or (80 if parsed.scheme == "http" else 443)
    except ValueError:
        return False
    authority = _authority(host)
    return bool(
        authority
        and parsed.scheme == "http"
        and parsed.username is None
        and parsed.password is None
        and origin_host == authority[0]
        and origin_port == authority[1]
    )


class LocalAPISecurityMiddleware:
    """Reject non-loopback Host headers and protect state-changing local API calls."""

    def __init__(self, app: ASGIApp, *, csrf_token: str) -> None:
        self.app = app
        self.csrf_token = csrf_token

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = Headers(scope=scope)
        host = headers.get("host", "")
        if _authority(host) is None:
            await JSONResponse(
                {"detail": "Use-LLLMはloopback Hostからのみ利用できます。"},
                status_code=400,
            )(scope, receive, send)
            return

        path = scope.get("path", "")
        root_path = scope.get("root_path", "")
        local_path = path[len(root_path) :] if root_path and path.startswith(root_path) else path
        local_path = local_path or "/"
        method = scope.get("method", "GET").upper()
        if local_path.startswith("/api/") and method in _UNSAFE_METHODS:
            supplied = headers.get(CSRF_HEADER, "")
            if not supplied or not hmac.compare_digest(supplied, self.csrf_token):
                await JSONResponse(
                    {"detail": "ローカルAPIの起動トークンが不正です。"},
                    status_code=403,
                )(scope, receive, send)
                return

            origin = headers.get("origin")
            if origin is not None and not _origin_matches(origin, host):
                await JSONResponse(
                    {"detail": "このOriginからの設定変更は許可されていません。"},
                    status_code=403,
                )(scope, receive, send)
                return

            fetch_site = headers.get("sec-fetch-site")
            if fetch_site is not None and fetch_site not in {"same-origin", "none"}:
                await JSONResponse(
                    {"detail": "cross-siteの設定変更は許可されていません。"},
                    status_code=403,
                )(scope, receive, send)
                return

        await self.app(scope, receive, send)
