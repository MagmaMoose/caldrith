"""Tests for the webhook ingest endpoint.

Uses FastAPI's TestClient with fakeredis for dedup and a recording fake ARQ pool so we
can assert which jobs would be enqueued — without a real Redis or worker.
"""

from __future__ import annotations

import json
from typing import Any

import fakeredis.aioredis
import pytest
from fastapi.testclient import TestClient

import caldrith.api.app as app_module
from caldrith.api.app import create_app


class FakeArqPool:
    """Records ``enqueue_job`` calls."""

    def __init__(self) -> None:
        self.jobs: list[tuple[str, dict[str, Any]]] = []

    async def enqueue_job(self, name: str, **kwargs: Any) -> None:
        self.jobs.append((name, kwargs))

    async def aclose(self) -> None:
        return None


@pytest.fixture(autouse=True)
def _fake_connections(monkeypatch: pytest.MonkeyPatch) -> FakeArqPool:
    """Replace the app's Redis + ARQ pool factories so the lifespan needs no server.

    Both ``redis.asyncio.from_url`` (dedup client) and ``arq.create_pool`` (enqueue
    pool) are patched at the point they're used in :mod:`caldrith.api.app`.
    """
    fake_redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    fake_arq = FakeArqPool()

    monkeypatch.setattr(app_module.aioredis, "from_url", lambda *a, **k: fake_redis)

    async def _fake_create_pool(*_a: Any, **_k: Any) -> FakeArqPool:
        return fake_arq

    monkeypatch.setattr(app_module, "create_pool", _fake_create_pool)
    return fake_arq


@pytest.fixture
def client(_fake_connections: FakeArqPool) -> TestClient:
    """A TestClient backed by the fake Redis + ARQ pool."""
    app = create_app()
    with TestClient(app) as test_client:
        test_client.fake_arq = _fake_connections  # type: ignore[attr-defined]
        yield test_client


def _post(client: TestClient, payload: dict, *, event: str, delivery: str) -> Any:
    import hashlib
    import hmac

    body = json.dumps(payload).encode()
    sig = "sha256=" + hmac.new(b"test-secret", body, hashlib.sha256).hexdigest()
    return client.post(
        "/",
        content=body,
        headers={
            "X-GitHub-Event": event,
            "X-GitHub-Delivery": delivery,
            "X-Hub-Signature-256": sig,
            "Content-Type": "application/json",
        },
    )


def test_healthz_no_deps(client: TestClient) -> None:
    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_readyz_ok_when_redis_reachable(client: TestClient) -> None:
    resp = client.get("/readyz")
    assert resp.status_code == 200


def test_invalid_signature_rejected(client: TestClient) -> None:
    body = json.dumps({"installation": {"id": 1}}).encode()
    resp = client.post(
        "/",
        content=body,
        headers={
            "X-GitHub-Event": "push",
            "X-GitHub-Delivery": "d1",
            "X-Hub-Signature-256": "sha256=deadbeef",
        },
    )
    assert resp.status_code == 401


def test_push_to_admin_default_branch_enqueues_full_sync(client: TestClient) -> None:
    payload = {
        "ref": "refs/heads/main",
        "repository": {
            "name": "admin",
            "default_branch": "main",
            "owner": {"login": "acme"},
        },
        "installation": {"id": 42},
    }
    resp = _post(client, payload, event="push", delivery="push-1")
    assert resp.status_code == 202
    jobs = client.fake_arq.jobs  # type: ignore[attr-defined]
    assert jobs == [("reconcile_installation", {"installation_id": 42, "owner": "acme"})]


def test_push_touching_settings_also_updates_open_prs(client: TestClient) -> None:
    payload = {
        "ref": "refs/heads/main",
        "repository": {
            "name": "admin",
            "default_branch": "main",
            "owner": {"login": "acme"},
        },
        "commits": [{"modified": [".github/settings.yml"], "added": [], "removed": []}],
        "installation": {"id": 42},
    }
    resp = _post(client, payload, event="push", delivery="push-settings")
    assert resp.status_code == 202
    jobs = client.fake_arq.jobs  # type: ignore[attr-defined]
    assert jobs == [
        ("reconcile_installation", {"installation_id": 42, "owner": "acme"}),
        ("update_admin_prs", {"installation_id": 42, "owner": "acme"}),
    ]


def test_push_not_touching_settings_skips_pr_update(client: TestClient) -> None:
    payload = {
        "ref": "refs/heads/main",
        "repository": {
            "name": "admin",
            "default_branch": "main",
            "owner": {"login": "acme"},
        },
        "commits": [{"modified": ["README.md"], "added": [], "removed": []}],
        "installation": {"id": 42},
    }
    resp = _post(client, payload, event="push", delivery="push-readme")
    assert resp.status_code == 202
    jobs = client.fake_arq.jobs  # type: ignore[attr-defined]
    assert jobs == [("reconcile_installation", {"installation_id": 42, "owner": "acme"})]


