"""Reconcile repository milestones (create/update by title; never pruned).

Milestones are matched by ``title``. A declared milestone that is absent is created;
one that exists with a drifted ``state`` / ``description`` / ``due_on`` is updated.
Undeclared milestones are left alone — pruning would orphan issues assigned to them.
"""

from __future__ import annotations

from typing import Any

from githubkit import GitHub

from caldrith.config.schema import Milestone, RepoScoped
from caldrith.github_json import response_json
from caldrith.reconcile.base import RepoTier, TierResult
from caldrith.reconcile.planner import TargetRepo


async def _list_milestones(client: GitHub, target: TargetRepo) -> dict[str, dict[str, Any]]:
    """Title -> live milestone for every milestone on the repo (paginated, all states)."""
    out: dict[str, dict[str, Any]] = {}
    page = 1
    while True:
        response = await client.rest.issues.async_list_milestones(
            owner=target.owner, repo=target.name, state="all", per_page=100, page=page
        )
        batch = response_json(response) or []
        for milestone in batch:
            out[milestone["title"]] = milestone
        if len(batch) < 100:
            break
        page += 1
    return out


def _body(desired: Milestone) -> dict[str, Any]:
    body: dict[str, Any] = {"title": desired.title}
    if desired.state is not None:
        body["state"] = desired.state
    if desired.description is not None:
        body["description"] = desired.description
    if desired.due_on is not None:
        body["due_on"] = desired.due_on
    return body


def _due_date(value: str | None) -> str | None:
    """The ``YYYY-MM-DD`` date portion of an ISO-8601 date/datetime (or ``None``).

    GitHub snaps a milestone ``due_on`` to a fixed time-of-day and echoes it back as a full
    timestamp — send ``2024-01-01`` and the API returns ``2024-01-01T08:00:00Z``. Comparing
    the raw strings would report drift on every reconcile and re-``PATCH`` forever, so we
    compare only the date, which is all a milestone due date actually carries.
    """
    return value[:10] if value else None


def _drifted(desired: Milestone, live: dict[str, Any]) -> bool:
    if desired.state is not None and desired.state != live.get("state"):
        return True
    if desired.description is not None and desired.description != (live.get("description") or ""):
        return True
    return desired.due_on is not None and _due_date(desired.due_on) != _due_date(live.get("due_on"))


async def reconcile(
    client: GitHub, target: TargetRepo, config: RepoScoped, *, dry_run: bool = False
) -> list[TierResult]:
    """Reconcile milestones on ``target`` (create/update unless dry-run)."""
    if not config.milestones:
        return []
    issues = client.rest.issues
    live = await _list_milestones(client, target)
    result = TierResult(tier="milestones", scope=target.full_name)

    for desired in config.milestones:
        existing = live.get(desired.title)
        if existing is None:
            result.changed = True
            result.notes.append(f"create milestone: {desired.title}")
            if not dry_run:
                await issues.async_create_milestone(
                    owner=target.owner, repo=target.name, data=_body(desired)
                )
        elif _drifted(desired, existing):
            result.changed = True
            result.notes.append(f"update milestone: {desired.title}")
            if not dry_run:
                await issues.async_update_milestone(
                    owner=target.owner,
                    repo=target.name,
                    milestone_number=existing["number"],
                    data=_body(desired),
                )

    result.applied = result.changed and not dry_run
    return [result]


TIER = RepoTier(name="milestones", configured=lambda c: bool(c.milestones), reconcile=reconcile)
