"""Tests for the CodeQL default-setup tier."""

from __future__ import annotations

import json

import httpx
import respx
from githubkit import GitHub

from caldrith.config.schema import CodeScanningDefaultSetup, SafeSettingsConfig
from caldrith.reconcile.code_scanning import reconcile
from caldrith.reconcile.planner import TargetRepo

_REPO = "https://api.github.com/repos/acme/widget"
_SETUP = f"{_REPO}/code-scanning/default-setup"
_TARGET = TargetRepo("acme", "widget")


def _cfg(**kwargs: object) -> SafeSettingsConfig:
    return SafeSettingsConfig(code_scanning=CodeScanningDefaultSetup(**kwargs))


def _live(**kwargs: object) -> httpx.Response:
    return httpx.Response(200, json=kwargs)


async def _run(config: SafeSettingsConfig, *, dry_run: bool = False):
    async with GitHub("token") as client:
        return await reconcile(client, _TARGET, config, dry_run=dry_run)


@respx.mock
async def test_enables_when_not_configured() -> None:
    respx.get(_SETUP).mock(return_value=_live(state="not-configured"))
    patch = respx.patch(_SETUP).mock(return_value=httpx.Response(202, json={}))

    [result] = await _run(_cfg(state="configured"))

    assert patch.called and result.changed and result.applied
    assert json.loads(patch.calls.last.request.content)["state"] == "configured"


@respx.mock
async def test_noop_when_already_configured() -> None:
    respx.get(_SETUP).mock(return_value=_live(state="configured"))
    patch = respx.patch(_SETUP).mock(return_value=httpx.Response(202, json={}))

    [result] = await _run(_cfg(state="configured"))

    assert not patch.called and result.changed is False


@respx.mock
async def test_query_suite_drift_updates() -> None:
    respx.get(_SETUP).mock(return_value=_live(state="configured", query_suite="default"))
    patch = respx.patch(_SETUP).mock(return_value=httpx.Response(202, json={}))

    await _run(_cfg(state="configured", query_suite="extended"))

    assert patch.called
    assert json.loads(patch.calls.last.request.content)["query_suite"] == "extended"


@respx.mock
async def test_languages_unset_does_not_drift() -> None:
    # The repo has auto-detected languages; the config doesn't pin them -> no drift.
    respx.get(_SETUP).mock(return_value=_live(state="configured", languages=["python", "go"]))
    patch = respx.patch(_SETUP).mock(return_value=httpx.Response(202, json={}))

    [result] = await _run(_cfg(state="configured"))

    assert not patch.called and result.changed is False


@respx.mock
async def test_disables_when_configured() -> None:
    respx.get(_SETUP).mock(return_value=_live(state="configured"))
    patch = respx.patch(_SETUP).mock(return_value=httpx.Response(202, json={}))

    await _run(_cfg(state="not-configured"))

    assert patch.called
    assert json.loads(patch.calls.last.request.content)["state"] == "not-configured"


@respx.mock
async def test_dry_run_does_not_write() -> None:
    respx.get(_SETUP).mock(return_value=_live(state="not-configured"))
    patch = respx.patch(_SETUP).mock(return_value=httpx.Response(202, json={}))

    [result] = await _run(_cfg(state="configured"), dry_run=True)

    assert not patch.called and result.changed and result.applied is False


@respx.mock
async def test_unavailable_repo_get_404_treated_as_not_configured() -> None:
    # Default setup unavailable / unlicensed for the repo -> GET 404 -> treated as not
    # configured, so enabling still attempts (the PATCH is what may then fail in prod,
    # isolated per tier by the runner).
    respx.get(_SETUP).mock(return_value=httpx.Response(404, json={"message": "Not Found"}))
    patch = respx.patch(_SETUP).mock(return_value=httpx.Response(202, json={}))

    [result] = await _run(_cfg(state="configured"))

    assert patch.called and result.applied
