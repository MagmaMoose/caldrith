# Caldrith

Caldrith is a **multi-tenant GitHub App** that enforces GitHub **configuration as
code**. You install it on an account, create an **admin repo** containing
`.github/settings.yml`, and Caldrith continuously **reconciles your repositories'
settings to match** — the same model as
[github/safe-settings](https://github.com/github/safe-settings), reimplemented as
a long-running Python service.

It is **not** a CLI and **not** a Marketplace Action. Caldrith is a FastAPI
service that receives GitHub webhooks and applies your declared configuration
through the GitHub API.

!!! note "Multi-tenant, multi-platform, all visibilities"
    One deployment serves many installations. It works on both **Organization**
    and **User** accounts on github.com today (GHES is on the roadmap), and
    reconciles **private, public, and internal** repos alike.

## What it does

You describe the desired state once, in the admin repo, and Caldrith keeps every
target repo in line with it.

Caldrith enforces a broad slice of the safe-settings surface: the full
`repository:` block, branch protection, repository **and** organization rulesets,
labels, milestones, collaborators, teams, autolinks, custom properties, interaction
limits, Actions settings, variables, secrets (presence), environments, Pages,
provisioned files (required workflows via PR), the `organization:` block, and
suborg/per-repo overlays. The config **schema** mirrors safe-settings, so an
existing safe-settings `settings.yml` is compatible. See
[Configuration](configuration.md) for the full per-tier reference.

## How it reacts

| Event | Trigger | Effect |
| --- | --- | --- |
| `push` | to the **admin repo's default branch** | Reconcile **all** target repos (one job per repo). |
| `repository` | `created` / `edited` | Reconcile **that** repo. |
| `pull_request` | settings change on a **non-default** branch | **Dry run** — post a Check Run with the diff. Mutates nothing. |
| drift events | `label` / `milestone` / `member` / `branch_protection_rule` / `repository_ruleset` / `public` | **Self-heal** — re-reconcile the affected repo (or org). |

Reconcile is **idempotent**: Caldrith diffs live settings against the desired
state and writes only on a real change.

## Next steps

- [Configuration](configuration.md) — the `settings.yml` schema, tier by tier.
- [Architecture](architecture.md) — ingest → queue → worker, and the pure core.
- [Self-hosting](self-hosting.md) — run the service and register the App.
- [Security](security.md) — the admin-repo blast radius and how to lock it down.

## License

MIT.
