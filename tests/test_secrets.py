"""Tests for the secrets tier (presence-only; never rotates a live secret)."""

from __future__ import annotations

from typing import Any

import httpx
import pytest
import respx
from githubkit import GitHub
from nacl.encoding import Base64Encoder
from nacl.public import PrivateKey

from caldrith.config.schema import SafeSettingsConfig
from caldrith.reconcile import secrets
from caldrith.reconcile.planner import TargetRepo

_TARGET = TargetRepo("acme", "widget")
_ACTIONS = "https://api.github.com/repos/acme/widget/actions/secrets"
_DEPENDABOT = "https://api.github.com/repos/acme/widget/dependabot/secrets"


def _pubkey() -> str:
    """A valid base64-encoded NaCl public key for sealed-box encryption."""
    return PrivateKey.generate().public_key.encode(Base64Encoder).decode()


def _list_body(*names: str) -> dict[str, Any]:
    return {"total_count": len(names), "secrets": [{"name": n} for n in names]}


def _config(**kwargs: Any) -> SafeSettingsConfig:
    return SafeSettingsConfig.model_validate({"secrets": kwargs})


@respx.mock
async def test_missing_with_env_value_is_created(monkeypatch: pytest.MonkeyPatch) -> None:
    """(a) declared + missing + env value present -> public-key GET + PUT, applied."""
    monkeypatch.setenv("CALDRITH_SECRET_DEPLOY_KEY", "s3cret")
    respx.get(_ACTIONS).mock(return_value=httpx.Response(200, json=_list_body()))
    pk = respx.get(f"{_ACTIONS}/public-key").mock(
        return_value=httpx.Response(200, json={"key_id": "123", "key": _pubkey()})
    )
    put = respx.put(f"{_ACTIONS}/DEPLOY_KEY").mock(return_value=httpx.Response(201))

    async with GitHub("token") as client:
        results = await secrets.reconcile(
            client, _TARGET, _config(actions=["DEPLOY_KEY"], prune=False)
        )

    (result,) = results
    assert pk.called
    assert put.called
    assert result.tier == "secrets"
    assert result.scope == "acme/widget"
    assert result.changed is True
    assert result.applied is True


@respx.mock
async def test_existing_secret_is_never_rotated(monkeypatch: pytest.MonkeyPatch) -> None:
    """(b) declared secret already present -> no write, even with an env value available."""
    monkeypatch.setenv("CALDRITH_SECRET_DEPLOY_KEY", "s3cret")
    respx.get(_ACTIONS).mock(return_value=httpx.Response(200, json=_list_body("DEPLOY_KEY")))
    pk = respx.get(f"{_ACTIONS}/public-key").mock(
        return_value=httpx.Response(200, json={"key_id": "123", "key": _pubkey()})
    )
    put = respx.put(f"{_ACTIONS}/DEPLOY_KEY").mock(return_value=httpx.Response(201))

    async with GitHub("token") as client:
        results = await secrets.reconcile(
            client, _TARGET, _config(actions=["DEPLOY_KEY"], prune=False)
        )

    (result,) = results
    assert not pk.called
    assert not put.called  # present -> never rotated
    assert result.changed is False
    assert result.applied is False


@respx.mock
async def test_missing_without_env_value_reports_gap(monkeypatch: pytest.MonkeyPatch) -> None:
    """(c) declared + missing + NO env value -> changed, no PUT, note, not applied."""
    monkeypatch.delenv("CALDRITH_SECRET_DEPLOY_KEY", raising=False)
    respx.get(_ACTIONS).mock(return_value=httpx.Response(200, json=_list_body()))
    pk = respx.get(f"{_ACTIONS}/public-key").mock(
        return_value=httpx.Response(200, json={"key_id": "123", "key": _pubkey()})
    )
    put = respx.put(f"{_ACTIONS}/DEPLOY_KEY").mock(return_value=httpx.Response(201))

    async with GitHub("token") as client:
        results = await secrets.reconcile(
            client, _TARGET, _config(actions=["DEPLOY_KEY"], prune=False)
        )

    (result,) = results
    assert not pk.called
    assert not put.called
    assert result.changed is True
    assert result.applied is False
    assert any("no value supplied" in n and "DEPLOY_KEY" in n for n in result.notes)


