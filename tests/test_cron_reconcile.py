"""Tests for the periodic ``reconcile_all_installations`` cron job and its cron-list builder."""

from __future__ import annotations

import httpx
import pytest
import respx
from githubkit import GitHub

from caldrith.worker.worker import _cron_jobs, reconcile_all_installations


class _FakeArqRedis:
    def __init__(self) -> None:
        self.jobs: list[tuple[str, dict]] = []

    async def enqueue_job(self, name: str, **kwargs: object) -> None:
        self.jobs.append((name, kwargs))


class _FakeFactory:
    """Returns a pre-built githubkit client (respx mocks the HTTP)."""

    def __init__(self, client: GitHub) -> None:
        self._client = client

    def for_app(self, base_url: str | None = None) -> GitHub:
        return self._client


@respx.mock
async def test_reconcile_all_installations_enqueues_one_job_per_install() -> None:
    respx.get("https://api.github.com/app/installations").mock(
        return_value=httpx.Response(
            200,
            json=[
                {"id": 1, "account": {"login": "Acme", "type": "Organization"}},
                {"id": 2, "account": {"login": "Wile", "type": "Organization"}},
                {"id": 3, "account": {"login": "Globex", "type": "Organization"}},
            ],
        )
    )
    redis = _FakeArqRedis()
    ctx = {"client_factory": _FakeFactory(GitHub("token")), "redis": redis}

    count = await reconcile_all_installations(ctx)

    assert count == 3
    assert all(n == "reconcile_installation" for n, _ in redis.jobs)
    assert {(kw["installation_id"], kw["owner"]) for _, kw in redis.jobs} == {
        (1, "Acme"),
        (2, "Wile"),
        (3, "Globex"),
    }


def test_cron_jobs_empty_when_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    # Default (or 0) -> no cron registered.
    monkeypatch.delenv("RECONCILE_CRON_MINUTES", raising=False)
    from caldrith.settings import get_config

    get_config.cache_clear()
    assert _cron_jobs() == []
    get_config.cache_clear()


def test_cron_jobs_enabled_with_every_n_minutes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RECONCILE_CRON_MINUTES", "15")
    from caldrith.settings import get_config

    get_config.cache_clear()
    jobs = _cron_jobs()
    assert len(jobs) == 1
    # ARQ CronJob exposes the minute set we configured (every 15 minutes -> 0/15/30/45).
    assert jobs[0].minute == {0, 15, 30, 45}
    get_config.cache_clear()


def test_cron_jobs_multi_hour_uses_hour_axis(monkeypatch: pytest.MonkeyPatch) -> None:
    # >= 60 must drive `hour=` (not `minute=`); else 120/720/1440 silently collapse
    # to hourly@:00. 120 -> every 2 hours at minute 0.
    monkeypatch.setenv("RECONCILE_CRON_MINUTES", "120")
    from caldrith.settings import get_config

    get_config.cache_clear()
    jobs = _cron_jobs()
    assert len(jobs) == 1
    assert jobs[0].hour == {0, 2, 4, 6, 8, 10, 12, 14, 16, 18, 20, 22}
    assert jobs[0].minute == {0}
    get_config.cache_clear()
