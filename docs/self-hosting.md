# Self-hosting

Caldrith is a long-running service (FastAPI ingest + an ARQ/Redis worker). You run
**one** deployment and register **one** GitHub App; many accounts can install it.

## 1. Register a GitHub App

Create a GitHub App on github.com with:

- **Webhook URL** pointing at your deployment's `POST /api/github/webhooks`.
- **Webhook secret** — a strong random string (becomes `WEBHOOK_SECRET`).
- **Permissions** (P1): repository **Administration** read/write (to update
  settings), **Contents** read (to fetch the admin repo's `settings.yml`), and
  **Checks** read/write (to post dry-run Check Runs).
- **Subscribe to events**: `push`, `repository`, `pull_request`.

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
