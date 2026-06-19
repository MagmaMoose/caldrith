# Security

Caldrith holds a privileged position: it authenticates as a GitHub App with
repository **Administration** write across every installation, and it derives the
**desired state from the admin repo**. That makes the admin repo a high-value
target — treat write access to it as equivalent to admin over every managed repo.

## The admin-repo privilege-escalation risk

Anyone who can land a change in the admin repo's `settings.yml` on the **default
branch** can change settings on **every** repo in the installation. A push to the
default branch reconciles the whole account. So:

!!! danger "Lock down the admin repo"
    - **Require a CODEOWNERS review** on `.github/settings.yml`. Restrict approval
      to a small, trusted set.
    - **Require signed commits** and a protected default branch (no direct pushes,
      no force-push, required PR review).
    - Treat admin-repo write access as **account-admin-equivalent** and audit it
      accordingly.

## Dry-run on PRs limits the blast radius

Changes proposed on a **non-default** branch via a pull request are run in
**dry-run only**: Caldrith posts a GitHub **Check Run** with the computed diff and
**mutates nothing**. Reconciliation happens only after the change reaches the
default branch. This gives reviewers a precise, reviewable preview before anything
is applied.

## Secrets

- `PRIVATE_KEY`, `WEBHOOK_SECRET`, and the Redis URL are **env-only**, sourced
  from a secret store in production. They are never committed (`.gitignore` plus a
  pre-commit guard block `*.pem` / `.env*`).
- **Never log or echo the private key or any installation token.** Structured logs
  bind `installation_id` / `delivery_id` / `repo` — never credentials.
- Installation tokens are minted **per job, per installation** and never shared
  across installations.

## Webhook authenticity

Every webhook is verified with an **HMAC-SHA256** over the **raw** request body
using a constant-time comparison (`hmac.compare_digest`) before any parsing. The
signature is checked against the **unmodified bytes** — JSON is never parsed
first. Deliveries are deduplicated on `X-GitHub-Delivery` so a replayed delivery
cannot trigger a second reconcile.

## Reporting a vulnerability

Please report security issues privately via the repository's security advisory
process rather than opening a public issue.
