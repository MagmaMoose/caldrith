"""Reconcile repository autolink references (full-replace).

Autolinks have no update endpoint — only create and delete — so a changed entry is a
delete+recreate. The declared ``autolinks`` list is the COMPLETE set: a live autolink
whose ``(key_prefix, url_template, is_alphanumeric)`` triple is not declared is deleted,
and any declared triple missing from the repo is created. ``is_alphanumeric`` defaults to
GitHub's default (``True``) when unset, so comparison is exact and idempotent.
"""

from __future__ import annotations

from typing import Any

from githubkit import GitHub

from caldrith.config.schema import Autolink, RepoScoped
from caldrith.github_json import response_json
from caldrith.reconcile.base import RepoTier, TierResult
from caldrith.reconcile.planner import TargetRepo

# A comparable identity for an autolink: (key_prefix, url_template, is_alphanumeric).
_Triple = tuple[str, str, bool]


def _desired_triple(autolink: Autolink) -> _Triple:
    alpha = True if autolink.is_alphanumeric is None else autolink.is_alphanumeric
    return (autolink.key_prefix, autolink.url_template, alpha)


def _live_triple(autolink: dict[str, Any]) -> _Triple:
    return (
        autolink.get("key_prefix", ""),
        autolink.get("url_template", ""),
        bool(autolink.get("is_alphanumeric", True)),
    )


async def reconcile(
    client: GitHub, target: TargetRepo, config: RepoScoped, *, dry_run: bool = False
) -> list[TierResult]:
    """Reconcile autolinks on ``target`` (create/delete unless dry-run)."""
    if config.autolinks is None:
        return []
    repos = client.rest.repos
    live = (
        response_json(await repos.async_list_autolinks(owner=target.owner, repo=target.name)) or []
    )
    result = TierResult(tier="autolinks", scope=target.full_name)

    live_by_triple = {_live_triple(a): a for a in live}
    desired_triples = {_desired_triple(a): a for a in config.autolinks}

    for triple, autolink in live_by_triple.items():
        if triple not in desired_triples:
            result.changed = True
            result.notes.append(f"delete autolink: {triple[0]}")
            if not dry_run:
                await repos.async_delete_autolink(
                    owner=target.owner, repo=target.name, autolink_id=autolink["id"]
                )

    for triple, autolink in desired_triples.items():
        if triple not in live_by_triple:
            result.changed = True
            result.notes.append(f"create autolink: {autolink.key_prefix}")
            if not dry_run:
                body: dict[str, Any] = {
                    "key_prefix": autolink.key_prefix,
                    "url_template": autolink.url_template,
                    "is_alphanumeric": triple[2],
                }
                await repos.async_create_autolink(owner=target.owner, repo=target.name, data=body)

    result.applied = result.changed and not dry_run
    return [result]


TIER = RepoTier(name="autolinks", configured=lambda c: c.autolinks is not None, reconcile=reconcile)
