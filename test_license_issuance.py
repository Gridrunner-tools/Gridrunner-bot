"""Stripe webhook unit tests with a mocked registry (no DB, no network)."""
import hashlib
import hmac
import json
import os
import time
from unittest.mock import patch

import license_issuance as li


def payload(event_type="checkout.session.completed", product=None, sid="cs_test"):
    meta = {} if product is None else {"product": product}
    return json.dumps(
        {"type": event_type, "data": {"object": {"id": sid, "metadata": meta, "customer_details": {"email": None}}}}
    ).encode()


def sig(raw, secret="s", ts=None):
    ts = int(time.time() if ts is None else ts)
    digest = hmac.new(secret.encode(), str(ts).encode() + b"." + raw, hashlib.sha256).hexdigest()
    return f"t={ts},v1={digest}"


def test_valid_signature_fulfills(monkeypatch):
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "s")
    with patch.object(li, "issue_product_license", return_value="K") as f:
        assert li.handle_stripe_webhook(payload(), sig(payload())) == "K"
        assert f.call_args[0][1] == "gridrunner" and f.call_args[0][2] == "cs_test"


def test_invalid_and_expired_signatures_rejected(monkeypatch):
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "s")
    with patch.object(li, "issue_product_license", return_value="K"):
        for bad in ("t=1,v1=bad", sig(payload(), ts=1), sig(payload(), secret="wrong")):
            try:
                li.handle_stripe_webhook(payload(), bad)
                raise AssertionError("bad signature accepted")
            except ValueError:
                pass


def test_event_type_filter(monkeypatch):
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "s")
    raw = payload("payment_intent.succeeded")
    with patch.object(li, "issue_product_license", return_value="K") as f:
        assert li.handle_stripe_webhook(raw, sig(raw)) is None
        f.assert_not_called()


def test_unknown_product_rejected(monkeypatch):
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "s")
    raw = payload(product="sniper")
    with patch.object(li, "issue_product_license", return_value="K") as f:
        try:
            li.handle_stripe_webhook(raw, sig(raw))
            raise AssertionError("unknown product accepted")
        except ValueError:
            pass
        f.assert_not_called()


def test_duplicate_events_single_issuance(monkeypatch):
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "s")
    with patch.object(li, "issue_product_license", side_effect=["K", None]) as f:
        assert li.handle_stripe_webhook(payload(), sig(payload())) == "K"
        assert li.handle_stripe_webhook(payload(), sig(payload())) is None
        assert f.call_count == 2 and f.call_args_list[1][0][2] == f.call_args_list[0][0][2]


def test_email_failure_non_fatal(monkeypatch):
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "s")
    with patch.object(li, "issue_product_license", return_value="K"):
        assert li.handle_stripe_webhook(payload(), sig(payload())) == "K"
