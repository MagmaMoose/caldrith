# Self-hosting

Caldrith is a long-running service (FastAPI ingest + an ARQ/Redis worker). You run
**one** deployment and register **one** GitHub App; many accounts can install it.

## 1. Register a GitHub App

Create a GitHub App on github.com with:

- **Webhook URL** pointing at your deployment's `POST /`.
- **Webhook secret** — a strong random string (becomes `WEBHOOK_SECRET`).
- **Permissions**: repository **Administration** read/write (settings, branch
  protection, rulesets, autolinks), **Contents** read/write (fetch the admin repo's
  `settings.yml`; open provisioning PRs), **Pull requests** read/write (provisioning
  PRs), **Checks** read/write (dry-run Check Runs), **Issues** read/write (labels,
  milestones), **Members** / **Administration** as needed for collaborators and
  teams, **Secrets** / **Variables** / **Environments** read/write, **Actions** /
  **Workflows** read/write, **Pages** read/write, and **Organization
  administration** read/write (the `organization:` block, org rulesets, org custom
  properties). Grant only the tiers you use.
- **Subscribe to events**: `push`, `repository`, `pull_request`, plus the
  self-healing drift events `label`, `milestone`, `member`,
  `branch_protection_rule`, `repository_ruleset`, and `public`.

Generate a **private key** (PEM) and note the **App ID**.

## 2. Configuration (env vars)

Caldrith reads all secrets and settings from the environment via
pydantic-settings. In production these come from your platform's secret store (for
example a Kubernetes env populated by an OCI Vault `ExternalSecret`).

| Variable | Default | Purpose |
| --- | --- | --- |
| `APP_ID` | — | GitHub App ID. |
| `PRIVATE_KEY` | — | App private key, **PEM string**. |
| `WEBHOOK_SECRET` | — | Webhook HMAC secret. |
| `REDIS_URL` | — | Redis connection (ARQ queue + dedup). |
| `GITHUB_API_URL` | `https://api.github.com` | API base; set for GHES later. |
| `ADMIN_REPO` | `admin` | Admin repo name per installation. |
| `CONFIG_PATH` | `.github` | Directory holding the settings file. |
| `SETTINGS_FILE_PATH` | `settings.yml` | The settings file name. |
| `MANUAL_TRIGGER_TOKEN` | — | Bearer token guarding `POST /reconcile`. Unset disables the endpoint (it returns 404). Set to a long random string to enable on-demand reconciles. |
| `RECONCILE_CRON_MINUTES` | `0` | Periodic full reconcile across every installation, every N minutes (`0` disables). Belt-and-braces against missed webhooks. |
| `CALDRITH_SECRET_<NAME>` | — | Value for a declared `secrets:` entry `<NAME>` (used to create a missing repo secret; never read back from GitHub). |

!!! danger "Never put secrets in the repo"
    `PRIVATE_KEY`, `WEBHOOK_SECRET`, and friends are **env-only**. The `.gitignore`
    and a pre-commit guard block `*.pem` / `.env*` from being committed. Never log
    or echo the private key or any installation token.

## 3. Run it

The API and the worker are two processes sharing Redis:

```sh
uv sync
# API (webhook ingest)
uv run uvicorn caldrith.api.app:create_app --factory --host 0.0.0.0 --port 8000
# Worker (reconcile jobs)
uv run arq caldrith.worker.worker.WorkerSettings
```

`GET /healthz` is a dependency-free liveness probe; `GET /readyz` checks that
Redis is reachable.

## 4. Install on an account

Install the App on an Organization or User account, grant it the repos you want
managed (including the admin repo), and push a `.github/settings.yml`. See
[Configuration](configuration.md).

## 5. Operations / break-glass

Caldrith normally reconciles on the admin repo's `push`/`pull_request` webhook. Two
fallbacks make it survive missed deliveries (webhook misconfig, secret rotation, network
blips):

**Manual trigger** — `POST /reconcile`, guarded by `MANUAL_TRIGGER_TOKEN`. Enqueues a full
reconcile for one or every installation; returns immediately (the work runs in the
worker). 401 on a bad/missing token, 404 when the env var is unset (the endpoint isn't
advertised).

```sh
# Set MANUAL_TRIGGER_TOKEN in the env, then:
curl -X POST https://caldrith.example.com/reconcile \
  -H "Authorization: Bearer $MANUAL_TRIGGER_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"owner":"MagmaMoose"}'        # one org; omit to fan out across every installation
```

**Periodic re-reconcile** — set `RECONCILE_CRON_MINUTES` to (e.g.) `60`. The worker runs
`reconcile_all_installations` on that minute cadence: a single `apps.list_installations`
call, then one `reconcile_installation` job per installation. Every job is idempotent
(the reconcile diffs desired vs. live and writes only on drift), so duplicate runs are
no-ops and a stuck webhook can't strand an org indefinitely.

## Local development

```sh
uv sync                        # install deps + dev tools (pytest, ruff, mypy)
uv run pytest -q               # run the test suite
uv run ruff check .           # lint
uv run ruff format --check .  # format check (CI gate)
uv run mypy src               # type-check
```

## Building these docs

```sh
uv run --group docs mkdocs serve   # live preview at http://127.0.0.1:8000
uv run --group docs mkdocs build   # render to ./site (gitignored)
```
