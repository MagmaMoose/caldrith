"""Reconcile direct repository collaborators (full-replace).

The declared ``collaborators`` list is the COMPLETE set of *direct* collaborators (the
``affiliation=direct`` view — access inherited from org membership or teams is left
untouched). Missing collaborators are invited at the declared permission, drifted
permissions are updated, and undeclared direct collaborators are removed.

Permissions are declared in the ``pull|triage|push|maintain|admin`` vocabulary used by
the *add* endpoint; the *list* endpoint reports the equivalent role name
(``read|triage|write|maintain|admin``), so we map between them to detect drift.
"""

from __future__ import annotations

from typing import Any

from githubkit import GitHub

from caldrith.config.schema import RepoScoped
from caldrith.github_json import response_json
from caldrith.reconcile.base import RepoTier, TierResult
from caldrith.reconcile.planner import TargetRepo

# Desired permission -> the ``role_name`` the list endpoint reports for it.
_ROLE_FOR_PERMISSION = {
    "pull": "read",
    "triage": "triage",
    "push": "write",
    "maintain": "maintain",
    "admin": "admin",
}


async def _list_collaborators(client: GitHub, target: TargetRepo) -> dict[str, dict[str, Any]]:
    """Lower-cased login -> live collaborator (direct affiliation only, paginated)."""
    out: dict[str, dict[str, Any]] = {}
    page = 1
    while True:
        response = await client.rest.repos.async_list_collaborators(
            owner=target.owner, repo=target.name, affiliation="direct", per_page=100, page=page
        )
        batch = response_json(response) or []
        for collaborator in batch:
            out[collaborator["login"].lower()] = collaborator
        if len(batch) < 100:
            break
        page += 1
    return out


async def reconcile(
    client: GitHub, target: TargetRepo, config: RepoScoped, *, dry_run: bool = False
) -> list[TierResult]:
    """Reconcile direct collaborators on ``target`` (add/update/remove unless dry-run)."""
    if config.collaborators is None:
        return []
    repos = client.rest.repos
    live = await _list_collaborators(client, target)
    result = TierResult(tier="collaborators", scope=target.full_name)
    declared: set[str] = set()

    for desired in config.collaborators:
        key = desired.username.lower()
        declared.add(key)
        current = live.get(key)
        expected_role = _ROLE_FOR_PERMISSION.get(desired.permission, desired.permission)
        if current is None or current.get("role_name") != expected_role:
            verb = "add" if current is None else "update"
            result.changed = True
            result.notes.append(f"{verb} collaborator: {desired.username} ({desired.permission})")
            if not dry_run:
                await repos.async_add_collaborator(
                    owner=target.owner,
                    repo=target.name,
                    username=desired.username,
                    data={"permission": desired.permission},
                )

    for key, collaborator in live.items():
        if key not in declared:
            result.changed = True
            result.notes.append(f"remove collaborator: {collaborator['login']}")
            if not dry_run:
                await repos.async_remove_collaborator(
                    owner=target.owner, repo=target.name, username=collaborator["login"]
                )

    result.applied = result.changed and not dry_run
    return [result]


TIER = RepoTier(
    name="collaborators", configured=lambda c: c.collaborators is not None, reconcile=reconcile
)
