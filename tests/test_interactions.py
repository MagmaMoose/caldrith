"""Tests for the interaction-limits tier (set/remove on ``limit`` drift)."""

from __future__ import annotations

import json

import httpx
import respx
from githubkit import GitHub

from caldrith.config.schema import SafeSettingsConfig
from caldrith.reconcile.interactions import reconcile
from caldrith.reconcile.planner import TargetRepo

_LIMITS = "https://api.github.com/repos/acme/widget/interaction-limits"


def _cfg(**interaction_limits: object) -> SafeSettingsConfig:
    return SafeSettingsConfig.model_validate({"interaction_limits": interaction_limits})


@respx.mock
async def test_set_when_different_puts() -> None:
    respx.get(_LIMITS).mock(return_value=httpx.Response(200, json={}))  # no limit
    put = respx.put(_LIMITS).mock(
        return_value=httpx.Response(
            200, json={"limit": "collaborators_only", "origin": "repository"}
        )
    )

    async with GitHub("token") as client:
        results = await reconcile(
            client, TargetRepo("acme", "widget"), _cfg(limit="collaborators_only")
        )

    assert put.called
    body = json.loads(put.calls.last.request.content)
    assert body["limit"] == "collaborators_only"
    (result,) = results
    assert result.tier == "interaction_limits"
    assert result.scope == "acme/widget"
    assert result.changed is True
    assert result.applied is True


@respx.mock
async def test_converged_no_write() -> None:
    respx.get(_LIMITS).mock(
        return_value=httpx.Response(
            200,
            json={
                "limit": "collaborators_only",
                "origin": "repository",
                "expires_at": "2026-01-01T00:00:00Z",
            },
        )
    )
    put = respx.put(_LIMITS).mock(return_value=httpx.Response(200, json={}))
    delete = respx.delete(_LIMITS).mock(return_value=httpx.Response(204))

    async with GitHub("token") as client:
        results = await reconcile(
            client, TargetRepo("acme", "widget"), _cfg(limit="collaborators_only")
        )

    assert not put.called
    assert not delete.called
    (result,) = results
    assert result.changed is False
    assert result.applied is False


@respx.mock
async def test_dry_run_never_writes() -> None:
    respx.get(_LIMITS).mock(return_value=httpx.Response(200, json={}))
    put = respx.put(_LIMITS).mock(return_value=httpx.Response(200, json={}))

    async with GitHub("token") as client:
        results = await reconcile(
            client,
            TargetRepo("acme", "widget"),
            _cfg(limit="collaborators_only"),
            dry_run=True,
        )

    assert not put.called
    (result,) = results
    assert result.changed is True
    assert result.applied is False


@respx.mock
async def test_remove_when_present_deletes() -> None:
    respx.get(_LIMITS).mock(
        return_value=httpx.Response(
            200, json={"limit": "collaborators_only", "origin": "repository"}
        )
    )
    delete = respx.delete(_LIMITS).mock(return_value=httpx.Response(204))

    async with GitHub("token") as client:
        results = await reconcile(client, TargetRepo("acme", "widget"), _cfg(limit=None))

    assert delete.called
    (result,) = results
    assert result.changed is True
    assert result.applied is True


@respx.mock
async def test_remove_when_absent_is_noop() -> None:
    respx.get(_LIMITS).mock(return_value=httpx.Response(200, json={}))  # already no limit
    delete = respx.delete(_LIMITS).mock(return_value=httpx.Response(204))

    async with GitHub("token") as client:
        results = await reconcile(client, TargetRepo("acme", "widget"), _cfg(limit=None))

    assert not delete.called
    (result,) = results
    assert result.changed is False
    assert result.applied is False
