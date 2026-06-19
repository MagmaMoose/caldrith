"""Tests for repo selection (built-in excludes + restrictedRepos globs)."""

from __future__ import annotations

from caldrith.config.schema import RestrictedRepos
from caldrith.reconcile.planner import TargetRepo
from caldrith.reconcile.selection import is_managed, select_targets


def _managed(name: str, *, admin_repo: str = "admin", restricted=None) -> bool:
    return is_managed(name, admin_repo=admin_repo, restricted=restricted)


def test_builtin_excludes_admin_and_dotgithub() -> None:
    assert _managed("widget") is True
    assert _managed("admin") is False
    assert _managed(".github") is False


def test_custom_admin_repo_excluded() -> None:
    assert _managed("ops", admin_repo="ops") is False
    assert _managed("admin", admin_repo="ops") is True  # only the configured admin repo


def test_list_form_excludes_matching_globs() -> None:
    restricted = ["legacy-*", "*-archive"]
    assert _managed("legacy-api", restricted=restricted) is False
    assert _managed("foo-archive", restricted=restricted) is False
    assert _managed("widget", restricted=restricted) is True


def test_object_include_is_allowlist() -> None:
    restricted = RestrictedRepos(include=["svc-*"])
    assert _managed("svc-auth", restricted=restricted) is True
    assert _managed("widget", restricted=restricted) is False


def test_object_exclude_skips_matching() -> None:
    restricted = RestrictedRepos(exclude=["*-pro"])
    assert _managed("dash-pro", restricted=restricted) is False
    assert _managed("dash", restricted=restricted) is True


def test_object_include_and_exclude() -> None:
    restricted = RestrictedRepos(include=["svc-*"], exclude=["svc-legacy"])
    assert _managed("svc-auth", restricted=restricted) is True
    assert _managed("svc-legacy", restricted=restricted) is False
    assert _managed("widget", restricted=restricted) is False


def test_builtin_excludes_win_over_include() -> None:
    # Even if an include allowlist would match, admin/.github are never managed.
    restricted = RestrictedRepos(include=["a*", ".g*"])
    assert _managed("admin", restricted=restricted) is False
    assert _managed(".github", restricted=restricted) is False


def test_brace_and_extglob_globs_supported() -> None:
    assert _managed("alpha-svc", restricted=["{alpha,beta}-svc"]) is False
    assert _managed("gamma-svc", restricted=["{alpha,beta}-svc"]) is True
    assert _managed("test", restricted=["@(test|tmp)"]) is False


def test_select_targets_filters_list() -> None:
    targets = [
        TargetRepo("acme", "admin"),
        TargetRepo("acme", ".github"),
        TargetRepo("acme", "widget"),
        TargetRepo("acme", "legacy-x"),
    ]
    out = select_targets(targets, admin_repo="admin", restricted=["legacy-*"])
    assert [t.name for t in out] == ["widget"]
