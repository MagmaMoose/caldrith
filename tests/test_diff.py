"""Tests for the deep-diff engine, with emphasis on idempotency."""

from __future__ import annotations

from caldrith.config.diff import Diff, compare_deep


def test_no_changes_when_equal() -> None:
    actual = {"allow_auto_merge": True, "delete_branch_on_merge": False}
    desired = {"allow_auto_merge": True, "delete_branch_on_merge": False}
    diff = compare_deep(actual, desired)
    assert diff.has_changes is False
    assert diff.changed_payload() == {}


def test_modification_detected() -> None:
    actual = {"allow_auto_merge": False}
    desired = {"allow_auto_merge": True}
    diff = compare_deep(actual, desired)
    assert diff.modifications == {"allow_auto_merge": True}
    assert diff.has_changes is True


def test_addition_detected() -> None:
    actual: dict = {}
    desired = {"allow_update_branch": True}
    diff = compare_deep(actual, desired)
    assert diff.additions == {"allow_update_branch": True}
    assert diff.has_changes is True


def test_extra_actual_keys_ignored() -> None:
    # Keys only present in actual must not produce changes.
    actual = {"allow_auto_merge": True, "has_issues": True, "stargazers": 99}
    desired = {"allow_auto_merge": True}
    diff = compare_deep(actual, desired)
    assert diff.has_changes is False


def test_url_and_id_keys_ignored() -> None:
    actual = {"id": 1, "node_id": "abc", "html_url": "x", "allow_auto_merge": False}
    desired = {"id": 2, "node_id": "zzz", "html_url": "y", "allow_auto_merge": True}
    diff = compare_deep(actual, desired)
    # Only the real field should appear; id/node_id/url keys are filtered.
    assert diff.changed_payload() == {"allow_auto_merge": True}


def test_idempotency_apply_once() -> None:
    """Applying the diff once then re-diffing yields no further changes."""
    actual = {"allow_auto_merge": False, "delete_branch_on_merge": False}
    desired = {"allow_auto_merge": True, "delete_branch_on_merge": True}

    first = compare_deep(actual, desired)
    assert first.has_changes is True

    # Simulate the PATCH: merge the changed payload into the live state.
    applied = {**actual, **first.changed_payload()}

    second = compare_deep(applied, desired)
    assert second.has_changes is False
    assert second.changed_payload() == {}


def test_list_compared_as_multiset() -> None:
    actual = {"topics": ["b", "a"]}
    desired = {"topics": ["a", "b"]}
    assert compare_deep(actual, desired).has_changes is False

    desired2 = {"topics": ["a", "c"]}
    assert compare_deep(actual, desired2).has_changes is True


def test_diff_dataclass_defaults_empty() -> None:
    diff = Diff()
    assert diff.additions == {}
    assert diff.modifications == {}
    assert diff.deletions == {}
    assert diff.has_changes is False
