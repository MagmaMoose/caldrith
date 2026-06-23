"""Apply the ``repository:`` block to a single repository.

Computes the diff between the desired :class:`RepositorySettings` and the repo's live
state, then either PATCHes the difference (``repos.update``) or, in dry-run, returns a
no-op result carrying the diff for the Check Run. Idempotent: the diff drives the
PATCH, so re-applying an already-converged repo issues no mutation.

P1 wires three fields end-to-end (``allow_auto_merge``, ``delete_branch_on_merge``,
``allow_update_branch``); any other *set* field on the model that maps to a
``repos.update`` parameter is also diffed and applied. Only fields explicitly set in
the admin config participate (``exclude_unset=True``), so unspecified settings are
left untouched.
"""

from __future__ import annotations

from dataclasses import dataclass

from githubkit import GitHub

from caldrith.config.diff import Diff, compare_deep
from caldrith.config.schema import RepoScoped, RepositorySettings
from caldrith.github_json import response_json
from caldrith.reconcile.base import RepoTier, TierResult
from caldrith.reconcile.planner import TargetRepo


@dataclass
class ApplyResult:
    """Outcome of reconciling one repository's repository block."""

    repo: str
    diff: Diff
    applied: bool

    @property
    def has_changes(self) -> bool:
        return self.diff.has_changes


class RepositoryApplier:
    """Reconciles a single repo's settings against the desired repository block."""

    def __init__(self, client: GitHub, *, dry_run: bool = False) -> None:
        self._client = client
        self._dry_run = dry_run

    async def apply(self, target: TargetRepo, desired: RepositorySettings) -> ApplyResult:
        """Diff ``desired`` against ``target``'s live settings and apply (unless dry-run).

        Returns an :class:`ApplyResult`; ``applied`` is ``False`` when there were no
        changes or when running in dry-run mode (a NopResult-style outcome).
        """
        # Only consider fields the admin config explicitly set.
        desired_dict = desired.model_dump(exclude_unset=True, exclude_none=True)
        # `security` and `topics` are reconciled via dedicated endpoints/tiers
        # (reconcile.security / reconcile.topics), not repos.update — drop them from
        # the PATCH body so they never reach `repos.async_update`.
        desired_dict.pop("security", None)
        desired_dict.pop("topics", None)
        if not desired_dict:
            return ApplyResult(repo=target.full_name, diff=Diff(), applied=False)

        live = await self._client.rest.repos.async_get(owner=target.owner, repo=target.name)
        actual = response_json(live)

        # Archived repos reject settings PATCHes (403/422); treat as a no-op so a
        # stray event on one (or one slipping past selection) never errors the job.
        if actual.get("archived"):
            return ApplyResult(repo=target.full_name, diff=Diff(), applied=False)

        diff = compare_deep(actual=actual, desired=desired_dict)

        if not diff.has_changes or self._dry_run:
            return ApplyResult(repo=target.full_name, diff=diff, applied=False)

        await self._client.rest.repos.async_update(
            owner=target.owner,
            repo=target.name,
            **diff.changed_payload(),
        )
        return ApplyResult(repo=target.full_name, diff=diff, applied=True)


def _patchable_fields(repository: RepositorySettings) -> dict[str, object]:
    """The set repository fields that the ``repos.update`` PATCH would act on.

    Excludes ``security`` and ``topics``, which have dedicated tiers/endpoints.
    """
    desired = repository.model_dump(exclude_unset=True, exclude_none=True)
    desired.pop("security", None)
    desired.pop("topics", None)
    return desired


def _configured(config: RepoScoped) -> bool:
    """True when the repository block declares at least one ``repos.update`` field."""
    return config.repository is not None and bool(_patchable_fields(config.repository))


async def reconcile(
    client: GitHub, target: TargetRepo, config: RepoScoped, *, dry_run: bool = False
) -> list[TierResult]:
    """Uniform adapter: reconcile the repository block for one repo."""
    if config.repository is None:
        return []
    result = await RepositoryApplier(client, dry_run=dry_run).apply(target, config.repository)
    notes = [f"{k} -> {v!r}" for k, v in result.diff.changed_payload().items()]
    return [
        TierResult(
            tier="repository",
            scope=result.repo,
            changed=result.has_changes,
            applied=result.applied,
            notes=notes,
        )
    ]


TIER = RepoTier(name="repository", configured=_configured, reconcile=reconcile)
