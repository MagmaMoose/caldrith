"""Resolve the effective per-repo config from base + suborg + repo overlays.

``settings.yml`` may layer settings: the top-level block is the org-wide base, ``suborgs``
overlays apply to repo subsets (by name glob), and ``repos`` overlays target individual
repos. For a given repo the effective config is the base with each matching overlay
merged on top, last-wins, in order: base -> suborgs (declared order) -> repos.

Merge rules (mirroring github/safe-settings):
- The ``repository`` block is **field-merged** (an overlay overrides only the fields it
  sets; unset fields fall through to the base).
- Every other tier (``labels``, ``branches``, ``variables``, ...) is **replaced
  wholesale** when an overlay declares it — a list is an all-or-nothing statement.

This module is pure (no I/O): it takes a parsed :class:`SafeSettingsConfig` and a repo
name and returns a new :class:`SafeSettingsConfig` carrying only the resolved repo-scoped
tiers (org/suborgs/repos fields are dropped from the result — they are not repo-scoped).
"""

from __future__ import annotations

from typing import Any

from wcmatch import fnmatch as _fnmatch

from caldrith.config.schema import (
    RepoScoped,
    RepositorySettings,
    SafeSettingsConfig,
)

_GLOB_FLAGS = _fnmatch.EXTMATCH | _fnmatch.BRACE
_REPO_SCOPED_FIELDS = tuple(RepoScoped.model_fields)


def _matches(name: str, patterns: list[str] | None) -> bool:
    pats = list(patterns or [])
    return bool(pats) and _fnmatch.fnmatch(name, pats, flags=_GLOB_FLAGS)


def _merge_repository(
    base: RepositorySettings | None, over: RepositorySettings | None
) -> RepositorySettings | None:
    """Field-merge two repository blocks (``over`` wins on the fields it set)."""
    if base is None:
        return over
    if over is None:
        return base
    data = base.model_dump(exclude_unset=True)
    data.update(over.model_dump(exclude_unset=True))
    return RepositorySettings(**data)


def resolve_for_repo(config: SafeSettingsConfig, repo_name: str) -> SafeSettingsConfig:
    """Return the effective repo-scoped config for ``repo_name`` after overlays.

    Overlays are applied base -> matching suborgs (in order) -> matching repo overrides
    (in order). When no overlays match, this is equivalent to the base config's
    repo-scoped tiers.
    """
    layers: list[RepoScoped] = [config]
    for suborg in config.suborgs or []:
        if _matches(repo_name, suborg.repos):
            layers.append(suborg)
    for override in config.repos or []:
        if _matches(repo_name, [override.name]):
            layers.append(override)

    merged: dict[str, Any] = {}
    for layer in layers:
        for field in _REPO_SCOPED_FIELDS:
            value = getattr(layer, field)
            if value is None:
                continue
            if field == "repository":
                merged["repository"] = _merge_repository(merged.get("repository"), value)
            else:
                merged[field] = value
    return SafeSettingsConfig(**merged)


def has_overlays(config: SafeSettingsConfig) -> bool:
    """True if the config declares any ``suborgs`` or ``repos`` overlays."""
    return bool(config.suborgs or config.repos)
