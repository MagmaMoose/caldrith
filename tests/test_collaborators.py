"""Tests for the collaborators tier (full-replace direct collaborators)."""

from __future__ import annotations

import httpx
import respx
from githubkit import GitHub

from caldrith.config.schema import SafeSettingsConfig
from caldrith.reconcile.collaborators import reconcile
from caldrith.reconcile.planner import TargetRepo

_LIST = "https://api.github.com/repos/acme/widget/collaborators"
_TARGET = TargetRepo("acme", "widget")


def _collab(login: str, role_name: str) -> dict:
    """A list-endpoint collaborator row (role_name vocabulary)."""
    return {
        "login": login,
        "role_name": role_name,
        "permissions": {
            "admin": role_name == "admin",
            "maintain": role_name == "maintain",
            "push": role_name == "write",
            "triage": role_name == "triage",
            "pull": True,
        },
    }


def _config(*collaborators: dict) -> SafeSettingsConfig:
    return SafeSettingsConfig.model_validate({"collaborators": list(collaborators)})


@respx.mock
async def test_add_collaborator_when_missing() -> None:
    respx.get(_LIST).mock(return_value=httpx.Response(200, json=[]))
    put = respx.put("https://api.github.com/repos/acme/widget/collaborators/octocat").mock(
        return_value=httpx.Response(201, json={})
    )

    config = _config({"username": "octocat", "permission": "push"})
    async with GitHub("token") as client:
        results = await reconcile(client, _TARGET, config)

    assert put.called
    body = put.calls.last.request.read()
    assert b'"permission"' in body and b"push" in body
    result = results[0]
    assert result.tier == "collaborators"
    assert result.scope == "acme/widget"
    assert result.changed is True
    assert result.applied is True


@respx.mock
async def test_update_collaborator_when_permission_drifts() -> None:
    # Live role_name is "read" (pull) but we want push -> mapped role "write" differs.
    respx.get(_LIST).mock(return_value=httpx.Response(200, json=[_collab("octocat", "read")]))
    put = respx.put("https://api.github.com/repos/acme/widget/collaborators/octocat").mock(
        return_value=httpx.Response(204, json={})
    )

    config = _config({"username": "octocat", "permission": "push"})
    async with GitHub("token") as client:
        results = await reconcile(client, _TARGET, config)

    assert put.called
    assert results[0].changed is True
    assert results[0].applied is True


@respx.mock
async def test_noop_when_converged() -> None:
    # Desired push maps to role_name "write"; live already "write" -> no write.
    respx.get(_LIST).mock(return_value=httpx.Response(200, json=[_collab("octocat", "write")]))
    put = respx.put("https://api.github.com/repos/acme/widget/collaborators/octocat").mock(
        return_value=httpx.Response(204, json={})
    )

    config = _config({"username": "octocat", "permission": "push"})
    async with GitHub("token") as client:
        results = await reconcile(client, _TARGET, config)

    assert not put.called
    assert results[0].changed is False
    assert results[0].applied is False


@respx.mock
async def test_dry_run_never_writes() -> None:
    respx.get(_LIST).mock(return_value=httpx.Response(200, json=[]))
    put = respx.put("https://api.github.com/repos/acme/widget/collaborators/octocat").mock(
        return_value=httpx.Response(201, json={})
    )

    config = _config({"username": "octocat", "permission": "push"})
    async with GitHub("token") as client:
        results = await reconcile(client, _TARGET, config, dry_run=True)

    assert not put.called
    assert results[0].changed is True
    assert results[0].applied is False


@respx.mock
async def test_prune_undeclared_direct_collaborator() -> None:
    # "stale" is a live DIRECT collaborator that is not declared -> DELETE.
    respx.get(_LIST).mock(
        return_value=httpx.Response(
            200, json=[_collab("octocat", "write"), _collab("stale", "admin")]
        )
    )
    put = respx.put("https://api.github.com/repos/acme/widget/collaborators/octocat").mock(
        return_value=httpx.Response(204, json={})
    )
    delete = respx.delete("https://api.github.com/repos/acme/widget/collaborators/stale").mock(
        return_value=httpx.Response(204)
    )

    config = _config({"username": "octocat", "permission": "push"})
    async with GitHub("token") as client:
        results = await reconcile(client, _TARGET, config)

    assert not put.called  # octocat already converged
    assert delete.called  # stale removed
    assert results[0].changed is True
    assert results[0].applied is True


@respx.mock
async def test_prune_skipped_in_dry_run() -> None:
    respx.get(_LIST).mock(return_value=httpx.Response(200, json=[_collab("stale", "admin")]))
    delete = respx.delete("https://api.github.com/repos/acme/widget/collaborators/stale").mock(
        return_value=httpx.Response(204)
    )

    config = _config()
    async with GitHub("token") as client:
        results = await reconcile(client, _TARGET, config, dry_run=True)

    assert not delete.called
    assert results[0].changed is True
    assert results[0].applied is False