def test_push_to_non_admin_repo_ignored(client: TestClient) -> None:
    payload = {
        "ref": "refs/heads/main",
        "repository": {
            "name": "widget",
            "default_branch": "main",
            "owner": {"login": "acme"},
        },
        "installation": {"id": 42},
    }
    resp = _post(client, payload, event="push", delivery="push-2")
    assert resp.status_code == 202
    assert client.fake_arq.jobs == []  # type: ignore[attr-defined]


def test_push_to_non_default_branch_ignored(client: TestClient) -> None:
    payload = {
        "ref": "refs/heads/feature",
        "repository": {
            "name": "admin",
            "default_branch": "main",
            "owner": {"login": "acme"},
        },
        "installation": {"id": 42},
    }
    resp = _post(client, payload, event="push", delivery="push-3")
    assert resp.status_code == 202
    assert client.fake_arq.jobs == []  # type: ignore[attr-defined]


def test_repository_created_enqueues_single_repo(client: TestClient) -> None:
    payload = {
        "action": "created",
        "repository": {"name": "widget", "owner": {"login": "acme"}},
        "installation": {"id": 42},
    }
    resp = _post(client, payload, event="repository", delivery="repo-1")
    assert resp.status_code == 202
    name, kwargs = client.fake_arq.jobs[0]  # type: ignore[attr-defined]
    assert name == "reconcile_repo"
    assert kwargs["repo"] == "widget"
    assert kwargs["dry_run"] is False


def test_pull_request_settings_change_is_dry_run(client: TestClient) -> None:
    payload = {
        "action": "opened",
        "repository": {
            "name": "admin",
            "default_branch": "main",
            "owner": {"login": "acme"},
        },
        "pull_request": {"head": {"ref": "feature", "sha": "abc123"}},
        "installation": {"id": 42},
    }
    resp = _post(client, payload, event="pull_request", delivery="pr-1")
    assert resp.status_code == 202
    name, kwargs = client.fake_arq.jobs[0]  # type: ignore[attr-defined]
    assert name == "reconcile_repo"
    assert kwargs["dry_run"] is True
    assert kwargs["head_sha"] == "abc123"


def test_duplicate_delivery_dropped(client: TestClient) -> None:
    payload = {
        "ref": "refs/heads/main",
        "repository": {
            "name": "admin",
            "default_branch": "main",
            "owner": {"login": "acme"},
        },
        "installation": {"id": 42},
    }
    first = _post(client, payload, event="push", delivery="dup")
    second = _post(client, payload, event="push", delivery="dup")
    assert first.json()["status"] == "accepted"
    assert second.json()["status"] == "duplicate"
    # Only the first delivery enqueued a job.
    assert len(client.fake_arq.jobs) == 1  # type: ignore[attr-defined]


def test_payload_without_installation_ignored(client: TestClient) -> None:
    resp = _post(client, {"zen": "ping"}, event="ping", delivery="ping-1")
    assert resp.status_code == 202
    assert resp.json()["status"] == "ignored"
    assert client.fake_arq.jobs == []  # type: ignore[attr-defined]


@pytest.mark.parametrize(
    "event",
    ["label", "milestone", "member", "branch_protection_rule", "public"],
)
def test_drift_event_reconciles_affected_repo(client: TestClient, event: str) -> None:
    """An out-of-band change to a managed setting self-heals the affected repo."""
    payload = {
        "action": "edited",
        "repository": {"name": "widget", "owner": {"login": "acme"}},
        "installation": {"id": 42},
    }
    resp = _post(client, payload, event=event, delivery=f"drift-{event}")
    assert resp.status_code == 202
    name, kwargs = client.fake_arq.jobs[0]  # type: ignore[attr-defined]
    assert name == "reconcile_repo"
    assert kwargs["owner"] == "acme"
    assert kwargs["repo"] == "widget"
    assert kwargs["dry_run"] is False


def test_repo_ruleset_drift_reconciles_repo(client: TestClient) -> None:
    """A repo-scoped ruleset change re-reconciles that repository."""
    payload = {
        "action": "edited",
        "repository": {"name": "widget", "owner": {"login": "acme"}},
        "installation": {"id": 42},
    }
    resp = _post(client, payload, event="repository_ruleset", delivery="rs-repo")
    assert resp.status_code == 202
    name, kwargs = client.fake_arq.jobs[0]  # type: ignore[attr-defined]
    assert name == "reconcile_repo" and kwargs["repo"] == "widget"


def test_org_ruleset_drift_reconciles_org(client: TestClient) -> None:
    """An org-scoped ruleset change (no repository) re-reconciles the organization."""
    payload = {
        "action": "edited",
        "organization": {"login": "acme"},
        "installation": {"id": 42},
    }
    resp = _post(client, payload, event="repository_ruleset", delivery="rs-org")
    assert resp.status_code == 202
    assert client.fake_arq.jobs == [  # type: ignore[attr-defined]
        ("reconcile_org", {"installation_id": 42, "owner": "acme"})
    ]


def test_unknown_event_ignored(client: TestClient) -> None:
    payload = {
        "repository": {"name": "widget", "owner": {"login": "acme"}},
        "installation": {"id": 7},
    }
    resp = _post(client, payload, event="star", delivery="star-1")
    assert resp.status_code == 202
    assert client.fake_arq.jobs == []  # type: ignore[attr-defined]
