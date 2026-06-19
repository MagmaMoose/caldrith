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

## Module map

```
src/caldrith/
  settings.py              # pydantic-settings AppConfig (env: APP_ID, PRIVATE_KEY, …)
  api/
    app.py                 # create_app() factory; GET /healthz, GET /readyz
    webhooks.py            # POST /api/github/webhooks: raw -> verify -> dedup -> enqueue -> 202
    security.py            # verify_signature() — hmac.compare_digest over RAW body
    ratelimit.py           # slowapi limiter (per-IP + per-installation)
  auth/
    client.py              # GitHubClientFactory.for_installation() (githubkit, per-install)
  config/                  # ★ PURE CORE — no I/O, heavily tested
    schema.py              #   RepositorySettings + SafeSettingsConfig (mirrors safe-settings)
    loader.py              #   fetch settings.yml via API -> yaml.safe_load -> validate
    diff.py                #   compare_deep() -> Diff(additions, modifications, deletions)
  reconcile/
    planner.py             # list target repos (Organization vs User aware) + build plans
    repository.py          # RepositoryApplier: diff live vs desired, PATCH or NopResult
    runner.py              # orchestrate load -> plan -> apply; dry-run posts a Check Run
  worker/
    worker.py              # ARQ WorkerSettings + reconcile_installation / reconcile_repo jobs
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

## Data flow (push to the admin repo)

1. **`webhooks`** verifies + dedups + enqueues `reconcile_installation`.
2. **`worker.reconcile_installation`** builds a per-installation client via
   **`auth.client`**, loads the admin config with **`config.loader`**, and uses
   **`reconcile.planner`** to list target repos (Organization vs User aware).
3. It fans out a `reconcile_repo` job per repo.
4. **`reconcile.repository.RepositoryApplier`** reads live settings, runs
   **`config.diff.compare_deep`**, and `PATCH`es **only on a real change**.

For `pull_request` events the same path runs in **dry-run**: it computes the diff
and posts a GitHub **Check Run** summarising it, mutating nothing.

## Idempotency & isolation

`compare_deep` ignores keys containing `url` plus `{id, node_id}`, and reports
`has_changes`. The applier `PATCH`es only when changes exist, so applying the same
config twice yields one mutation. Every job authenticates as **its own
installation** with a freshly minted token — never shared.

## Testing

Tests mirror modules 1:1 under `tests/`. The pure core is tested with synthetic
dicts; the ingest path uses FastAPI's `TestClient` + `fakeredis` + `respx`, and
the GitHub client boundary is mocked, so nothing requires a live App, Redis, or
the GitHub API.
