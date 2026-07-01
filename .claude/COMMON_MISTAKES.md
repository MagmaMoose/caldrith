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

- **Add a tier via the registry, not a new runner branch.** A repository-scoped
  tier is a module exposing `async def reconcile(client, target, config, *,
  dry_run) -> list[TierResult]` plus a module-level `TIER = RepoTier(name,
  configured, reconcile)`; register it in `runner.REPO_TIERS`. The runner is a flat
  loop — don't grow per-tier `if` branches. `configured(config)` is checked on the
  **resolved** (overlay-merged) config per repo, so a tier declared only in a
  `repos:`/`suborgs:` overlay still runs.

- **`topics` and `repository.security` are NOT `repos.update`.** Both live under the
  `repository:` block in the schema for safe-settings compatibility but are routed to
  their own tiers/endpoints (`replace_all_topics`; the Dependabot toggles). The
  repository applier `pop`s `security` and never PATCHes `topics`. Don't add either to
  the `repos.update` payload — it errors or silently no-ops.

- **Full-replace tiers PRUNE undeclared items.** `labels`, `collaborators`, `teams`,
  `autolinks`, `variables` delete anything not declared (and `secrets` only when
  `prune: true`). A partial list is a *complete* statement — declaring one label
  removes the rest. `milestones`, `environments`, `rulesets` are create/update-only
  (never pruned) because they carry associations/secrets/history.

- **Secret VALUES are write-only — manage presence only.** You cannot read a secret
  back, so never diff or rotate a value. The `secrets` tier ensures a declared secret
  EXISTS, creating a missing one from `CALDRITH_SECRET_<NAME>` (sealed-box encrypted
  via PyNaCl with the repo public key). No env value → report the gap, don't fabricate.
  Never log a secret value or the decrypted env var.

- **Org settings are a separate, once-per-account path.** `organization:` is reconciled
  by `run_org_reconcile` (the `reconcile_org` job), gated on
  `account_type == "Organization"` — a graceful no-op otherwise. It is NOT in
  `REPO_TIERS` and does not run per repo.

- **Reactive drift events converge, they don't loop.** `label`/`member`/
  `branch_protection_rule`/`repository_ruleset`/`public`/`milestone` events re-reconcile
  the affected repo (or org). Caldrith's own write echoes back as an event, but the
  re-reconcile finds no drift and issues no write — so it self-terminates. Keep new
  tiers idempotent or this guarantee breaks.

- **A managed branch name must never be a path-prefix of another.** The files tier's
  per-base branches are `ci/caldrith/managed-files` (default) and
  `ci/caldrith/managed-files-<base>` (others) — a **hyphen**, not `/`. A `/` would nest
  the base branch under the default one (`…/staging`), and Git can't hold both a ref and
  a ref nested under it (a directory/file conflict): once `ci/caldrith/managed-files`
  exists, creating `ci/caldrith/managed-files/staging` fails with `cannot lock ref`
  (HTTP 422), so that base silently never provisions (default PR opens, staging PR never
  does). `respx` mocks don't model Git's ref rules, so this passed unit tests but broke
  live — assert branch names are siblings, and verify multi-branch flows against real Git
  behaviour, not just mocks.
