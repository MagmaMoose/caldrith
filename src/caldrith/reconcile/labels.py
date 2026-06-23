"""Reconcile repository issue labels (full-replace).

The declared ``labels`` list is the COMPLETE desired label set: missing labels are
created, drifted labels (color/description) are updated, and labels that are not declared
are **pruned**. ``oldname`` renames an existing label in place (preserving its issue
associations) rather than delete+create.

Idempotent: a label whose colour and description already match issues no write. Colours
are normalised (leading ``#`` stripped, lower-cased) before comparison, since the API
returns them without the ``#``.
"""

from __future__ import annotations

from typing import Any

from githubkit import GitHub

from caldrith.config.schema import Label, RepoScoped
from caldrith.github_json import response_json
from caldrith.reconcile.base import RepoTier, TierResult
from caldrith.reconcile.planner import TargetRepo


def _norm_color(color: str | None) -> str | None:
    return color.lstrip("#").lower() if color is not None else None


async def _list_labels(client: GitHub, target: TargetRepo) -> dict[str, dict[str, Any]]:
    """Name -> live label object for every label on the repo (paginated)."""
    out: dict[str, dict[str, Any]] = {}
    page = 1
    while True:
        response = await client.rest.issues.async_list_labels_for_repo(
            owner=target.owner, repo=target.name, per_page=100, page=page
        )
        batch = response_json(response) or []
        for label in batch:
            out[label["name"]] = label
        if len(batch) < 100:
            break
        page += 1
    return out


def _drifted(desired: Label, live: dict[str, Any]) -> bool:
    """True if the live label's colour or description differs from desired."""
    if desired.color is not None and _norm_color(desired.color) != _norm_color(live.get("color")):
        return True
    return desired.description is not None and desired.description != (
        live.get("description") or ""
    )


async def reconcile(
    client: GitHub, target: TargetRepo, config: RepoScoped, *, dry_run: bool = False
) -> list[TierResult]:
    """Reconcile labels on ``target`` (create/rename/update/prune unless dry-run)."""
    if config.labels is None:
        return []
    issues = client.rest.issues
    live = await _list_labels(client, target)
    result = TierResult(tier="labels", scope=target.full_name)

    # Names we will keep (declared targets + sources we rename from) — the rest get pruned.
    keep: set[str] = set()

    for desired in config.labels:
        keep.add(desired.name)
        body: dict[str, Any] = {"name": desired.name}
        if desired.color is not None:
            body["color"] = _norm_color(desired.color)
        if desired.description is not None:
            body["description"] = desired.description

        if desired.oldname and desired.oldname in live and desired.oldname != desired.name:
            keep.add(desired.oldname)
            result.changed = True
            result.notes.append(f"rename label: {desired.oldname} -> {desired.name}")
            if not dry_run:
                await issues.async_update_label(
                    owner=target.owner,
                    repo=target.name,
                    name=desired.oldname,
                    data={
                        "new_name": desired.name,
                        **{k: v for k, v in body.items() if k != "name"},
                    },
                )
        elif desired.name in live:
            if _drifted(desired, live[desired.name]):
                result.changed = True
                result.notes.append(f"update label: {desired.name}")
                if not dry_run:
                    await issues.async_update_label(
                        owner=target.owner,
                        repo=target.name,
                        name=desired.name,
                        data={k: v for k, v in body.items() if k != "name"},
                    )
        else:
            result.changed = True
            result.notes.append(f"create label: {desired.name}")
            if not dry_run:
                await issues.async_create_label(owner=target.owner, repo=target.name, data=body)

    for name in live:
        if name not in keep:
            result.changed = True
            result.notes.append(f"delete label: {name}")
            if not dry_run:
                await issues.async_delete_label(owner=target.owner, repo=target.name, name=name)

    result.applied = result.changed and not dry_run
    return [result]


TIER = RepoTier(name="labels", configured=lambda c: c.labels is not None, reconcile=reconcile)
