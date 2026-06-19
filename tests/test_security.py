"""Tests for webhook signature verification."""

from __future__ import annotations

import hashlib
import hmac

from caldrith.api.security import verify_signature

SECRET = "test-secret"


def _sig(body: bytes, secret: str = SECRET) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def test_valid_signature_accepted() -> None:
    body = b'{"action":"opened"}'
    assert verify_signature(SECRET, body, _sig(body)) is True


def test_tampered_body_rejected() -> None:
    body = b'{"action":"opened"}'
    sig = _sig(body)
    assert verify_signature(SECRET, b'{"action":"closed"}', sig) is False


def test_wrong_secret_rejected() -> None:
    body = b'{"x":1}'
    assert verify_signature(SECRET, body, _sig(body, "other-secret")) is False


def test_missing_header_rejected() -> None:
    assert verify_signature(SECRET, b"{}", None) is False


def test_header_without_prefix_rejected() -> None:
    body = b"{}"
    raw = hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()
    assert verify_signature(SECRET, body, raw) is False  # no "sha256=" prefix


def test_empty_body_signed_correctly() -> None:
    body = b""
    assert verify_signature(SECRET, body, _sig(body)) is True


def test_signature_is_case_sensitive_hex() -> None:
    body = b'{"x":1}'
    sig = _sig(body).upper().replace("SHA256=", "sha256=")
    # Hex digest is lowercase; uppercasing it must fail constant-time compare.
    assert verify_signature(SECRET, body, sig) is False
