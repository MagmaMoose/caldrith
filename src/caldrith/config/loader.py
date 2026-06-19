"""Fetch and validate the admin repo's ``settings.yml`` into a typed config.

This is the only module under :mod:`caldrith.config` that performs I/O. It fetches the
file contents through the GitHub REST API (so it works against GHES too), base64-
decodes them, parses with ``yaml.safe_load``, and validates into
:class:`~caldrith.config.schema.SafeSettingsConfig`.
"""

from __future__ import annotations

import base64

import yaml
from githubkit import GitHub

from caldrith.config.schema import SafeSettingsConfig
from caldrith.github_json import response_json


class ConfigNotFoundError(Exception):
    """Raised when the admin repo has no settings file at the expected path."""


def _join_path(config_path: str, settings_file: str) -> str:
    """Join the config dir and file name into an API content path."""
    cleaned = config_path.strip("/")
    return f"{cleaned}/{settings_file}" if cleaned else settings_file


async def load_admin_config(
    client: GitHub,
    owner: str,
    admin_repo: str,
    config_path: str,
    settings_file: str,
    ref: str | None = None,
) -> SafeSettingsConfig:
    """Load and validate the admin config from ``owner/admin_repo``.

    Args:
        client: A per-installation githubkit client.
        owner: The account login that owns the admin repo.
        admin_repo: The admin repo name (default ``"admin"``).
        config_path: Directory holding the settings file (default ``".github"``).
        settings_file: Settings file name (default ``"settings.yml"``).
        ref: Optional git ref (branch/sha) to read from; used for PR dry-runs so the
            check reflects the proposed (non-default-branch) config.

    Raises:
        ConfigNotFoundError: if the path resolves to a directory/symlink rather than
            a file, or the file is missing.
    """
    path = _join_path(config_path, settings_file)
    kwargs: dict[str, str] = {}
    if ref is not None:
        kwargs["ref"] = ref

    response = await client.rest.repos.async_get_content(
        owner=owner,
        repo=admin_repo,
        path=path,
        **kwargs,  # type: ignore[arg-type]
    )
    content = response_json(response)

    # A file response is an object carrying base64 ``content``; dirs are a list, and
    # symlinks/submodules lack a base64 ``content`` field.
    if not isinstance(content, dict):
        raise ConfigNotFoundError(
            f"{owner}/{admin_repo}/{path} did not resolve to a file (got a directory)"
        )
    encoded = content.get("content")
    encoding = content.get("encoding")
    if encoded is None or encoding != "base64":
        raise ConfigNotFoundError(
            f"{owner}/{admin_repo}/{path} did not resolve to a base64-encoded file"
        )

    raw = base64.b64decode(encoded)
    data = yaml.safe_load(raw) or {}
    return SafeSettingsConfig.model_validate(data)
