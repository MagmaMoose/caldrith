"""Tests for the autolinks tier (full-replace by triple; delete+create, no update)."""

from __future__ import annotations

import httpx
import respx
from githubkit import GitHub

from caldrith.config.schema import SafeSettingsConfig
from caldrith.reconcile.autolinks import reconcile
from caldrith.reconcile.planner import TargetRepo

_LIST = "https://api.github.com/repos/acme/widget/autolinks"
_TARGET = TargetRepo("acme", "widget")


def _live(
    autolink_id: int, key_prefix: str, url_template: str, is_alphanumeric: bool = True
) -> dict:
    return {
        "id": autolink_id,
        "key_prefix": key_prefix,
        "url_template": url_template,
        "is_alphanumeric": is_alphanumeric,
    }


def _config(*autolinks: dict) -> SafeSettingsConfig:
    return SafeSettingsConfig.model_validate({"autolinks": list(autolinks)})


@respx.mock
async def test_create_when_missing() -> None:
    respx.get(_LIST).mock(return_value=httpx.Response(200, json=[]))
    create = respx.post(_LIST).mock(
        return_value=httpx.Response(
            201, json=_live(1, "JIRA-", "https://jira.example.com/browse/JIRA-<num>")
        )
    )

    config = _config(
        {"key_prefix": "JIRA-", "url_template": "https://jira.example.com/browse/JIRA-<num>"}
    )
    async with GitHub("token") as client:
        results = await reconcile(client, _TARGET, config)

    assert create.called
    body = create.calls.last.request.read()
    assert b"JIRA-" in body
    result = results[0]
    assert result.tier == "autolinks"
    assert result.scope == "acme/widget"
    assert result.changed is True
    assert result.applied is True


@respx.mock
async def test_noop_when_converged() -> None:
    # Live triple matches desired exactly (is_alphanumeric defaults to True).
    respx.get(_LIST).mock(
        return_value=httpx.Response(
            200,
            json=[_live(1, "JIRA-", "https://jira.example.com/browse/JIRA-<num>")],
        )
    )
    create = respx.post(_LIST).mock(return_value=httpx.Response(201, json={}))
    delete = respx.delete("https://api.github.com/repos/acme/widget/autolinks/1").mock(
        return_value=httpx.Response(204)
    )

    config = _config(
        {"key_prefix": "JIRA-", "url_template": "https://jira.example.com/browse/JIRA-<num>"}
    )
    async with GitHub("token") as client:
        results = await reconcile(client, _TARGET, config)

    assert not create.called
    assert not delete.called
    assert results[0].changed is False
    assert results[0].applied is False


@respx.mock
async def test_dry_run_never_writes() -> None:
    respx.get(_LIST).mock(return_value=httpx.Response(200, json=[]))
    create = respx.post(_LIST).mock(return_value=httpx.Response(201, json={}))

    config = _config(
        {"key_prefix": "JIRA-", "url_template": "https://jira.example.com/browse/JIRA-<num>"}
    )
    async with GitHub("token") as client:
        results = await reconcile(client, _TARGET, config, dry_run=True)

    assert not create.called
    assert results[0].changed is True
    assert results[0].applied is False


@respx.mock
async def test_delete_undeclared_and_create_missing() -> None:
    # Live has a "stale" autolink not declared; desired declares a "new" one absent live.
    # A changed entry == delete + create, exercised here as one prune + one add.
    respx.get(_LIST).mock(
        return_value=httpx.Response(200, json=[_live(7, "OLD-", "https://old.example.com/<num>")])
    )
    create = respx.post(_LIST).mock(
        return_value=httpx.Response(201, json=_live(8, "NEW-", "https://new.example.com/<num>"))
    )
    delete = respx.delete("https://api.github.com/repos/acme/widget/autolinks/7").mock(
        return_value=httpx.Response(204)
    )

    config = _config({"key_prefix": "NEW-", "url_template": "https://new.example.com/<num>"})
    async with GitHub("token") as client:
        results = await reconcile(client, _TARGET, config)

    assert delete.called  # undeclared OLD- pruned
    assert create.called  # missing NEW- created
    assert results[0].changed is True
    assert results[0].applied is True


@respx.mock
async def test_changed_entry_is_delete_plus_create() -> None:
    # Same key_prefix but a different url_template -> different triple -> delete old, create new.
    respx.get(_LIST).mock(
        return_value=httpx.Response(
            200, json=[_live(3, "JIRA-", "https://old.example.com/JIRA-<num>")]
        )
    )
    create = respx.post(_LIST).mock(
        return_value=httpx.Response(
            201, json=_live(4, "JIRA-", "https://new.example.com/JIRA-<num>")
        )
    )
    delete = respx.delete("https://api.github.com/repos/acme/widget/autolinks/3").mock(
        return_value=httpx.Response(204)
    )

    config = _config({"key_prefix": "JIRA-", "url_template": "https://new.example.com/JIRA-<num>"})
    async with GitHub("token") as client:
        results = await reconcile(client, _TARGET, config)

    assert delete.called
    assert create.called
    assert results[0].changed is True
    assert results[0].applied is True
