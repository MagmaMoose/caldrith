# Architectural decisions (ADRs)

One Markdown file per decision, named `NNNN-short-title.md` (zero-padded,
incrementing). Run `/adr` to record one. Load these **only** when a task relates
to a past decision — they are not auto-loaded into context.

Good ADR candidates for this repo: the thin-ingest / heavy-worker split, the
pure-`config/` boundary (schema + diff are I/O-free), raw-body-before-parse
signature verification, the per-installation client/token isolation, the
`compare_deep` ignore-set + idempotency contract, the PR dry-run-via-Check-Run
behaviour, the configurable `base_url` seam for GHES, and the safe-settings schema
compatibility choice.
