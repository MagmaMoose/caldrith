# Architecture map

Caldrith is a long-running **FastAPI** service plus an **ARQ/Redis** worker. The
design splits into a **pure core** (config schema + diff — no I/O) and the
**side-effecting edges** (HTTP ingest, GitHub API, the queue).

## The ingest contract (keep it THIN)

`POST /` must finish well inside GitHub's 10s timeout:
read **raw** body → **verify** HMAC-SHA256 over those bytes → **dedup** on
`X-GitHub-Delivery` (Redis `SETNX`+TTL) → **enqueue** an ARQ job → **`202`**.
No reconcile in the request. Full-account syncs **fan out one job per repo** (plus
one `reconcile_org` job for Organization accounts).

## Pure core vs edges

- **PURE (`src/caldrith/config/`)** — `schema.py` (all tier models on `RepoScoped` +
  `OrganizationSettings` + overlays, mirrors safe-settings), `loader.py` (fetch
  settings.yml → `yaml.safe_load` → validate), `diff.py` (`compare_deep` → additions /
  modifications / deletions / has_changes; ignore `url`-keys + `id`/`node_id`).
  No `githubkit`, ARQ, or FastAPI imports here.
- **EDGES** — `api/` (app factory, webhooks + reactive drift events, security HMAC,
  slowapi ratelimit), `auth/client.py` (GitHubClientFactory.for_installation —
  githubkit AppInstallationAuthStrategy, per-install token, configurable base_url),
  `reconcile/` (**registry design**: `base.py` = `TierResult` + `RepoTier`; each tier
  module — repository/security/topics/labels/milestones/collaborators/teams/autolinks/
  custom_properties/interactions/actions/variables/secrets/environments/pages/ruleset/
  files/branch — exposes a `reconcile()` adapter + a `TIER`; `runner.REPO_TIERS` is a
  flat per-repo loop; `org.py` = `run_org_reconcile` for the `organization` block;
  `overlay.py` = `resolve_for_repo` (base → suborgs → repos); planner lists repos /
  resolves account type), `worker/` (jobs reconcile_installation / reconcile_repo /
  reconcile_org; queue.py dedup_delivery), `audit/logging.py` (structlog JSON).

## Webhook → effect

| Event | Trigger | Effect |
| --- | --- | --- |
| `push` | admin repo default branch | reconcile ALL repos (fan out per repo) + org |
| `repository` | created/edited | reconcile THAT repo |
| `pull_request` | settings change, non-default branch | DRY-RUN → Check Run; mutate nothing |
| `label`/`milestone`/`member`/`branch_protection_rule`/`repository_ruleset`/`public` | out-of-band change | self-heal: reconcile the affected repo (or org) |

## Idempotency & isolation

Diff ignores `url`-bearing keys + `{id, node_id}`; applier `PATCH`es only on a
real change → applying twice = one mutation. Every job mints its **own**
installation token; never shared across installations. `base_url` is configurable
(default `https://api.github.com`) for the GHES future.

Full module table + dependencies: read `./PROJECT_INDEX.json`.
