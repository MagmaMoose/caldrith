# Common mistakes & footguns

- **Keep the ingest path THIN.** `POST /` only verifies,
  dedups, enqueues, and returns `202` — all under GitHub's 10s timeout. **No**
  reconcile, no GitHub API calls in the request handler. Heavy work runs in the
  ARQ worker; full-account syncs fan out one job per repo.

- **Verify the signature over the RAW body, before any parse.** Read
  `await request.body()` first and HMAC-SHA256 those exact bytes with
  `hmac.compare_digest`. If you `await request.json()` (or otherwise re-serialize)
  before verifying, the bytes differ and every signature fails.

- **Never log or echo the private key or any token.** `PRIVATE_KEY` /
  `WEBHOOK_SECRET` are env-only and must never hit logs, the tree, or a response.
  Structured logs bind `installation_id` / `delivery_id` / `repo` — never
  credentials. The `.gitignore` + a pre-commit guard block `*.pem` / `.env*`.

- **Keep `config/` pure.** No `githubkit`, ARQ, FastAPI, or network imports in
  `schema.py` / `loader.py` / `diff.py`. The diff engine is unit-tested with
  synthetic dicts; the GitHub-client boundary lives in `auth/` + `reconcile/`.

- **The diff must be idempotent.** `compare_deep` ignores keys containing `url`
  plus `{id, node_id}`; the applier `PATCH`es **only** when `has_changes`. A test
  must assert that applying the same config twice yields exactly one mutation.

- **One client per installation, freshly built.** Use githubkit
  `AppInstallationAuthStrategy` and build a NEW client per job. Never share a token
  across installations. `base_url` is configurable — never hardcode
  `api.github.com` (GHES is on the roadmap).

- **Account type matters in the planner.** Listing target repos differs for
  **Organization** vs **User** installations — handle both.

- **PR events are DRY-RUN only.** On a settings PR (non-default branch) post a
  Check Run with the diff and mutate **nothing**. Reconcile happens only on push
  to the admin repo's default branch.
