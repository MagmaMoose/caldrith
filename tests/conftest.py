"""Shared fixtures and factories for the Caldrith test suite.

Provides:
  - environment + config setup so :func:`caldrith.settings.get_config` works in tests;
  - a fakeredis client;
  - webhook header/signing factories;
  - installation + GitHub response factories.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from collections.abc import Callable
from typing import Any

import fakeredis.aioredis
import pytest

WEBHOOK_SECRET = "test-secret"
APP_ID = "123456"
# A throwaway, syntactically-valid-looking PEM. Auth strategy construction is not
# exercised against GitHub in unit tests; this just satisfies the settings model.
FAKE_PRIVATE_KEY = "-----BEGIN RSA PRIVATE KEY-----\nMIIBOgIBAAJB\n-----END RSA PRIVATE KEY-----"


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Populate the env and reset the cached config for every test."""
    monkeypatch.setenv("APP_ID", APP_ID)
    monkeypatch.setenv("PRIVATE_KEY", FAKE_PRIVATE_KEY)
    monkeypatch.setenv("WEBHOOK_SECRET", WEBHOOK_SECRET)
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379")
    monkeypatch.setenv("GITHUB_API_URL", "https://api.github.com")
    monkeypatch.setenv("ADMIN_REPO", "admin")
    monkeypatch.setenv("CONFIG_PATH", ".github")
    monkeypatch.setenv("SETTINGS_FILE_PATH", "settings.yml")

    from caldrith.settings import get_config

    get_config.cache_clear()
    yield
    get_config.cache_clear()


@pytest.fixture
async def fake_redis() -> fakeredis.aioredis.FakeRedis:
    """An async fakeredis client with string decoding (mirrors prod config)."""
    client = fakeredis.aioredis.FakeRedis(decode_responses=True)
    yield client
    await client.aclose()


@pytest.fixture
def sign() -> Callable[[bytes], str]:
    """Return a function that produces a valid ``sha256=`` signature for a body."""

    def _sign(body: bytes, secret: str = WEBHOOK_SECRET) -> str:
        digest = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        return f"sha256={digest}"

    return _sign


@pytest.fixture
def make_webhook_headers(sign: Callable[[bytes], str]) -> Callable[..., dict[str, str]]:
    """Factory for a complete, correctly-signed set of webhook headers."""

    def _make(
        body: bytes,
        *,
        event: str = "push",
        delivery: str = "delivery-1",
        secret: str = WEBHOOK_SECRET,
    ) -> dict[str, str]:
        return {
            "X-GitHub-Event": event,
            "X-GitHub-Delivery": delivery,
            "X-Hub-Signature-256": sign(body, secret),
            "Content-Type": "application/json",
        }

    return _make


@pytest.fixture
def make_installation() -> Callable[..., dict[str, Any]]:
    """Factory for an ``installation`` payload fragment."""

    def _make(installation_id: int = 42) -> dict[str, Any]:
        return {"id": installation_id}

    return _make


@pytest.fixture
def make_push_payload(make_installation: Callable[..., dict]) -> Callable[..., dict]:
    """Factory for a ``push`` webhook payload (defaults to admin repo default branch)."""

    def _make(
        *,
        owner: str = "acme",
        repo: str = "admin",
        ref: str = "refs/heads/main",
        default_branch: str = "main",
        installation_id: int = 42,
    ) -> dict[str, Any]:
        return {
            "ref": ref,
            "repository": {
                "name": repo,
                "default_branch": default_branch,
                "owner": {"login": owner},
            },
            "installation": make_installation(installation_id),
        }

    return _make


@pytest.fixture
def make_repo_response() -> Callable[..., dict[str, Any]]:
    """Factory for a minimal GitHub ``repos.get`` JSON response body."""

    def _make(
        *,
        owner: str = "acme",
        name: str = "widget",
        allow_auto_merge: bool = False,
        delete_branch_on_merge: bool = False,
        allow_update_branch: bool = False,
    ) -> dict[str, Any]:
        return {
            "id": 1,
            "node_id": "R_kgDO",
            "name": name,
            "full_name": f"{owner}/{name}",
            "html_url": f"https://github.com/{owner}/{name}",
            "url": f"https://api.github.com/repos/{owner}/{name}",
            "owner": {"login": owner, "id": 5, "url": "https://api.github.com/users/x"},
            "private": False,
            "default_branch": "main",
            "allow_auto_merge": allow_auto_merge,
            "delete_branch_on_merge": delete_branch_on_merge,
            "allow_update_branch": allow_update_branch,
        }

    return _make


def settings_yaml(**repository: Any) -> str:
    """Render a ``settings.yml`` body with the given repository block fields."""
    lines = ["repository:"]
    for key, value in repository.items():
        lines.append(f"  {key}: {json.dumps(value)}")
    return "\n".join(lines) + "\n"
