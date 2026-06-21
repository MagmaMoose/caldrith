"""Pydantic v2 models mirroring safe-settings' ``settings.yml`` keys.

These models are deliberately compatible with existing ``github/safe-settings``
configs: parse YAML with ``yaml.safe_load`` then validate into
:class:`SafeSettingsConfig`. This module is pure (no I/O) and unit-tested.

P1 scope: only the ``repository:`` block flows through reconcile, and within it only
three fields are *required* end-to-end (``allow_auto_merge``,
``delete_branch_on_merge``, ``allow_update_branch``). Other common repository fields
are accepted as optional so real-world configs validate, but the applier only acts on
fields that are explicitly set.

DEFERRED SEAMS (intentionally stubs — do not implement here): branch protection,
rulesets, labels, teams, collaborators, environments, custom properties, and the
suborg/repo overlay tiers. They are declared as permissive ``Any`` holders below so
existing configs validate and the slots exist, but nothing consumes them yet.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


class RepositorySecurity(BaseModel):
    """``repository.security`` — Dependabot + private vulnerability reporting toggles.

    Applied via dedicated GitHub endpoints (not ``repos.update``); see
    :mod:`caldrith.reconcile.security`. Field names mirror github/safe-settings.
    """

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    enable_vulnerability_alerts: bool | None = Field(
        default=None, alias="enableVulnerabilityAlerts"
    )
    enable_automated_security_fixes: bool | None = Field(
        default=None, alias="enableAutomatedSecurityFixes"
    )
    enable_private_vulnerability_reporting: bool | None = Field(
        default=None, alias="enablePrivateVulnerabilityReporting"
    )


class RepositorySettings(BaseModel):
    """The ``repository:`` block of ``settings.yml``.

    Only fields that are explicitly set are reconciled (``model_dump(exclude_unset=
    True)``), so a config that omits a field leaves the live value untouched. The
    three P1 fields are ordinary optionals here — "required end-to-end" means they
    are the ones wired through the applier, not that the YAML must set them.
    """

    # Extra keys are forbidden so typos in the admin config surface as validation
    # errors rather than silently no-op'ing.
    model_config = ConfigDict(extra="forbid")

    # --- P1: the three fields wired end-to-end through reconcile ---
    allow_auto_merge: bool | None = None
    delete_branch_on_merge: bool | None = None
    allow_update_branch: bool | None = None

    # --- Common optional repository fields (accepted; safe-settings compatible) ---
    # These validate so existing configs load; the P1 applier only diffs the set
    # fields it knows how to PATCH. Additional fields slot in without schema churn.
    name: str | None = None
    description: str | None = None
    homepage: str | None = None
    private: bool | None = None
    visibility: str | None = None
    has_issues: bool | None = None
    has_projects: bool | None = None
    has_wiki: bool | None = None
    has_downloads: bool | None = None
    default_branch: str | None = None
    allow_squash_merge: bool | None = None
    allow_merge_commit: bool | None = None
    allow_rebase_merge: bool | None = None
    allow_forking: bool | None = None
    web_commit_signoff_required: bool | None = None
    archived: bool | None = None
    is_template: bool | None = None
    topics: list[str] | None = None

    # Repository security toggles (Dependabot + private vuln reporting). Applied via
    # dedicated endpoints, NOT repos.update — see reconcile.security. Held here for
    # safe-settings compatibility (the block nests under `repository:`).
    security: RepositorySecurity | None = None


class RestrictedRepos(BaseModel):
    """Object form of ``restrictedRepos`` — glob-based repo selection.

    ``include`` is an allowlist (when set, only repos matching it are managed);
    ``exclude`` skips repos matching it. Patterns are minimatch-style globs.
    """

    model_config = ConfigDict(extra="forbid")

    include: list[str] | None = None
    exclude: list[str] | None = None


class RequiredStatusChecks(BaseModel):
    """``required_status_checks`` — required CI contexts and strict (up-to-date) mode."""

    model_config = ConfigDict(extra="forbid")

    strict: bool | None = None
    contexts: list[str] | None = None

    @model_validator(mode="after")
    def _reject_empty(self) -> RequiredStatusChecks:
        # An explicitly-present-but-empty block (``required_status_checks: {}``) would
        # otherwise canonicalise to None and silently read as "no required checks",
        # which is almost certainly not what the author meant. Force them to declare
        # at least one field — or omit the block entirely.
        if all(getattr(self, f) is None for f in type(self).model_fields):
            raise ValueError(
                "required_status_checks must set at least one field "
                "(strict, contexts) — omit the block to mean 'no required checks'."
            )
        return self


class RequiredPullRequestReviews(BaseModel):
    """``required_pull_request_reviews`` — review requirements before merge."""

    model_config = ConfigDict(extra="forbid")

    dismiss_stale_reviews: bool | None = None
    require_code_owner_reviews: bool | None = None
    required_approving_review_count: int | None = None
    require_last_push_approval: bool | None = None

    @model_validator(mode="after")
    def _reject_empty(self) -> RequiredPullRequestReviews:
        # See RequiredStatusChecks._reject_empty — same foot-gun.
        if all(getattr(self, f) is None for f in type(self).model_fields):
            raise ValueError(
                "required_pull_request_reviews must set at least one field "
                "(dismiss_stale_reviews, require_code_owner_reviews, "
                "required_approving_review_count, require_last_push_approval) — "
                "omit the block to mean 'no required reviews'."
            )
        return self


class BranchProtection(BaseModel):
    """Desired protection for a branch (the safe-settings ``protection:`` block).

    Declarative + full-replace: the block is the COMPLETE desired protection, so
    omitted fields fall back to GitHub's "off" defaults (no required reviews,
    ``enforce_admins=false``, force-pushes allowed, ...). Only the fields below are
    supported; ``restrictions`` (push restrictions) and ``required_signatures``
    (a separate endpoint) are deferred and rejected (``extra="forbid"``) so they
    never silently no-op.
    """

    model_config = ConfigDict(extra="forbid")

    required_status_checks: RequiredStatusChecks | None = None
    enforce_admins: bool | None = None
    required_pull_request_reviews: RequiredPullRequestReviews | None = None
    required_linear_history: bool | None = None
    allow_force_pushes: bool | None = None
    allow_deletions: bool | None = None
    required_conversation_resolution: bool | None = None


class BranchConfig(BaseModel):
    """A ``branches:`` entry: a branch ``name`` (or ``default``) and its ``protection``.

    ``protection: null`` (or omitted) removes branch protection from the branch.
    """

    model_config = ConfigDict(extra="forbid")

    name: str
    protection: BranchProtection | None = None


class RulesetBypassActor(BaseModel):
    """An actor allowed to bypass a ruleset (role, team, app, or org admin)."""

    model_config = ConfigDict(extra="forbid")

    actor_id: int | None = None
    actor_type: str  # RepositoryRole | Team | Integration | OrganizationAdmin | DeployKey
    bypass_mode: str = "always"  # always | pull_request


class Ruleset(BaseModel):
    """A repository ruleset Caldrith reconciles onto each managed repo.

    ``conditions`` and ``rules`` are passed through to the GitHub rulesets API as-is
    (their schemas are large and evolving); ``bypass_actors`` is typed. Reconciliation
    is by ``name``: create if absent, update on drift. Idempotency uses a subset match
    so GitHub's server-added defaults / echo fields don't trigger re-writes.
    """

    model_config = ConfigDict(extra="forbid")

    name: str
    target: str = "branch"  # branch | tag | push
    enforcement: str = "active"  # active | evaluate | disabled
    conditions: dict[str, Any] | None = None
    rules: list[dict[str, Any]] = Field(default_factory=list)
    bypass_actors: list[RulesetBypassActor] | None = None


class ManagedFile(BaseModel):
    """A file Caldrith provisions into each managed repo via a pull request.

    Used to roll required workflows (e.g. the Chargate gate, a Diatreme release) out
    org-wide. ``create_only`` provisions the file only when absent and never overwrites
    an existing one (right for per-repo-customised files like a release workflow);
    the default keeps the file in sync with ``content`` (right for a uniform gate).
    """

    model_config = ConfigDict(extra="forbid")

    path: str
    content: str
    create_only: bool = False


class SafeSettingsConfig(BaseModel):
    """Top-level ``settings.yml`` document.

    Only ``repository`` is consumed in P1. The remaining fields are deferred seams:
    they are accepted (as permissive holders) so a complete safe-settings config
    validates, but no reconciler reads them yet. Replace the ``Any`` holders with
    real models as each feature lands.
    """

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    repository: RepositorySettings | None = None

    # Which repos this installation manages. A list of globs excludes matching repos;
    # an object {include, exclude} allowlists/denies by glob. The admin repo and
    # `.github` are always excluded regardless (see reconcile.selection).
    restricted_repos: list[str] | RestrictedRepos | None = Field(
        default=None, alias="restrictedRepos"
    )

    # --- DEFERRED seams: accepted-but-unused. Typed loosely on purpose. ---
    branches: list[BranchConfig] | None = None  # branch protection
    rulesets: list[Ruleset] | None = None  # repo rulesets (reconciled per managed repo)
    files: list[ManagedFile] | None = None  # workflow/files provisioned into repos via PR
    labels: Any | None = None  # DEFERRED
    teams: Any | None = None  # DEFERRED
    collaborators: Any | None = None  # DEFERRED
    environments: Any | None = None  # DEFERRED
    custom_properties: Any | None = None  # DEFERRED
    suborgs: Any | None = None  # suborg overlay tier — DEFERRED (stub)
    repos: Any | None = None  # per-repo overlay tier — DEFERRED (stub)


def config_json_schema() -> dict[str, Any]:
    """Return the JSON Schema for :class:`SafeSettingsConfig` (usable for docs)."""
    return SafeSettingsConfig.model_json_schema()
