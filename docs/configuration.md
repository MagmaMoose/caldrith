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

Each tier below documents its keys as a `Key | Default | Purpose` reference table. A
**Default** of `—` means the key is optional and **unset** — caldrith leaves the live
value alone; `required` marks a key with no default; any other value is the literal
default applied when you omit the key.

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
| `labels`, `collaborators`, `teams`, `autolinks`, `variables`, `topics`, branch protection (full-replace), `secrets` *(only when `prune: true`)* | `milestones`, `environments`, `rulesets`, `custom_properties` |

`files` is a special case: it prunes a file the config no longer declares from the
**provisioning PR** (the `ci/caldrith/managed-files` branch) — never from a repo's
default branch, which Caldrith only changes when that PR is merged. See
[Provisioned files](#provisioned-files-required-workflows).

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

**Fields** — `restrictedRepos` object form (or give a plain list of exclude globs instead):

| Key | Default | Purpose |
| --- | --- | --- |
| `include` | — | Allowlist of repo-name globs; when set, only matching repos are managed. |
| `exclude` | — | Repo-name globs to skip. |

## Plan-gated settings

caldrith reconciles whatever your GitHub plan allows — it does **not** check entitlements
before writing. Most tiers work on every plan, but several are **plan-gated on private and
internal repositories** (all are free on public repos). If you declare one on a repo whose
plan doesn't include it, GitHub rejects the write (HTTP `403`/`422`); caldrith isolates
that to the one tier on the one repo — it logs `reconcile.tier.failed` and carries on — so
the setting simply never takes effect. (A dry-run Check still shows it as a pending change:
caldrith diffs desired vs. live and can't tell in advance that the apply will be refused.)

| Setting | Free on | Paid plan needed (private / internal repos) |
| --- | --- | --- |
| `branches` — branch protection | public repos | GitHub Pro / Team / Enterprise |
| `rulesets` — repo rulesets | public repos | GitHub Pro / Team / Enterprise |
| `repository.security_and_analysis` — secret scanning + push protection | public repos | **GitHub Secret Protection** (Team / Enterprise, per active committer) |
| `repository.security_and_analysis` — `advanced_security` / code scanning | public repos | **GitHub Code Security** (Team / Enterprise, per active committer) |
| `rulesets` — `code_quality` / code-coverage merge gates (+ enabling Code Quality via `security_and_analysis`) | public repos | **GitHub Code Quality** (Team / Enterprise, per active committer) |
| `environments` — required `reviewers` | public repos | GitHub Enterprise |
| `environments` — `deployment_branch_policy` | public repos | GitHub Pro / Team / Enterprise |
| `pages` on a private repo | public repos | GitHub Pro / Team / Enterprise |
| `organization.*_for_new_repositories` security defaults | — | the org must hold GitHub Advanced Security / Secret Protection / Code Security seats |
| `organization` `rulesets`, `custom_property_definitions` | — | GitHub Team / Enterprise |

Everything else works on **every** plan, public or private: repository basics, `topics`,
`labels`, `milestones`, `collaborators`, `teams`, `autolinks`, `variables`, `secrets`,
`actions`, `interaction_limits`, provisioned `files`, and the Dependabot toggles under
`repository.security` (vulnerability alerts, automated security fixes, private
vulnerability reporting).

!!! warning "One unavailable field can block its whole tier"
    `repository.security_and_analysis` rides in the **same `repos.update` PATCH** as the
    rest of the `repository:` block (and the org security defaults in the same
    `orgs.update` PATCH as the rest of `organization:`). If GitHub refuses the unavailable
    field, the **entire** PATCH fails — so the other fields in that block don't apply for
    that repo/org either. Scope plan-gated fields to repos that can use them with a
    `repos:` / `suborgs:` overlay (or `restrictedRepos`) rather than declaring them
    org-wide.

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

**Fields** — every key maps to a `repos.update` parameter; set only what you want managed.

| Key | Default | Purpose |
| --- | --- | --- |
| `name` | — | Repository name; renames the repo. |
| `description` | — | Short repo description. |
| `homepage` | — | Project homepage URL. |
| `private` | — | Whether the repo is private (`true`) or public (`false`). |
| `visibility` | — | Repo visibility: `public` \| `private` \| `internal`. |
| `has_issues` | — | Enable the Issues feature. |
| `has_projects` | — | Enable the Projects feature. |
| `has_wiki` | — | Enable the Wiki feature. |
| `has_downloads` | — | Enable the Downloads feature. |
| `has_discussions` | — | Enable the Discussions feature. |
| `is_template` | — | Mark the repo as a template repository. |
| `default_branch` | — | Name of the default branch. |
| `allow_squash_merge` | — | Allow squash-merging pull requests. |
| `allow_merge_commit` | — | Allow merging pull requests with a merge commit. |
| `allow_rebase_merge` | — | Allow rebase-merging pull requests. |
| `allow_auto_merge` | — | Allow auto-merge on pull requests. |
| `delete_branch_on_merge` | — | Auto-delete head branches after merge. |
| `allow_update_branch` | — | Allow updating PR branches behind the base. |
| `use_squash_pr_title_as_default` | — | Use the PR title as the default squash commit title. |
| `squash_merge_commit_title` | — | Squash commit title source: `PR_TITLE` \| `COMMIT_OR_PR_TITLE`. |
| `squash_merge_commit_message` | — | Squash commit message source: `PR_BODY` \| `COMMIT_MESSAGES` \| `BLANK`. |
| `merge_commit_title` | — | Merge commit title source: `PR_TITLE` \| `MERGE_MESSAGE`. |
| `merge_commit_message` | — | Merge commit message source: `PR_BODY` \| `PR_TITLE` \| `BLANK`. |
| `allow_forking` | — | Allow forking of the repository. |
| `web_commit_signoff_required` | — | Require sign-off on web-based commits. |
| `archived` | — | Archive (`true`) or unarchive the repo. |
| `security_and_analysis` | — | Passthrough code-security/analysis object; sent verbatim and deep-diffed (nested shape not enumerated). |

## Topics

`repository.topics` is a **full-replace**: the live topic set is replaced with
exactly the declared set, via the dedicated topics endpoint. The comparison is
order-insensitive, so re-applying a converged repo issues no write.

```yaml
repository:
  topics: [payments, golang, internal]
```

**Fields**

| Key | Default | Purpose |
| --- | --- | --- |
| `repository.topics` | — | Full topic set; full-replace (order-insensitive). Unset leaves topics alone; `[]` clears all. |

## Repository security

!!! info "These toggles are free; secret scanning is not"
    The Dependabot/reporting toggles here work on **every** plan, public or private.
    Secret scanning, push protection and code scanning are configured under
    `repository.security_and_analysis` (in the `repository:` block) and are **paid on
    private/internal repos** — see [Plan-gated settings](#plan-gated-settings).

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

**Fields** — the three keys are camelCase aliases (the YAML key, not the python name).

| Key | Default | Purpose |
| --- | --- | --- |
| `enableVulnerabilityAlerts` | — | Toggle Dependabot vulnerability alerts. Reconciled first (automated fixes depend on it). |
| `enableAutomatedSecurityFixes` | — | Toggle Dependabot automated security updates. |
| `enablePrivateVulnerabilityReporting` | — | Toggle private vulnerability reporting. |

## Branch protection

!!! info "Paid on private repos"
    Branch protection on a private/internal repo needs GitHub Pro / Team / Enterprise
    (free on public repos) — see [Plan-gated settings](#plan-gated-settings).

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

**Fields** — each `branches[]` entry:

| Key | Default | Purpose |
| --- | --- | --- |
| `name` | required | Branch to protect; the literal `default` resolves to the repo's default branch. |
| `protection` | — | Protection block (below); `null`/omitted removes protection from the branch. |

**`protection`** (full-replace — omitted sub-fields fall back to GitHub's "off" defaults)

| Key | Default | Purpose |
| --- | --- | --- |
| `required_status_checks` | — | Required-CI-checks block (below); omitted = no required checks. |
| `enforce_admins` | — | Apply protection to admins too. |
| `required_pull_request_reviews` | — | PR-review-requirements block (below); omitted = no required reviews. |
| `restrictions` | — | Push-restriction block (below); omitted = no push restriction. |
| `required_linear_history` | — | Require a linear commit history (no merge commits). |
| `allow_force_pushes` | — | Allow force pushes to the branch. |
| `allow_deletions` | — | Allow the protected branch to be deleted. |
| `required_conversation_resolution` | — | Require all PR conversations resolved before merge. |
| `block_creations` | — | Block creation of matching branches. |
| `lock_branch` | — | Lock the branch read-only (no pushes). |
| `allow_fork_syncing` | — | Allow syncing a fork's branch even when locked. |
| `required_signatures` | — | Require signed commits (applied via the commit-signature endpoint, not the protection PUT). |

**`protection.required_status_checks`** (set at least one field or omit the block)

| Key | Default | Purpose |
| --- | --- | --- |
| `strict` | — | Require branches to be up to date before merging. |
| `contexts` | — | List of required status-check context names. |

**`protection.required_pull_request_reviews`** (set at least one field or omit the block)

| Key | Default | Purpose |
| --- | --- | --- |
| `dismiss_stale_reviews` | — | Dismiss stale approvals when new commits are pushed. |
| `require_code_owner_reviews` | — | Require review from CODEOWNERS. |
| `required_approving_review_count` | — | Integer count of approving reviews required. |
| `require_last_push_approval` | — | Require approval of the most recent reviewable push. |

**`protection.restrictions`** (an explicit, even empty, block locks pushes to exactly the listed actors)

| Key | Default | Purpose |
| --- | --- | --- |
| `users` | `[]` | User logins allowed to push. |
| `teams` | `[]` | Team slugs allowed to push. |
| `apps` | `[]` | GitHub App slugs allowed to push. |

## Rulesets

!!! info "Paid on private repos"
    Rulesets on a private/internal repo need GitHub Pro / Team / Enterprise (free on
    public repos); org-level rulesets need Team / Enterprise — see
    [Plan-gated settings](#plan-gated-settings).

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

**Fields** — each `rulesets[]` entry (reconciled by `name`; never pruned):

| Key | Default | Purpose |
| --- | --- | --- |
| `name` | required | Ruleset name; the create/update match key. |
| `target` | `branch` | What the ruleset targets: `branch` \| `tag` \| `push`. |
| `enforcement` | `active` | Enforcement level: `active` \| `evaluate` \| `disabled`. |
| `conditions` | — | Ref/name conditions, passed to the rulesets API as-is. |
| `rules` | `[]` | List of rule objects, passed to the rulesets API as-is. |
| `bypass_actors` | — | Actors allowed to bypass (below); when set, the live set must match exactly. |

**`rulesets[].bypass_actors[]`**

| Key | Default | Purpose |
| --- | --- | --- |
| `actor_id` | — | Numeric id of the actor (role/team/app id). |
| `actor_type` | required | `RepositoryRole` \| `Team` \| `Integration` \| `OrganizationAdmin` \| `DeployKey`. |
| `bypass_mode` | `always` | When the bypass applies: `always` \| `pull_request`. |

### Conditions

`conditions` selects which refs (and, for org rulesets, which repositories) a ruleset
applies to. `branch` and `tag` rulesets use `ref_name`:

```yaml
conditions:
  ref_name:
    include: ["~DEFAULT_BRANCH"]      # also ~ALL, refs/heads/main, refs/heads/release/* (fnmatch)
    exclude: ["refs/heads/legacy/*"]
```

- `~DEFAULT_BRANCH` — the repo's default branch, resolved per repo.
- `~ALL` — every ref of the target type.
- Otherwise fully-qualified refs (`refs/heads/…`, `refs/tags/…`) or fnmatch patterns.

**Organization rulesets** (`organization.rulesets`) also target *which repos* they reach —
add one of `repository_name`, `repository_id`, or `repository_property`:

```yaml
conditions:
  ref_name: { include: ["~DEFAULT_BRANCH"], exclude: [] }
  repository_name:                          # by name (fnmatch); or:
    include: ["svc-*"]
    exclude: ["svc-legacy"]
    protected: true                         # optional — only repos flagged protected
  # repository_id: { repository_ids: [123, 456] }
  # repository_property: { include: [{ name: tier, property_values: ["gold"] }], exclude: [] }
```

A `push` ruleset applies to the whole repository, so it takes **no** `ref_name`.

### Rules

Each `rules` entry is `{ type, parameters? }`, sent to the API verbatim. Common
branch/tag rules and their key parameters:

| `type` | Parameters |
| --- | --- |
| `creation`, `deletion` | — (restrict who may create / delete matching refs) |
| `update` | `update_allows_fetch_and_merge` |
| `required_linear_history` | — |
| `required_signatures` | — (signed commits) |
| `non_fast_forward` | — (block force-pushes) |
| `required_deployments` | `required_deployment_environments: [str]` |
| `pull_request` | `required_approving_review_count`, `dismiss_stale_reviews_on_push`, `require_code_owner_review`, `require_last_push_approval`, `required_review_thread_resolution`, `allowed_merge_methods: [merge\|squash\|rebase]` |
| `required_status_checks` | `required_status_checks: [{context, integration_id?}]`, `strict_required_status_checks_policy`, `do_not_enforce_on_create?` |
| `commit_message_pattern`, `commit_author_email_pattern`, `committer_email_pattern`, `branch_name_pattern`, `tag_name_pattern` | `operator: starts_with\|ends_with\|contains\|regex`, `pattern`, `name?`, `negate?` |

`push` rulesets (`target: push`) add file rules — `file_path_restriction`,
`file_extension_restriction`, `max_file_path_length`, `max_file_size`.

Other rules gate a merge on one of GitHub's paid security/quality products and need the
matching per-committer licence:

- `code_scanning` — block on code-scanning alert severity / analysis state (**GitHub Code
  Security**).
- `code_quality` — block a PR that doesn't meet a **code-quality** severity threshold, and
  **code-coverage** gates that block below a coverage threshold (**GitHub Code Quality** —
  [GA 20 July 2026](https://github.blog/changelog/2026-06-16-github-code-quality-generally-available-july-20-2026/);
  [coverage on PRs](https://github.blog/changelog/2026-05-26-code-coverage-in-pull-requests-is-now-in-public-preview/)
  entered preview May 2026). Enabling Code Quality on a repo is a code-security-and-analysis
  setting, so it goes through `repository.security_and_analysis` (passthrough); the merge
  gate is this ruleset rule.

caldrith special-cases **no** rule — `rules` is a passthrough list — so you can declare any
of these today without a caldrith change. Because these are new/evolving, take the exact
`type` and parameter names from GitHub's
[Available rules for rulesets](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-rulesets/available-rules-for-rulesets)
and the [Rules REST reference](https://docs.github.com/en/rest/repos/rules) rather than
hard-coding them from memory.

!!! note "Two ways to require signed commits"
    A ruleset `required_signatures` rule and `branches[].protection.required_signatures`
    both enforce signing, by different mechanisms — pick one to avoid two overlapping
    controls on the same branch.

### Bypass actors — finding `actor_id`

`actor_type` determines what `actor_id` means:

| `actor_type` | `actor_id` |
| --- | --- |
| `RepositoryRole` | role id: `1` read, `2` triage, `3` write, `4` maintain, `5` admin |
| `Team` | the team's numeric id |
| `Integration` | the GitHub App's id (e.g. a release / Flux app that must push without the gate) |
| `OrganizationAdmin` | `1` |
| `DeployKey` | n/a — the deploy key used for the push |

`bypass_mode` is `always` or `pull_request` (bypass only for changes that go through a
PR). caldrith treats a declared `bypass_actors` list as the **exact** desired set — a
manually-added extra is reverted as drift; omit the key to leave the live list unmanaged.

### Repository vs. organization rulesets

- **Repository rulesets** — the top-level `rulesets:` (or a `repos:` / `suborgs:` overlay),
  applied per managed repo. caldrith reconciles only a repo's **own** rulesets
  (`source_type: Repository`); org-inherited ones (`source_type: Organization`) are left
  alone, so an org ruleset is never duplicated or edited from the repo side.
- **Organization rulesets** — `organization.rulesets`, defined once on the org and applied
  across repos via the `repository_*` conditions above. (Org rulesets need GitHub Team /
  Enterprise — see [Plan-gated settings](#plan-gated-settings).)

### More examples

Protect release tags (only bypass actors may create / delete / move them):

```yaml
rulesets:
  - name: Protect release tags
    target: tag
    enforcement: active
    conditions:
      ref_name: { include: ["refs/tags/v*"], exclude: [] }
    rules:
      - type: deletion
      - type: update
      - type: required_signatures
```

A fuller default-branch gate — PR review + green CI + linear history + no force-push:

```yaml
rulesets:
  - name: Default branch protections
    target: branch
    enforcement: active
    conditions:
      ref_name: { include: ["~DEFAULT_BRANCH"], exclude: [] }
    rules:
      - type: pull_request
        parameters:
          required_approving_review_count: 1
          dismiss_stale_reviews_on_push: true
          require_code_owner_review: true
          required_review_thread_resolution: true
      - type: required_status_checks
        parameters:
          required_status_checks:
            - context: "chargate / chargate"
          strict_required_status_checks_policy: true
      - type: required_linear_history
      - type: non_fast_forward
```

Roll a ruleset out in monitor-only mode first with `enforcement: evaluate` (it reports
what *would* be blocked without blocking), then flip to `active` once it's clean.

An organization ruleset scoped to a subset of repos by custom property:

```yaml
organization:
  rulesets:
    - name: Org default-branch signing
      target: branch
      enforcement: active
      conditions:
        ref_name: { include: ["~DEFAULT_BRANCH"], exclude: [] }
        repository_property:
          include: [{ name: tier, property_values: ["gold"] }]
          exclude: []
      rules:
        - type: required_signatures
```

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

**Fields** — each `labels[]` entry (full-replace; undeclared labels are pruned):

| Key | Default | Purpose |
| --- | --- | --- |
| `name` | required | Label name; reconciliation key and the rename target. |
| `color` | — | 6-hex colour, with or without leading `#` (normalised before compare). |
| `description` | — | Label description. |
| `oldname` | — | Existing label to rename to `name` (preserving issue links) instead of delete+create. |

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

**Fields** — each `milestones[]` entry (reconciled by `title`; never pruned):

| Key | Default | Purpose |
| --- | --- | --- |
| `title` | required | Milestone title; the create/update match key. |
| `state` | — | Milestone state: `open` \| `closed`. |
| `description` | — | Free-text milestone description. |
| `due_on` | — | Due date as an ISO-8601 timestamp. |

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

**Fields** — each `collaborators[]` entry (full-replace over *direct* collaborators):

| Key | Default | Purpose |
| --- | --- | --- |
| `username` | required | GitHub login to grant direct access (matched case-insensitively). |
| `permission` | `push` | Access level: `pull` \| `triage` \| `push` \| `maintain` \| `admin`. |

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

**Fields** — each `teams[]` entry (Organization installs only; full-replace):

| Key | Default | Purpose |
| --- | --- | --- |
| `name` | required | Team slug to grant repo access. |
| `permission` | `push` | Access level: `pull` \| `triage` \| `push` \| `maintain` \| `admin`. |

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

**Fields** — each `autolinks[]` entry (full-replace; changed entries are delete+recreate):

| Key | Default | Purpose |
| --- | --- | --- |
| `key_prefix` | required | Reference prefix that triggers the autolink (e.g. `JIRA-`). |
| `url_template` | required | Target URL with a `<num>` placeholder for the matched reference. |
| `is_alphanumeric` | — | Alphanumeric (`true`) or numeric-only (`false`) suffix; unset compares as GitHub's default `true`. |

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

**Fields** — `custom_properties` is a mapping of property name → value (caldrith manages only the names you declare):

| Key | Default | Purpose |
| --- | --- | --- |
| `custom_properties.<name>: <string>` | — | Set a single-value property. The org must define the property first. |
| `custom_properties.<name>: [<string>, …]` | — | Set a multi-select property (order-insensitive diff). |
| `custom_properties.<name>: null` | — | Explicitly clear the property's value on the repo. |

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

**Fields**

| Key | Default | Purpose |
| --- | --- | --- |
| `limit` | — | Who may interact while active: `existing_users` \| `contributors_only` \| `collaborators_only`. `null` removes any active limit. |
| `expiry` | — | How long the limit stays active: `one_day` \| `three_days` \| `one_week` \| `one_month` \| `six_months`. |

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

**Fields**

| Key | Default | Purpose |
| --- | --- | --- |
| `enabled` | — | Whether GitHub Actions is enabled for the repo. |
| `allowed_actions` | — | Which actions may run: `all` \| `local_only` \| `selected`. |
| `default_workflow_permissions` | — | Default `GITHUB_TOKEN` scope: `read` \| `write`. |
| `can_approve_pull_request_reviews` | — | Whether Actions can approve pull request reviews. |

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

**Fields** — each `variables[]` entry (full-replace; undeclared variables are deleted):

| Key | Default | Purpose |
| --- | --- | --- |
| `name` | required | Actions variable name; reconciled by name. |
| `value` | required | Variable value; readable, so diffed exactly and updated on drift. |

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

**Fields**

| Key | Default | Purpose |
| --- | --- | --- |
| `actions` | `[]` | Names of Actions secrets to ensure exist (created from `CALDRITH_SECRET_<NAME>`; never rotated). |
| `dependabot` | `[]` | Names of Dependabot secrets to ensure exist; same create-if-missing semantics. |
| `prune` | `false` | When `true`, delete live secrets not declared — only in the stores you populate. |

## Environments

!!! info "Protection rules are paid on private repos"
    On private/internal repos, required `reviewers` need GitHub Enterprise and
    `deployment_branch_policy` needs Pro / Team / Enterprise (both free on public repos) —
    see [Plan-gated settings](#plan-gated-settings).

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

**Fields** — each `environments[]` entry (reconciled by `name`; never pruned):

| Key | Default | Purpose |
| --- | --- | --- |
| `name` | required | Environment name; matched against live environments. |
| `wait_timer` | — | Minutes to delay before deployments may proceed. |
| `prevent_self_review` | — | Block the deployer from approving their own run. |
| `reviewers` | — | List of required reviewers (below). |
| `deployment_branch_policy` | — | Passthrough dict controlling which branches/tags can deploy (below). |

**`environments[].reviewers[]`**

| Key | Default | Purpose |
| --- | --- | --- |
| `type` | required | Reviewer kind: `User` \| `Team`. |
| `id` | required | Numeric GitHub user or team id. |

**`environments[].deployment_branch_policy`** (passthrough dict — the API's standard keys)

| Key | Default | Purpose |
| --- | --- | --- |
| `protected_branches` | — | `true` restricts deployments to branches matching the repo's protection rules. |
| `custom_branch_policies` | — | `true` enables custom name-pattern policies (mutually exclusive with `protected_branches`). |

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

**Fields** — only declared sub-fields are reconciled; Pages is enabled if unset and a source/build type is declared, but never disabled.

| Key | Default | Purpose |
| --- | --- | --- |
| `build_type` | — | Pages build type: `legacy` \| `workflow`. |
| `source_branch` | — | Branch serving the site for legacy builds. |
| `source_path` | — | Source directory for legacy builds: `/` \| `/docs`. |
| `cname` | — | Custom domain for the Pages site. |
| `https_enforced` | — | Whether HTTPS is enforced on the Pages site. |

## Provisioned files (required workflows)

A `files:` list makes Caldrith **provision files into every managed repo via a
pull request** — the way required workflows (the Chargate gate, a Diatreme release)
get rolled out org-wide. Caldrith never pushes to the default branch directly: it
opens (and reuses) one PR per repo from a stable `ci/caldrith/managed-files` branch.

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

**Pruning.** When you drop a file from `files:` (or add a repo to its `skip_repos`),
Caldrith removes it from the provisioning PR on the next reconcile so the PR reflects
only the currently-required files. It only ever edits its own `ci/caldrith/managed-files`
branch: a file it had added net-new is deleted; a repo file it had merely updated is
reverted to the default branch's version (the repo's own files are never removed). If
pruning leaves nothing to provision, the now-empty PR is **closed and its branch
deleted** rather than left dangling.

!!! warning "Removing the last file does not clean up"
    Pruning runs only while the `files:` block still declares **at least one** file —
    that is what makes the tier run. Removing the *last* entry (or the whole block) skips
    the files tier entirely, so existing provisioning PRs are **not** pruned or closed.
    Keep one file declared, or close any leftover managed PRs by hand.

!!! tip "Sequencing with a required-check ruleset"
    To enforce the Chargate gate org-wide, provision `security.yml` (here) **and** add a
    `required_status_checks` ruleset. But the check only reports once each repo's
    provisioning PR is **merged** — so keep the ruleset's `enforcement: evaluate` (or
    omit it) until the gate PRs land, then flip to `active`. Otherwise the ruleset blocks
    every PR on a check that hasn't started running yet.

**Fields** — each `files[]` entry:

| Key | Default | Purpose |
| --- | --- | --- |
| `path` | required | Repo-relative path of the file to provision (e.g. `.github/workflows/gate.yml`). |
| `content` | required | Full file body caldrith writes; a matching file is skipped, a drifted one updated (unless `create_only`). |
| `create_only` | `false` | When `true`, provision only when absent and never overwrite; a `.yml`/`.yaml` sibling counts as present. |
| `skip_repos` | — | Repo-name globs to exclude THIS file from (a matched repo is skipped and any prior managed copy pruned). |

## The `organization:` block

The `organization:` block is applied **once per Organization installation** (it is a
graceful no-op on User accounts). Scalar fields map to `orgs.update`
(`PATCH /orgs/{org}`) and are diffed/applied generically — profile, member
privileges, and the security defaults for *new* repositories. The nested `actions`,
`interaction_limits`, `custom_property_definitions`, `rulesets`, and
`code_security_configuration` are reconciled via their own endpoints.

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

**Fields** — scalar keys map to `orgs.update`; the nested blocks use their own endpoints.

| Key | Default | Purpose |
| --- | --- | --- |
| `billing_email` | — | Org billing email (profile). |
| `company` | — | Org company name (profile). |
| `email` | — | Org public email (profile). |
| `twitter_username` | — | Org Twitter handle (profile). |
| `location` | — | Org location (profile). |
| `name` | — | Org display name (profile). |
| `description` | — | Org description (profile). |
| `blog` | — | Org blog/website URL (profile). |
| `has_organization_projects` | — | Allow org-level projects. |
| `has_repository_projects` | — | Allow repo-level projects across the org. |
| `default_repository_permission` | — | Base permission members get on all repos: `read` \| `write` \| `admin` \| `none`. |
| `members_can_create_repositories` | — | Members may create repos. |
| `members_can_create_internal_repositories` | — | Members may create internal repos. |
| `members_can_create_private_repositories` | — | Members may create private repos. |
| `members_can_create_public_repositories` | — | Members may create public repos. |
| `members_can_create_pages` | — | Members may publish GitHub Pages sites. |
| `members_can_create_public_pages` | — | Members may publish public Pages sites. |
| `members_can_create_private_pages` | — | Members may publish private Pages sites. |
| `members_can_fork_private_repositories` | — | Members may fork private repos. |
| `web_commit_signoff_required` | — | Require sign-off on web-based commits org-wide. |
| `advanced_security_enabled_for_new_repositories` | — | Enable Advanced Security on new repos. |
| `dependabot_alerts_enabled_for_new_repositories` | — | Enable Dependabot alerts on new repos. |
| `dependabot_security_updates_enabled_for_new_repositories` | — | Enable Dependabot security updates on new repos. |
| `dependency_graph_enabled_for_new_repositories` | — | Enable dependency graph on new repos. |
| `secret_scanning_enabled_for_new_repositories` | — | Enable secret scanning on new repos. |
| `secret_scanning_push_protection_enabled_for_new_repositories` | — | Enable secret-scanning push protection on new repos. |
| `secret_scanning_push_protection_custom_link_enabled` | — | Show a custom link in push-protection blocks. |
| `secret_scanning_push_protection_custom_link` | — | URL for the push-protection custom link. |
| `actions` | — | Nested org Actions policy (below). |
| `interaction_limits` | — | Nested org-wide interaction limit (below). |
| `custom_property_definitions` | — | Nested org custom-property definitions (below). |
| `rulesets` | — | Nested org rulesets (same shape as repo `rulesets`). |

**`organization.actions`**

| Key | Default | Purpose |
| --- | --- | --- |
| `enabled_repositories` | — | Which repos may run Actions: `all` \| `none` \| `selected`. |
| `allowed_actions` | — | Which actions are permitted: `all` \| `local_only` \| `selected`. |
| `default_workflow_permissions` | — | Default `GITHUB_TOKEN` scope: `read` \| `write`. |
| `can_approve_pull_request_reviews` | — | Allow Actions to approve pull request reviews. |

**`organization.interaction_limits`**

| Key | Default | Purpose |
| --- | --- | --- |
| `limit` | — | `existing_users` \| `contributors_only` \| `collaborators_only`. `null` removes any active limit. |
| `expiry` | — | `one_day` \| `three_days` \| `one_week` \| `one_month` \| `six_months`. |

**`organization.custom_property_definitions[]`**

| Key | Default | Purpose |
| --- | --- | --- |
| `property_name` | required | Name of the org custom-property definition. |
| `value_type` | required | `string` \| `single_select` \| `multi_select` \| `true_false`. |
| `required` | — | Whether the property is required on repos. |
| `default_value` | — | Default value (string or list). |
| `description` | — | Human description of the property. |
| `allowed_values` | — | Permitted values for select-type properties. |

### `organization.code_security_configuration`

GitHub's org **code-security configuration** is the modern, enforceable way to set
Dependabot / secret-scanning / code-scanning / dependency-graph defaults across the org —
and the **only** API path for a couple of settings with no per-repo equivalent. caldrith
reconciles it by `name` (create if absent, update on drift), then — only after a create or
update — attaches it and/or sets it as the default for new repos.

```yaml
organization:
  code_security_configuration:
    name: "MagmaMoose baseline"
    dependency_graph: enabled
    dependency_graph_autosubmit_action: enabled     # "Automatic dependency submission"
    dependabot_alerts: enabled
    dependabot_security_updates: enabled
    dependabot_delegated_alert_dismissal: enabled   # "Prevent direct alert dismissals"
    secret_scanning: enabled
    secret_scanning_push_protection: enabled
    private_vulnerability_reporting: enabled
    enforcement: enforced
    apply_to: all_repos            # attach to all repos (scope=all)
    default_for_new_repos: all     # set as the default for new repos
```

**Fields** — every toggle takes `enabled` | `disabled` | `not_set`; `apply_to` /
`default_for_new_repos` are caldrith-only (not sent in the API body).

| Key | Default | Purpose |
| --- | --- | --- |
| `name` | required | Configuration name; the create/update match key. |
| `description` | — | Human description. |
| `advanced_security` | — | GitHub Advanced Security. |
| `dependency_graph` | — | Dependency graph. |
| `dependency_graph_autosubmit_action` | — | **Automatic dependency submission** (needs `dependency_graph: enabled`). |
| `dependency_graph_autosubmit_action_options` | — | Passthrough dict, e.g. `{labeled_runners: true}`. |
| `dependabot_alerts` | — | Dependabot alerts. |
| `dependabot_security_updates` | — | Dependabot security updates. |
| `dependabot_delegated_alert_dismissal` | — | **Prevent direct alert dismissals** (needs `dependabot_alerts: enabled`). |
| `code_scanning_default_setup` | — | CodeQL default setup. |
| `code_scanning_delegated_alert_dismissal` | — | Delegated dismissal for code-scanning alerts. |
| `secret_scanning` | — | Secret scanning. |
| `secret_scanning_push_protection` | — | Secret-scanning push protection. |
| `secret_scanning_non_provider_patterns` | — | Non-provider patterns. |
| `secret_scanning_validity_checks` | — | Validity checks. |
| `secret_scanning_delegated_alert_dismissal` | — | Delegated dismissal for secret-scanning alerts. |
| `private_vulnerability_reporting` | — | Private vulnerability reporting. |
| `enforcement` | — | `enforced` \| `unenforced`. |
| `apply_to` | — | `all_repos` → attach the config to every repo (scope=all). |
| `default_for_new_repos` | — | `all` \| `none` \| `private_and_internal` \| `public` → set as the default for new repos. |

!!! note "Attach / set-default only fire after a change"
    To avoid kicking an attach job on every reconcile, caldrith attaches and sets-default
    only when it just created or updated the configuration. A repo manually detached from
    the config is not re-attached until the config itself next changes.

!!! warning "Some Dependabot settings have no API"
    **Dependabot malware alerts** and **Dependabot on self-hosted runners** are UI-only —
    no REST field, not in this configuration object, not in `dependabot.yml`. Enable them
    by hand (org Settings → Code security → Dependabot). **Version updates** and **grouped
    updates** are file-based: provision a `.github/dependabot.yml` via the
    [files](#provisioned-files-required-workflows) tier (`updates:` + a `groups:` block
    with `applies-to: security-updates`).

## Overlays (suborgs + per-repo overrides)

The top-level block is the org-wide **base**. Two overlay layers refine it:

- **`suborgs`** — repo-scoped settings applied to a subset of repos, membership by
  name glob (`repos`) **and/or repo `visibility`** (`public` / `private` / `internal`).
  `name` is a label for logs/diagnostics.
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

### Scope by visibility — paid features on public repos only

GitHub Advanced Security, secret scanning, code scanning, **Code Quality** and **code
coverage** are free on public repos but paid (per active committer) on private/internal
ones. A `visibility`-scoped suborg applies a slice of settings to just one visibility
class, so you can turn these on
everywhere they're free without ever enabling (and being billed for) them on private
repos:

```yaml
suborgs:
  - name: public-security
    visibility: [public]                 # only public repos get the block below
    repository:
      security_and_analysis:             # GHAS / secret scanning / code scanning
        advanced_security: { status: enabled }
        secret_scanning: { status: enabled }
        secret_scanning_push_protection: { status: enabled }
    rulesets:
      - name: Quality gate
        target: branch
        enforcement: active
        conditions: { ref_name: { include: ["~DEFAULT_BRANCH"], exclude: [] } }
        rules:
          - type: code_quality           # + a code-coverage rule once Code Quality is enabled
```

Keep these out of the org-wide **base** — only the `visibility: [public]` suborg should
carry them, so private/internal repos never receive them. A repo whose visibility caldrith
can't determine is treated as **not** matching (safer to skip a paid feature than to apply
it), and `visibility` combines with `repos` by **AND** when both are set.

**Fields** — `suborgs[]` and `repos[]` carry the structural keys below **plus every
repository-scoped tier** (`repository`, `branches`, `rulesets`, `files`, `labels`,
`milestones`, `collaborators`, `teams`, `autolinks`, `custom_properties`,
`interaction_limits`, `actions`, `variables`, `secrets`, `environments`, `pages` — each
documented in its own section above).

| Key | Default | Purpose |
| --- | --- | --- |
| `suborgs[].name` | required | Label for logs/diagnostics identifying this sub-org overlay. |
| `suborgs[].repos` | — | Repo-name globs defining which repos this overlay applies to. |
| `suborgs[].visibility` | — | Match repos by visibility — any of `public` / `private` / `internal`. With `repos` set, a repo must match both. |
| `repos[].name` | required | Repo name or glob this per-repo override matches. |

## The generated JSON Schema

`config_json_schema()` (`SafeSettingsConfig.model_json_schema()`) produces a JSON
Schema for the config, suitable for editor autocompletion and documentation.
(Wiring it into a published schema URL is a docs task for a later slice.)
