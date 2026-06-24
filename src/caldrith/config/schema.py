"""Pydantic v2 models mirroring safe-settings' ``settings.yml`` keys.

These models are deliberately compatible with existing ``github/safe-settings``
configs: parse YAML with ``yaml.safe_load`` then validate into
:class:`SafeSettingsConfig`. This module is pure (no I/O) and unit-tested.

The config is split into *tiers*, each owning a slice of the document and a reconciler
under :mod:`caldrith.reconcile`. Repository-scoped tiers live on :class:`RepoScoped`
(reused verbatim by the ``repos:`` and ``suborgs:`` overlays); organization-scoped
settings live on :class:`OrganizationSettings`.

Every model uses ``extra="forbid"`` so a typo in the admin config surfaces as a
validation error rather than silently no-op'ing. Fields are optional: only fields the
admin config explicitly *sets* are reconciled (the appliers use
``model_dump(exclude_unset=True)``), so an omitted field leaves the live value alone.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

# ---------------------------------------------------------------------------
# repository: block
# ---------------------------------------------------------------------------


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

    Every field that maps to a ``repos.update`` (``PATCH /repos/{owner}/{repo}``)
    parameter is diffed and applied generically; only fields explicitly set in the admin
    config participate (``exclude_unset=True``). ``topics`` and ``security`` are *not*
    sent through ``repos.update`` — they have dedicated endpoints and are routed to the
    :mod:`~caldrith.reconcile.topics` / :mod:`~caldrith.reconcile.security` tiers.
    """

    model_config = ConfigDict(extra="forbid")

    # --- Identity / visibility ---
    name: str | None = None
    description: str | None = None
    homepage: str | None = None
    private: bool | None = None
    visibility: str | None = None  # public | private | internal

    # --- Features ---
    has_issues: bool | None = None
    has_projects: bool | None = None
    has_wiki: bool | None = None
    has_downloads: bool | None = None
    has_discussions: bool | None = None
    is_template: bool | None = None

    # --- Default branch ---
    default_branch: str | None = None

    # --- Merge strategy + commit messages ---
    allow_squash_merge: bool | None = None
    allow_merge_commit: bool | None = None
    allow_rebase_merge: bool | None = None
    allow_auto_merge: bool | None = None
    delete_branch_on_merge: bool | None = None
    allow_update_branch: bool | None = None
    use_squash_pr_title_as_default: bool | None = None
    squash_merge_commit_title: str | None = None  # PR_TITLE | COMMIT_OR_PR_TITLE
    squash_merge_commit_message: str | None = None  # PR_BODY | COMMIT_MESSAGES | BLANK
    merge_commit_title: str | None = None  # PR_TITLE | MERGE_MESSAGE
    merge_commit_message: str | None = None  # PR_BODY | PR_TITLE | BLANK

    # --- Misc ---
    allow_forking: bool | None = None
    web_commit_signoff_required: bool | None = None
    archived: bool | None = None

    # Nested code-security + analysis object, passed through to ``repos.update``
    # verbatim (its shape is large and evolving); the deep diff handles nesting.
    security_and_analysis: dict[str, Any] | None = None

    # Routed to dedicated tiers (NOT repos.update):
    topics: list[str] | None = None  # -> reconcile.topics (PUT /repos/.../topics)
    security: RepositorySecurity | None = None  # -> reconcile.security


# ---------------------------------------------------------------------------
# branches: block (branch protection)
# ---------------------------------------------------------------------------


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


class BranchRestrictions(BaseModel):
    """``restrictions`` — who may push to a protected branch (users / teams / apps).

    An explicit (possibly empty) restrictions block locks the branch down to exactly the
    listed actors; omit the block entirely to mean "no push restriction". Slugs/logins
    are accepted (resolved by GitHub); apps are GitHub App slugs.
    """

    model_config = ConfigDict(extra="forbid")

    users: list[str] = Field(default_factory=list)
    teams: list[str] = Field(default_factory=list)
    apps: list[str] = Field(default_factory=list)


