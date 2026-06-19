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

## What it does (and the one thing, today)

You describe the desired state once, in the admin repo, and Caldrith keeps every
target repo in line with it.

Caldrith is built in vertical slices. **Right now (P1) it enforces only the
`repository:` block, and within it only three merge settings end-to-end:**
`allow_auto_merge`, `delete_branch_on_merge`, and `allow_update_branch`. The
config **schema** mirrors safe-settings, so other `repository:` fields parse and
validate — they just are not applied yet. See [Configuration](configuration.md).

## How it reacts

| Event | Trigger | Effect |
| --- | --- | --- |
| `push` | to the **admin repo's default branch** | Reconcile **all** target repos (one job per repo). |
| `repository` | `created` / `edited` | Reconcile **that** repo. |
| `pull_request` | settings change on a **non-default** branch | **Dry run** — post a Check Run with the diff. Mutates nothing. |

Reconcile is **idempotent**: Caldrith diffs live settings against the desired
state and `PATCH`es only on a real change.

## Next steps

- [Configuration](configuration.md) — the `settings.yml` schema and the three P1
  fields.
- [Architecture](architecture.md) — ingest → queue → worker, and the pure core.
- [Self-hosting](self-hosting.md) — run the service and register the App.
- [Security](security.md) — the admin-repo blast radius and how to lock it down.

## License

MIT.
