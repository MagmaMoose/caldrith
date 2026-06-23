"""Tests for the reconcile runner orchestration, including dry-run check runs."""

from __future__ import annotations

import base64
import json

import httpx
import respx
from githubkit import GitHub

from caldrith.reconcile.runner import run_reconcile

SETTINGS_YAML = """
repository:
  allow_auto_merge: true
"""


def _content_response() -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "type": "file",
            "encoding": "base64",
            "name": "settings.yml",
            "path": ".github/settings.yml",
            "content": base64.b64encode(SETTINGS_YAML.encode()).decode(),
        },
    )


def _repo_json(allow_auto_merge: bool = False) -> dict:
    return {
        "id": 1,
        "node_id": "R_1",
        "name": "widget",
        "full_name": "acme/widget",
        "url": "https://api.github.com/repos/acme/widget",
        "html_url": "https://github.com/acme/widget",
        "owner": {"login": "acme", "id": 5, "url": "https://api.github.com/users/acme"},
        "private": False,
        "allow_auto_merge": allow_auto_merge,
    }


@respx.mock
async def test_run_reconcile_applies_explicit_repo() -> None:
    respx.get("https://api.github.com/repos/acme/admin/contents/.github/settings.yml").mock(
        return_value=_content_response()
    )
    respx.get("https://api.github.com/repos/acme/widget").mock(
        return_value=httpx.Response(200, json=_repo_json())
    )
    patch = respx.patch("https://api.github.com/repos/acme/widget").mock(
        return_value=httpx.Response(200, json=_repo_json(True))
    )

    async with GitHub("token") as client:
        summary = await run_reconcile(client, installation_id=42, owner="acme", repos=["widget"])

    assert patch.called
    assert len(summary.applied) == 1


@respx.mock
async def test_dry_run_posts_check_and_no_patch() -> None:
    respx.get(
        "https://api.github.com/repos/acme/admin/contents/.github/settings.yml",
        params={"ref": "headsha"},
    ).mock(return_value=_content_response())
    respx.get("https://api.github.com/repos/acme/widget").mock(
        return_value=httpx.Response(200, json=_repo_json())
    )
    patch = respx.patch("https://api.github.com/repos/acme/widget").mock(
        return_value=httpx.Response(200, json=_repo_json(True))
    )
    check = respx.post("https://api.github.com/repos/acme/admin/check-runs").mock(
        return_value=httpx.Response(
            201,
            json={
                "id": 1,
                "name": "caldrith/settings",
                "head_sha": "headsha",
                "status": "completed",
            },
        )
    )

    async with GitHub("token") as client:
        summary = await run_reconcile(
            client,
            installation_id=42,
            owner="acme",
            repos=["widget"],
            dry_run=True,
            head_sha="headsha",
        )

    assert not patch.called
    assert check.called
    assert summary.dry_run is True
    assert len(summary.changed) == 1
    assert len(summary.applied) == 0


_MULTI_TIER_YAML = """
repository:
  allow_auto_merge: true
labels:
  - name: bug
    color: ff0000
"""


@respx.mock
async def test_run_reconcile_runs_multiple_tiers() -> None:
    """The registry loop runs every configured tier per repo (repository + labels)."""
    respx.get("https://api.github.com/repos/acme/admin/contents/.github/settings.yml").mock(
        return_value=httpx.Response(
            200,
            json={
                "type": "file",
                "encoding": "base64",
                "content": base64.b64encode(_MULTI_TIER_YAML.encode()).decode(),
            },
        )
    )
    respx.get("https://api.github.com/repos/acme/widget").mock(
        return_value=httpx.Response(200, json=_repo_json())
    )
    repo_patch = respx.patch("https://api.github.com/repos/acme/widget").mock(
        return_value=httpx.Response(200, json=_repo_json(True))
    )
    respx.get("https://api.github.com/repos/acme/widget/labels").mock(
        return_value=httpx.Response(200, json=[])  # no labels live -> create
    )
    label_post = respx.post("https://api.github.com/repos/acme/widget/labels").mock(
        return_value=httpx.Response(201, json={"name": "bug", "color": "ff0000"})
    )

    async with GitHub("token") as client:
        summary = await run_reconcile(client, installation_id=42, owner="acme", repos=["widget"])

    assert repo_patch.called and label_post.called
    applied_tiers = {r.tier for r in summary.applied}
    assert applied_tiers == {"repository", "labels"}


_OVERLAY_YAML = """
repository:
  allow_auto_merge: true
repos:
  - name: widget
    repository:
      has_wiki: false
"""


@respx.mock
async def test_run_reconcile_applies_repo_overlay() -> None:
    """A per-repo `repos:` override field-merges over the base repository block."""
    respx.get("https://api.github.com/repos/acme/admin/contents/.github/settings.yml").mock(
        return_value=httpx.Response(
            200,
            json={
                "type": "file",
                "encoding": "base64",
                "content": base64.b64encode(_OVERLAY_YAML.encode()).decode(),
            },
        )
    )
    respx.get("https://api.github.com/repos/acme/widget").mock(
        return_value=httpx.Response(200, json=_repo_json() | {"has_wiki": True})
    )
    patch = respx.patch("https://api.github.com/repos/acme/widget").mock(
        return_value=httpx.Response(200, json=_repo_json(True))
    )

    async with GitHub("token") as client:
        summary = await run_reconcile(client, installation_id=42, owner="acme", repos=["widget"])

    assert patch.called
    body = json.loads(patch.calls.last.request.content)
    # Both the base field and the overlay field are in one PATCH (overlay field-merge).
    assert body["allow_auto_merge"] is True
    assert body["has_wiki"] is False
    assert summary.any_changed