@respx.mock
async def test_prune_deletes_undeclared_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    """(d) prune=True -> an undeclared live secret is DELETEd."""
    monkeypatch.setenv("CALDRITH_SECRET_DEPLOY_KEY", "s3cret")
    respx.get(_ACTIONS).mock(
        return_value=httpx.Response(200, json=_list_body("DEPLOY_KEY", "OLD_KEY"))
    )
    delete = respx.delete(f"{_ACTIONS}/OLD_KEY").mock(return_value=httpx.Response(204))
    # prune=True also reconciles the dependabot store (empty here -> nothing to prune).
    respx.get(_DEPENDABOT).mock(return_value=httpx.Response(200, json=_list_body()))

    async with GitHub("token") as client:
        results = await secrets.reconcile(
            client, _TARGET, _config(actions=["DEPLOY_KEY"], prune=True)
        )

    (result,) = results
    assert delete.called
    assert result.changed is True
    assert result.applied is True
    assert any("delete actions secret: OLD_KEY" in n for n in result.notes)


@respx.mock
async def test_dry_run_never_writes(monkeypatch: pytest.MonkeyPatch) -> None:
    """(e) dry_run=True -> drift detected but no public-key GET, no PUT, no DELETE."""
    monkeypatch.setenv("CALDRITH_SECRET_DEPLOY_KEY", "s3cret")
    respx.get(_ACTIONS).mock(return_value=httpx.Response(200, json=_list_body("OLD_KEY")))
    pk = respx.get(f"{_ACTIONS}/public-key").mock(
        return_value=httpx.Response(200, json={"key_id": "123", "key": _pubkey()})
    )
    put = respx.put(f"{_ACTIONS}/DEPLOY_KEY").mock(return_value=httpx.Response(201))
    delete = respx.delete(f"{_ACTIONS}/OLD_KEY").mock(return_value=httpx.Response(204))
    # prune=True also lists the dependabot store (reads only; dry-run writes nothing).
    respx.get(_DEPENDABOT).mock(return_value=httpx.Response(200, json=_list_body()))

    async with GitHub("token") as client:
        results = await secrets.reconcile(
            client, _TARGET, _config(actions=["DEPLOY_KEY"], prune=True), dry_run=True
        )

    (result,) = results
    assert not pk.called
    assert not put.called
    assert not delete.called
    assert result.changed is True
    assert result.applied is False


@respx.mock
async def test_dependabot_store_is_managed(monkeypatch: pytest.MonkeyPatch) -> None:
    """A declared dependabot secret uses the parallel dependabot endpoints."""
    monkeypatch.setenv("CALDRITH_SECRET_NPM_TOKEN", "tok")
    respx.get(_DEPENDABOT).mock(return_value=httpx.Response(200, json=_list_body()))
    pk = respx.get(f"{_DEPENDABOT}/public-key").mock(
        return_value=httpx.Response(200, json={"key_id": "9", "key": _pubkey()})
    )
    put = respx.put(f"{_DEPENDABOT}/NPM_TOKEN").mock(return_value=httpx.Response(201))

    async with GitHub("token") as client:
        results = await secrets.reconcile(client, _TARGET, _config(dependabot=["NPM_TOKEN"]))

    (result,) = results
    assert pk.called
    assert put.called
    assert result.changed is True
    assert result.applied is True


async def test_unconfigured_returns_empty() -> None:
    async with GitHub("token") as client:
        results = await secrets.reconcile(client, _TARGET, SafeSettingsConfig.model_validate({}))
    assert results == []
