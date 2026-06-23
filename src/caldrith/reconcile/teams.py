"""Reconcile team access to a repository (Organization installs only; full-replace).

The declared ``teams`` list is the COMPLETE set of teams with repo access. Missing teams
are granted the declared permission, drifted permissions are updated, and teams with
access that are not declared are removed. On User accounts (no teams) the tier is a
graceful no-op.

Drift detection maps the live ``permissions`` object (booleans per role) to the highest
granted role and compares it to the declared ``pull|triage|push|maintain|admin``.
"""

from __future__ import annotations

from typing import Any

from githubkit import GitHub
from githubkit.exception import RequestFailed

from caldrith.config.schema import RepoScoped
from caldrith.github_json import response_json
from caldrith.reconcile.base import RepoTier, TierResult
from caldrith.reconcile.planner import TargetRepo

# Highest-to-lowest so we can read the effective permission off the live booleans.
_PERMISSION_ORDER = ("admin", "maintain", "push", "triage", "pull")


def _effective_permission(team: dict[str, Any]) -> str | None:
    """The highest role granted to ``team`` per its live ``permissions`` booleans."""
    perms = team.get("permissions") or {}
    for permission in _PERMISSION_ORDER:
        if perms.get(permission):
            return permission
    # Fall back to the flat ``permission`` field (legacy pull/push/admin).
    return team.get("permission")


async def _list_teams(client: GitHub, target: TargetRepo) -> dict[str, dict[str, Any]] | None:
    """Slug -> live team with repo access, or ``None`` on a User account (no teams)."""
    out: dict[str, dict[str, Any]] = {}
    page = 1
    while True:
        try:
            response = await client.rest.repos.async_list_teams(
                owner=target.owner, repo=target.name, per_page=100, page=page
            )
        except RequestFailed as exc:
            if exc.response.status_code in (403, 404):
                return None  # User-owned repo: teams do not apply.
            raise
        batch = response_json(response) or []
        for team in batch:
            out[team["slug"]] = team
        if len(batch) < 100:
            break
        page += 1
    return out


async def reconcile(
    client: GitHub, target: TargetRepo, config: RepoScoped, *, dry_run: bool = False
) -> list[TierResult]:
    """Reconcile team access on ``target`` (add/update/remove unless dry-run)."""
    if config.teams is None:
        return []
    live = await _list_teams(client, target)
    result = TierResult(tier="teams", scope=target.full_name)
    if live is None:
        return [result]  # not an org repo — nothing to do

    teams_api = client.rest.teams
    declared: set[str] = set()
    for desired in config.teams:
        declared.add(desired.name)
        current = live.get(desired.name)
        if current is None or _effective_permission(current) != desired.permission:
            verb = "add" if current is None else "update"
            result.changed = True
            result.notes.append(f"{verb} team: {desired.name} ({desired.permission})")
            if not dry_run:
                await teams_api.async_add_or_update_repo_permissions_in_org(
                    org=target.owner,
                    team_slug=desired.name,
                    owner=target.owner,
                    repo=target.name,
                    data={"permission": desired.permission},
                )

    for slug in live:
        if slug not in declared:
            result.changed = True
            result.notes.append(f"remove team: {slug}")
            if not dry_run:
                await teams_api.async_remove_repo_in_org(
                    org=target.owner, team_slug=slug, owner=target.owner, repo=target.name
                )

    result.applied = result.changed and not dry_run
    return [result]


TIER = RepoTier(name="teams", configured=lambda c: c.teams is not None, reconcile=reconcile)
