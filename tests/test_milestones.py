"""Tests for the milestones tier (match by title; create/update, never pruned)."""

from __future__ import annotations

import json
from typing import Any

import httpx
import respx
from githubkit import GitHub

from caldrith.config.schema import SafeSettingsConfig
from caldrith.reconcile import milestones
from caldrith.reconcile.planner import TargetRepo

_MILESTONES = "https://api.github.com/repos/acme/widget/milestones"

_TARGET = TargetRepo("acme", "widget")


def _milestone(
    number: int,
    title: str,
    state: str = "open",
    description: str | None = "",
    due_on: str | None = None,
) -> dict[str, Any]:
    return {
        "number": number,
        "title": title,
        "state": state,
        "description": description,
        "due_on": due_on,
    }


def _config(milestone_list: list[dict[str, Any]]) -> SafeSettingsConfig:
    return SafeSettingsConfig.model_validate({"milestones": milestone_list})


@respx.mock
async def test_creates_missing_milestone() -> None:
    respx.get(_MILESTONES).mock(return_value=httpx.Response(200, json=[]))
    post = respx.post(_MILESTONES).mock(
        return_value=httpx.Response(201, json=_milestone(1, "v1.0"))
    )

    async with GitHub("token") as client:
        results = await milestones.reconcile(
            client, _TARGET, _config([{"title": "v1.0", "description": "first"}])
        )

    assert post.called
    body = json.loads(post.calls.last.request.content)
    assert body["title"] == "v1.0"
    result = results[0]
    assert result.tier == "milestones"
    assert result.scope == "acme/widget"
    assert result.changed is True
    assert result.applied is True


@respx.mock
async def test_updates_drifted_milestone() -> None:
    respx.get(_MILESTONES).mock(
        return_value=httpx.Response(200, json=[_milestone(7, "v1.0", state="open")])
    )
    patch = respx.patch(f"{_MILESTONES}/7").mock(
        return_value=httpx.Response(200, json=_milestone(7, "v1.0", state="closed"))
    )

    async with GitHub("token") as client:
        results = await milestones.reconcile(
            client, _TARGET, _config([{"title": "v1.0", "state": "closed"}])
        )

    assert patch.called
    body = json.loads(patch.calls.last.request.content)
    assert body["state"] == "closed"
    result = results[0]
    assert result.changed is True
    assert result.applied is True


@respx.mock
async def test_noop_when_converged() -> None:
    respx.get(_MILESTONES).mock(
        return_value=httpx.Response(
            200, json=[_milestone(7, "v1.0", state="open", description="first")]
        )
    )
    post = respx.post(_MILESTONES).mock(return_value=httpx.Response(201, json={}))
    patch = respx.patch(url__startswith=f"{_MILESTONES}/").mock(
        return_value=httpx.Response(200, json={})
    )

    async with GitHub("token") as client:
        results = await milestones.reconcile(
            client, _TARGET, _config([{"title": "v1.0", "state": "open", "description": "first"}])
        )

    assert not post.called
    assert not patch.called
    result = results[0]
    assert result.changed is False
    assert result.applied is False


@respx.mock
async def test_undeclared_milestone_not_pruned() -> None:
    # 'old-release' is live but undeclared -> milestones are NEVER deleted.
    respx.get(_MILESTONES).mock(
        return_value=httpx.Response(
            200,
            json=[
                _milestone(7, "v1.0", state="open", description="first"),
                _milestone(8, "old-release", state="closed"),
            ],
        )
    )
    post = respx.post(_MILESTONES).mock(return_value=httpx.Response(201, json={}))
    patch = respx.patch(url__startswith=f"{_MILESTONES}/").mock(
        return_value=httpx.Response(200, json={})
    )
    delete = respx.delete(url__startswith=f"{_MILESTONES}/").mock(return_value=httpx.Response(204))

    async with GitHub("token") as client:
        results = await milestones.reconcile(
            client, _TARGET, _config([{"title": "v1.0", "state": "open", "description": "first"}])
        )

    assert not delete.called  # never pruned
    assert not post.called
    assert not patch.called
    result = results[0]
    assert result.changed is False
    assert result.applied is False


@respx.mock
async def test_dry_run_never_writes() -> None:
    respx.get(_MILESTONES).mock(return_value=httpx.Response(200, json=[]))
    post = respx.post(_MILESTONES).mock(return_value=httpx.Response(201, json={}))

    async with GitHub("token") as client:
        results = await milestones.reconcile(
            client, _TARGET, _config([{"title": "v1.0", "description": "first"}]), dry_run=True
        )

    assert not post.called
    result = results[0]
    assert result.changed is True
    assert result.applied is False
