"""Tests for the teams tier (Org-only full-replace; User repo = no-op)."""

from __future__ import annotations

import httpx
import respx
from githubkit import GitHub

from caldrith.config.schema import SafeSettingsConfig
from caldrith.reconcile.planner import TargetRepo
from caldrith.reconcile.teams import reconcile

_LIST = "https://api.github.com/repos/acme/widget/teams"
_TARGET = TargetRepo("acme", "widget")


def _team(slug: str, permission: str) -> dict:
    """A list-endpoint team row; permissions booleans derived from the effective role."""
    order = ("admin", "maintain", "push", "triage", "pull")
    idx = order.index(permission)
    return {
        "slug": slug,
        "permission": permission,
        "permissions": {role: i >= idx for i, role in enumerate(order)},
    }


def _config(*teams: dict) -> SafeSettingsConfig:
    return SafeSettingsConfig.model_validate({"teams": list(teams)})


@respx.mock
async def test_add_team_when_missing() -> None:
    respx.get(_LIST).mock(return_value=httpx.Response(200, json=[]))
    put = respx.put("https://api.github.com/orgs/acme/teams/core/repos/acme/widget").mock(
        return_value=httpx.Response(204)
    )

    config = _config({"name": "core", "permission": "push"})
    async with GitHub("token") as client:
        results = await reconcile(client, _TARGET, config)

    assert put.called
    body = put.calls.last.request.read()
    assert b'"permission"' in body and b"push" in body
    result = results[0]
    assert result.tier == "teams"
    assert result.scope == "acme/widget"
    assert result.changed is True
    assert result.applied is True


@respx.mock
async def test_update_team_when_permission_drifts() -> None:
    # Live effective permission is pull; we want push -> update.
    respx.get(_LIST).mock(return_value=httpx.Response(200, json=[_team("core", "pull")]))
    put = respx.put("https://api.github.com/orgs/acme/teams/core/repos/acme/widget").mock(
        return_value=httpx.Response(204)
    )

    config = _config({"name": "core", "permission": "push"})
    async with GitHub("token") as client:
        results = await reconcile(client, _TARGET, config)

    assert put.called
    assert results[0].changed is True
    assert results[0].applied is True


@respx.mock
async def test_noop_when_converged() -> None:
    respx.get(_LIST).mock(return_value=httpx.Response(200, json=[_team("core", "push")]))
    put = respx.put("https://api.github.com/orgs/acme/teams/core/repos/acme/widget").mock(
        return_value=httpx.Response(204)
    )

    config = _config({"name": "core", "permission": "push"})
    async with GitHub("token") as client:
        results = await reconcile(client, _TARGET, config)

    assert not put.called
    assert results[0].changed is False
    assert results[0].applied is False


@respx.mock
async def test_dry_run_never_writes() -> None:
    respx.get(_LIST).mock(return_value=httpx.Response(200, json=[]))
    put = respx.put("https://api.github.com/orgs/acme/teams/core/repos/acme/widget").mock(
        return_value=httpx.Response(204)
    )

    config = _config({"name": "core", "permission": "push"})
    async with GitHub("token") as client:
        results = await reconcile(client, _TARGET, config, dry_run=True)

    assert not put.called
    assert results[0].changed is True
    assert results[0].applied is False


@respx.mock
async def test_prune_undeclared_team() -> None:
    respx.get(_LIST).mock(
        return_value=httpx.Response(200, json=[_team("core", "push"), _team("stale", "admin")])
    )
    put = respx.put("https://api.github.com/orgs/acme/teams/core/repos/acme/widget").mock(
        return_value=httpx.Response(204)
    )
    delete = respx.delete("https://api.github.com/orgs/acme/teams/stale/repos/acme/widget").mock(
        return_value=httpx.Response(204)
    )

    config = _config({"name": "core", "permission": "push"})
    async with GitHub("token") as client:
        results = await reconcile(client, _TARGET, config)

    assert not put.called  # core converged
    assert delete.called  # stale removed
    assert results[0].changed is True
    assert results[0].applied is True


@respx.mock
async def test_user_repo_404_is_graceful_noop() -> None:
    # A User-owned repo: the teams list endpoint returns 404 -> no writes, no change.
    respx.get(_LIST).mock(return_value=httpx.Response(404, json={"message": "Not Found"}))
    put = respx.put("https://api.github.com/orgs/acme/teams/core/repos/acme/widget").mock(
        return_value=httpx.Response(204)
    )
    delete = respx.delete("https://api.github.com/orgs/acme/teams/core/repos/acme/widget").mock(
        return_value=httpx.Response(204)
    )

    config = _config({"name": "core", "permission": "push"})
    async with GitHub("token") as client:
        results = await reconcile(client, _TARGET, config)

    assert not put.called
    assert not delete.called
    assert results[0].changed is False
    assert results[0].applied is False
