"""Tests for the pages tier (enable when disabled; update on drift; never disable)."""

from __future__ import annotations

import json
from typing import Any

import httpx
import respx
from githubkit import GitHub

from caldrith.config.schema import SafeSettingsConfig
from caldrith.reconcile import pages
from caldrith.reconcile.planner import TargetRepo

_TARGET = TargetRepo("acme", "widget")
_PAGES = "https://api.github.com/repos/acme/widget/pages"


def _live(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "build_type": "workflow",
        "source": {"branch": "main", "path": "/"},
        "cname": "x.io",
        "https_enforced": True,
    }
    base.update(overrides)
    return base


def _config(**kwargs: Any) -> SafeSettingsConfig:
    return SafeSettingsConfig.model_validate({"pages": kwargs})


@respx.mock
async def test_enables_pages_when_404() -> None:
    respx.get(_PAGES).mock(return_value=httpx.Response(404, json={"message": "Not Found"}))
    create = respx.post(_PAGES).mock(
        return_value=httpx.Response(201, json={"build_type": "workflow"})
    )
    # On enable, declared cname/https_enforced are applied via a follow-up PUT.
    put = respx.put(_PAGES).mock(return_value=httpx.Response(200, json=_live()))

    async with GitHub("token") as client:
        results = await pages.reconcile(
            client, _TARGET, _config(build_type="workflow", https_enforced=True)
        )

    (result,) = results
    assert create.called
    assert put.called  # https_enforced extra set after enabling
    extras = json.loads(put.calls.last.request.content)
    assert extras == {"https_enforced": True}
    assert result.tier == "pages"
    assert result.scope == "acme/widget"
    assert result.changed is True
    assert result.applied is True
    assert any("enable pages" in n for n in result.notes)


@respx.mock
async def test_updates_on_drift() -> None:
    # Live https_enforced is True; desired is False -> drift -> PUT.
    respx.get(_PAGES).mock(return_value=httpx.Response(200, json=_live(https_enforced=True)))
    put = respx.put(_PAGES).mock(return_value=httpx.Response(200, json=_live(https_enforced=False)))

    async with GitHub("token") as client:
        results = await pages.reconcile(
            client, _TARGET, _config(build_type="workflow", https_enforced=False)
        )

    (result,) = results
    assert put.called
    body = json.loads(put.calls.last.request.content)
    assert body["https_enforced"] is False
    assert result.changed is True
    assert result.applied is True
    assert any("update pages" in n for n in result.notes)


@respx.mock
async def test_converged_is_noop() -> None:
    respx.get(_PAGES).mock(return_value=httpx.Response(200, json=_live()))
    create = respx.post(_PAGES).mock(return_value=httpx.Response(201))
    put = respx.put(_PAGES).mock(return_value=httpx.Response(200, json=_live()))

    async with GitHub("token") as client:
        results = await pages.reconcile(
            client, _TARGET, _config(build_type="workflow", https_enforced=True)
        )

    (result,) = results
    assert not create.called
    assert not put.called
    assert result.changed is False
    assert result.applied is False


@respx.mock
async def test_dry_run_enable_never_writes() -> None:
    respx.get(_PAGES).mock(return_value=httpx.Response(404, json={"message": "Not Found"}))
    create = respx.post(_PAGES).mock(return_value=httpx.Response(201))
    put = respx.put(_PAGES).mock(return_value=httpx.Response(200, json=_live()))

    async with GitHub("token") as client:
        results = await pages.reconcile(
            client,
            _TARGET,
            _config(build_type="workflow", https_enforced=True),
            dry_run=True,
        )

    (result,) = results
    assert not create.called
    assert not put.called
    assert result.changed is True  # enable detected for the check run
    assert result.applied is False


@respx.mock
async def test_dry_run_update_never_writes() -> None:
    respx.get(_PAGES).mock(return_value=httpx.Response(200, json=_live(https_enforced=True)))
    put = respx.put(_PAGES).mock(return_value=httpx.Response(200, json=_live()))

    async with GitHub("token") as client:
        results = await pages.reconcile(
            client, _TARGET, _config(https_enforced=False), dry_run=True
        )

    (result,) = results
    assert not put.called
    assert result.changed is True
    assert result.applied is False


async def test_unconfigured_returns_empty() -> None:
    async with GitHub("token") as client:
        results = await pages.reconcile(client, _TARGET, SafeSettingsConfig.model_validate({}))
    assert results == []
