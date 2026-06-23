# Caldrith

[![License](https://img.shields.io/github/license/magmamoose/caldrith)](LICENSE)
[![CI](https://github.com/magmamoose/caldrith/actions/workflows/ci.yml/badge.svg)](https://github.com/magmamoose/caldrith/actions/workflows/ci.yml)
[![Release](https://img.shields.io/github/v/release/magmamoose/caldrith?sort=semver)](https://github.com/magmamoose/caldrith/releases)

Caldrith is a **multi-tenant GitHub App** that enforces GitHub configuration as
code. You install it on an account, create an **admin repo** with a
`.github/settings.yml`, and Caldrith continuously **reconciles your repositories'
settings to match that file** — the same model as
[github/safe-settings](https://github.com/github/safe-settings), reimplemented as
a long-running Python service.

It is **not** a CLI and **not** a Marketplace Action — there is no `action.yml`.
Caldrith is a FastAPI service that receives GitHub webhooks and applies your
declared configuration through the GitHub API.

> **Multi-tenant, multi-platform, all visibilities.** One deployment serves many
> installations. It works on both **Organization** and **User** accounts on
> github.com today (GHES is on the roadmap), and reconciles **private, public, and
> internal** repos alike.

## What it does

You describe the desired state once, in the admin repo, and Caldrith keeps every
target repo in line with it — reacting to pushes, repo creation/edits, PRs, and
out-of-band drift.

Caldrith enforces a broad slice of the safe-settings surface: the full
`repository:` block (all `repos.update` fields, topics, security toggles), branch
protection (including push `restrictions` and `required_signatures`), repository and
organization **rulesets**, **labels**, **milestones**, **collaborators**, **teams**,
**autolinks**, **custom properties**, **interaction limits**, **Actions** settings,
**variables**, **secrets** (presence), **environments**, **Pages**, **provisioned
files** (required workflows via PR), the **`organization:`** block, and
**suborg/per-repo overlays**. The config **schema** mirrors safe-settings, so an
existing safe-settings `settings.yml` is compatible. See
[`docs/configuration.md`](docs/configuration.md) for the full per-tier reference.

## Quickstart

1. **Install the GitHub App** on your Organization or User account, granting it
   access to the repos you want managed (and the admin repo).
2. **Create an admin repo** (default name: `admin`) containing
   `.github/settings.yml`:

   ```yaml
   # .github/settings.yml
   repository:
     allow_auto_merge: true
     delete_branch_on_merge: true
     allow_update_branch: true
   ```

3. **Push it.** On a push to the admin repo's default branch, Caldrith reconciles
   every target repo in the installation to match. From then on it also reacts to
   repository created/edited events and to settings PRs.

That's it — no per-repo workflow files, no Action to pin. The service does the
rest.

## Behaviour & semantics

Caldrith ingests GitHub webhooks at `POST /` (the App's webhook URL is simply
`https://caldrith.magmamoose.com`). The ingest is deliberately thin: verify the
signature over the **raw** body, deduplicate on the
`X-GitHub-Delivery` id, enqueue a background job, and return `202` — all well
inside GitHub's 10-second timeout. The heavy reconcile runs in an async
([ARQ](https://arq-docs.helpmanual.io/) / Redis) worker.

| Event | Trigger | Effect |
| --- | --- | --- |
| `push` | to the **admin repo's default branch** | Reconcile **all** target repos in the installation (fan out one job per repo). |
| `repository` | `created` / `edited` | Reconcile **that** repo. |
| `pull_request` | `opened` / `reopened` / `synchronize` touching the admin repo's `settings.yml` on a **non-default** branch | **Dry run.** Post a GitHub **Check Run** with the diff. **Mutates nothing.** |
| drift events | `label` / `milestone` / `member` / `branch_protection_rule` / `repository_ruleset` / `public` | **Self-heal.** Re-reconcile the affected repo (or org) back to the declared state. |

**Reconcile is idempotent.** Caldrith reads the live state, computes a deep diff
against the desired state (ignoring `url`-bearing keys plus `id` / `node_id`), and
writes **only when there is a real change**. Applying the same config twice yields
exactly one mutation, and a self-healing convergence that finds no drift issues no
write — so drift events do not loop.

**Per-installation isolation.** Every job builds a **fresh** client authenticated
as that installation; tokens are never shared across installations. The GitHub API
base URL is configurable (default `https://api.github.com`) so a GHES target slots
in later without code changes.

## Deferred (clearly seamed, not yet implemented)

GHES multi-registration and a CEL policy layer are still out of scope. The schema
and module layout leave clean seams for each so they slot in without rework.

## Conventions

Python 3.12, **uv + Ruff + pytest**, full type hints. Pure logic (the config
schema and the diff engine) is I/O-free and unit-tested; the webhook ingest path
verifies signatures over the **raw** request bytes before any parsing. Tests
mirror modules 1:1 under `tests/`. SHA-pin external GitHub Actions with a
`# vX.Y.Z` comment.

**Releases** are automated: pushing to `main` runs python-semantic-release (the
diatreme flow), which cuts the next `vX.Y.Z` from conventional commits and bumps
`project.version` + `__init__.__version__` — never bump those by hand.

Full human docs live in [`./docs`](docs/index.md) (MkDocs Material).

## License

MIT. See [LICENSE](LICENSE).
