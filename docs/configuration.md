# Configuration

Caldrith reads a single declarative file from your **admin repo**:
`.github/settings.yml`. The location is configurable
(`CONFIG_PATH` default `.github`, `SETTINGS_FILE_PATH` default `settings.yml`,
`ADMIN_REPO` default `admin`).

The schema mirrors [github/safe-settings](https://github.com/github/safe-settings),
so an existing safe-settings `settings.yml` is compatible. Caldrith parses it with
`yaml.safe_load` and validates it into a pydantic v2 model
(`SafeSettingsConfig`).

The document is split into **tiers** — each owns a slice of the file and is
reconciled by its own module under `caldrith.reconcile`. Repository-scoped tiers
(everything except the `organization:` block) run once per managed repo; the
`organization:` block runs once per Organization installation. Every model uses
`extra="forbid"`, so a typo surfaces as a validation error rather than silently
no-op'ing, and every field is optional — **only fields you explicitly set are
reconciled** (an omitted field leaves the live value alone).

## Reconcile semantics

Caldrith reads the live state, computes a **deep diff** against your desired
state, and issues a write **only when something actually differs**. The diff
ignores keys whose name contains `url`, plus `id` and `node_id` (GitHub echoes
these back and they are never settable). Reconcile is **idempotent** — applying
the same file twice produces exactly one mutation.

When a change arrives via a **pull request** on a non-default branch of the admin
repo, Caldrith runs the same diff in **dry-run** and posts a GitHub **Check Run**
(`caldrith/settings`) summarising the pending changes per tier. It mutates
nothing — review the Check, then merge to apply. The Check conclusion is always
`neutral`: a dry-run never fails a PR, it only surfaces what *would* change.

### Pruning vs. additive tiers

Some tiers are **full-replace** — the declared list is the *complete* desired set,
and anything live but undeclared is **pruned**. Others only **add or update** and
never remove (pruning would orphan data). Know which is which:

| Prunes undeclared | Additive (never prunes) |
| --- | --- |
| `labels`, `collaborators`, `teams`, `autolinks`, `variables`, `topics`, branch protection (full-replace), `secrets` *(only when `prune: true`)* | `milestones`, `environments`, `rulesets`, `custom_properties`, `files` |

### Self-healing (drift events)

Beyond the admin-repo `push` / `repository` / `pull_request` events, Caldrith
subscribes to a set of **drift events** so an out-of-band change to a managed
setting is corrected automatically. When someone edits a managed setting directly
on GitHub, the matching event re-reconciles the affected repo (or org) back to the
declared state:

| Event | Re-reconciles |
| --- | --- |
| `label`, `milestone`, `member`, `branch_protection_rule` | the affected **repo** |
| `repository_ruleset` | the repo (or the **org**, for an org-level ruleset) |
| `public` (repo visibility flipped) | the affected **repo** |

A convergence that finds no drift (Caldrith's own write echoing back) issues no
further write, so self-healing does not loop.

## Which repositories are managed

By default Caldrith reconciles **every repository the App is installed on**, with two
exceptions that are **always excluded**:

- your **admin repo** (`ADMIN_REPO`, default `admin`) and **`.github`** — caldrith's
  own meta repos are never managed by caldrith;
- **archived** repositories — they reject settings changes (`403`/`422`), so they are
  skipped (both when enumerating repos and, defensively, before any write).

To narrow the set further, add an optional `restrictedRepos` block. Patterns are
minimatch-style globs (brace `{a,b}` and extglob `@(a|b)` supported):

```yaml
# A list excludes any repo matching one of the globs:
restrictedRepos:
  - "legacy-*"
  - "*-sandbox"
```

```yaml
# An object form gives an allowlist (include) and/or a denylist (exclude):
restrictedRepos:
  include: ["svc-*", "app-*"]   # when set, ONLY matching repos are managed
  exclude: ["svc-legacy"]       # ...minus these
```

The built-in exclusions (admin repo, `.github`, archived) apply regardless of
`restrictedRepos`.

## The `repository:` block

The `repository:` block maps to the GitHub repo-update API (`PATCH /repos/{owner}/{repo}`).
**Every** field that maps to a `repos.update` parameter is diffed and applied
generically — identity/visibility, features, default branch, merge strategy and
the squash/merge commit-message knobs, plus the nested `security_and_analysis`
object (passed through verbatim; the deep diff handles its nesting). Only fields
you set participate, so a partial block never clobbers the rest.

```yaml
repository:
  description: "Payments service"
  homepage: "https://example.com"
  private: true
  has_issues: true
  has_wiki: false
  has_discussions: true
  default_branch: main

  # Merge strategy + commit messages
  allow_squash_merge: true
  allow_merge_commit: false
  allow_rebase_merge: false
  allow_auto_merge: true
  delete_branch_on_merge: true
  allow_update_branch: true
  squash_merge_commit_title: PR_TITLE        # PR_TITLE | COMMIT_OR_PR_TITLE
  squash_merge_commit_message: PR_BODY       # PR_BODY | COMMIT_MESSAGES | BLANK
  merge_commit_title: PR_TITLE               # PR_TITLE | MERGE_MESSAGE
  merge_commit_message: PR_BODY              # PR_BODY | PR_TITLE | BLANK

  # Nested code-security + analysis object (passed through as-is)
  security_and_analysis:
    secret_scanning:
      status: enabled
```

Idempotent: the diff drives the `PATCH`, so a converged repo issues no mutation.

!!! note "`topics` and `security` live here but route elsewhere"
    `repository.topics` and `repository.security` are written under the
    `repository:` block in your file for safe-settings compatibility, but they
    are **not** sent through `repos.update` — they have dedicated endpoints and
    are documented in their own sections below.

## Topics

`repository.topics` is a **full-replace**: the live topic set is replaced with
exactly the declared set, via the dedicated topics endpoint. The comparison is
order-insensitive, so re-applying a converged repo issues no write.

```yaml
repository:
  topics: [payments, golang, internal]
```

## Repository security

A `repository.security` block toggles the repo's Dependabot and reporting switches.
These are read and flipped only on drift (idempotent), via dedicated GitHub
endpoints (not `repos.update`):

```yaml
repository:
  security:
    enableVulnerabilityAlerts: true            # Dependabot alerts
    enableAutomatedSecurityFixes: true         # Dependabot security updates
    enablePrivateVulnerabilityReporting: true  # private vulnerability reporting
```

Each is optional — omit a key to leave that toggle untouched. (Vulnerability alerts
are reconciled first, since automated security fixes depend on them.) Secret
scanning and push protection live under `repository.security_and_analysis` (passed
through to `repos.update`).

## Branch protection

Add a `branches:` list to protect branches. Each entry has a `name` (a literal
branch, or `default` to resolve the repo's default branch) and a `protection`
block. The block is **declarative and full-replace** — it is the *complete* desired
protection, so fields you omit fall back to GitHub's "off" defaults.

```yaml
branches:
  - name: default
    protection:
      enforce_admins: true
      required_linear_history: true
      allow_force_pushes: false
      allow_deletions: false
      required_conversation_resolution: true
      required_signatures: true
      required_pull_request_reviews:
        required_approving_review_count: 1
        dismiss_stale_reviews: true
        require_code_owner_reviews: true
      required_status_checks:
        strict: true
        contexts: ["ci/build"]
      restrictions:
        users: ["release-bot"]
        teams: ["platform"]
        apps: ["my-release-app"]
  - name: release
    protection: null   # remove protection from this branch
```

Reconcile is **idempotent**: caldrith canonicalises GitHub's asymmetric API (the
`GET` wraps booleans as `{enabled: …}`) and only issues a `PUT` when the protection
actually differs. `protection: null` removes protection.

!!! note "Supported fields"
    Through the branch-protection `PUT`: `required_pull_request_reviews`
    (`dismiss_stale_reviews`, `require_code_owner_reviews`,
    `required_approving_review_count`, `require_last_push_approval`),
    `required_status_checks` (`strict`, `contexts`), `enforce_admins`,
    `restrictions` (push restrictions — `users` / `teams` / `apps`),
    `required_linear_history`, `allow_force_pushes`, `allow_deletions`,
    `required_conversation_resolution`, `block_creations`, `lock_branch`, and
    `allow_fork_syncing`. `required_signatures` (commit signature protection) is
    reconciled via its own endpoint, since the `PUT` body does not accept it.

!!! warning "`restrictions` is full-replace"
    Because the block is the *complete* desired protection, declaring
    `restrictions` locks the branch to exactly the listed actors — a push
    restriction added manually that you do not declare is reverted on the next
    reconcile. **Omit** `restrictions` to leave a branch's push restriction
    unmanaged.

## Rulesets

A `rulesets:` list declares **repository rulesets** Caldrith reconciles onto every
managed repo (matched by `name`: created if absent, updated on drift). `rules`
and `conditions` are passed to the GitHub rulesets API as-is; `bypass_actors` lets
trusted apps/roles skip the rules. (Org-level rulesets are declared the same way
under the `organization:` block.)

```yaml
rulesets:
  - name: Chargate required
    target: branch
    enforcement: active
    conditions:
      ref_name: { include: ["~DEFAULT_BRANCH"], exclude: [] }
    rules:
      - type: required_status_checks
        parameters:
          required_status_checks:
            - context: "chargate / chargate"
          strict_required_status_checks_policy: false
    bypass_actors:
      - actor_id: 2134967      # e.g. the release/Flux app, so it can push without the gate
        actor_type: Integration
        bypass_mode: always
```

Idempotent: GitHub echoes server-added defaults, so Caldrith only `PUT`s when a
declared field actually differs (a **subset match**). Rulesets are **not pruned** —
removing one from the config does not delete it (deletes are manual). Only repo-level
rulesets are touched; org-inherited ones are left alone.

One exception to the subset rule: when you declare `bypass_actors`, the live set must
match **exactly**. A manually-added extra bypass actor on a required-check ruleset is a
silent escape hatch around the gate, so Caldrith treats it as drift and reverts it on
the next reconcile. (Omit `bypass_actors` entirely to leave a repo's bypass list
unmanaged.)

!!! warning "Required-check rulesets need the check to actually run"
    A `required_status_checks` rule blocks PRs until that check reports. The check only
    runs if the repo has the workflow that produces it (e.g. the Chargate gate). Pair
    this with provisioning that workflow into the repo, or the ruleset will block every
    PR on a check that never runs.

## Labels

A `labels:` list is the **complete** desired label set (full-replace): missing
labels are created, drifted `color`/`description` are updated, and labels that are
not declared are **pruned**. `oldname` renames an existing label in place
(preserving its issue associations) instead of delete+create. Colours are
normalised (leading `#` stripped, lower-cased) before comparison.

```yaml
labels:
  - name: bug
    color: d73a4a
    description: Something isn't working
  - name: enhancement
    oldname: feature   # rename "feature" -> "enhancement", keeping issue links
    color: a2eeef
```

## Milestones

A `milestones:` list is reconciled by `title` (create/update). A declared
milestone that is absent is created; one with drift in `state` / `description` /
`due_on` is updated. Milestones are **never pruned** — removing one would orphan
issues assigned to it.

```yaml
milestones:
  - title: v1.0
    state: open               # open | closed
    description: First stable release
    due_on: "2026-12-31T00:00:00Z"   # ISO-8601
```

## Collaborators

A `collaborators:` list is the **complete** set of *direct* collaborators
(full-replace over the `affiliation=direct` view — access inherited from org
membership or teams is left untouched). Missing collaborators are invited at the
declared permission, drifted permissions are updated, and undeclared direct
collaborators are **removed**.

```yaml
collaborators:
  - username: octocat
    permission: maintain    # pull | triage | push | maintain | admin
  - username: hubot
    permission: push
```

## Teams

A `teams:` list grants team access to a repo (**Organization installs only**;
full-replace). Missing teams are granted the declared permission, drifted
permissions are updated, and teams with access that are not declared are
**removed**. On a **User account** (no teams) this tier is a graceful no-op.

```yaml
teams:
  - name: platform        # team slug
    permission: admin     # pull | triage | push | maintain | admin
  - name: developers
    permission: push
```

## Autolinks

An `autolinks:` list is the **complete** set of autolink references (full-replace).
Autolinks have no update endpoint, so a changed entry is **delete+recreate**: a live
autolink whose `(key_prefix, url_template, is_alphanumeric)` triple is not declared
is deleted, and any declared triple missing from the repo is created.
`is_alphanumeric` defaults to GitHub's default (`true`) when unset, so comparison is
exact and idempotent.

```yaml
autolinks:
  - key_prefix: "JIRA-"
    url_template: "https://jira.example.com/browse/JIRA-<num>"
    is_alphanumeric: false
```

## Custom properties

`custom_properties` is a mapping of property name → value (a string, a list of
strings for multi-select, or `null` to clear). Caldrith diffs each declared
property and issues a single create-or-update with only the drifted ones
(idempotent). It manages only the properties you declare; values for properties you
do not mention are left untouched (no pruning).

```yaml
custom_properties:
  team: payments
  tier: gold
  environments: [dev, staging, prod]   # multi-select
  legacy_flag: null                    # clear the value
```

!!! note "Define the property at the org first"
    Setting a value for an undefined property fails at the API. Declare the
    property schema under `organization.custom_property_definitions` first.

## Interaction limits

An `interaction_limits` block temporarily restricts who can comment / open issues /
open PRs on a repo. Drift is detected on the `limit` value only — the API reports an
absolute `expires_at` rather than the declared relative `expiry`, so a matching
`limit` is treated as converged (re-applying does not reset the clock). `limit: null`
removes any active limit.

```yaml
interaction_limits:
  limit: contributors_only   # existing_users | contributors_only | collaborators_only
  expiry: one_week           # one_day | three_days | one_week | one_month | six_months
```

## Actions

An `actions` block reconciles a repo's GitHub Actions settings via two independent
endpoints, each read-compared-and-set only on drift:

- **permissions** — whether Actions is `enabled` and which `allowed_actions`
  (`all` / `local_only` / `selected`); `allowed_actions` is only meaningful when
  Actions is enabled, so it is sent only then.
- **default workflow token permissions** — the default `GITHUB_TOKEN` scope
  (`default_workflow_permissions`: `read` / `write`) and whether Actions
  `can_approve_pull_request_reviews`.

Only the sub-settings you declare are touched, so a partial declaration never
clobbers the other half.

```yaml
actions:
  enabled: true
  allowed_actions: selected            # all | local_only | selected
  default_workflow_permissions: read   # read | write
  can_approve_pull_request_reviews: false
```

## Variables

A `variables:` list is the **complete** set of Actions variables (full-replace).
Unlike secrets, variable values *are* readable, so this tier is fully declarative:
missing variables are created, value drift is updated, and undeclared variables are
**deleted**. Comparison is exact on the value, so a converged repo issues no write.

```yaml
variables:
  - name: NODE_ENV
    value: production
  - name: REGION
    value: eu-west-1
```

## Secrets

Secret **values cannot be read back** from the GitHub API, so Caldrith never diffs
or rotates a value — it manages **presence** only:

- A declared secret that already exists is left untouched (no rotation).
- A declared secret that is missing is created from an environment-supplied value
  (`CALDRITH_SECRET_<NAME>`, name upper-cased), sealed-box encrypted with the
  repo's public key. If no value is available, the gap is reported but cannot be
  filled.
- When `prune: true`, secrets that are not declared are **deleted**.

Both the Actions and Dependabot secret stores are supported.

```yaml
secrets:
  actions: [NPM_TOKEN, DEPLOY_KEY]   # values from CALDRITH_SECRET_NPM_TOKEN, ...
  dependabot: [PRIVATE_REGISTRY_PAT]
  prune: false                       # set true to delete undeclared secrets
```

!!! warning "Values are write-once, supplied out-of-band"
    Because values are unreadable, declaring a secret only guarantees it
    *exists* — it is never overwritten once present. To rotate a secret, delete
    it on GitHub (or let `prune` remove it) and let the next reconcile recreate it
    from the current `CALDRITH_SECRET_<NAME>` env value.

## Environments

An `environments:` list is reconciled by `name` (create/update). A declared
environment that is absent is created; one with drift in a *declared* field
(`wait_timer`, `prevent_self_review`, `reviewers`, `deployment_branch_policy`) is
updated. Only declared fields participate, so a partial declaration never resets the
others. Environments are **never pruned** — they can hold secrets and deployment
history.

```yaml
environments:
  - name: production
    wait_timer: 10
    prevent_self_review: true
    reviewers:
      - { type: Team, id: 42 }       # User | Team + numeric id
    deployment_branch_policy:
      protected_branches: true
      custom_branch_policies: false
```

## Pages

A `pages` block reconciles GitHub Pages. If Pages is not enabled and you declare a
source / build type, Caldrith enables it; if it is already enabled, declared fields
are compared and updated only on drift. Only declared fields participate. Pages is
**never disabled** by this tier.

```yaml
pages:
  build_type: workflow      # legacy | workflow
  source_branch: gh-pages   # branch for a legacy build
  source_path: "/"          # "/" | "/docs"
  cname: docs.example.com
  https_enforced: true
```

## Provisioned files (required workflows)

A `files:` list makes Caldrith **provision files into every managed repo via a
pull request** — the way required workflows (the Chargate gate, a Diatreme release)
get rolled out org-wide. Caldrith never pushes to the default branch directly: it
opens (and reuses) one PR per repo from a stable `caldrith/managed-files` branch.

```yaml
files:
  - path: .github/workflows/security.yml
    content: |
      name: Security
      on: { pull_request: {}, push: { branches: [main] } }
      permissions: { contents: read, pull-requests: read, security-events: write }
      jobs:
        chargate:
          uses: magmamoose/chargate/.github/workflows/gate.yml@<sha>
  - path: .github/workflows/release.yaml
    create_only: true        # don't overwrite a repo's own release workflow
    skip_repos: ["chargate"] # ...and leave this repo's copy alone entirely
    content: |
      # ... a Diatreme release workflow ...
```

- **Default** (`create_only: false`): keeps the file in sync with `content` (right for a
  uniform file like the gate).
- **`create_only: true`**: provisions only when the file is absent, never overwriting an
  existing one (right for per-repo-customised files like a release workflow). A `.yml`/
  `.yaml` **sibling counts as present** — a managed `release.yaml` is not added next to a
  repo's existing `release.yml`, so you never get two workflows firing on the same event.
- **`skip_repos`**: a list of repo-name globs to exclude from **this file only**. Unlike
  `restrictedRepos` (which drops a repo from *all* management), this is a per-file escape
  hatch — use it for a repo whose copy is intentionally bespoke (e.g. chargate's
  self-referential security workflow that calls its own local gate).

Idempotent and non-destructive: files already matching are skipped, the PR branch is
reused, and an open PR is never duplicated — so re-running while a PR is pending does
nothing. An **empty repository** (no commit on its default branch) is skipped gracefully
— there is nothing to branch a PR from yet.

!!! tip "Sequencing with a required-check ruleset"
    To enforce the Chargate gate org-wide, provision `security.yml` (here) **and** add a
    `required_status_checks` ruleset. But the check only reports once each repo's
    provisioning PR is **merged** — so keep the ruleset's `enforcement: evaluate` (or
    omit it) until the gate PRs land, then flip to `active`. Otherwise the ruleset blocks
    every PR on a check that hasn't started running yet.

## The `organization:` block

The `organization:` block is applied **once per Organization installation** (it is a
graceful no-op on User accounts). Scalar fields map to `orgs.update`
(`PATCH /orgs/{org}`) and are diffed/applied generically — profile, member
privileges, and the security defaults for *new* repositories. The nested `actions`,
`interaction_limits`, `custom_property_definitions`, and `rulesets` are reconciled
via their own endpoints.

```yaml
organization:
  # profile
  billing_email: ops@example.com
  description: "Example org"

  # member privileges
  default_repository_permission: read   # read | write | admin | none
  members_can_create_repositories: false
  web_commit_signoff_required: true

  # security defaults for NEW repositories
  advanced_security_enabled_for_new_repositories: true
  dependabot_alerts_enabled_for_new_repositories: true
  secret_scanning_enabled_for_new_repositories: true

  # org Actions policy + default workflow token permissions
  actions:
    enabled_repositories: all            # all | none | selected
    allowed_actions: selected            # all | local_only | selected
    default_workflow_permissions: read   # read | write
    can_approve_pull_request_reviews: false

  # org-wide interaction limit
  interaction_limits:
    limit: existing_users

  # custom-property SCHEMA (the definition, not a repo's value)
  custom_property_definitions:
    - property_name: team
      value_type: single_select         # string | single_select | multi_select | true_false
      required: true
      default_value: unknown
      allowed_values: [payments, identity, platform]

  # org-level rulesets (same shape as repo rulesets)
  rulesets:
    - name: Org default branch
      target: branch
      enforcement: active
      conditions:
        ref_name: { include: ["~DEFAULT_BRANCH"], exclude: [] }
      rules:
        - type: required_signatures
```

The same pruning rules apply per nested tier: org `rulesets` and
`custom_property_definitions` are **not pruned**; scalar fields are diffed and only
the drifted ones are written.

## Overlays (suborgs + per-repo overrides)

The top-level block is the org-wide **base**. Two overlay layers refine it:

- **`suborgs`** — repo-scoped settings applied to a subset of repos, membership by
  name glob (`repos`). `name` is a label for logs/diagnostics.
- **`repos`** — per-repo overrides, matched by `name` (an exact name or a glob).

For a given repo the effective config is the base with each matching overlay merged
on top, **last-wins, in order: base → suborgs (declared order) → repos**.

```yaml
# base (org-wide)
labels:
  - { name: bug, color: d73a4a }
repository:
  has_wiki: false

suborgs:
  - name: services
    repos: ["svc-*"]
    repository:
      has_issues: true        # field-merged onto the base repository block
    branches:                 # whole-list replace for this suborg's repos
      - name: default
        protection: { enforce_admins: true }

repos:
  - name: svc-payments
    repository:
      has_wiki: true          # overrides just this field for this repo
```

!!! note "Merge granularity"
    The `repository` block is **field-merged** — an overlay overrides only the
    fields it sets, and unset fields fall through to the base. **Every other
    tier** (`labels`, `branches`, `variables`, …) is **replaced wholesale** when
    an overlay declares it: a list is an all-or-nothing statement.

## The generated JSON Schema

`config_json_schema()` (`SafeSettingsConfig.model_json_schema()`) produces a JSON
Schema for the config, suitable for editor autocompletion and documentation.
(Wiring it into a published schema URL is a docs task for a later slice.)
