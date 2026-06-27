# Architecture

Caldrith is a long-running FastAPI service backed by an async ([ARQ](https://arq-docs.helpmanual.io/)
/ Redis) worker. The design splits cleanly into a **pure core** (config schema +
diff engine — no I/O) and the **side-effecting edges** (HTTP ingest, GitHub API,
the queue).

## The ingest contract

The HTTP path is **deliberately thin** and must stay that way — it has to finish
well inside GitHub's 10-second webhook timeout:

1. Read the **raw** request body.
2. **Verify** the `X-Hub-Signature-256` HMAC over those raw bytes.
3. **Dedup** on `X-GitHub-Delivery` (Redis `SETNX` with a TTL).
4. **Enqueue** an ARQ job.
5. Return **`202`**.

No reconcile work happens in the request. The worker does the heavy lifting; for a
full-account sync the planner **fans out one job per repo**.

When the admin-repo `push` modifies the settings file itself on the default branch,
Caldrith additionally **re-bases the admin repo's open PRs** onto the new baseline (the
`update_admin_prs` job): each open config PR is a proposed change whose dry-run preview
now diffs against a stale base, so any branch *behind* its base is merged forward with
GitHub's "Update branch". A PR already up to date — or one with a conflict the App
cannot auto-resolve — is skipped, so the sweep is idempotent and never blocks on one PR.

Besides the admin-repo `push` / `repository` / `pull_request` events, the ingest
also handles **drift events** (`label`, `milestone`, `member`,
`branch_protection_rule`, `repository_ruleset`, `public`): an out-of-band change to
a managed setting re-reconciles the affected repo (or, for an org-level ruleset, the
org) back to the declared state. A convergence that finds no drift issues no further
write, so self-healing does not loop.

## Module map

```
src/caldrith/
  settings.py              # pydantic-settings AppConfig (env: APP_ID, PRIVATE_KEY, …)
  api/
    app.py                 # create_app() factory; GET /healthz, GET /readyz
    webhooks.py            # POST /: raw -> verify -> dedup -> enqueue -> 202
    security.py            # verify_signature() — hmac.compare_digest over RAW body
    ratelimit.py           # slowapi limiter (per-IP + per-installation)
  auth/
    client.py              # GitHubClientFactory.for_installation() (githubkit, per-install)
  config/                  # ★ PURE CORE — no I/O, heavily tested
    schema.py              #   tiered SafeSettingsConfig / RepoScoped / OrganizationSettings (mirrors safe-settings)
    loader.py              #   fetch settings.yml via API -> yaml.safe_load -> validate
    diff.py                #   compare_deep() -> Diff(additions, modifications, deletions)
  reconcile/
    base.py                # RepoTier protocol + uniform TierResult; per-tier reconcile adapter
    runner.py              # REPO_TIERS registry; flat loop load -> select -> resolve overlay -> apply; dry-run Check Run
    planner.py             # list target repos (Organization vs User aware)
    selection.py           # select_targets(): admin/.github/archived exclusions + restrictedRepos globs
    overlay.py             # resolve_for_repo(): base -> suborgs -> repos overlay merge (pure)
    org.py                 # run_org_reconcile(): orgs.update + org actions/limits/property-defs/rulesets
    repository.py … files.py  # one module per repo-scoped tier, each exposing a TIER
    pr_update.py           # update_open_prs(): re-base the admin repo's open PRs behind their base
  worker/
    worker.py              # ARQ WorkerSettings + reconcile_installation / reconcile_repo / reconcile_org / update_admin_prs jobs
    queue.py               # enqueue helpers + dedup_delivery() (SETNX)
  audit/
    logging.py             # structlog JSON config; bind installation_id/delivery_id/repo
```

A machine-readable version (exports, dependencies) lives at
[`PROJECT_INDEX.json`](https://github.com/magmamoose/caldrith/blob/main/PROJECT_INDEX.json)
in the repo root.

## The design rule

`config/` (`schema` → `loader` → `diff`) is the **pure core**. The diff engine and
schema take already-parsed dicts / models and return verdicts; they do not import
`httpx`, `githubkit`, ARQ, or FastAPI.

!!! warning "Keep the boundary"
    Do **not** import GitHub-client or queue code into `config/`. That separation
    is what makes the diff engine deterministic and unit-testable with synthetic
    dicts — no live repo required.

## The tier registry

Caldrith reconciles many independent **tiers** — the `repository` block, branch
protection, labels, collaborators, environments, rulesets, … — each owning a slice
of `settings.yml` and its own GitHub endpoints. To keep the orchestrator from
growing a branch per tier, every tier exposes a uniform `RepoTier` (in
`reconcile.base`):

- `name` — a stable identifier for logs and the dry-run Check Run.
- `configured(config)` — whether the admin config asks this tier to do anything (so
  we skip listing repos / building clients when nothing is declared).
- `reconcile(client, target, config, *, dry_run)` — do the work for one repo and
  return zero or more `TierResult` rows.

Each tier keeps its own typed applier and result dataclass (unit-tested in
isolation); a module-level `reconcile` **adapter** maps those onto the uniform
`TierResult` the runner aggregates. `runner.REPO_TIERS` is the ordered registry, and
`run_reconcile` is a **flat loop** over it — so adding a tier is one import + one
list entry, no new branches. Order matters where tiers interact: `repository` runs
first (it can rename the default branch or flip features others depend on). Per-tier,
per-repo failures are isolated and logged, so one bad repo never aborts the run.

## Data flow (push to the admin repo)

1. **`webhooks`** verifies + dedups + enqueues `reconcile_installation`.
2. **`worker.reconcile_installation`** builds a per-installation client via
   **`auth.client`**, loads the admin config with **`config.loader`**, enqueues a
   `reconcile_org` job if an `organization:` block is declared, then uses
   **`reconcile.planner`** to list target repos (Organization vs User aware) and
   **`reconcile.selection`** to drop excluded ones.
3. It fans out a `reconcile_repo` job per managed repo.
4. **`worker.reconcile_repo`** calls **`runner.run_reconcile`**, which resolves the
   effective per-repo config via **`reconcile.overlay.resolve_for_repo`** (base →
   suborgs → repos) and runs every *configured* tier in **`REPO_TIERS`** against the
   repo — each reading live state, diffing, and writing **only on a real change**.

For `pull_request` events the same path runs in **dry-run**: it computes the diff
across all tiers and posts a GitHub **Check Run** (`caldrith/settings`, conclusion
`neutral`) summarising it per tier, mutating nothing.

## The org reconcile path

Organization-scoped settings (`orgs.update`, org Actions policy, interaction limits,
custom-property definitions, org rulesets) apply once per **account**, not per repo,
so they have a separate entrypoint: **`reconcile.org.run_org_reconcile`**, run by the
**`reconcile_org`** worker job. `reconcile_installation` enqueues it when an
`organization:` block is present; the `repository_ruleset` drift event re-enqueues it
for an org-level ruleset change. On a non-Organization account it is a graceful no-op.

## Idempotency & isolation

`compare_deep` ignores keys containing `url` plus `{id, node_id}`, and reports
`has_changes`. Each tier writes only when its slice actually differs (the
generically-diffed tiers via `compare_deep`; the bespoke ones via their own canonical
comparison), so applying the same config twice yields one mutation. Every job
authenticates as **its own installation** with a freshly minted token — never shared.

## Testing

Tests mirror modules 1:1 under `tests/`. The pure core is tested with synthetic
dicts; the ingest path uses FastAPI's `TestClient` + `fakeredis` + `respx`, and
the GitHub client boundary is mocked, so nothing requires a live App, Redis, or
the GitHub API.
