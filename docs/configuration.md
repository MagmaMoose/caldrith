# Configuration

Caldrith reads a single declarative file from your **admin repo**:
`.github/settings.yml`. The location is configurable
(`CONFIG_PATH` default `.github`, `SETTINGS_FILE_PATH` default `settings.yml`,
`ADMIN_REPO` default `admin`).

The schema mirrors [github/safe-settings](https://github.com/github/safe-settings),
so an existing safe-settings `settings.yml` is compatible. Caldrith parses it with
`yaml.safe_load` and validates it into a pydantic v2 model
(`SafeSettingsConfig`).

## What is enforced today (P1)

Only the `repository:` block is applied, and within it only **three** fields flow
through reconcile end-to-end:

| Field | Type | Meaning |
| --- | --- | --- |
| `allow_auto_merge` | `bool` | Allow auto-merge on pull requests. |
| `delete_branch_on_merge` | `bool` | Delete head branches after merge. |
| `allow_update_branch` | `bool` | Allow updating a PR branch from the base. |

```yaml
# .github/settings.yml
repository:
  allow_auto_merge: true
  delete_branch_on_merge: true
  allow_update_branch: true
```

!!! note "Other fields parse but are not yet applied"
    `RepositorySettings` accepts common safe-settings fields (e.g.
    `has_issues`, `has_projects`, `has_wiki`, `default_branch`, `description`,
    `homepage`, `topics`, `private`) as **optional** so your config validates —
    but in P1 **only the three above are reconciled**. Everything else is a clean
    seam for a later slice.

## Reconcile semantics

Caldrith reads the live repository settings, computes a **deep diff** against your
desired state, and issues a `PATCH` **only when something actually differs**. The
diff ignores keys whose name contains `url`, plus `id` and `node_id` (GitHub
echoes these back and they are never settable). Reconcile is **idempotent** —
applying the same file twice produces exactly one mutation.

When a change arrives via a **pull request** on a non-default branch of the admin
repo, Caldrith runs the same diff in **dry-run** and posts a GitHub **Check Run**
with the summary. It mutates nothing — review the Check, then merge to apply.

## Which repositories are managed

By default Caldrith reconciles **every repository the App is installed on**, with two
exceptions that are **always excluded**:

- your **admin repo** (`ADMIN_REPO`, default `admin`) and **`.github`** — caldrith's
  own meta repos are never managed by caldrith;
- **archived** repositories — they reject settings changes (`403`/`422`), so they are
  skipped (both when enumerating repos and, defensively, before any `PATCH`).

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
are reconciled first, since automated security fixes depend on them.) Secret scanning
and push protection (`security_and_analysis`) are a later slice.

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
      required_pull_request_reviews:
        required_approving_review_count: 1
        dismiss_stale_reviews: true
        require_code_owner_reviews: true
      required_status_checks:
        strict: true
        contexts: ["ci/build"]
  - name: release
    protection: null   # remove protection from this branch
```

Reconcile is **idempotent**: caldrith canonicalises GitHub's asymmetric API (the
`GET` wraps booleans as `{enabled: …}`) and only issues a `PUT` when the protection
actually differs. `protection: null` removes protection.

!!! note "Supported fields"
    `enforce_admins`, `required_linear_history`, `allow_force_pushes`,
    `allow_deletions`, `required_conversation_resolution`,
    `required_pull_request_reviews` (`dismiss_stale_reviews`,
    `require_code_owner_reviews`, `required_approving_review_count`,
    `require_last_push_approval`), and `required_status_checks` (`strict`,
    `contexts`). **Deferred** (and rejected so they don't silently no-op):
    `restrictions` (push restrictions) and `required_signatures`.

    Because the PUT body always carries `restrictions: null`, any push
    restrictions set manually on a managed branch are **wiped on the next
    drift-triggered reconcile** — not on the first run, which no-ops if
    nothing else changed, but on whichever later PUT does fire. If you rely
    on push restrictions today, hold off declaring `branches:` for that
    branch until the deferred `restrictions` block lands.

## Rulesets

A top-level `rulesets:` list declares **repository rulesets** Caldrith reconciles onto
every managed repo (matched by `name`: created if absent, updated on drift). `rules`
and `conditions` are passed to the GitHub rulesets API as-is; `bypass_actors` lets
trusted apps/roles skip the rules.

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

## Provisioned files (required workflows)

A top-level `files:` list makes Caldrith **provision files into every managed repo via a
pull request** — the way required workflows (the Chargate gate, a Diatreme release) get
rolled out org-wide. Caldrith never pushes to the default branch directly: it opens (and
reuses) one PR per repo from a stable `caldrith/managed-files` branch.

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

## Deferred config tiers

The schema reserves seams for the safe-settings surface not yet implemented:
labels, teams, collaborators, environments, custom properties, and suborg/repo overlay
tiers (present only as a stub). These keys may appear in your file and validate, but are
not applied yet.

## The generated JSON Schema

`SafeSettingsConfig.model_json_schema()` produces a JSON Schema for the config,
suitable for editor autocompletion and documentation. (Wiring it into a published
schema URL is a docs task for a later slice.)
