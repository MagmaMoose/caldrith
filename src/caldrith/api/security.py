"""Webhook signature verification.

GitHub signs webhook deliveries with HMAC-SHA256 over the *raw* request body,
sending the digest in the ``X-Hub-Signature-256`` header as ``sha256=<hex>``. We
verify against the raw bytes (never the re-serialized JSON) using a constant-time
comparison.

We hand-roll the HMAC rather than calling ``githubkit.webhooks.verify`` because that
helper (githubkit 0.16.0) re-normalizes dict/model payloads before hashing, whereas
the only correct input here is the exact bytes GitHub hashed. Hand-rolling keeps the
raw-body guarantee explicit and dependency-free.
"""

from __future__ import annotations

import hashlib
import hmac

_SIGNATURE_PREFIX = "sha256="


def verify_signature(secret: str, body: bytes, sig256_header: str | None) -> bool:
    """Return ``True`` iff ``sig256_header`` is a valid HMAC-SHA256 of ``body``.

    Args:
        secret: The shared webhook secret (``WEBHOOK_SECRET``).
        body: The raw request body bytes, exactly as received.
        sig256_header: The ``X-Hub-Signature-256`` header value, e.g.
            ``"sha256=abc123..."``. May be ``None`` if the header is absent.

    The comparison is constant-time via :func:`hmac.compare_digest`.
    """
    if not sig256_header or not sig256_header.startswith(_SIGNATURE_PREFIX):
        return False

    expected = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    received = sig256_header[len(_SIGNATURE_PREFIX) :]
    return hmac.compare_digest(expected, received)
