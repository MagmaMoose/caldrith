"""Tests for the org code-security configuration tier."""

from __future__ import annotations

import json

import httpx
import respx
from githubkit import GitHub

from caldrith.config.schema import CodeSecurityConfiguration
from caldrith.reconcile.org import _reconcile_code_security_config

_CONFIGS = "https://api.github.com/orgs/acme/code-security/configurations"


def _cfg(**kwargs: object) -> CodeSecurityConfiguration:
    return CodeSecurityConfiguration(name="Baseline", **kwargs)


async def _run(cfg: CodeSecurityConfiguration, *, dry_run: bool = False):
    async with GitHub("token") as client:
        return await _reconcile_code_security_config(client, "acme", cfg, dry_run=dry_run)


@respx.mock
async def test_creates_when_absent_then_attaches_and_defaults() -> None:
    respx.get(_CONFIGS).mock(return_value=httpx.Response(200, json=[]))
    create = respx.post(_CONFIGS).mock(
        return_value=httpx.Response(201, json={"id": 7, "name": "Baseline"})
    )
    attach = respx.post(f"{_CONFIGS}/7/attach").mock(return_value=httpx.Response(202, json={}))
    default = respx.put(f"{_CONFIGS}/7/defaults").mock(return_value=httpx.Response(200, json={}))

    result = await _run(
        _cfg(
            dependency_graph_autosubmit_action="enabled",
            dependabot_delegated_alert_dismissal="enabled",
            apply_to="all_repos",
            default_for_new_repos="all",
        )
    )

    assert create.called and attach.called and default.called
    body = json.loads(create.calls.last.request.content)
    assert body["dependency_graph_autosubmit_action"] == "enabled"
    # caldrith-only keys must not leak into the API body
    assert "apply_to" not in body and "default_for_new_repos" not in body
    assert json.loads(attach.calls.last.request.content)["scope"] == "all"
    assert json.loads(default.calls.last.request.content)["default_for_new_repos"] == "all"
    assert result.changed and result.applied


@respx.mock
async def test_updates_on_drift() -> None:
    respx.get(_CONFIGS).mock(
        return_value=httpx.Response(
            200, json=[{"id": 5, "name": "Baseline", "dependabot_alerts": "disabled"}]
        )
    )
    patch = respx.patch(f"{_CONFIGS}/5").mock(return_value=httpx.Response(200, json={"id": 5}))
    attach = respx.post(f"{_CONFIGS}/5/attach").mock(return_value=httpx.Response(202, json={}))

    result = await _run(_cfg(dependabot_alerts="enabled", apply_to="all_repos"))

    assert patch.called and attach.called  # changed -> update + re-attach
    assert json.loads(patch.calls.last.request.content)["dependabot_alerts"] == "enabled"
    assert result.changed


@respx.mock
async def test_noop_when_matching() -> None:
    respx.get(_CONFIGS).mock(
        return_value=httpx.Response(
            200, json=[{"id": 5, "name": "Baseline", "dependabot_alerts": "enabled"}]
        )
    )
    patch = respx.patch(f"{_CONFIGS}/5").mock(return_value=httpx.Response(200, json={}))
    attach = respx.post(f"{_CONFIGS}/5/attach").mock(return_value=httpx.Response(202, json={}))

    result = await _run(_cfg(dependabot_alerts="enabled", apply_to="all_repos"))

    assert not patch.called and not attach.called  # no drift -> no write, no re-attach
    assert result.changed is False


@respx.mock
async def test_dry_run_writes_nothing() -> None:
    respx.get(_CONFIGS).mock(return_value=httpx.Response(200, json=[]))
    create = respx.post(_CONFIGS).mock(return_value=httpx.Response(201, json={"id": 7}))
    attach = respx.post(f"{_CONFIGS}/7/attach").mock(return_value=httpx.Response(202, json={}))

    result = await _run(_cfg(dependabot_alerts="enabled", apply_to="all_repos"), dry_run=True)

    assert not create.called and not attach.called
    assert result.changed and result.applied is False


@respx.mock
async def test_no_attach_when_apply_to_unset() -> None:
    respx.get(_CONFIGS).mock(return_value=httpx.Response(200, json=[]))
    create = respx.post(_CONFIGS).mock(return_value=httpx.Response(201, json={"id": 7}))
    attach = respx.post(f"{_CONFIGS}/7/attach").mock(return_value=httpx.Response(202, json={}))

    result = await _run(_cfg(secret_scanning="enabled"))

    assert create.called and not attach.called  # no apply_to -> created but not attached
    assert result.changed
