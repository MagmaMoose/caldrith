# CLAUDE.md

Caldrith is a **multi-tenant GitHub App** (FastAPI service + ARQ/Redis worker, NOT
a CLI, NOT a Marketplace Action — there is **no** `action.yml`). It enforces GitHub
config-as-code: install the App, create an **admin repo** with
`.github/settings.yml`, and Caldrith **reconciles** repo settings to match —
modelled on github/safe-settings.

**P1 scope (narrow):** only the `repository:` block, and within it only three
fields end-to-end — `allow_auto_merge`, `delete_branch_on_merge`,
`allow_update_branch`. github.com, Organization **and** User accounts. Everything
else (branch protection, rulesets, labels, teams, suborgs, GHES, CEL policy) is
**deferred** — leave clean seams, don't implement.

@.claude/QUICK_START.md
@.claude/ARCHITECTURE_MAP.md
@.claude/COMMON_MISTAKES.md

## Conventions

Python 3.12, **uv + hatchling**, **Ruff** (line-length 100; select E,F,I,UP,B,SIM,RUF),
**pytest** + pytest-asyncio, **mypy**, full type hints. Every module starts with a
triple-quoted docstring (no per-file SPDX/license headers). Tests mirror modules
1:1 under `tests/`. SHA-pin external GitHub Actions with a `# vX.Y.Z` comment. MIT.

**Releases** are automated: pushing to `main` runs python-semantic-release (the
diatreme flow), cutting `vX.Y.Z` from conventional commits and bumping
`project.version` + `src/caldrith/__init__.py:__version__` — never bump by hand.

## Finding code & context

- Before locating unfamiliar code, read `./PROJECT_INDEX.json` first (module map).
  Loaded on demand — do **not** @-import it.
- Load `.claude/decisions` and `.claude/sessions` ONLY when the task relates to
  them. Full human docs live in `./docs` (MkDocs).

## [tooling]

- Prefer targeted line-range reads; use `PROJECT_INDEX.json` to find location first.
- grep/find/glob: return matching paths + matched lines only, not whole files.
- Flood-prone output: pipe through `head`/`tail`/`grep` or redirect to
  `.claude/last_output.txt` and read ranges.
- After a successful write/edit, trust it; don't re-read just to "verify".

## [maintenance]

- Bug that took >1h: append to `.claude/COMMON_MISTAKES.md`.
- Architectural decision: run `/adr`.
- Public behaviour/API/config/setup changed: run `/update-docs`.
- `PROJECT_INDEX.json` stale (new module, big refactor): regenerate the affected
  section only.
- Keep `CLAUDE.md` under ~500 tokens; push detail into on-demand `.claude/` files.
