#!/usr/bin/env python3
"""Webhook listener: verifies a GitHub push to main and redeploys the api service."""
import hashlib
import hmac


def verify_signature(secret: bytes, body: bytes, signature_header: str | None) -> bool:
    if not signature_header or not signature_header.startswith("sha256="):
        return False
    expected = hmac.new(secret, body, hashlib.sha256).hexdigest()
    received = signature_header.removeprefix("sha256=")
    return hmac.compare_digest(expected, received)


def should_deploy(event_header: str | None, payload: dict) -> bool:
    if event_header != "push":
        return False
    return payload.get("ref") == "refs/heads/main"