class BranchProtection(BaseModel):
    """Desired protection for a branch (the safe-settings ``protection:`` block).

    Declarative + full-replace: the block is the COMPLETE desired protection, so omitted
    fields fall back to GitHub's "off" defaults (no required reviews,
    ``enforce_admins=false``, force-pushes allowed, ...). ``required_signatures`` is
    applied via a dedicated endpoint (commit signature protection); everything else goes
    through the branch-protection ``PUT``.
    """

    model_config = ConfigDict(extra="forbid")

    required_status_checks: RequiredStatusChecks | None = None
    enforce_admins: bool | None = None
    required_pull_request_reviews: RequiredPullRequestReviews | None = None
    restrictions: BranchRestrictions | None = None
    required_linear_history: bool | None = None
    allow_force_pushes: bool | None = None
    allow_deletions: bool | None = None
    required_conversation_resolution: bool | None = None
    block_creations: bool | None = None
    lock_branch: bool | None = None
    allow_fork_syncing: bool | None = None
    required_signatures: bool | None = None


class BranchConfig(BaseModel):
    """A ``branches:`` entry: a branch ``name`` (or ``default``) and its ``protection``.

    ``protection: null`` (or omitted) removes branch protection from the branch.
    """

    model_config = ConfigDict(extra="forbid")

    name: str
    protection: BranchProtection | None = None


# ---------------------------------------------------------------------------
# rulesets: block (repo + org rulesets reuse this model)
# ---------------------------------------------------------------------------


class RulesetBypassActor(BaseModel):
    """An actor allowed to bypass a ruleset (role, team, app, or org admin)."""

    model_config = ConfigDict(extra="forbid")

    actor_id: int | None = None
    actor_type: str  # RepositoryRole | Team | Integration | OrganizationAdmin | DeployKey
    bypass_mode: str = "always"  # always | pull_request


