"""Tests for the single-repo RepositoryApplier."""

from __future__ import annotations

import httpx
import respx
from githubkit import GitHub

from caldrith.config.schema import RepositorySettings
from caldrith.reconcile.planner import TargetRepo
from caldrith.reconcile.repository import RepositoryApplier


def _repo_json(**overrides: object) -> dict:
    base = {
        "id": 1,
        "node_id": "R_1",
        "name": "widget",
        "full_name": "acme/widget",
        "url": "https://api.github.com/repos/acme/widget",
        "html_url": "https://github.com/acme/widget",
        "owner": {"login": "acme", "id": 5, "url": "https://api.github.com/users/acme"},
        "private": False,
        "allow_auto_merge": False,
        "delete_branch_on_merge": False,
        "allow_update_branch": False,
    }
    base.update(overrides)
    return base


@respx.mock
async def test_apply_patches_when_changed() -> None:
    respx.get("https://api.github.com/repos/acme/widget").mock(
        return_value=httpx.Response(200, json=_repo_json())
    )
    patch = respx.patch("https://api.github.com/repos/acme/widget").mock(
        return_value=httpx.Response(200, json=_repo_json(allow_auto_merge=True))
    )

    async with GitHub("token") as client:
        applier = RepositoryApplier(client)
        result = await applier.apply(
            TargetRepo("acme", "widget"),
            RepositorySettings(allow_auto_merge=True),
        )

    assert patch.called
    assert result.applied is True
    assert result.diff.changed_payload() == {"allow_auto_merge": True}


@respx.mock
async def test_apply_noop_when_converged() -> None:
    respx.get("https://api.github.com/repos/acme/widget").mock(
        return_value=httpx.Response(200, json=_repo_json(allow_auto_merge=True))
    )
    patch = respx.patch("https://api.github.com/repos/acme/widget").mock(
        return_value=httpx.Response(200, json=_repo_json())
    )

    async with GitHub("token") as client:
        applier = RepositoryApplier(client)
        result = await applier.apply(
            TargetRepo("acme", "widget"),
            RepositorySettings(allow_auto_merge=True),
        )

    assert not patch.called  # idempotent: no change -> no PATCH
    assert result.applied is False
    assert result.has_changes is False


@respx.mock
async def test_dry_run_never_patches() -> None:
    respx.get("https://api.github.com/repos/acme/widget").mock(
        return_value=httpx.Response(200, json=_repo_json())
    )
    patch = respx.patch("https://api.github.com/repos/acme/widget").mock(
        return_value=httpx.Response(200, json=_repo_json())
    )

    async with GitHub("token") as client:
        applier = RepositoryApplier(client, dry_run=True)
        result = await applier.apply(
            TargetRepo("acme", "widget"),
            RepositorySettings(allow_auto_merge=True),
        )

    assert not patch.called
    assert result.applied is False
    assert result.has_changes is True  # diff is still computed for the check run


@respx.mock
async def test_archived_repo_is_noop() -> None:
    respx.get("https://api.github.com/repos/acme/widget").mock(
        return_value=httpx.Response(200, json=_repo_json(archived=True))
    )
    patch = respx.patch("https://api.github.com/repos/acme/widget").mock(
        return_value=httpx.Response(200, json=_repo_json())
    )

    async with GitHub("token") as client:
        applier = RepositoryApplier(client)
        result = await applier.apply(
            TargetRepo("acme", "widget"),
            RepositorySettings(allow_auto_merge=True),
        )

    assert not patch.called  # archived repos can't be PATCHed -> skip
    assert result.applied is False
    assert result.has_changes is False


async def test_empty_desired_is_noop() -> None:
    async with GitHub("token") as client:
        applier = RepositoryApplier(client)
        result = await applier.apply(TargetRepo("acme", "widget"), RepositorySettings())
    assert result.applied is False
    assert result.has_changes is False
