from fastapi import FastAPI
from fastapi.testclient import TestClient

from use_lllm.app import create_app


def _stub_general() -> FastAPI:
    sub = FastAPI()

    @sub.get("/api/health")
    async def _health() -> dict[str, str]:
        return {"surface": "general"}

    return sub


def test_root_redirects_to_general() -> None:
    app = create_app(general_app=_stub_general())
    with TestClient(app) as client:
        resp = client.get("/", follow_redirects=False)
        assert resp.status_code == 307
        assert resp.headers["location"] == "/general/"


def test_general_is_mounted() -> None:
    app = create_app(general_app=_stub_general())
    with TestClient(app) as client:
        assert client.get("/general/api/health").json()["surface"] == "general"