class Ruleset(BaseModel):
    """A ruleset Caldrith reconciles onto a repo (or org).

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


# ---------------------------------------------------------------------------
# files: block (provisioned via PR)
# ---------------------------------------------------------------------------


class ManagedFile(BaseModel):
    """A file Caldrith provisions into each managed repo via a pull request.

    Used to roll required workflows (e.g. the Chargate gate, a Diatreme release) out
    org-wide. ``create_only`` provisions the file only when absent and never overwrites
    an existing one (right for per-repo-customised files like a release workflow);
    the default keeps the file in sync with ``content`` (right for a uniform gate).

    ``upgrade_only`` makes the sync **never downgrade** a SHA-pinned action: if the target
    repo pins any action the file declares (``uses: owner/repo@<sha> # vX.Y.Z``) at a
    *newer* version than this ``content`` — i.e. Dependabot / Renovate has bumped it — the
    file is left as-is instead of reverted. (Without it, a bot bump looks like drift and
    is overwritten back to the baseline.)

    ``skip_repos`` is a list of repo-name globs to exclude from THIS file only — a
    per-file escape hatch that, unlike ``restrictedRepos``, leaves the repo under all
    other Caldrith management.
    """

    model_config = ConfigDict(extra="forbid")

    path: str
    content: str
    create_only: bool = False
    upgrade_only: bool = False
    skip_repos: list[str] | None = None


# ---------------------------------------------------------------------------
# labels / milestones / collaborators / teams / autolinks
# ---------------------------------------------------------------------------


class Label(BaseModel):
    """A repository issue label. Reconciled full-replace: undeclared labels are pruned.

    ``oldname`` renames an existing label to ``name`` (preserving its issue
    associations) instead of delete+create.
    """

    model_config = ConfigDict(extra="forbid")

    name: str
    color: str | None = None  # 6-hex, with or without leading '#'
    description: str | None = None
    oldname: str | None = None


class Milestone(BaseModel):
    """A repository milestone, reconciled by ``title`` (create/update; never pruned)."""

    model_config = ConfigDict(extra="forbid")

    title: str
    state: str | None = None  # open | closed
    description: str | None = None
    due_on: str | None = None  # ISO-8601 timestamp


class Collaborator(BaseModel):
    """A direct repository collaborator and their permission.

    Reconciled full-replace over *direct* collaborators (org/team access untouched):
    undeclared direct collaborators are removed.
    """

    model_config = ConfigDict(extra="forbid")

    username: str
    permission: str = "push"  # pull | triage | push | maintain | admin


class TeamAccess(BaseModel):
    """A team's access to a repository (Organization installs only).

    Reconciled full-replace: teams with repo access that are not declared are removed.
    """

    model_config = ConfigDict(extra="forbid")

    name: str  # team slug
    permission: str = "push"  # pull | triage | push | maintain | admin


class Autolink(BaseModel):
    """An autolink reference. Autolinks have no update endpoint, so a changed entry is
    delete+recreate; undeclared autolinks are pruned (full-replace)."""

    model_config = ConfigDict(extra="forbid")

    key_prefix: str
    url_template: str
    is_alphanumeric: bool | None = None


# ---------------------------------------------------------------------------
# interaction limits / actions / variables / secrets / environments / pages
# ---------------------------------------------------------------------------


class InteractionLimits(BaseModel):
    """Interaction limits for a repo or org. ``limit: null`` removes any active limit."""

    model_config = ConfigDict(extra="forbid")

    limit: str | None = None  # existing_users | contributors_only | collaborators_only
    expiry: str | None = None  # one_day | three_days | one_week | one_month | six_months


class ActionsSettings(BaseModel):
    """Repository GitHub Actions permissions + default workflow token permissions."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool | None = None
    allowed_actions: str | None = None  # all | local_only | selected
    default_workflow_permissions: str | None = None  # read | write
    can_approve_pull_request_reviews: bool | None = None


class Variable(BaseModel):
    """An Actions variable (name + value). Values are readable, so they are diffed and
    updated on drift; undeclared variables are pruned (full-replace)."""

    model_config = ConfigDict(extra="forbid")

    name: str
    value: str


class SecretsConfig(BaseModel):
    """Declared Actions / Dependabot secrets, managed by *presence* only.

    Secret VALUES cannot be read back from the API, so Caldrith never diffs or rotates a
    value. It ensures each declared secret EXISTS — creating a missing one from an
    environment-supplied value (``CALDRITH_SECRET_<NAME>``), encrypted with the repo's
    public key — and, when ``prune`` is set, removes secrets that are not declared.
    """

    model_config = ConfigDict(extra="forbid")

    actions: list[str] = Field(default_factory=list)
    dependabot: list[str] = Field(default_factory=list)
    prune: bool = False


class EnvironmentReviewer(BaseModel):
    """A required reviewer for a deployment environment."""

    model_config = ConfigDict(extra="forbid")

    type: str  # User | Team
    id: int


class Environment(BaseModel):
    """A deployment environment. Reconciled by ``name`` (create/update; never pruned).

    ``deployment_branch_policy`` is passed through to the API
    (``{protected_branches, custom_branch_policies}``).
    """

    model_config = ConfigDict(extra="forbid")

    name: str
    wait_timer: int | None = None
    prevent_self_review: bool | None = None
    reviewers: list[EnvironmentReviewer] | None = None
    deployment_branch_policy: dict[str, Any] | None = None


class PagesConfig(BaseModel):
    """GitHub Pages configuration for a repository."""

    model_config = ConfigDict(extra="forbid")

    build_type: str | None = None  # legacy | workflow
    source_branch: str | None = None  # branch for legacy build
    source_path: str | None = None  # "/" | "/docs"
    cname: str | None = None
    https_enforced: bool | None = None


class CodeScanningDefaultSetup(BaseModel):
    """CodeQL **default setup** — code scanning without a committed workflow file.

    Enabled via the API rather than a workflow; free on public repos, but on
    private/internal repos it needs GitHub Code Security (the update 403/422s without it,
    isolated per tier). ``state`` mirrors the API; ``query_suite`` and ``languages`` are
    optional refinements — omit ``languages`` to let GitHub auto-detect.
    """

    model_config = ConfigDict(extra="forbid")

    state: str  # configured | not-configured
    query_suite: str | None = None  # default | extended
    languages: list[str] | None = None
    runner_type: str | None = None  # standard | labeled
    runner_label: str | None = None

    @model_validator(mode="after")
    def _check_state(self) -> CodeScanningDefaultSetup:
        if self.state not in {"configured", "not-configured"}:
            raise ValueError("code_scanning.state must be 'configured' or 'not-configured'")
        return self


# ---------------------------------------------------------------------------
# Repository-scoped tier container (reused by overlays)
# ---------------------------------------------------------------------------


# Custom property values: a mapping of property name -> value (string, list, or null).
CustomPropertyValues = dict[str, "str | list[str] | None"]


class RepoScoped(BaseModel):
    """All tiers that apply *per repository*.

    :class:`SafeSettingsConfig` is the top-level document; the ``repos:`` and
    ``suborgs:`` overlays carry the SAME fields, layered over the base per repo by
    :func:`caldrith.reconcile.overlay.resolve_for_repo`.
    """

    model_config = ConfigDict(extra="forbid")

    repository: RepositorySettings | None = None
    branches: list[BranchConfig] | None = None
    rulesets: list[Ruleset] | None = None
    files: list[ManagedFile] | None = None
    labels: list[Label] | None = None
    milestones: list[Milestone] | None = None
    collaborators: list[Collaborator] | None = None
    teams: list[TeamAccess] | None = None
    autolinks: list[Autolink] | None = None
    custom_properties: CustomPropertyValues | None = None
    interaction_limits: InteractionLimits | None = None
    actions: ActionsSettings | None = None
    variables: list[Variable] | None = None
    secrets: SecretsConfig | None = None
    environments: list[Environment] | None = None
    pages: PagesConfig | None = None
    code_scanning: CodeScanningDefaultSetup | None = None


class RestrictedRepos(BaseModel):
    """Object form of ``restrictedRepos`` — glob-based repo selection.

    ``include`` is an allowlist (when set, only repos matching it are managed);
    ``exclude`` skips repos matching it. Patterns are minimatch-style globs.
    """

    model_config = ConfigDict(extra="forbid")

    include: list[str] | None = None
    exclude: list[str] | None = None


# ---------------------------------------------------------------------------
# organization: block
# ---------------------------------------------------------------------------


class OrgActions(BaseModel):
    """Organization-wide GitHub Actions policy + default workflow token permissions."""

    model_config = ConfigDict(extra="forbid")

    enabled_repositories: str | None = None  # all | none | selected
    allowed_actions: str | None = None  # all | local_only | selected
    default_workflow_permissions: str | None = None  # read | write
    can_approve_pull_request_reviews: bool | None = None


class OrgCustomPropertyDefinition(BaseModel):
    """An organization custom-property *definition* (the schema, not a repo's value)."""

    model_config = ConfigDict(extra="forbid")

    property_name: str
    value_type: str  # string | single_select | multi_select | true_false
    required: bool | None = None
    default_value: str | list[str] | None = None
    description: str | None = None
    allowed_values: list[str] | None = None


_CODE_SECURITY_STATUS = {"enabled", "disabled", "not_set"}
_CODE_SECURITY_TOGGLES = (
    "advanced_security",
    "dependency_graph",
    "dependency_graph_autosubmit_action",
    "dependabot_alerts",
    "dependabot_security_updates",
    "dependabot_delegated_alert_dismissal",
    "code_scanning_default_setup",
    "code_scanning_delegated_alert_dismissal",
    "secret_scanning",
    "secret_scanning_push_protection",
    "secret_scanning_non_provider_patterns",
    "secret_scanning_validity_checks",
    "secret_scanning_delegated_alert_dismissal",
    "private_vulnerability_reporting",
)


class CodeSecurityConfiguration(BaseModel):
    """An organization **code-security configuration** (the modern org-wide security knob).

    Mirrors ``POST``/``PATCH /orgs/{org}/code-security/configurations``; reconciled by
    ``name`` (created if absent, updated on drift). Each toggle takes ``enabled`` /
    ``disabled`` / ``not_set``. This is the only API path for a few settings with no
    per-repo equivalent — ``dependency_graph_autosubmit_action`` ("automatic dependency
    submission") and ``dependabot_delegated_alert_dismissal`` ("prevent direct alert
    dismissals"). The caldrith-only ``apply_to`` / ``default_for_new_repos`` keys (not part
    of the API config body) attach the configuration to repos and set it as the default for
    new repos after a create/update.
    """

    model_config = ConfigDict(extra="forbid")

    name: str
    description: str | None = None

    advanced_security: str | None = None
    dependency_graph: str | None = None
    dependency_graph_autosubmit_action: str | None = None
    dependency_graph_autosubmit_action_options: dict[str, Any] | None = None
    dependabot_alerts: str | None = None
    dependabot_security_updates: str | None = None
    dependabot_delegated_alert_dismissal: str | None = None
    code_scanning_default_setup: str | None = None
    code_scanning_delegated_alert_dismissal: str | None = None
    secret_scanning: str | None = None
    secret_scanning_push_protection: str | None = None
    secret_scanning_non_provider_patterns: str | None = None
    secret_scanning_validity_checks: str | None = None
    secret_scanning_delegated_alert_dismissal: str | None = None
    private_vulnerability_reporting: str | None = None
    enforcement: str | None = None  # enforced | unenforced

    # caldrith-only (NOT part of the API config body):
    apply_to: str | None = None  # "all_repos" -> attach with scope=all
    default_for_new_repos: str | None = None  # all | none | private_and_internal | public

    @model_validator(mode="after")
    def _check(self) -> CodeSecurityConfiguration:
        for field in _CODE_SECURITY_TOGGLES:
            value = getattr(self, field)
            if value is not None and value not in _CODE_SECURITY_STATUS:
                raise ValueError(
                    f"code_security_configuration.{field} must be one of "
                    f"{sorted(_CODE_SECURITY_STATUS)}"
                )
        if self.enforcement is not None and self.enforcement not in {"enforced", "unenforced"}:
            raise ValueError(
                "code_security_configuration.enforcement must be enforced | unenforced"
            )
        if self.apply_to is not None and self.apply_to != "all_repos":
            raise ValueError("code_security_configuration.apply_to must be 'all_repos'")
        defaults = {"all", "none", "private_and_internal", "public"}
        if self.default_for_new_repos is not None and self.default_for_new_repos not in defaults:
            raise ValueError(
                "code_security_configuration.default_for_new_repos must be one of "
                f"{sorted(defaults)}"
            )
        return self


class OrganizationSettings(BaseModel):
    """The ``organization:`` block — applied once per Organization installation.

    Scalar fields map to ``orgs.update`` (``PATCH /orgs/{org}``) and are diffed/applied
    generically. The nested ``actions`` / ``interaction_limits`` /
    ``custom_property_definitions`` / ``rulesets`` / ``code_security_configuration`` are
    reconciled via their own endpoints.
    """

    model_config = ConfigDict(extra="forbid")

    # --- profile ---
    billing_email: str | None = None
    company: str | None = None
    email: str | None = None
    twitter_username: str | None = None
    location: str | None = None
    name: str | None = None
    description: str | None = None
    blog: str | None = None

    # --- member privileges ---
    has_organization_projects: bool | None = None
    has_repository_projects: bool | None = None
    default_repository_permission: str | None = None  # read | write | admin | none
    members_can_create_repositories: bool | None = None
    members_can_create_internal_repositories: bool | None = None
    members_can_create_private_repositories: bool | None = None
    members_can_create_public_repositories: bool | None = None
    members_can_create_pages: bool | None = None
    members_can_create_public_pages: bool | None = None
    members_can_create_private_pages: bool | None = None
    members_can_fork_private_repositories: bool | None = None
    web_commit_signoff_required: bool | None = None

    # --- security defaults for new repos ---
    advanced_security_enabled_for_new_repositories: bool | None = None
    dependabot_alerts_enabled_for_new_repositories: bool | None = None
    dependabot_security_updates_enabled_for_new_repositories: bool | None = None
    dependency_graph_enabled_for_new_repositories: bool | None = None
    secret_scanning_enabled_for_new_repositories: bool | None = None
    secret_scanning_push_protection_enabled_for_new_repositories: bool | None = None
    secret_scanning_push_protection_custom_link_enabled: bool | None = None
    secret_scanning_push_protection_custom_link: str | None = None

    # --- nested tiers (own endpoints) ---
    actions: OrgActions | None = None
    interaction_limits: InteractionLimits | None = None
    custom_property_definitions: list[OrgCustomPropertyDefinition] | None = None
    rulesets: list[Ruleset] | None = None
    code_security_configuration: CodeSecurityConfiguration | None = None


# ---------------------------------------------------------------------------
# overlays: suborgs + per-repo overrides
# ---------------------------------------------------------------------------


class SubOrg(RepoScoped):
    """A sub-organization overlay: repo-scoped settings applied to a subset of repos.

    Membership is by ``repos`` (name globs) and/or ``visibility`` (``public`` /
    ``private`` / ``internal``). Set either or both; when both are set a repo must match
    both, and a suborg with neither matches nothing. A ``visibility``-only suborg is the
    clean way to apply settings to one visibility class — e.g. enabling GitHub Advanced
    Security / secret scanning / Code Quality on **public repos only** (free there, paid
    per-committer on private/internal). ``name`` is a label for logs/diagnostics. Overlay
    layers are applied base -> suborg -> repo override (last wins).
    """

    model_config = ConfigDict(extra="forbid")

    name: str
    repos: list[str] | None = None  # repo-name globs that belong to this suborg
    visibility: list[str] | None = None  # match by repo visibility: public | private | internal

    @model_validator(mode="after")
    def _check_visibility(self) -> SubOrg:
        allowed = {"public", "private", "internal"}
        bad = [v for v in (self.visibility or []) if v not in allowed]
        if bad:
            raise ValueError(
                f"suborg visibility entries must be one of {sorted(allowed)}; got {bad}"
            )
        return self


class RepoOverride(RepoScoped):
    """A per-repo override overlay, matched by ``name`` (an exact name or a glob)."""

    model_config = ConfigDict(extra="forbid")

    name: str  # repo name or glob


# ---------------------------------------------------------------------------
# top-level document
# ---------------------------------------------------------------------------


class SafeSettingsConfig(RepoScoped):
    """Top-level ``settings.yml`` document.

    Inherits every repository-scoped tier from :class:`RepoScoped` and adds the
    account-wide knobs: repo selection (``restrictedRepos``), the ``organization`` block,
    and the ``suborgs`` / ``repos`` overlays.
    """

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    # Which repos this installation manages. A list of globs excludes matching repos;
    # an object {include, exclude} allowlists/denies by glob. The admin repo and
    # `.github` are always excluded regardless (see reconcile.selection).
    restricted_repos: list[str] | RestrictedRepos | None = Field(
        default=None, alias="restrictedRepos"
    )

    organization: OrganizationSettings | None = None
    suborgs: list[SubOrg] | None = None
    repos: list[RepoOverride] | None = None


def config_json_schema() -> dict[str, Any]:
    """Return the JSON Schema for :class:`SafeSettingsConfig` (usable for docs)."""
    return SafeSettingsConfig.model_json_schema()
