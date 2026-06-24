"""Tests for the manual reconcile endpoint."""

from __future__ import annotations

from typing import Any

import fakeredis.aioredis
import httpx
import pytest
import respx
from fastapi.testclient import TestClient
from githubkit import GitHub

import caldrith.api.app as app_module
import caldrith.api.reconcile as reconcile_module
from caldrith.api.app import create_app

_TOKEN = "secret-trigger-token"


class FakeArqPool:
    def __init__(self) -> None:
        self.jobs: list[tuple[str, dict[str, Any]]] = []

    async def enqueue_job(self, name: str, **kwargs: Any) -> None:
        self.jobs.append((name, kwargs))

    async def aclose(self) -> None:
        return None


@pytest.fixture(autouse=True)
def _fake_connections(monkeypatch: pytest.MonkeyPatch) -> FakeArqPool:
    fake_redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    fake_arq = FakeArqPool()
    monkeypatch.setattr(app_module.aioredis, "from_url", lambda *a, **k: fake_redis)

    async def _fake_create_pool(*_a: Any, **_k: Any) -> FakeArqPool:
        return fake_arq

    monkeypatch.setattr(app_module, "create_pool", _fake_create_pool)
    return fake_arq


class _FakeFactory:
    """Stand-in for GitHubClientFactory: skips real App-JWT construction in tests.

    The conftest PEM is a placeholder, not a parseable RSA key, so building a
    JWT-signing AppAuthStrategy fails. The reconcile endpoint only uses ``for_app``,
    so returning a respx-mocked client there is enough.
    """

    def __init__(self, *_a: Any, **_k: Any) -> None:
        pass

    def for_app(self, base_url: str | None = None) -> GitHub:
        return GitHub("test-token")


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch, _fake_connections: FakeArqPool) -> TestClient:
    """Test client with the manual-trigger token enabled."""
    monkeypatch.setenv("MANUAL_TRIGGER_TOKEN", _TOKEN)
    monkeypatch.setattr(reconcile_module, "GitHubClientFactory", _FakeFactory)
    # Force AppConfig to re-read env (it's @lru_cache'd in get_config).
    from caldrith.settings import get_config

    get_config.cache_clear()
    app = create_app()
    with TestClient(app) as c:
        c.fake_arq = _fake_connections  # type: ignore[attr-defined]
        yield c
    get_config.cache_clear()


def test_disabled_when_token_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    # Conftest doesn't set MANUAL_TRIGGER_TOKEN -> endpoint should 404 (not advertised).
    monkeypatch.delenv("MANUAL_TRIGGER_TOKEN", raising=False)
    from caldrith.settings import get_config

    get_config.cache_clear()
    app = create_app()
    with TestClient(app) as c:
        resp = c.post("/reconcile", headers={"Authorization": f"Bearer {_TOKEN}"})
    assert resp.status_code == 404
    get_config.cache_clear()


def test_rejects_missing_or_wrong_token(client: TestClient) -> None:
    assert client.post("/reconcile").status_code == 401
    assert client.post("/reconcile", headers={"Authorization": "Bearer nope"}).status_code == 401


@respx.mock
def test_enqueues_for_one_owner(client: TestClient) -> None:
    respx.get("https://api.github.com/orgs/MagmaMoose/installation").mock(
        return_value=httpx.Response(
            200, json={"id": 42, "account": {"login": "MagmaMoose", "type": "Organization"}}
        )
    )

    resp = client.post(
        "/reconcile",
        json={"owner": "MagmaMoose"},
        headers={"Authorization": f"Bearer {_TOKEN}"},
    )

    assert resp.status_code == 202
    assert resp.json() == {"enqueued": [{"installation_id": 42, "owner": "MagmaMoose"}]}
    assert client.fake_arq.jobs == [  # type: ignore[attr-defined]
        ("reconcile_installation", {"installation_id": 42, "owner": "MagmaMoose"})
    ]


@respx.mock
def test_fans_out_to_all_installations_when_no_owner(client: TestClient) -> None:
    respx.get("https://api.github.com/app/installations").mock(
        return_value=httpx.Response(
            200,
            json=[
                {"id": 1, "account": {"login": "Acme", "type": "Organization"}},
                {"id": 2, "account": {"login": "Wile", "type": "Organization"}},
            ],
        )
    )

    resp = client.post("/reconcile", headers={"Authorization": f"Bearer {_TOKEN}"})

    assert resp.status_code == 202
    assert {(j[1]["installation_id"], j[1]["owner"]) for j in client.fake_arq.jobs} == {  # type: ignore[attr-defined]
        (1, "Acme"),
        (2, "Wile"),
    }


@respx.mock
def test_unknown_owner_returns_empty(client: TestClient) -> None:
    respx.get("https://api.github.com/orgs/no-such/installation").mock(
        return_value=httpx.Response(404, json={"message": "Not Found"})
    )

    resp = client.post(
        "/reconcile",
        json={"owner": "no-such"},
        headers={"Authorization": f"Bearer {_TOKEN}"},
    )

    assert resp.status_code == 202
    assert resp.json() == {"enqueued": []}
    assert client.fake_arq.jobs == []  # type: ignore[attr-defined]
