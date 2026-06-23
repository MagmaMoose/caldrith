"""Tests for the Actions variables tier (full-replace, value-diffable)."""

from __future__ import annotations

import json
from typing import Any

import httpx
import respx
from githubkit import GitHub

from caldrith.config.schema import SafeSettingsConfig
from caldrith.reconcile import variables
from caldrith.reconcile.planner import TargetRepo

_TARGET = TargetRepo("acme", "widget")
_LIST = "https://api.github.com/repos/acme/widget/actions/variables"


def _list_body(*vars_: dict[str, Any]) -> dict[str, Any]:
    return {"total_count": len(vars_), "variables": list(vars_)}


def _config(*vars_: dict[str, str]) -> SafeSettingsConfig:
    return SafeSettingsConfig.model_validate({"variables": list(vars_)})


@respx.mock
async def test_creates_missing_variable() -> None:
    respx.get(_LIST).mock(return_value=httpx.Response(200, json=_list_body()))
    create = respx.post(_LIST).mock(
        return_value=httpx.Response(201, json={"name": "FOO", "value": "bar"})
    )

    async with GitHub("token") as client:
        results = await variables.reconcile(
            client, _TARGET, _config({"name": "FOO", "value": "bar"})
        )

    (result,) = results
    assert create.called
    body = json.loads(create.calls.last.request.content)
    assert body == {"name": "FOO", "value": "bar"}
    assert result.tier == "variables"
    assert result.scope == "acme/widget"
    assert result.changed is True
    assert result.applied is True


@respx.mock
async def test_updates_value_drift() -> None:
    respx.get(_LIST).mock(
        return_value=httpx.Response(200, json=_list_body({"name": "FOO", "value": "stale"}))
    )
    patch = respx.patch(f"{_LIST}/FOO").mock(
        return_value=httpx.Response(204, json={"name": "FOO", "value": "bar"})
    )

    async with GitHub("token") as client:
        results = await variables.reconcile(
            client, _TARGET, _config({"name": "FOO", "value": "bar"})
        )

    (result,) = results
    assert patch.called
    body = json.loads(patch.calls.last.request.content)
    assert body == {"value": "bar"}
    assert result.changed is True
    assert result.applied is True


@respx.mock
async def test_prunes_undeclared_variable() -> None:
    respx.get(_LIST).mock(
        return_value=httpx.Response(
            200,
            json=_list_body(
                {"name": "FOO", "value": "bar"},
                {"name": "STALE", "value": "x"},
            ),
        )
    )
    delete = respx.delete(f"{_LIST}/STALE").mock(return_value=httpx.Response(204))

    async with GitHub("token") as client:
        results = await variables.reconcile(
            client, _TARGET, _config({"name": "FOO", "value": "bar"})
        )

    (result,) = results
    assert delete.called
    assert result.changed is True
    assert result.applied is True
    assert any("delete variable: STALE" in n for n in result.notes)


@respx.mock
async def test_converged_is_noop() -> None:
    respx.get(_LIST).mock(
        return_value=httpx.Response(200, json=_list_body({"name": "FOO", "value": "bar"}))
    )
    create = respx.post(_LIST).mock(return_value=httpx.Response(201))
    patch = respx.patch(f"{_LIST}/FOO").mock(return_value=httpx.Response(204))
    delete = respx.delete(f"{_LIST}/FOO").mock(return_value=httpx.Response(204))

    async with GitHub("token") as client:
        results = await variables.reconcile(
            client, _TARGET, _config({"name": "FOO", "value": "bar"})
        )

    (result,) = results
    assert not create.called
    assert not patch.called
    assert not delete.called
    assert result.changed is False
    assert result.applied is False


@respx.mock
async def test_dry_run_never_writes() -> None:
    respx.get(_LIST).mock(
        return_value=httpx.Response(200, json=_list_body({"name": "STALE", "value": "x"}))
    )
    create = respx.post(_LIST).mock(return_value=httpx.Response(201))
    delete = respx.delete(f"{_LIST}/STALE").mock(return_value=httpx.Response(204))

    async with GitHub("token") as client:
        results = await variables.reconcile(
            client, _TARGET, _config({"name": "FOO", "value": "bar"}), dry_run=True
        )

    (result,) = results
    assert not create.called  # missing FOO would be created if not dry-run
    assert not delete.called  # undeclared STALE would be pruned if not dry-run
    assert result.changed is True  # drift still detected for the check run
    assert result.applied is False


async def test_unconfigured_returns_empty() -> None:
    async with GitHub("token") as client:
        results = await variables.reconcile(client, _TARGET, SafeSettingsConfig.model_validate({}))
    assert results == []
