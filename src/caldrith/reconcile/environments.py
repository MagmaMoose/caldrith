"""Reconcile deployment environments (create/update by name; never pruned).

Environments are matched by ``name``. A declared environment that is absent is created;
one that exists with drift in a *declared* field (``wait_timer``, ``prevent_self_review``,
``reviewers``, ``deployment_branch_policy``) is updated. Only declared fields participate,
so a partial declaration never resets the others. Environments are not pruned — they can
hold secrets and deployment history.

The GET response nests these settings under ``protection_rules`` (one rule per type),
while the create/update body takes them flat; we normalise both to a comparable shape.
"""

from __future__ import annotations

from typing import Any

from githubkit import GitHub

from caldrith.config.schema import Environment, RepoScoped
from caldrith.github_json import response_json
from caldrith.reconcile.base import RepoTier, TierResult
from caldrith.reconcile.planner import TargetRepo


async def _list_environments(client: GitHub, target: TargetRepo) -> dict[str, dict[str, Any]]:
    """Name -> live environment object for the repo."""
    response = await client.rest.repos.async_get_all_environments(
        owner=target.owner, repo=target.name
    )
    body = response_json(response)
    return {env["name"]: env for env in (body.get("environments") or [])}


def _actual_wait_timer(env: dict[str, Any]) -> int | None:
    for rule in env.get("protection_rules") or []:
        if rule.get("type") == "wait_timer":
            return rule.get("wait_timer")
    return None


def _actual_reviewers(env: dict[str, Any]) -> list[tuple[str | None, int | None]]:
    for rule in env.get("protection_rules") or []:
        if rule.get("type") == "required_reviewers":
            return sorted(
                (rv.get("type"), (rv.get("reviewer") or {}).get("id"))
                for rv in (rule.get("reviewers") or [])
            )
    return []


def _actual_prevent_self_review(env: dict[str, Any]) -> bool | None:
    for rule in env.get("protection_rules") or []:
        if rule.get("type") == "required_reviewers":
            return rule.get("prevent_self_review")
    return None


def _desired_reviewers(env: Environment) -> list[tuple[str, int]]:
    return sorted((r.type, r.id) for r in (env.reviewers or []))


def _drifted(desired: Environment, live: dict[str, Any]) -> bool:
    if desired.wait_timer is not None and _actual_wait_timer(live) != desired.wait_timer:
        return True
    if (
        desired.prevent_self_review is not None
        and _actual_prevent_self_review(live) != desired.prevent_self_review
    ):
        return True
    if desired.reviewers is not None and _actual_reviewers(live) != _desired_reviewers(desired):
        return True
    return (
        desired.deployment_branch_policy is not None
        and live.get("deployment_branch_policy") != desired.deployment_branch_policy
    )


def _body(desired: Environment) -> dict[str, Any]:
    body: dict[str, Any] = {}
    if desired.wait_timer is not None:
        body["wait_timer"] = desired.wait_timer
    if desired.prevent_self_review is not None:
        body["prevent_self_review"] = desired.prevent_self_review
    if desired.reviewers is not None:
        body["reviewers"] = [{"type": r.type, "id": r.id} for r in desired.reviewers]
    if desired.deployment_branch_policy is not None:
        body["deployment_branch_policy"] = desired.deployment_branch_policy
    return body


async def reconcile(
    client: GitHub, target: TargetRepo, config: RepoScoped, *, dry_run: bool = False
) -> list[TierResult]:
    """Reconcile environments on ``target`` (create/update unless dry-run)."""
    if not config.environments:
        return []
    repos = client.rest.repos
    live = await _list_environments(client, target)
    result = TierResult(tier="environments", scope=target.full_name)

    for desired in config.environments:
        existing = live.get(desired.name)
        if existing is None:
            result.changed = True
            result.notes.append(f"create environment: {desired.name}")
        elif _drifted(desired, existing):
            result.changed = True
            result.notes.append(f"update environment: {desired.name}")
        else:
            continue
        if not dry_run:
            await repos.async_create_or_update_environment(
                owner=target.owner,
                repo=target.name,
                environment_name=desired.name,
                data=_body(desired),  # type: ignore[arg-type]
            )

    result.applied = result.changed and not dry_run
    return [result]


TIER = RepoTier(name="environments", configured=lambda c: bool(c.environments), reconcile=reconcile)
