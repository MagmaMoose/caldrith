"""Deep-diff engine porting safe-settings' ``compareDeep`` semantics.

Given the *actual* (live, from the GitHub API) and *desired* (from the admin config)
state as plain dicts, produce a :class:`Diff` describing what would change. The engine
is pure and I/O-free, and is the heart of Caldrith's idempotency guarantee: applying a
diff and re-diffing yields ``has_changes == False``.

safe-settings rules ported here:
- Ignore any field whose key *contains* ``"url"`` (case-insensitive) and the keys
  ``id`` / ``node_id`` — these are server-assigned and never declared by the user.
- Only keys present in ``desired`` are considered. A key absent from ``desired`` is
  left untouched (we never delete live config the user didn't mention). ``deletions``
  is therefore reserved for nested structures where the user explicitly cleared a
  value; in P1's flat repository block it stays empty, but the field exists so the
  shape is stable as nested features land.
- A key in ``desired`` but missing from ``actual`` is an *addition*; a key in both
  whose values differ (deep equality) is a *modification*.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

_IGNORED_EXACT_KEYS = {"id", "node_id"}


def _is_ignored_key(key: str) -> bool:
    """Return ``True`` if ``key`` should be skipped during comparison."""
    lowered = key.lower()
    return "url" in lowered or lowered in _IGNORED_EXACT_KEYS


@dataclass
class Diff:
    """The result of comparing desired vs actual state.

    Attributes:
        additions: keys present in desired but missing from actual, mapped to the
            desired value.
        modifications: keys present in both whose values differ, mapped to the
            desired value (the value to PATCH to).
        deletions: keys the user explicitly cleared (reserved for nested diffs;
            empty in P1's flat repository block).
    """

    additions: dict[str, Any] = field(default_factory=dict)
    modifications: dict[str, Any] = field(default_factory=dict)
    deletions: dict[str, Any] = field(default_factory=dict)

    @property
    def has_changes(self) -> bool:
        """``True`` iff any of additions/modifications/deletions are non-empty."""
        return bool(self.additions or self.modifications or self.deletions)

    def changed_payload(self) -> dict[str, Any]:
        """Return the minimal mapping to PATCH (additions + modifications).

        This is what gets sent to ``repos.update``: only fields that actually
        differ, so applying twice produces exactly one mutation.
        """
        payload: dict[str, Any] = {}
        payload.update(self.additions)
        payload.update(self.modifications)
        return payload


def _deep_equal(a: Any, b: Any) -> bool:
    """Order-insensitive deep equality for JSON-ish values.

    Lists are compared as multisets where elements are hashable (mirrors
    safe-settings treating e.g. topic lists as sets); otherwise positionally.
    """
    if isinstance(a, dict) and isinstance(b, dict):
        keys = (set(a) | set(b)) - {k for k in (set(a) | set(b)) if _is_ignored_key(k)}
        return all(_deep_equal(a.get(k), b.get(k)) for k in keys)
    if isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            return False
        try:
            return sorted(a) == sorted(b)  # type: ignore[type-var]
        except TypeError:
            return all(_deep_equal(x, y) for x, y in zip(a, b, strict=True))
    return a == b


def compare_deep(actual: dict[str, Any], desired: dict[str, Any]) -> Diff:
    """Compare ``desired`` against ``actual`` and return a :class:`Diff`.

    Only keys present in ``desired`` (and not ignored) are evaluated; keys that exist
    only in ``actual`` are left untouched.
    """
    diff = Diff()
    for key, desired_value in desired.items():
        if _is_ignored_key(key):
            continue
        if key not in actual:
            diff.additions[key] = desired_value
        elif not _deep_equal(actual[key], desired_value):
            diff.modifications[key] = desired_value
    return diff
