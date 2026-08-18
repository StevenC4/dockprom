"""HMAC signing for gateway -> backend dispatch.

This module is the wire contract. Every backend MUST verify exactly what this signs, so
this file is duplicated byte-for-byte into each backend repo. If you change it here, change
it there, and update the shared test vector (see ``tests/test_sign.py``) in all of them.

    basestring = "<timestamp>" + "." + <raw body bytes>
    signature  = "v1=" + hex(HMAC_SHA256(secret, basestring))

Modelled on Slack's own request signing so it stays boring and reviewable.
"""

from __future__ import annotations

import hashlib
import hmac
import time

TIMESTAMP_HEADER = "X-Homelab-Timestamp"
SIGNATURE_HEADER = "X-Homelab-Signature"

SIGNATURE_PREFIX = "v1="
DEFAULT_MAX_SKEW_SECONDS = 300


def sign(secret: str, timestamp: int, raw_body: bytes) -> str:
    """Return the signature for ``raw_body`` at ``timestamp``.

    ``raw_body`` must be the exact bytes put on the wire. Signing a re-serialized parse of
    the body will produce a signature the receiver cannot reproduce, because key order and
    whitespace are not stable across serializers.
    """
    basestring = f"{timestamp}.".encode() + raw_body
    digest = hmac.new(secret.encode(), basestring, hashlib.sha256).hexdigest()
    return SIGNATURE_PREFIX + digest


def verify(
    secret: str,
    timestamp_header: str | None,
    signature_header: str | None,
    raw_body: bytes,
    *,
    now: int | None = None,
    max_skew_seconds: int = DEFAULT_MAX_SKEW_SECONDS,
) -> bool:
    """Verify a dispatch signature. Returns False rather than raising, for any failure.

    Enforces the skew window *before* comparing, so a captured-and-replayed envelope stops
    being accepted once it ages out.
    """
    if not timestamp_header or not signature_header:
        return False

    try:
        timestamp = int(timestamp_header)
    except (TypeError, ValueError):
        return False

    now = int(time.time()) if now is None else now
    if abs(now - timestamp) > max_skew_seconds:
        return False

    expected = sign(secret, timestamp, raw_body)
    return hmac.compare_digest(expected, signature_header)
