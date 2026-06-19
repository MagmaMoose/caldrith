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

## Deferred config tiers

The schema reserves seams for the safe-settings surface that P1 does **not** yet
implement: branch protection, rulesets, labels, teams, collaborators,
environments, custom properties, and suborg/repo overlay tiers (present only as a
stub). These keys may appear in your file and validate, but are not applied yet.

## The generated JSON Schema

`SafeSettingsConfig.model_json_schema()` produces a JSON Schema for the config,
suitable for editor autocompletion and documentation. (Wiring it into a published
schema URL is a docs task for a later slice.)
