"""Tests for the admin config loader (network mocked with respx)."""

from __future__ import annotations

import base64

import httpx
import pytest
import respx
from githubkit import GitHub

from caldrith.config.loader import ConfigNotFoundError, load_admin_config

SETTINGS_YAML = """
repository:
  allow_auto_merge: true
  delete_branch_on_merge: true
  allow_update_branch: false
"""


def _content_response(body: str) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "type": "file",
            "encoding": "base64",
            "name": "settings.yml",
            "path": ".github/settings.yml",
            "content": base64.b64encode(body.encode()).decode(),
        },
    )


@respx.mock
async def test_load_admin_config_parses_file() -> None:
    route = respx.get("https://api.github.com/repos/acme/admin/contents/.github/settings.yml").mock(
        return_value=_content_response(SETTINGS_YAML)
    )

    async with GitHub("token") as client:
        cfg = await load_admin_config(
            client,
            owner="acme",
            admin_repo="admin",
            config_path=".github",
            settings_file="settings.yml",
        )

    assert route.called
    assert cfg.repository is not None
    assert cfg.repository.allow_auto_merge is True
    assert cfg.repository.delete_branch_on_merge is True
    assert cfg.repository.allow_update_branch is False


@respx.mock
async def test_load_admin_config_passes_ref() -> None:
    route = respx.get(
        "https://api.github.com/repos/acme/admin/contents/.github/settings.yml",
        params={"ref": "feature-branch"},
    ).mock(return_value=_content_response(SETTINGS_YAML))

    async with GitHub("token") as client:
        await load_admin_config(
            client,
            owner="acme",
            admin_repo="admin",
            config_path=".github",
            settings_file="settings.yml",
            ref="feature-branch",
        )

    assert route.called


@respx.mock
async def test_directory_response_raises() -> None:
    respx.get("https://api.github.com/repos/acme/admin/contents/.github/settings.yml").mock(
        return_value=httpx.Response(200, json=[{"type": "file", "name": "x"}])
    )

    async with GitHub("token") as client:
        with pytest.raises(ConfigNotFoundError):
            await load_admin_config(
                client,
                owner="acme",
                admin_repo="admin",
                config_path=".github",
                settings_file="settings.yml",
            )
