"""HTTP ingest layer: FastAPI app factory, webhook endpoint, and security helpers.

This layer is intentionally thin. Per the design contract it only verifies the
signature, dedups the delivery, enqueues an ARQ job, and returns ``202`` — well
under GitHub's 10s webhook timeout. All heavy work happens in the worker.
"""
