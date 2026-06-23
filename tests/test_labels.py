"""Tests for the labels tier (full-replace: create / update / rename / prune)."""

from __future__ import annotations

import json
from typing import Any

import httpx
import respx
from githubkit import GitHub

from caldrith.config.schema import SafeSettingsConfig
from caldrith.reconcile import labels
from caldrith.reconcile.planner import TargetRepo

_LABELS = "https://api.github.com/repos/acme/widget/labels"

_TARGET = TargetRepo("acme", "widget")


def _label(name: str, color: str, description: str | None = "") -> dict[str, Any]:
    """A live label as the API returns it (colour WITHOUT a leading '#')."""
    return {"name": name, "color": color, "description": description}


def _config(label_list: list[dict[str, Any]]) -> SafeSettingsConfig:
    return SafeSettingsConfig.model_validate({"labels": label_list})


@respx.mock
async def test_creates_missing_label() -> None:
    respx.get(_LABELS).mock(return_value=httpx.Response(200, json=[]))
    post = respx.post(_LABELS).mock(
        return_value=httpx.Response(201, json=_label("bug", "ff0000", "broken"))
    )

    async with GitHub("token") as client:
        results = await labels.reconcile(
            client, _TARGET, _config([{"name": "bug", "color": "#ff0000", "description": "broken"}])
        )

    assert post.called
    body = json.loads(post.calls.last.request.content)
    assert body["name"] == "bug"
    assert body["color"] == "ff0000"  # normalised: leading '#' stripped
    result = results[0]
    assert result.tier == "labels"
    assert result.scope == "acme/widget"
    assert result.changed is True
    assert result.applied is True


@respx.mock
async def test_noop_when_converged() -> None:
    # Live colour stored without '#'; desired carries '#' -> compared normalised -> equal.
    respx.get(_LABELS).mock(
        return_value=httpx.Response(200, json=[_label("bug", "ff0000", "broken")])
    )
    post = respx.post(_LABELS).mock(return_value=httpx.Response(201, json={}))
    patch = respx.patch(url__startswith=f"{_LABELS}/").mock(
        return_value=httpx.Response(200, json={})
    )
    delete = respx.delete(url__startswith=f"{_LABELS}/").mock(return_value=httpx.Response(204))

    async with GitHub("token") as client:
        results = await labels.reconcile(
            client, _TARGET, _config([{"name": "bug", "color": "#FF0000", "description": "broken"}])
        )

    assert not post.called
    assert not patch.called
    assert not delete.called
    result = results[0]
    assert result.changed is False
    assert result.applied is False


@respx.mock
async def test_updates_drifted_label() -> None:
    respx.get(_LABELS).mock(return_value=httpx.Response(200, json=[_label("bug", "ff0000", "old")]))
    patch = respx.patch(f"{_LABELS}/bug").mock(return_value=httpx.Response(200, json={}))

    async with GitHub("token") as client:
        results = await labels.reconcile(
            client, _TARGET, _config([{"name": "bug", "color": "#00ff00", "description": "new"}])
        )

    assert patch.called
    body = json.loads(patch.calls.last.request.content)
    assert body["color"] == "00ff00"
    assert body["description"] == "new"
    result = results[0]
    assert result.changed is True
    assert result.applied is True


@respx.mock
async def test_prunes_undeclared_label() -> None:
    respx.get(_LABELS).mock(
        return_value=httpx.Response(
            200,
            json=[_label("bug", "ff0000", "broken"), _label("stale", "cccccc", "old")],
        )
    )
    delete = respx.delete(f"{_LABELS}/stale").mock(return_value=httpx.Response(204))

    async with GitHub("token") as client:
        results = await labels.reconcile(
            client, _TARGET, _config([{"name": "bug", "color": "#ff0000", "description": "broken"}])
        )

    assert delete.called
    result = results[0]
    assert result.changed is True
    assert result.applied is True


@respx.mock
async def test_renames_via_oldname() -> None:
    # 'oldname' renames in place: PATCH the OLD name with a new_name body, no delete+create.
    respx.get(_LABELS).mock(
        return_value=httpx.Response(200, json=[_label("wontfix", "ff0000", "x")])
    )
    rename = respx.patch(f"{_LABELS}/wontfix").mock(return_value=httpx.Response(200, json={}))
    delete = respx.delete(url__startswith=f"{_LABELS}/").mock(return_value=httpx.Response(204))

    async with GitHub("token") as client:
        results = await labels.reconcile(
            client,
            _TARGET,
            _config(
                [{"name": "wont-fix", "color": "#ff0000", "description": "x", "oldname": "wontfix"}]
            ),
        )

    assert rename.called
    body = json.loads(rename.calls.last.request.content)
    assert body["new_name"] == "wont-fix"
    assert not delete.called  # rename preserves the label, does not prune the source
    result = results[0]
    assert result.changed is True
    assert result.applied is True


@respx.mock
async def test_dry_run_never_writes() -> None:
    respx.get(_LABELS).mock(return_value=httpx.Response(200, json=[]))
    post = respx.post(_LABELS).mock(return_value=httpx.Response(201, json={}))

    async with GitHub("token") as client:
        results = await labels.reconcile(
            client,
            _TARGET,
            _config([{"name": "bug", "color": "#ff0000", "description": "broken"}]),
            dry_run=True,
        )

    assert not post.called
    result = results[0]
    assert result.changed is True
    assert result.applied is False
