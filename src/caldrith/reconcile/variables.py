"""Reconcile repository Actions variables (full-replace).

Unlike secrets, variable values ARE readable, so this tier is fully declarative: the
``variables`` list is the COMPLETE set. Missing variables are created, value drift is
updated, and undeclared variables are deleted. Comparison is exact on the value, so a
converged repo issues no write.
"""

from __future__ import annotations

from typing import Any

from githubkit import GitHub

from caldrith.config.schema import RepoScoped
from caldrith.github_json import response_json
from caldrith.reconcile.base import RepoTier, TierResult
from caldrith.reconcile.planner import TargetRepo


async def _list_variables(client: GitHub, target: TargetRepo) -> dict[str, dict[str, Any]]:
    """Name -> live variable for every Actions variable on the repo (paginated)."""
    out: dict[str, dict[str, Any]] = {}
    page = 1
    while True:
        response = await client.rest.actions.async_list_repo_variables(
            owner=target.owner, repo=target.name, per_page=100, page=page
        )
        body = response_json(response)
        batch = body.get("variables") or []
        for variable in batch:
            out[variable["name"]] = variable
        if len(batch) < 100:
            break
        page += 1
    return out


async def reconcile(
    client: GitHub, target: TargetRepo, config: RepoScoped, *, dry_run: bool = False
) -> list[TierResult]:
    """Reconcile Actions variables on ``target`` (create/update/delete unless dry-run)."""
    if config.variables is None:
        return []
    actions = client.rest.actions
    live = await _list_variables(client, target)
    result = TierResult(tier="variables", scope=target.full_name)
    declared: set[str] = set()

    for desired in config.variables:
        declared.add(desired.name)
        current = live.get(desired.name)
        if current is None:
            result.changed = True
            result.notes.append(f"create variable: {desired.name}")
            if not dry_run:
                await actions.async_create_repo_variable(
                    owner=target.owner,
                    repo=target.name,
                    data={"name": desired.name, "value": desired.value},
                )
        elif current.get("value") != desired.value:
            result.changed = True
            result.notes.append(f"update variable: {desired.name}")
            if not dry_run:
                await actions.async_update_repo_variable(
                    owner=target.owner,
                    repo=target.name,
                    name=desired.name,
                    data={"value": desired.value},
                )

    for name in live:
        if name not in declared:
            result.changed = True
            result.notes.append(f"delete variable: {name}")
            if not dry_run:
                await actions.async_delete_repo_variable(
                    owner=target.owner, repo=target.name, name=name
                )

    result.applied = result.changed and not dry_run
    return [result]


TIER = RepoTier(name="variables", configured=lambda c: c.variables is not None, reconcile=reconcile)
