"""Tests for the reconcile runner orchestration, including dry-run check runs."""

from __future__ import annotations

import base64

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
