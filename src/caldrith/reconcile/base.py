"""Uniform tier protocol shared by every repository-scoped reconciler.

Caldrith reconciles many independent *tiers* (the repository block, security toggles,
branch protection, labels, collaborators, environments, ...). Each tier owns a slice of
``settings.yml`` and a set of GitHub endpoints, but the orchestrator
(:mod:`caldrith.reconcile.runner`) only needs three things from every tier:

1. ``name`` — a stable identifier used in logs and the dry-run Check Run.
2. ``configured(config)`` — whether the admin config asks this tier to do anything (so
   we can skip listing repos / building clients when nothing is declared).
3. ``reconcile(client, target, config, *, dry_run)`` — do the work for one repo and
   return zero or more :class:`TierResult` rows.

Tiers keep their own typed appliers and result dataclasses (unit-tested in isolation);
the module-level ``reconcile`` adapter maps those onto the uniform :class:`TierResult`
the runner aggregates. This keeps the runner a flat loop over a registry instead of a
growing pile of per-tier branches, so adding a tier is one registry entry.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from githubkit import GitHub

from caldrith.config.schema import RepoScoped
from caldrith.reconcile.planner import TargetRepo


@dataclass
class TierResult:
    """The outcome of reconciling one tier against one target (repo or org).

    Attributes:
        tier: the tier ``name`` (e.g. ``"repository"``, ``"labels"``).
        scope: what was reconciled — a repo ``owner/name`` or an org login.
        changed: ``True`` if a drift was detected (whether or not it was applied).
        applied: ``True`` if a mutation was actually issued (always ``False`` in
            dry-run, and ``False`` when there was no change).
        notes: human-readable one-liners describing each change, rendered into the
            dry-run Check Run summary (e.g. ``"create label: bug"``).
    """

    tier: str
    scope: str
    changed: bool = False
    applied: bool = False
    notes: list[str] = field(default_factory=list)


# Signature of a tier's reconcile adapter. ``dry_run`` is keyword-only by convention.
# Tiers receive a :class:`RepoScoped` (the resolved, overlay-merged config for one repo);
# :class:`~caldrith.config.schema.SafeSettingsConfig` is a subtype, so the runner can pass
# the top-level document directly when there are no overlays.
ReconcileFn = Callable[[GitHub, TargetRepo, RepoScoped], Awaitable[list[TierResult]]]


@dataclass(frozen=True)
class RepoTier:
    """A registry entry: a repository-scoped tier the runner reconciles per repo.

    ``configured`` lets the runner short-circuit (skip repo enumeration entirely) when
    no tier is declared, and skip individual tiers whose slice is absent. ``reconcile``
    is the adapter that runs the tier's applier for one repo and returns
    :class:`TierResult` rows.
    """

    name: str
    configured: Callable[[RepoScoped], bool]
    reconcile: Callable[..., Awaitable[list[TierResult]]]
