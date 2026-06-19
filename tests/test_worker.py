"""Tests for the ARQ worker's reconcile_installation fan-out.

Covers the org-wide path: load the admin config, enumerate accessible repos, drop the
meta/archived/restricted ones, and enqueue one reconcile_repo job per managed repo.
"""

from __future__ import annotations

import base64

import httpx
import respx
from githubkit import GitHub

from caldrith.worker.worker import reconcile_installation


class _FakeArqRedis:
    """Records enqueue_job calls instead of touching Redis."""

    def __init__(self) -> None:
        self.jobs: list[tuple[str, dict]] = []

    async def enqueue_job(self, name: str, **kwargs: object) -> None:
        self.jobs.append((name, kwargs))


class _FakeFactory:
    """Returns a pre-built githubkit client (respx mocks the HTTP)."""

    def __init__(self, client: GitHub) -> None:
        self._client = client

    def for_installation(self, installation_id: int, base_url: str | None = None) -> GitHub:
        return self._client


def _mock_settings(body: str) -> None:
    encoded = base64.b64encode(body.encode()).decode()
    respx.get("https://api.github.com/repos/acme/admin/contents/.github/settings.yml").mock(
        return_value=httpx.Response(
            200, json={"content": encoded, "encoding": "base64", "type": "file"}
        )
    )


def _mock_repos(*names_archived: tuple[str, bool]) -> respx.Route:
    repos = [
        {"id": i, "name": n, "owner": {"login": "acme", "id": 1}, "archived": a}
        for i, (n, a) in enumerate(names_archived)
    ]
    return respx.get("https://api.github.com/installation/repositories").mock(
        return_value=httpx.Response(200, json={"total_count": len(repos), "repositories": repos})
    )


@respx.mock
async def test_fan_out_excludes_meta_and_archived() -> None:
    _mock_settings("repository:\n  allow_auto_merge: true\n")
    _mock_repos(
        ("admin", False),  # the config repo -> excluded
        (".github", False),  # meta repo -> excluded
        ("widget", False),  # managed
        ("old", True),  # archived -> skipped by the planner
        ("svc", False),  # managed
    )
    redis = _FakeArqRedis()
    ctx = {"client_factory": _FakeFactory(GitHub("token")), "redis": redis}

    count = await reconcile_installation(ctx, installation_id=42, owner="acme")

    enqueued = {kw["repo"] for _, kw in redis.jobs}
    assert enqueued == {"widget", "svc"}
    assert count == 2
    assert all(name == "reconcile_repo" for name, _ in redis.jobs)
    assert all(kw["dry_run"] is False for _, kw in redis.jobs)


@respx.mock
async def test_fan_out_honours_restricted_repos() -> None:
    _mock_settings("repository:\n  allow_auto_merge: true\nrestrictedRepos:\n  - 'svc-*'\n")
    _mock_repos(("widget", False), ("svc-auth", False), ("svc-api", False))
    redis = _FakeArqRedis()
    ctx = {"client_factory": _FakeFactory(GitHub("token")), "redis": redis}

    count = await reconcile_installation(ctx, installation_id=42, owner="acme")

    assert {kw["repo"] for _, kw in redis.jobs} == {"widget"}
    assert count == 1


@respx.mock
async def test_fan_out_skips_when_no_repository_block() -> None:
    _mock_settings("labels:\n  - name: bug\n")  # valid config, but no repository block
    repos_route = _mock_repos(("widget", False))
    redis = _FakeArqRedis()
    ctx = {"client_factory": _FakeFactory(GitHub("token")), "redis": redis}

    count = await reconcile_installation(ctx, installation_id=42, owner="acme")

    assert count == 0
    assert redis.jobs == []
    assert not repos_route.called  # short-circuits before listing repos
