"""Decide which repositories an installation reconciles.

Between "every repo the App can access" and "repos we actually reconcile" sit two
filters:

1. Built-in exclusions, always skipped: the admin (config) repo and ``.github`` —
   caldrith's own meta repos must never be managed by caldrith.
2. The tenant's optional ``restrictedRepos`` from ``settings.yml``:
   * a list of globs  -> exclude any repo matching one of them;
   * an object ``{include, exclude}`` -> ``include`` is an allowlist (when set, only
     matching repos are managed) and ``exclude`` additionally skips matches.

Glob patterns use minimatch-style semantics (brace + extglob) via :mod:`wcmatch`,
matching github/safe-settings' targeting behaviour. The built-in exclusions apply
regardless of ``restrictedRepos``.
"""

from __future__ import annotations

from collections.abc import Iterable

from wcmatch import fnmatch as _fnmatch

from caldrith.config.schema import RestrictedRepos
from caldrith.reconcile.planner import TargetRepo

_GLOB_FLAGS = _fnmatch.EXTMATCH | _fnmatch.BRACE


def builtin_excludes(admin_repo: str) -> frozenset[str]:
    """Repos caldrith never manages: its own config repo and ``.github``."""
    return frozenset({admin_repo, ".github"})


def _matches_any(name: str, patterns: Iterable[str] | None) -> bool:
    pats = list(patterns or [])
    return bool(pats) and _fnmatch.fnmatch(name, pats, flags=_GLOB_FLAGS)


def is_managed(
    name: str,
    *,
    admin_repo: str,
    restricted: list[str] | RestrictedRepos | None,
) -> bool:
    """Return whether the repo ``name`` should be reconciled."""
    if name in builtin_excludes(admin_repo):
        return False
    if restricted is None:
        return True
    if isinstance(restricted, RestrictedRepos):
        if restricted.include and not _matches_any(name, restricted.include):
            return False
        return not _matches_any(name, restricted.exclude)
    # List form: an exclude list of globs.
    return not _matches_any(name, restricted)


def select_targets(
    targets: list[TargetRepo],
    *,
    admin_repo: str,
    restricted: list[str] | RestrictedRepos | None,
) -> list[TargetRepo]:
    """Filter ``targets`` to the repos this installation should reconcile."""
    return [t for t in targets if is_managed(t.name, admin_repo=admin_repo, restricted=restricted)]
