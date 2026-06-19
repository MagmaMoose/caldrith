"""Tests for the repository security applier (Dependabot + private vuln reporting)."""

from __future__ import annotations

import httpx
import respx
from githubkit import GitHub

from caldrith.config.schema import RepositorySecurity
from caldrith.reconcile.planner import TargetRepo
from caldrith.reconcile.security import RepositorySecurityApplier

_VULN = "https://api.github.com/repos/acme/widget/vulnerability-alerts"
_FIXES = "https://api.github.com/repos/acme/widget/automated-security-fixes"
_PVR = "https://api.github.com/repos/acme/widget/private-vulnerability-reporting"


@respx.mock
async def test_enables_when_disabled() -> None:
    respx.get(_VULN).mock(return_value=httpx.Response(404))  # currently disabled
    put = respx.put(_VULN).mock(return_value=httpx.Response(204))

    async with GitHub("token") as client:
        result = await RepositorySecurityApplier(client).apply(
            TargetRepo("acme", "widget"),
            RepositorySecurity(enable_vulnerability_alerts=True),
        )

    assert put.called
    assert result.changed_fields == ["vulnerability_alerts"]
    assert result.applied is True


@respx.mock
async def test_noop_when_already_enabled() -> None:
    respx.get(_VULN).mock(return_value=httpx.Response(204))  # already enabled
    put = respx.put(_VULN).mock(return_value=httpx.Response(204))

    async with GitHub("token") as client:
        result = await RepositorySecurityApplier(client).apply(
            TargetRepo("acme", "widget"),
            RepositorySecurity(enable_vulnerability_alerts=True),
        )

    assert not put.called
    assert result.changed is False


@respx.mock
async def test_disables_when_enabled() -> None:
    respx.get(_VULN).mock(return_value=httpx.Response(204))  # enabled
    delete = respx.delete(_VULN).mock(return_value=httpx.Response(204))

    async with GitHub("token") as client:
        result = await RepositorySecurityApplier(client).apply(
            TargetRepo("acme", "widget"),
            RepositorySecurity(enable_vulnerability_alerts=False),
        )

    assert delete.called
    assert result.changed_fields == ["vulnerability_alerts"]


@respx.mock
async def test_automated_fixes_and_private_reporting() -> None:
    respx.get(_FIXES).mock(
        return_value=httpx.Response(200, json={"enabled": False, "paused": False})
    )
    put_fixes = respx.put(_FIXES).mock(return_value=httpx.Response(204))
    respx.get(_PVR).mock(return_value=httpx.Response(200, json={"enabled": True}))
    put_pvr = respx.put(_PVR).mock(return_value=httpx.Response(204))

    async with GitHub("token") as client:
        result = await RepositorySecurityApplier(client).apply(
            TargetRepo("acme", "widget"),
            RepositorySecurity(
                enable_automated_security_fixes=True,
                enable_private_vulnerability_reporting=True,  # already on -> no-op
            ),
        )

    assert put_fixes.called
    assert not put_pvr.called
    assert result.changed_fields == ["automated_security_fixes"]


@respx.mock
async def test_dry_run_never_writes() -> None:
    respx.get(_VULN).mock(return_value=httpx.Response(404))
    put = respx.put(_VULN).mock(return_value=httpx.Response(204))

    async with GitHub("token") as client:
        result = await RepositorySecurityApplier(client, dry_run=True).apply(
            TargetRepo("acme", "widget"),
            RepositorySecurity(enable_vulnerability_alerts=True),
        )

    assert not put.called
    assert result.changed is True and result.applied is False
