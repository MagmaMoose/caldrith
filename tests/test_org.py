"""Tests for the organization-scoped reconcile (:func:`run_org_reconcile`).

respx mocks every GitHub endpoint the tier touches (base ``https://api.github.com``).
Each test declares only the ``organization:`` sub-tiers it needs, so only those
endpoints have to be mocked.
"""

from __future__ import annotations

import base64

import httpx
import respx
from githubkit import GitHub

from caldrith.reconcile.org import run_org_reconcile

API = "https://api.github.com"


def _mock_settings(yaml_body: str) -> respx.Route:
    """Mock the admin ``settings.yml`` contents GET with a base64 YAML body."""
    encoded = base64.b64encode(yaml_body.encode()).decode()
    return respx.get(f"{API}/repos/acme/admin/contents/.github/settings.yml").mock(
        return_value=httpx.Response(
            200, json={"content": encoded, "encoding": "base64", "type": "file"}
        )
    )


def _mock_installation(account_type: str = "Organization") -> respx.Route:
    """Mock ``GET /app/installations/{id}`` with the account type."""
    return respx.get(f"{API}/app/installations/42").mock(
        return_value=httpx.Response(200, json={"account": {"type": account_type, "login": "acme"}})
    )


@respx.mock
async def test_settings_drift_patches_org() -> None:
    _mock_settings("organization:\n  billing_email: ops@acme.test\n")
    _mock_installation()
    respx.get(f"{API}/orgs/acme").mock(
        return_value=httpx.Response(200, json={"login": "acme", "billing_email": "old@acme.test"})
    )
    patch = respx.patch(f"{API}/orgs/acme").mock(
        return_value=httpx.Response(200, json={"login": "acme", "billing_email": "ops@acme.test"})
    )

    async with GitHub("token") as client:
        summary = await run_org_reconcile(client, installation_id=42, owner="acme")

    assert patch.called
    assert summary.any_changed is True
    settings = next(r for r in summary.results if r.tier == "organization")
    assert settings.changed is True
    assert settings.applied is True


@respx.mock
async def test_converged_settings_no_patch() -> None:
    _mock_settings("organization:\n  billing_email: ops@acme.test\n")
    _mock_installation()
    respx.get(f"{API}/orgs/acme").mock(
        return_value=httpx.Response(200, json={"login": "acme", "billing_email": "ops@acme.test"})
    )
    patch = respx.patch(f"{API}/orgs/acme").mock(return_value=httpx.Response(200, json={}))

    async with GitHub("token") as client:
        summary = await run_org_reconcile(client, installation_id=42, owner="acme")

    assert not patch.called
    assert summary.any_changed is False


@respx.mock
async def test_user_account_is_noop() -> None:
    _mock_settings("organization:\n  billing_email: ops@acme.test\n")
    _mock_installation(account_type="User")
    # No org endpoints are mocked: if the tier touches /orgs/... respx raises.
    org_get = respx.get(f"{API}/orgs/acme").mock(return_value=httpx.Response(200, json={}))
    org_patch = respx.patch(f"{API}/orgs/acme").mock(return_value=httpx.Response(200, json={}))

    async with GitHub("token") as client:
        summary = await run_org_reconcile(client, installation_id=42, owner="acme")

    assert summary.results == []
    assert summary.any_changed is False
    assert not org_get.called
    assert not org_patch.called


@respx.mock
async def test_org_actions_drift_puts_permissions() -> None:
    _mock_settings(
        "organization:\n  actions:\n    enabled_repositories: all\n    allowed_actions: all\n"
    )
    _mock_installation()
    # No org-settings drift: orgs.update must not be called, so converge the scalar diff.
    respx.get(f"{API}/orgs/acme").mock(return_value=httpx.Response(200, json={"login": "acme"}))
    respx.get(f"{API}/orgs/acme/actions/permissions").mock(
        return_value=httpx.Response(
            200, json={"enabled_repositories": "none", "allowed_actions": "local_only"}
        )
    )
    put = respx.put(f"{API}/orgs/acme/actions/permissions").mock(return_value=httpx.Response(204))

    async with GitHub("token") as client:
        summary = await run_org_reconcile(client, installation_id=42, owner="acme")

    assert put.called
    actions = next(r for r in summary.results if r.tier == "org_actions")
    assert actions.changed is True
    assert summary.any_changed is True


@respx.mock
async def test_org_ruleset_create() -> None:
    _mock_settings(
        "organization:\n"
        "  rulesets:\n"
        "    - name: default\n"
        "      target: branch\n"
        "      enforcement: active\n"
        "      rules:\n"
        "        - type: deletion\n"
    )
    _mock_installation()
    respx.get(f"{API}/orgs/acme").mock(return_value=httpx.Response(200, json={"login": "acme"}))
    # No existing rulesets -> POST to create.
    respx.get(f"{API}/orgs/acme/rulesets").mock(return_value=httpx.Response(200, json=[]))
    post = respx.post(f"{API}/orgs/acme/rulesets").mock(
        return_value=httpx.Response(201, json={"id": 7, "name": "default"})
    )

    async with GitHub("token") as client:
        summary = await run_org_reconcile(client, installation_id=42, owner="acme")

    assert post.called
    rulesets = next(r for r in summary.results if r.tier == "org_rulesets")
    assert rulesets.changed is True
    assert "create:default" in rulesets.notes
    assert summary.any_changed is True
