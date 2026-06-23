"""Pure unit tests for overlay resolution (base -> suborg -> repo override).

No I/O: these exercise :func:`caldrith.reconcile.overlay.resolve_for_repo` and
:func:`~caldrith.reconcile.overlay.has_overlays` against synthetic configs only.
"""

from __future__ import annotations

from caldrith.config.schema import SafeSettingsConfig
from caldrith.reconcile.overlay import has_overlays, resolve_for_repo


def test_no_overlays_equals_base() -> None:
    config = SafeSettingsConfig(
        repository={"allow_auto_merge": True},
        labels=[{"name": "bug"}],
    )
    resolved = resolve_for_repo(config, "widget")

    assert resolved.repository is not None
    assert resolved.repository.allow_auto_merge is True
    assert resolved.labels is not None
    assert [label.name for label in resolved.labels] == ["bug"]


def test_repo_override_field_merges_repository() -> None:
    config = SafeSettingsConfig(
        repository={"allow_auto_merge": True},
        repos=[{"name": "special", "repository": {"has_wiki": False}}],
    )
    resolved = resolve_for_repo(config, "special")

    assert resolved.repository is not None
    # Field-merge: the base field survives AND the override field is applied.
    assert resolved.repository.allow_auto_merge is True
    assert resolved.repository.has_wiki is False


def test_suborg_labels_apply_only_to_matching_repos() -> None:
    config = SafeSettingsConfig(
        suborgs=[{"name": "svc", "repos": ["svc-*"], "labels": [{"name": "svc"}]}],
    )

    matched = resolve_for_repo(config, "svc-api")
    assert matched.labels is not None
    assert [label.name for label in matched.labels] == ["svc"]

    unmatched = resolve_for_repo(config, "web")
    assert unmatched.labels is None


def test_suborg_visibility_applies_only_to_matching_visibility() -> None:
    # The "public repos only" pattern: a visibility-scoped suborg carries the settings that
    # are free on public but paid on private (GHAS, secret scanning, Code Quality, …), so
    # private and unknown-visibility repos get only the base.
    config = SafeSettingsConfig(
        labels=[{"name": "base"}],
        suborgs=[{"name": "public-sec", "visibility": ["public"], "labels": [{"name": "ghas"}]}],
    )

    public = resolve_for_repo(config, "anything", "public")
    assert [label.name for label in (public.labels or [])] == ["ghas"]

    private = resolve_for_repo(config, "anything", "private")
    assert [label.name for label in (private.labels or [])] == ["base"]

    unknown = resolve_for_repo(config, "anything", None)  # visibility unknown -> not applied
    assert [label.name for label in (unknown.labels or [])] == ["base"]


def test_suborg_repos_and_visibility_both_required() -> None:
    config = SafeSettingsConfig(
        labels=[{"name": "base"}],
        suborgs=[
            {"name": "x", "repos": ["svc-*"], "visibility": ["public"], "labels": [{"name": "x"}]}
        ],
    )

    both = resolve_for_repo(config, "svc-a", "public")
    assert [label.name for label in (both.labels or [])] == ["x"]
    # name matches, visibility does not:
    vis_miss = resolve_for_repo(config, "svc-a", "private")
    assert [label.name for label in (vis_miss.labels or [])] == ["base"]
    # visibility matches, name does not:
    name_miss = resolve_for_repo(config, "web", "public")
    assert [label.name for label in (name_miss.labels or [])] == ["base"]


def test_non_matching_override_leaves_base_unchanged() -> None:
    config = SafeSettingsConfig(
        repository={"allow_auto_merge": True},
        repos=[{"name": "special", "repository": {"has_wiki": False}}],
    )
    resolved = resolve_for_repo(config, "ordinary")

    assert resolved.repository is not None
    assert resolved.repository.allow_auto_merge is True
    # The override only targets "special"; "ordinary" never sees has_wiki.
    assert resolved.repository.has_wiki is None


def test_has_overlays() -> None:
    assert has_overlays(SafeSettingsConfig(suborgs=[{"name": "svc", "repos": ["svc-*"]}])) is True
    assert has_overlays(SafeSettingsConfig(repos=[{"name": "special"}])) is True
    assert has_overlays(SafeSettingsConfig(repository={"allow_auto_merge": True})) is False
    assert has_overlays(SafeSettingsConfig()) is False
