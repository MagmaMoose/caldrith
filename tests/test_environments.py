"""Tests for the environments tier (create/update by name; never pruned)."""

from __future__ import annotations

import json
from typing import Any

import httpx
import respx
from githubkit import GitHub

from caldrith.config.schema import SafeSettingsConfig
from caldrith.reconcile import environments
from caldrith.reconcile.planner import TargetRepo

_TARGET = TargetRepo("acme", "widget")
_LIST = "https://api.github.com/repos/acme/widget/environments"
_PROD = "https://api.github.com/repos/acme/widget/environments/prod"


def _prod_env(**overrides: Any) -> dict[str, Any]:
    """A live ``prod`` environment with wait_timer=30 + a Team(5) required reviewer."""
    base: dict[str, Any] = {
        "name": "prod",
        "protection_rules": [
            {"type": "wait_timer", "wait_timer": 30},
            {
                "type": "required_reviewers",
                "prevent_self_review": True,
                "reviewers": [{"type": "Team", "reviewer": {"id": 5}}],
            },
        ],
        "deployment_branch_policy": None,
    }
    base.update(overrides)
    return base


def _list_body(*envs: dict[str, Any]) -> dict[str, Any]:
    return {"total_count": len(envs), "environments": list(envs)}


def _config(*envs: dict[str, Any]) -> SafeSettingsConfig:
    return SafeSettingsConfig.model_validate({"environments": list(envs)})


@respx.mock
async def test_creates_absent_environment() -> None:
    respx.get(_LIST).mock(return_value=httpx.Response(200, json=_list_body()))
    put = respx.put(_PROD).mock(return_value=httpx.Response(200, json={"name": "prod"}))

    async with GitHub("token") as client:
        results = await environments.reconcile(
            client,
            _TARGET,
            _config({"name": "prod", "wait_timer": 30, "reviewers": [{"type": "Team", "id": 5}]}),
        )

    (result,) = results
    assert put.called
    body = json.loads(put.calls.last.request.content)
    assert body["wait_timer"] == 30
    assert body["reviewers"] == [{"type": "Team", "id": 5}]
    assert result.tier == "environments"
    assert result.scope == "acme/widget"
    assert result.changed is True
    assert result.applied is True
    assert any("create environment: prod" in n for n in result.notes)


@respx.mock
async def test_updates_drifted_environment() -> None:
    # Live wait_timer is 30; desired is 60 -> drift on a declared field.
    respx.get(_LIST).mock(return_value=httpx.Response(200, json=_list_body(_prod_env())))
    put = respx.put(_PROD).mock(return_value=httpx.Response(200, json={"name": "prod"}))

    async with GitHub("token") as client:
        results = await environments.reconcile(
            client, _TARGET, _config({"name": "prod", "wait_timer": 60})
        )

    (result,) = results
    assert put.called
    body = json.loads(put.calls.last.request.content)
    assert body == {"wait_timer": 60}  # only the declared field participates
    assert result.changed is True
    assert result.applied is True
    assert any("update environment: prod" in n for n in result.notes)


@respx.mock
async def test_converged_is_noop() -> None:
    # Desired matches the nested protection_rules exactly.
    respx.get(_LIST).mock(return_value=httpx.Response(200, json=_list_body(_prod_env())))
    put = respx.put(_PROD).mock(return_value=httpx.Response(200, json={"name": "prod"}))

    async with GitHub("token") as client:
        results = await environments.reconcile(
            client,
            _TARGET,
            _config(
                {
                    "name": "prod",
                    "wait_timer": 30,
                    "prevent_self_review": True,
                    "reviewers": [{"type": "Team", "id": 5}],
                }
            ),
        )

    (result,) = results
    assert not put.called
    assert result.changed is False
    assert result.applied is False


@respx.mock
async def test_never_prunes_undeclared_environment() -> None:
    # 'staging' is live but undeclared; declared 'prod' matches -> no write at all.
    respx.get(_LIST).mock(
        return_value=httpx.Response(
            200, json=_list_body(_prod_env(), {"name": "staging", "protection_rules": []})
        )
    )
    put_prod = respx.put(_PROD).mock(return_value=httpx.Response(200, json={"name": "prod"}))
    put_staging = respx.put("https://api.github.com/repos/acme/widget/environments/staging").mock(
        return_value=httpx.Response(200, json={"name": "staging"})
    )
    delete_staging = respx.delete(
        "https://api.github.com/repos/acme/widget/environments/staging"
    ).mock(return_value=httpx.Response(204))

    async with GitHub("token") as client:
        results = await environments.reconcile(
            client,
            _TARGET,
            _config(
                {
                    "name": "prod",
                    "wait_timer": 30,
                    "prevent_self_review": True,
                    "reviewers": [{"type": "Team", "id": 5}],
                }
            ),
        )

    (result,) = results
    assert not put_prod.called  # prod converged
    assert not put_staging.called  # staging never touched
    assert not delete_staging.called  # environments are never pruned
    assert result.changed is False


@respx.mock
async def test_dry_run_never_writes() -> None:
    respx.get(_LIST).mock(return_value=httpx.Response(200, json=_list_body()))
    put = respx.put(_PROD).mock(return_value=httpx.Response(200, json={"name": "prod"}))

    async with GitHub("token") as client:
        results = await environments.reconcile(
            client, _TARGET, _config({"name": "prod", "wait_timer": 30}), dry_run=True
        )

    (result,) = results
    assert not put.called
    assert result.changed is True  # create detected for the check run
    assert result.applied is False


async def test_unconfigured_returns_empty() -> None:
    async with GitHub("token") as client:
        results = await environments.reconcile(
            client, _TARGET, SafeSettingsConfig.model_validate({})
        )
    assert results == []
