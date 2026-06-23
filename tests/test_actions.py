"""Tests for the actions tier (two endpoints: permissions + default workflow perms)."""

from __future__ import annotations

import json

import httpx
import respx
from githubkit import GitHub

from caldrith.config.schema import SafeSettingsConfig
from caldrith.reconcile.actions import reconcile
from caldrith.reconcile.planner import TargetRepo

_PERMS = "https://api.github.com/repos/acme/widget/actions/permissions"
_WF = "https://api.github.com/repos/acme/widget/actions/permissions/workflow"


def _cfg(**actions: object) -> SafeSettingsConfig:
    return SafeSettingsConfig.model_validate({"actions": actions})


@respx.mock
async def test_both_subsettings_drift_two_puts() -> None:
    respx.get(_PERMS).mock(
        return_value=httpx.Response(200, json={"enabled": True, "allowed_actions": "all"})
    )
    respx.get(_WF).mock(
        return_value=httpx.Response(
            200,
            json={
                "default_workflow_permissions": "write",
                "can_approve_pull_request_reviews": True,
            },
        )
    )
    perms_put = respx.put(_PERMS).mock(return_value=httpx.Response(204))
    wf_put = respx.put(_WF).mock(return_value=httpx.Response(204))

    async with GitHub("token") as client:
        results = await reconcile(
            client,
            TargetRepo("acme", "widget"),
            _cfg(
                enabled=True,
                allowed_actions="selected",
                default_workflow_permissions="read",
                can_approve_pull_request_reviews=False,
            ),
        )

    assert perms_put.called and wf_put.called
    perms_body = json.loads(perms_put.calls.last.request.content)
    assert perms_body == {"enabled": True, "allowed_actions": "selected"}
    wf_body = json.loads(wf_put.calls.last.request.content)
    assert wf_body == {
        "default_workflow_permissions": "read",
        "can_approve_pull_request_reviews": False,
    }
    (result,) = results
    assert result.tier == "actions"
    assert result.scope == "acme/widget"
    assert result.changed is True
    assert result.applied is True


@respx.mock
async def test_converged_no_writes() -> None:
    respx.get(_PERMS).mock(
        return_value=httpx.Response(200, json={"enabled": True, "allowed_actions": "selected"})
    )
    respx.get(_WF).mock(
        return_value=httpx.Response(
            200,
            json={
                "default_workflow_permissions": "read",
                "can_approve_pull_request_reviews": False,
            },
        )
    )
    perms_put = respx.put(_PERMS).mock(return_value=httpx.Response(204))
    wf_put = respx.put(_WF).mock(return_value=httpx.Response(204))

    async with GitHub("token") as client:
        results = await reconcile(
            client,
            TargetRepo("acme", "widget"),
            _cfg(
                enabled=True,
                allowed_actions="selected",
                default_workflow_permissions="read",
                can_approve_pull_request_reviews=False,
            ),
        )

    assert not perms_put.called
    assert not wf_put.called
    (result,) = results
    assert result.changed is False
    assert result.applied is False


@respx.mock
async def test_dry_run_never_writes() -> None:
    respx.get(_PERMS).mock(
        return_value=httpx.Response(200, json={"enabled": True, "allowed_actions": "all"})
    )
    perms_put = respx.put(_PERMS).mock(return_value=httpx.Response(204))

    async with GitHub("token") as client:
        results = await reconcile(
            client,
            TargetRepo("acme", "widget"),
            _cfg(enabled=True, allowed_actions="selected"),
            dry_run=True,
        )

    assert not perms_put.called
    (result,) = results
    assert result.changed is True
    assert result.applied is False


@respx.mock
async def test_partial_config_touches_only_one_endpoint() -> None:
    # Config declares only the default-workflow sub-settings -> only the workflow GET/PUT
    # is exercised; the permissions endpoint is never hit.
    perms_get = respx.get(_PERMS).mock(
        return_value=httpx.Response(200, json={"enabled": True, "allowed_actions": "all"})
    )
    perms_put = respx.put(_PERMS).mock(return_value=httpx.Response(204))
    respx.get(_WF).mock(
        return_value=httpx.Response(
            200,
            json={
                "default_workflow_permissions": "write",
                "can_approve_pull_request_reviews": True,
            },
        )
    )
    wf_put = respx.put(_WF).mock(return_value=httpx.Response(204))

    async with GitHub("token") as client:
        results = await reconcile(
            client,
            TargetRepo("acme", "widget"),
            _cfg(default_workflow_permissions="read"),
        )

    assert not perms_get.called  # permissions endpoint untouched
    assert not perms_put.called
    assert wf_put.called
    wf_body = json.loads(wf_put.calls.last.request.content)
    # can_approve_* not declared -> falls back to the live value (True), never clobbered.
    assert wf_body == {
        "default_workflow_permissions": "read",
        "can_approve_pull_request_reviews": True,
    }
    (result,) = results
    assert result.changed is True
    assert result.applied is True
