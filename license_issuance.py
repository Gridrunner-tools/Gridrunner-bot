"""Stripe checkout fulfillment: issue a GridRunner license and email the key.

Limit Orders is bundled with the GridRunner license (owner-approved), so a
completed checkout always produces one GridRunner key. Failures are contained:
a license row is written before emailing, and email delivery failure is logged
but never turns a successful issuance into a 500.
"""
import hashlib
import hmac
import json
import logging
import os
import secrets
import sys
import time
import urllib.request
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "scripts"))
from issue_license import generate_key  # noqa: E402
from license_registry import _database_url, _driver, LicenseRegistryUnavailable  # noqa: E402

log = logging.getLogger(__name__)
PRODUCTS = {"gridrunner"}


def issue_product_license(email, product, stripe_session_id, days=365):
    """Insert one license key for *stripe_session_id*; returns key or None.

    None means the session was already fulfilled (ON CONFLICT DO NOTHING) —
    replay idempotency, never a second key.
    """
    if product not in PRODUCTS or not stripe_session_id:
        raise ValueError("invalid product or session")
    key = generate_key()
    expires = None if not days else datetime.now(timezone.utc) + timedelta(days=days)
    try:
        with _driver().connect(_database_url(), connect_timeout=10) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO licenses (license_key, license_type, expires_at, customer_email, stripe_session_id, product) "
                    "VALUES (%s, 'full', %s, %s, %s, %s) "
                    "ON CONFLICT (stripe_session_id) DO NOTHING RETURNING license_key",
                    (key, expires, email, stripe_session_id, product),
                )
                row = cur.fetchone()
                conn.commit()
    except Exception as exc:
        raise LicenseRegistryUnavailable("License registry write failed") from exc
    if not row:
        return None
    try:
        if email:
            send_license_email(email, row[0], product)
    except Exception:
        log.warning("License email delivery failed for session %s", stripe_session_id)
    return row[0]


def send_license_email(to, key, product):
    """Provider-agnostic transactional email (Resend-style HTTP API)."""
    api = os.environ.get("EMAIL_API_KEY")
    sender = os.environ.get("EMAIL_FROM")
    if not api or not sender:
        return
    payload = json.dumps(
        {
            "from": sender,
            "to": [to],
            "subject": "Your GridRunner license key",
            "text": f"Your {product} license key: {key}",
        }
    ).encode()
    req = urllib.request.Request(
        os.environ.get("EMAIL_API_URL", "https://api.resend.com/emails"),
        payload,
        {"Authorization": f"Bearer {api}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=10):
        pass


def _verify_signature(raw, signature, secret):
    """Stripe v1: HMAC-SHA256(secret, \"<ts>.<payload>\") with 5 min tolerance."""
    parts = {}
    for piece in (signature or "").split(","):
        if "=" in piece:
            k, v = piece.strip().split("=", 1)
            parts.setdefault(k, []).append(v)
    if not secret:
        raise ValueError("webhook secret is not configured")
    try:
        ts = int(parts.get("t", [""])[0])
    except (IndexError, ValueError):
        raise ValueError("invalid webhook signature")
    if abs(time.time() - ts) > 300:
        raise ValueError("expired webhook signature")
    expected = hmac.new(secret.encode(), str(ts).encode() + b"." + raw, hashlib.sha256).hexdigest()
    if not any(hmac.compare_digest(expected, v) for v in parts.get("v1", [])):
        raise ValueError("invalid webhook signature")


def handle_stripe_webhook(payload, signature):
    """Validate and fulfill a Stripe event; None = ignored event type."""
    raw = payload if isinstance(payload, bytes) else payload.encode()
    _verify_signature(raw, signature, os.environ.get("STRIPE_WEBHOOK_SECRET", ""))
    event = json.loads(raw)
    if event.get("type") != "checkout.session.completed":
        return None
    obj = event.get("data", {}).get("object", {})
    product = (obj.get("metadata") or {}).get("product", "gridrunner")
    if product not in PRODUCTS:
        raise ValueError("unknown product")
    email = (obj.get("customer_details") or {}).get("email")
    return issue_product_license(email, product, obj.get("id"))
