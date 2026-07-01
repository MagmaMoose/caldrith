"""Tests for the ARQ worker's reconcile_installation fan-out.

Covers the org-wide path: load the admin config, enumerate accessible repos, drop the
meta/archived/restricted ones, and enqueue one reconcile_repo job per managed repo.
"""

from __future__ import annotations

import base64

import httpx
import respx
from githubkit import GitHub

from caldrith.worker.queue import ARQ_QUEUE_NAME
from caldrith.worker.worker import reconcile_installation, update_admin_prs


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
async def test_fan_out_includes_non_repository_tiers() -> None:
    # A config with no `repository:` block but another repo-scoped tier still fans out.
    _mock_settings("labels:\n  - name: bug\n")
    _mock_repos(("widget", False))
    redis = _FakeArqRedis()
    ctx = {"client_factory": _FakeFactory(GitHub("token")), "redis": redis}

    count = await reconcile_installation(ctx, installation_id=42, owner="acme")

    assert count == 1
    assert {kw["repo"] for name, kw in redis.jobs if name == "reconcile_repo"} == {"widget"}


@respx.mock
async def test_fan_out_skips_when_nothing_configured() -> None:
    _mock_settings("restrictedRepos:\n  - 'tmp-*'\n")  # no tier, no org, no overlay
    repos_route = _mock_repos(("widget", False))
    redis = _FakeArqRedis()
    ctx = {"client_factory": _FakeFactory(GitHub("token")), "redis": redis}

    count = await reconcile_installation(ctx, installation_id=42, owner="acme")

    assert count == 0
    assert redis.jobs == []
    assert not repos_route.called  # short-circuits before listing repos


@respx.mock
async def test_organization_block_enqueues_org_reconcile() -> None:
    # An organization block enqueues a single reconcile_org (no repo fan-out needed here).
    _mock_settings("organization:\n  billing_email: ops@acme.test\n")
    repos_route = _mock_repos(("widget", False))
    redis = _FakeArqRedis()
    ctx = {"client_factory": _FakeFactory(GitHub("token")), "redis": redis}

    count = await reconcile_installation(ctx, installation_id=42, owner="acme")

    assert count == 0  # no repo-scoped tier declared
    assert [name for name, _ in redis.jobs] == ["reconcile_org"]
    assert redis.jobs[0][1] == {
        "installation_id": 42,
        "owner": "acme",
        "_queue_name": ARQ_QUEUE_NAME,
    }
    assert not repos_route.called


@respx.mock
async def test_repo_fan_out_targets_caldrith_queue() -> None:
    # Every fanned-out reconcile_repo job must carry the Caldrith queue name, or a
    # co-tenant ARQ worker on the shared Redis steals and fails it ("function not found").
    _mock_settings("labels:\n  - name: bug\n")  # a repo-scoped tier → triggers fan-out
    _mock_repos(("widget", False))
    redis = _FakeArqRedis()
    ctx = {"client_factory": _FakeFactory(GitHub("token")), "redis": redis}

    await reconcile_installation(ctx, installation_id=42, owner="acme")

    assert redis.jobs, "expected at least one fanned-out reconcile_repo"
    assert all(kwargs["_queue_name"] == ARQ_QUEUE_NAME for _, kwargs in redis.jobs)


def test_worker_consumes_the_caldrith_queue() -> None:
    # Consumer side of the same contract: the worker must NOT poll ARQ's shared default.
    from caldrith.worker.worker import WorkerSettings

    assert WorkerSettings.queue_name == ARQ_QUEUE_NAME
    assert WorkerSettings.queue_name != "arq:queue"


@respx.mock
async def test_update_admin_prs_job_updates_behind_pr() -> None:
    # The job sweeps the admin repo's open PRs and re-bases any behind their base.
    respx.get(
        "https://api.github.com/repos/acme/admin/pulls",
        params={"state": "open", "per_page": "100", "page": "1"},
    ).mock(
        return_value=httpx.Response(
            200,
            json=[{"number": 3, "base": {"ref": "main"}, "head": {"ref": "x", "label": "acme:x"}}],
        )
    )
    respx.get("https://api.github.com/repos/acme/admin/compare/main...acme:x").mock(
        return_value=httpx.Response(200, json={"behind_by": 2})
    )
    update = respx.put("https://api.github.com/repos/acme/admin/pulls/3/update-branch").mock(
        return_value=httpx.Response(202, json={})
    )
    ctx = {"client_factory": _FakeFactory(GitHub("token"))}

    updated = await update_admin_prs(ctx, installation_id=42, owner="acme")

    assert update.called
    assert updated == 1
