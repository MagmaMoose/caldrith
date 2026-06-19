"""ARQ worker: job functions and enqueue/dedup helpers.

The FastAPI ingest enqueues jobs here; the worker process runs the heavy reconcile.
Full-account syncs fan out one ``reconcile_repo`` job per repository so a slow or
failing repo can't stall the others.
"""
