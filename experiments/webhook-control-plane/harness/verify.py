"""GitHub webhook signature verification.

The invariant this module exists to guarantee: verification happens on the
**raw request body**, in constant time, and its failure is total — the
caller must not have consumed, recorded or acted on the delivery id before
this returns true.
"""
import hashlib
import hmac

SIGNATURE_HEADER = "X-Hub-Signature-256"
PREFIX = "sha256="


def compute_signature(secret: bytes, raw_body: bytes) -> str:
    return PREFIX + hmac.new(secret, raw_body, hashlib.sha256).hexdigest()


def verify(secret: bytes, raw_body: bytes, provided: str) -> bool:
    """Constant-time comparison; a missing or malformed header is a failure,
    never an exception the caller might swallow into success."""
    if not provided or not provided.startswith(PREFIX):
        return False
    return hmac.compare_digest(compute_signature(secret, raw_body), provided)
