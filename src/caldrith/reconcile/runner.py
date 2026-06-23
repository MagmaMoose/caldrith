"""Top-level reconcile orchestration.

``run_reconcile`` loads the admin config, selects the target repos, and runs every
*configured* repository-scoped tier against each (or computes a dry-run diff). Tiers are
registered in :data:`REPO_TIERS`; the runner is a flat loop over that registry, so
adding a tier is one import + one list entry — no new branches here.

In dry-run mode it posts a GitHub Check Run summarizing the changes and mutates nothing
— this backs the ``pull_request`` webhook flow where a proposed settings change on a
non-default branch is previewed as a check.

Org-scoped settings (``orgs.update``, org rulesets, ...) are handled separately by
:func:`run_org_reconcile`, since they apply once per account rather than per repo.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from githubkit import GitHub

from caldrith.audit.logging import bind_context, get_logger
from caldrith.config.loader import load_admin_config
from caldrith.reconcile import (
    actions,
    autolinks,
    branch,
    collaborators,
    custom_properties,
    environments,
    files,
    interactions,
    labels,
    milestones,
    pages,
    repository,
    ruleset,
    secrets,
    security,
    teams,
    topics,
    variables,
)
from caldrith.reconcile.base import RepoTier, TierResult
from caldrith.reconcile.org import run_org_reconcile
from caldrith.reconcile.overlay import has_overlays, resolve_for_repo
from caldrith.reconcile.planner import TargetRepo, list_target_repos
from caldrith.reconcile.selection import select_targets
from caldrith.settings import AppConfig, get_config

_log = get_logger(__name__)

_CHECK_NAME = "caldrith/settings"

# Registry of repository-scoped tiers, applied in order per repo. Ordering matters where
# tiers interact: ``repository`` runs first (it can rename the default branch / flip
# features other tiers depend on), then everything else. Adding a tier = one entry here.
REPO_TIERS: list[RepoTier] = [
    repository.TIER,
    security.TIER,
    topics.TIER,
    labels.TIER,
    milestones.TIER,
    collaborators.TIER,
    teams.TIER,
    autolinks.TIER,
    custom_properties.TIER,
    interactions.TIER,
    actions.TIER,
    variables.TIER,
    secrets.TIER,
    environments.TIER,
    pages.TIER,
    ruleset.TIER,
    files.TIER,
    branch.TIER,
]


@dataclass
class ReconcileSummary:
    """Aggregate outcome of a reconcile run, across all tiers and targets."""

    installation_id: int
    owner: str
    dry_run: bool
    results: list[TierResult] = field(default_factory=list)

    @property
    def changed(self) -> list[TierResult]:
        """Tier results where drift was detected (applied or not)."""
        return [r for r in self.results if r.changed]

    @property
    def applied(self) -> list[TierResult]:
        """Tier results where a mutation was actually issued."""
        return [r for r in self.results if r.applied]

    @property
    def any_changed(self) -> bool:
        """True if any tier on any target detected a change."""
        return any(r.changed for r in self.results)


def _format_check_summary(results: list[TierResult]) -> str:
    """Render a Markdown summary of pending changes for a Check Run body."""
    changed = [r for r in results if r.changed]
    if not changed:
        return "No configuration changes. All repositories match the desired state."

    by_tier: dict[str, list[TierResult]] = {}
    for result in changed:
        by_tier.setdefault(result.tier, []).append(result)

    lines: list[str] = []
    for tier in sorted(by_tier):
        rows = by_tier[tier]
        lines.append(f"### `{tier}` — {len(rows)} change(s)")
        lines.append("")
        for result in rows:
            notes = "; ".join(result.notes) if result.notes else "(changed)"
            lines.append(f"- `{result.scope}`: {notes}")
        lines.append("")
    return "\n".join(lines).rstrip()


async def _post_check_run(
    client: GitHub, owner: str, head_sha: str, results: list[TierResult]
) -> None:
    """Post a dry-run Check Run to the admin repo summarizing the diff.

    The check targets the admin repo (where the settings file lives); ``head_sha`` is
    the PR head commit. The conclusion is always ``neutral`` — a dry-run never fails a
    PR, it only surfaces what *would* change.
    """
    config = get_config()
    summary = _format_check_summary(results)
    await client.rest.checks.async_create(
        owner=owner,
        repo=config.admin_repo,
        data={
            "name": _CHECK_NAME,
            "head_sha": head_sha,
            "status": "completed",
            "conclusion": "neutral",
            "output": {
                "title": "Caldrith settings dry-run",
                "summary": summary,
            },
        },
    )


async def run_reconcile(
    client: GitHub,
    installation_id: int,
    owner: str,
    *,
    repos: list[str] | None = None,
    dry_run: bool = False,
    head_sha: str | None = None,
    config: AppConfig | None = None,
) -> ReconcileSummary:
    """Reconcile ``owner``'s repositories against the admin config.

    Runs every configured tier in :data:`REPO_TIERS` against each target. When ``repos``
    is given (a single repo from a ``repository`` webhook) only those repos are targeted;
    otherwise every accessible, non-excluded repo is. In ``dry_run`` mode no mutations are
    issued and (with ``head_sha``) a Check Run is posted. Per-tier, per-repo failures are
    isolated and logged so one bad repo never aborts the run.
    """
    cfg = config or get_config()
    log = bind_context(_log, installation_id=installation_id)

    ref = head_sha if dry_run else None
    settings_config = await load_admin_config(
        client,
        owner=owner,
        admin_repo=cfg.admin_repo,
        config_path=cfg.config_path,
        settings_file=cfg.settings_file_path,
        ref=ref,
    )

    overlays = has_overlays(settings_config)
    base_configured = any(tier.configured(settings_config) for tier in REPO_TIERS)
    if not base_configured and not overlays:
        log.info("reconcile.nothing_to_do", owner=owner, dry_run=dry_run)
        return ReconcileSummary(installation_id, owner, dry_run, [])

    if repos is not None:
        targets = [TargetRepo(owner=owner, name=name) for name in repos]
    else:
        targets = await list_target_repos(client)

    targets = select_targets(
        targets, admin_repo=cfg.admin_repo, restricted=settings_config.restricted_repos
    )

    results: list[TierResult] = []
    for target in targets:
        # Resolve overlays (suborgs / per-repo overrides) to the effective config for
        # THIS repo; without overlays every repo shares the base config unchanged.
        effective = (
            resolve_for_repo(settings_config, target.name, target.visibility)
            if overlays
            else settings_config
        )
        for tier in REPO_TIERS:
            if not tier.configured(effective):
                continue
            try:
                tier_results = await tier.reconcile(client, target, effective, dry_run=dry_run)
            except Exception as exc:  # isolate per-tier, per-repo failures
                bind_context(log, repo=target.full_name).warning(
                    "reconcile.tier.failed", tier=tier.name, error=str(exc), dry_run=dry_run
                )
                continue
            for result in tier_results:
                results.append(result)
                if result.changed:
                    bind_context(log, repo=result.scope).info(
                        "reconcile.tier",
                        tier=result.tier,
                        changed=result.changed,
                        applied=result.applied,
                        dry_run=dry_run,
                    )

    if dry_run and head_sha is not None:
        await _post_check_run(client, owner, head_sha, results)

    return ReconcileSummary(installation_id, owner, dry_run, results)


__all__ = ["REPO_TIERS", "ReconcileSummary", "run_org_reconcile", "run_reconcile"]
