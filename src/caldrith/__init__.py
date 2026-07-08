"""Caldrith — a multi-tenant GitHub App that enforces GitHub config-as-code.

Caldrith is a long-running FastAPI service (not a CLI, not a Marketplace Action)
modeled on `github/safe-settings`. You install the App on a GitHub account, create
an *admin* repo containing ``.github/settings.yml``, and Caldrith reconciles each
repository's settings to match the declared desired state.

The ingest path (:mod:`caldrith.api`) stays thin: verify the webhook signature over
the raw body, dedup on the delivery id, enqueue an ARQ job, and return ``202``. The
heavy reconcile work runs in the ARQ worker (:mod:`caldrith.worker`), fanning out one
job per repository for full-account syncs.
"""

__version__ = "1.13.4"
