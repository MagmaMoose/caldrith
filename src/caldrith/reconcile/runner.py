"""Top-level reconcile orchestration.

``run_reconcile`` loads the admin config, determines the target repos, and applies the
repository block to each (or computes a dry-run diff). In dry-run mode it posts a
GitHub Check Run summarizing the diff and mutates nothing — this backs the
``pull_request`` webhook flow where a proposed settings change on a non-default branch
is previewed as a check.
"""

from __future__ import annotations

from dataclasses import dataclass

from githubkit import GitHub

from caldrith.audit.logging import bind_context, get_logger
from caldrith.config.loader import load_admin_config
from caldrith.config.schema import RepositorySettings
from caldrith.reconcile.planner import TargetRepo, list_target_repos
from caldrith.reconcile.repository import ApplyResult, RepositoryApplier
from caldrith.reconcile.selection import select_targets
from caldrith.settings import AppConfig, get_config

_log = get_logger(__name__)

_CHECK_NAME = "caldrith/settings"


@dataclass
class ReconcileSummary:
    """Aggregate outcome of a reconcile run."""

    installation_id: int
    owner: str
    dry_run: bool
    results: list[ApplyResult]

    @property
    def changed(self) -> list[ApplyResult]:
        return [r for r in self.results if r.has_changes]

    @property
    def applied(self) -> list[ApplyResult]:
        return [r for r in self.results if r.applied]


def _format_check_summary(results: list[ApplyResult]) -> str:
    """Render a Markdown summary of pending changes for a Check Run body."""
    changed = [r for r in results if r.has_changes]
    if not changed:
        return "No configuration changes. All repositories match the desired state."

    lines = [f"Caldrith would apply changes to {len(changed)} repositor(y/ies):", ""]
    for result in changed:
        lines.append(f"### `{result.repo}`")
        payload = result.diff.changed_payload()
        for key in sorted(payload):
            lines.append(f"- `{key}` -> `{payload[key]!r}`")
        lines.append("")
    return "\n".join(lines).rstrip()


async def _post_check_run(
    client: GitHub,
    owner: str,
    head_sha: str,
    results: list[ApplyResult],
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

    Args:
        client: A per-installation githubkit client.
        installation_id: The installation being reconciled (for logging/context).
        owner: The account login that owns the admin repo and target repos.
        repos: Optional explicit repo names to reconcile (e.g. a single repo from a
            ``repository`` webhook). When ``None``, all accessible repos are targeted.
        dry_run: When ``True``, compute diffs and (if ``head_sha`` given) post a Check
            Run, but issue no mutations.
        head_sha: PR head commit SHA; required to post the dry-run Check Run.
        config: Optional config override (defaults to the process config).
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

    desired_repository: RepositorySettings | None = settings_config.repository
    results: list[ApplyResult] = []

    if desired_repository is None:
        log.info("reconcile.no_repository_block", owner=owner, dry_run=dry_run)
        return ReconcileSummary(installation_id, owner, dry_run, results)

    if repos is not None:
        targets = [TargetRepo(owner=owner, name=name) for name in repos]
    else:
        targets = await list_target_repos(client)

    # Drop repos this installation must not manage (admin/.github + restrictedRepos),
    # so even a direct event on an excluded repo is a no-op.
    targets = select_targets(
        targets, admin_repo=cfg.admin_repo, restricted=settings_config.restricted_repos
    )

    applier = RepositoryApplier(client, dry_run=dry_run)
    for target in targets:
        result = await applier.apply(target, desired_repository)
        results.append(result)
        bind_context(log, repo=result.repo).info(
            "reconcile.repo",
            applied=result.applied,
            has_changes=result.has_changes,
            dry_run=dry_run,
        )

    if dry_run and head_sha is not None:
        await _post_check_run(client, owner, head_sha, results)

    return ReconcileSummary(installation_id, owner, dry_run, results)
