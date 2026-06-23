"""Reconcile repository topics.

Topics are not part of ``repos.update``; they have their own endpoint
(``GET``/``PUT /repos/{owner}/{repo}/topics``). The desired ``repository.topics`` list
is a full-replace: the live topic set is replaced with exactly the declared set, and the
comparison is order-insensitive so re-applying a converged repo issues no write.
"""

from __future__ import annotations

from githubkit import GitHub

from caldrith.config.schema import RepoScoped
from caldrith.github_json import response_json
from caldrith.reconcile.base import RepoTier, TierResult
from caldrith.reconcile.planner import TargetRepo


def _configured(config: RepoScoped) -> bool:
    return config.repository is not None and config.repository.topics is not None


async def reconcile(
    client: GitHub, target: TargetRepo, config: RepoScoped, *, dry_run: bool = False
) -> list[TierResult]:
    """Reconcile the repo's topic set against ``repository.topics`` (full-replace)."""
    if config.repository is None or config.repository.topics is None:
        return []
    desired = sorted(set(config.repository.topics))

    live = response_json(
        await client.rest.repos.async_get_all_topics(owner=target.owner, repo=target.name)
    )
    current = sorted(set(live.get("names") or []))

    result = TierResult(tier="topics", scope=target.full_name)
    if current == desired:
        return [result]

    result.changed = True
    result.notes = [f"topics -> {desired}"]
    if not dry_run:
        await client.rest.repos.async_replace_all_topics(
            owner=target.owner,
            repo=target.name,
            data={"names": desired},
        )
        result.applied = True
    return [result]


TIER = RepoTier(name="topics", configured=_configured, reconcile=reconcile)
