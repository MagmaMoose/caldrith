"""Tests for the topics tier (full-replace, order-insensitive)."""

from __future__ import annotations

import json

import httpx
import respx
from githubkit import GitHub

from caldrith.config.schema import SafeSettingsConfig
from caldrith.reconcile import topics
from caldrith.reconcile.planner import TargetRepo

_TOPICS = "https://api.github.com/repos/acme/widget/topics"

_TARGET = TargetRepo("acme", "widget")


def _config(topic_list: list[str]) -> SafeSettingsConfig:
    return SafeSettingsConfig.model_validate({"repository": {"topics": topic_list}})


@respx.mock
async def test_replaces_topics_when_changed() -> None:
    respx.get(_TOPICS).mock(return_value=httpx.Response(200, json={"names": ["a", "b"]}))
    put = respx.put(_TOPICS).mock(return_value=httpx.Response(200, json={"names": ["a", "b", "c"]}))

    async with GitHub("token") as client:
        results = await topics.reconcile(client, _TARGET, _config(["a", "b", "c"]))

    assert put.called
    body = json.loads(put.calls.last.request.content)
    assert body == {"names": ["a", "b", "c"]}
    result = results[0]
    assert result.tier == "topics"
    assert result.scope == "acme/widget"
    assert result.changed is True
    assert result.applied is True


@respx.mock
async def test_noop_when_converged() -> None:
    # Live set matches desired despite differing order -> no write.
    respx.get(_TOPICS).mock(return_value=httpx.Response(200, json={"names": ["b", "a"]}))
    put = respx.put(_TOPICS).mock(return_value=httpx.Response(200, json={"names": ["a", "b"]}))

    async with GitHub("token") as client:
        results = await topics.reconcile(client, _TARGET, _config(["a", "b"]))

    assert not put.called
    result = results[0]
    assert result.changed is False
    assert result.applied is False


@respx.mock
async def test_dry_run_never_writes() -> None:
    respx.get(_TOPICS).mock(return_value=httpx.Response(200, json={"names": ["a"]}))
    put = respx.put(_TOPICS).mock(return_value=httpx.Response(200, json={"names": ["a", "b"]}))

    async with GitHub("token") as client:
        results = await topics.reconcile(client, _TARGET, _config(["a", "b"]), dry_run=True)

    assert not put.called
    result = results[0]
    assert result.changed is True
    assert result.applied is False
