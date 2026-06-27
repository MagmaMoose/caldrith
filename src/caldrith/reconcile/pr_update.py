"""Re-base open admin-repo PRs after a settings change on the default branch.

When the admin repo's settings file is modified on the default branch, every open PR is
typically a *proposed* config change whose dry-run Check Run diffs the proposal against
the live settings. Once the baseline moves, those branches go stale: the preview (and
any required status check) reflects an out-of-date base. This walks the admin repo's
open PRs and, for each branch that is *behind* its base, calls GitHub's "Update branch"
endpoint (a base->head merge) so the change is re-tested against the current settings.

Idempotent and tolerant by design: a PR already up to date is skipped (no merge issued),
and a branch that cannot be updated automatically (a merge conflict, or a fork head the
App cannot push to) is recorded and skipped rather than failing the whole sweep — the
next push re-tries it. Mirrors GitHub's own "Update branch" button, applied in bulk.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from githubkit import GitHub
from githubkit.exception import RequestFailed

from caldrith.audit.logging import bind_context, get_logger
from caldrith.github_json import response_json

_log = get_logger(__name__)


@dataclass
class UpdateSummary:
    """Outcome of an open-PR update sweep over a single repository."""

    repo: str
    updated: list[int] = field(default_factory=list)
    up_to_date: list[int] = field(default_factory=list)
    skipped: list[int] = field(default_factory=list)

    @property
    def any_updated(self) -> bool:
        """True when at least one PR branch was merged forward."""
        return bool(self.updated)


async def _list_open_prs(client: GitHub[Any], owner: str, repo: str) -> list[dict[str, Any]]:
    """Every open PR on ``repo`` (paginated)."""
    out: list[dict[str, Any]] = []
    page = 1
    while True:
        batch = response_json(
            await client.rest.pulls.async_list(
                owner=owner, repo=repo, state="open", per_page=100, page=page
            )
        )
        out.extend(batch)
        if len(batch) < 100:
            break
        page += 1
    return out


async def _is_behind(client: GitHub[Any], owner: str, repo: str, pr: dict[str, Any]) -> bool:
    """True when the PR's head branch is behind its base (missing base commits).

    Uses the commit-comparison endpoint: ``behind_by`` counts commits present on the
    base but not on the head, i.e. exactly what an "Update branch" merge would pull in.
    """
    base = (pr.get("base") or {}).get("ref")
    head_label = (pr.get("head") or {}).get("label") or (pr.get("head") or {}).get("ref")
    if not base or not head_label:
        return False
    comparison = response_json(
        await client.rest.repos.async_compare_commits(
            owner=owner, repo=repo, basehead=f"{base}...{head_label}"
        )
    )
    return int(comparison.get("behind_by") or 0) > 0


async def update_open_prs(client: GitHub[Any], *, owner: str, repo: str) -> UpdateSummary:
    """Update every open PR on ``repo`` whose branch is behind its base.

    Returns an :class:`UpdateSummary` partitioning the open PRs into updated / already
    up to date / skipped (conflict or un-pushable head). Never raises for a single PR
    that cannot be updated — that PR is recorded as skipped and the sweep continues.
    """
    log = bind_context(_log, repo=f"{owner}/{repo}")
    summary = UpdateSummary(repo=repo)

    for pr in await _list_open_prs(client, owner, repo):
        number = pr.get("number")
        if number is None:
            continue
        if not await _is_behind(client, owner, repo, pr):
            summary.up_to_date.append(number)
            continue
        try:
            await client.rest.pulls.async_update_branch(owner=owner, repo=repo, pull_number=number)
        except RequestFailed as exc:
            # 422 (merge conflict / not fast-forwardable) or 403 (fork head we cannot
            # push to): record and move on so one bad PR never blocks the rest.
            summary.skipped.append(number)
            log.info("pr_update.skipped", pr=number, status=exc.response.status_code)
            continue
        summary.updated.append(number)

    log.info(
        "pr_update.swept",
        updated=len(summary.updated),
        up_to_date=len(summary.up_to_date),
        skipped=len(summary.skipped),
    )
    return summary
