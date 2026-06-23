"""Reconcile a repository's custom-property values.

``custom_properties`` is a mapping of property name -> value (a string, a list of strings
for multi-select, or ``null`` to clear). Caldrith reads the live values, diffs each
declared property, and issues a single create-or-update with only the drifted properties
(idempotent). It manages only the properties the config declares; values for properties
the config does not mention are left untouched.

The org must define the property first (see the ``organization.custom_property_definitions``
tier); setting a value for an undefined property fails at the API.
"""

from __future__ import annotations

from typing import Any

from githubkit import GitHub

from caldrith.config.schema import RepoScoped
from caldrith.github_json import response_json
from caldrith.reconcile.base import RepoTier, TierResult
from caldrith.reconcile.planner import TargetRepo


def _normalize(value: Any) -> Any:
    """Order-insensitive form for list (multi-select) values; scalars pass through."""
    return sorted(value) if isinstance(value, list) else value


async def reconcile(
    client: GitHub, target: TargetRepo, config: RepoScoped, *, dry_run: bool = False
) -> list[TierResult]:
    """Reconcile declared custom-property values on ``target`` (set drift unless dry-run)."""
    if config.custom_properties is None:
        return []
    repos = client.rest.repos
    live_list = (
        response_json(
            await repos.async_custom_properties_for_repos_get_repository_values(
                owner=target.owner, repo=target.name
            )
        )
        or []
    )
    live = {item["property_name"]: item.get("value") for item in live_list}
    result = TierResult(tier="custom_properties", scope=target.full_name)

    changed_properties: list[dict[str, Any]] = []
    for name, value in config.custom_properties.items():
        if _normalize(live.get(name)) != _normalize(value):
            changed_properties.append({"property_name": name, "value": value})
            result.notes.append(f"set property: {name}")

    if changed_properties:
        result.changed = True
        if not dry_run:
            await repos.async_custom_properties_for_repos_create_or_update_repository_values(
                owner=target.owner,
                repo=target.name,
                data={"properties": changed_properties},
            )
            result.applied = True
    return [result]


TIER = RepoTier(
    name="custom_properties",
    configured=lambda c: c.custom_properties is not None,
    reconcile=reconcile,
)
