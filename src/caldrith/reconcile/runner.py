"""Top-level reconcile orchestration.

``run_reconcile`` loads the admin config, selects the target repos, and applies the
``repository`` block, ``repository.security`` toggles, ``rulesets``, provisioned
``files`` (via PR), and any ``branches`` protection to each (or computes a dry-run
diff). In dry-run mode it posts a GitHub Check Run summarizing the changes and mutates
nothing — this backs the ``pull_request`` webhook flow where a proposed settings change
on a non-default branch is previewed as a check.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from githubkit import GitHub

from caldrith.audit.logging import bind_context, get_logger
from caldrith.config.loader import load_admin_config
from caldrith.config.schema import RepositorySettings
from caldrith.reconcile.branch import BranchProtectionApplier, BranchResult
from caldrith.reconcile.files import FileProvisioner, FilesResult
from caldrith.reconcile.planner import TargetRepo, list_target_repos
from caldrith.reconcile.repository import ApplyResult, RepositoryApplier
from caldrith.reconcile.ruleset import RulesetApplier, RulesetResult
from caldrith.reconcile.security import RepositorySecurityApplier, SecurityResult
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
    branch_results: list[BranchResult] = field(default_factory=list)
    security_results: list[SecurityResult] = field(default_factory=list)
    ruleset_results: list[RulesetResult] = field(default_factory=list)
    files_results: list[FilesResult] = field(default_factory=list)

    @property
    def changed(self) -> list[ApplyResult]:
        return [r for r in self.results if r.has_changes]

    @property
    def applied(self) -> list[ApplyResult]:
        return [r for r in self.results if r.applied]

    @property
    def any_changed(self) -> bool:
        """True if any repo, security, ruleset, file, or branch change was detected."""
        return (
            bool(self.changed)
            or any(b.changed for b in self.branch_results)
            or any(s.changed for s in self.security_results)
            or any(r.changed for r in self.ruleset_results)
            or any(f.changed for f in self.files_results)
        )


def _format_check_summary(
    results: list[ApplyResult],
    branch_results: list[BranchResult],
    security_results: list[SecurityResult],
    ruleset_results: list[RulesetResult],
    files_results: list[FilesResult],
) -> str:
    """Render a Markdown summary of pending changes for a Check Run body."""
    changed = [r for r in results if r.has_changes]
    changed_branches = [b for b in branch_results if b.changed]
    changed_security = [s for s in security_results if s.changed]
    changed_rulesets = [r for r in ruleset_results if r.changed]
    changed_files = [f for f in files_results if f.changed]
    if not any((changed, changed_branches, changed_security, changed_rulesets, changed_files)):
        return "No configuration changes. All repositories match the desired state."

    lines: list[str] = []
    if changed:
        lines.append(f"Caldrith would apply repository changes to {len(changed)} repositor(y/ies):")
        lines.append("")
        for result in changed:
            lines.append(f"### `{result.repo}`")
            payload = result.diff.changed_payload()
            for key in sorted(payload):
                lines.append(f"- `{key}` -> `{payload[key]!r}`")
            lines.append("")
    if changed_security:
        lines.append(f"Security toggles on {len(changed_security)} repositor(y/ies):")
        lines.append("")
        for security in changed_security:
            lines.append(f"- `{security.repo}`: {', '.join(security.changed_fields)}")
        lines.append("")
    if changed_rulesets:
        lines.append(f"Ruleset changes on {len(changed_rulesets)} repositor(y/ies):")
        lines.append("")
        for ruleset in changed_rulesets:
            lines.append(f"- `{ruleset.repo}`: {', '.join(ruleset.changed_fields)}")
        lines.append("")
    if changed_files:
        lines.append(f"Workflow/file provisioning on {len(changed_files)} repositor(y/ies):")
        lines.append("")
        for provisioned in changed_files:
            lines.append(f"- `{provisioned.repo}`: {', '.join(provisioned.files)}")
        lines.append("")
    if changed_branches:
        lines.append(f"Branch protection changes on {len(changed_branches)} branch(es):")
        lines.append("")
        for branch in changed_branches:
            lines.append(f"- `{branch.repo}` @ `{branch.branch}` ({branch.action})")
        lines.append("")
    return "\n".join(lines).rstrip()


async def _post_check_run(
    client: GitHub,
    owner: str,
    head_sha: str,
    results: list[ApplyResult],
    branch_results: list[BranchResult],
    security_results: list[SecurityResult],
    ruleset_results: list[RulesetResult],
    files_results: list[FilesResult],
) -> None:
    """Post a dry-run Check Run to the admin repo summarizing the diff.

    The check targets the admin repo (where the settings file lives); ``head_sha`` is
    the PR head commit. The conclusion is always ``neutral`` — a dry-run never fails a
    PR, it only surfaces what *would* change.
    """
    config = get_config()
    summary = _format_check_summary(
        results, branch_results, security_results, ruleset_results, files_results
    )
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

    Applies the ``repository`` block, ``repository.security`` toggles, ``rulesets``,
    provisioned ``files``, and any ``branches`` protection. When ``repos`` is given (a
    single repo from a ``repository`` webhook) only those repos are targeted; otherwise
    every accessible, non-excluded repo is. In ``dry_run`` mode no mutations are issued
    and (with ``head_sha``) a Check Run is posted.
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
    branches = settings_config.branches or []
    rulesets = settings_config.rulesets or []
    files = settings_config.files or []
    desired_security = desired_repository.security if desired_repository else None
    results: list[ApplyResult] = []
    branch_results: list[BranchResult] = []
    security_results: list[SecurityResult] = []
    ruleset_results: list[RulesetResult] = []
    files_results: list[FilesResult] = []

    if desired_repository is None and not branches and not rulesets and not files:
        log.info("reconcile.nothing_to_do", owner=owner, dry_run=dry_run)
        return ReconcileSummary(installation_id, owner, dry_run, results)

    if repos is not None:
        targets = [TargetRepo(owner=owner, name=name) for name in repos]
    else:
        targets = await list_target_repos(client)

    targets = select_targets(
        targets, admin_repo=cfg.admin_repo, restricted=settings_config.restricted_repos
    )

    repo_applier = RepositoryApplier(client, dry_run=dry_run)
    security_applier = RepositorySecurityApplier(client, dry_run=dry_run)
    ruleset_applier = RulesetApplier(client, dry_run=dry_run)
    file_provisioner = FileProvisioner(client, dry_run=dry_run)
    branch_applier = BranchProtectionApplier(client, dry_run=dry_run)
    for target in targets:
        if desired_repository is not None:
            result = await repo_applier.apply(target, desired_repository)
            results.append(result)
            bind_context(log, repo=result.repo).info(
                "reconcile.repo",
                applied=result.applied,
                has_changes=result.has_changes,
                dry_run=dry_run,
            )
        if desired_security is not None:
            security_result = await security_applier.apply(target, desired_security)
            security_results.append(security_result)
            bind_context(log, repo=security_result.repo).info(
                "reconcile.security",
                changed_fields=security_result.changed_fields,
                applied=security_result.applied,
                dry_run=dry_run,
            )
        if rulesets:
            try:
                ruleset_result = await ruleset_applier.apply(target, rulesets)
            except Exception as exc:  # isolate per-repo ruleset failures
                bind_context(log, repo=target.full_name).warning(
                    "reconcile.ruleset.failed", error=str(exc), dry_run=dry_run
                )
            else:
                ruleset_results.append(ruleset_result)
                bind_context(log, repo=ruleset_result.repo).info(
                    "reconcile.ruleset",
                    changed_fields=ruleset_result.changed_fields,
                    applied=ruleset_result.applied,
                    dry_run=dry_run,
                )
        if files:
            try:
                files_result = await file_provisioner.apply(target, files)
            except Exception as exc:  # isolate per-repo provisioning failures
                bind_context(log, repo=target.full_name).warning(
                    "reconcile.files.failed", error=str(exc), dry_run=dry_run
                )
            else:
                files_results.append(files_result)
                bind_context(log, repo=files_result.repo).info(
                    "reconcile.files",
                    files=files_result.files,
                    pr_url=files_result.pr_url,
                    applied=files_result.applied,
                    dry_run=dry_run,
                )
        for branch_cfg in branches:
            try:
                branch_result = await branch_applier.apply(target, branch_cfg)
            except Exception as exc:
                bind_context(log, repo=target.full_name, branch=branch_cfg.name).warning(
                    "reconcile.branch.failed", error=str(exc), dry_run=dry_run
                )
                continue
            branch_results.append(branch_result)
            bind_context(log, repo=branch_result.repo, branch=branch_result.branch).info(
                "reconcile.branch",
                action=branch_result.action,
                changed=branch_result.changed,
                applied=branch_result.applied,
                dry_run=dry_run,
            )

    if dry_run and head_sha is not None:
        await _post_check_run(
            client,
            owner,
            head_sha,
            results,
            branch_results,
            security_results,
            ruleset_results,
            files_results,
        )

    return ReconcileSummary(
        installation_id,
        owner,
        dry_run,
        results,
        branch_results,
        security_results,
        ruleset_results,
        files_results,
    )
