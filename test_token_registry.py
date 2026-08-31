#!/usr/bin/env python3
"""
Regression tests for the token-registry live gates.

Root cause the task fixes: the owner's token_registry.py defined a full
``authorize_trade(symbol, liquidity_usd, price_impact_pct, route)`` gate, but
main.py only ever called it from ``place_order`` with a single arg (status-only),
and never from ``jupiter_swap`` / ``_raydium_execute_swap`` / ``dex_swap``. The
live liquidity / price-impact / route checks were therefore DEAD CODE, and the
docstring-referenced ``get_token_liquidity_usd`` / ``estimate_price_impact_pct``
helpers did not exist at all.

These tests pin the gate logic itself (status / liquidity floor / price-impact
cap / route restriction) plus the new ``_enforce_token_gate`` wiring (quote
currencies skipped, "uncertain = refuse" when live data is missing, and the
call into authorize_trade with live values + route).
"""
import unittest
from unittest import mock

import main
from main import _enforce_token_gate, get_token_liquidity_usd, estimate_price_impact_pct
from token_registry import authorize_trade, get_registry_entry, TOKEN_REGISTRY


class TestAuthorizeTrade(unittest.TestCase):
    """Pin the registry's own gate behavior (status + thresholds + route)."""

    def test_unknown_symbol_refused(self):
        ok, reason = authorize_trade("NOTATOKEN")
        self.assertFalse(ok)
        self.assertIn("not in the token registry", reason)

    def test_pending_verification_refused(self):
        ok, reason = authorize_trade("BNB")
        self.assertFalse(ok)
        self.assertIn("PENDING_VERIFICATION", reason)

    def test_rejected_refused(self):
        ok, reason = authorize_trade("STARLINK")
        self.assertFalse(ok)
        self.assertIn("REJECTED", reason)

    def test_approved_status_passes(self):
        ok, reason = authorize_trade("SOL")
        self.assertTrue(ok)
        self.assertEqual(reason, "ok")

    def test_liquidity_floor_blocks_low_liquidity(self):
        # SOL floor is 50_000 USD.
        ok, reason = authorize_trade("SOL", liquidity_usd=1_000)
        self.assertFalse(ok)
        self.assertIn("below the", reason)

    def test_liquidity_floor_passes_above(self):
        ok, reason = authorize_trade("SOL", liquidity_usd=100_000)
        self.assertTrue(ok)

    def test_price_impact_cap_blocks_high_impact(self):
        # SOL cap is 1.0%.
        ok, reason = authorize_trade("SOL", liquidity_usd=100_000, price_impact_pct=5.0)
        self.assertFalse(ok)
        self.assertIn("exceeds", reason)

    def test_route_restriction_blocks_disallowed_route(self):
        # SPCX is only authorized through Raydium.
        ok, reason = authorize_trade("SPCX", liquidity_usd=100_000,
                                     price_impact_pct=0.5, route="Jupiter")
        self.assertFalse(ok)
        self.assertIn("route", reason)


class TestEnforceTokenGate(unittest.TestCase):
    """Pin _enforce_token_gate wiring: quote skip, refuse-on-None, live pass."""

    def setUp(self):
        self._liq = main.get_token_liquidity_usd
        self._imp = main.estimate_price_impact_pct

    def tearDown(self):
        main.get_token_liquidity_usd = self._liq
        main.estimate_price_impact_pct = self._imp

    def _stub(self, liquidity=100_000, impact=0.5):
        main.get_token_liquidity_usd = lambda symbol: liquidity
        main.estimate_price_impact_pct = lambda f, t, a: impact

    def test_quote_currency_never_gated(self):
        # USDC/USDT legs must be skipped entirely.
        ok, reason = _enforce_token_gate("USDC", "Jupiter", "m", "t", 1_000_000)
        self.assertTrue(ok)
        ok, reason = _enforce_token_gate("USDT", "Raydium", "m", "t", 1_000_000)
        self.assertTrue(ok)

    def test_approved_with_live_data_and_allowed_route_passes(self):
        self._stub(liquidity=100_000, impact=0.5)
        ok, reason = _enforce_token_gate("SOL", "Jupiter", "m", "t", 1_000_000)
        self.assertTrue(ok, reason)

    def test_missing_liquidity_is_hard_fail(self):
        # "uncertain = refuse": for an APPROVED non-blue-chip (SPCX), a live
        # liquidity lookup failure must still block (fail-CLOSED).
        main.get_token_liquidity_usd = lambda symbol: None
        main.estimate_price_impact_pct = lambda f, t, a: 0.5
        ok, reason = _enforce_token_gate("SPCX", "Jupiter", "m", "t", 1_000_000)
        self.assertFalse(ok)
        self.assertIn("liquidity unavailable", reason)

    def test_missing_price_impact_is_hard_fail(self):
        main.get_token_liquidity_usd = lambda symbol: 100_000
        main.estimate_price_impact_pct = lambda f, t, a: None
        ok, reason = _enforce_token_gate("SPCX", "Jupiter", "m", "t", 1_000_000)
        self.assertFalse(ok)
        self.assertIn("price impact unavailable", reason)

    def test_blue_chip_missing_liquidity_fails_open(self):
        # APPROVED blue-chip (BTC): a transient dexscreener failure (None) must
        # NOT block a live buy — the gate fails OPEN with a warning.
        main.get_token_liquidity_usd = lambda symbol: None
        main.estimate_price_impact_pct = lambda f, t, a: 0.5
        ok, reason = _enforce_token_gate("BTC", "Raydium", "m", "t", 1_000_000)
        self.assertTrue(ok, reason)

    def test_blue_chip_missing_impact_fails_open(self):
        # APPROVED blue-chip (BTC): transient Jupiter quote failure must not block.
        main.get_token_liquidity_usd = lambda symbol: 100_000
        main.estimate_price_impact_pct = lambda f, t, a: None
        ok, reason = _enforce_token_gate("BTC", "Jupiter", "m", "t", 1_000_000)
        self.assertTrue(ok, reason)

    def test_blue_chip_real_low_liquidity_still_blocked(self):
        # Fail-open applies ONLY to None (lookup failure). A real low number
        # below the BTC floor ($25k) must still refuse.
        main.get_token_liquidity_usd = lambda symbol: 1_000
        main.estimate_price_impact_pct = lambda f, t, a: 0.5
        ok, reason = _enforce_token_gate("BTC", "Raydium", "m", "t", 1_000_000)
        self.assertFalse(ok)
        self.assertIn("below the", reason)

    def test_blue_chip_real_high_impact_still_blocked(self):
        # A real (non-None) impact above the BTC cap (1%) must still refuse.
        main.get_token_liquidity_usd = lambda symbol: 100_000
        main.estimate_price_impact_pct = lambda f, t, a: 5.0
        ok, reason = _enforce_token_gate("BTC", "Jupiter", "m", "t", 1_000_000)
        self.assertFalse(ok)
        self.assertIn("exceeds", reason)

    def test_pending_token_missing_liquidity_still_refused(self):
        # PENDING (non-APPROVED) token stays fail-CLOSED on lookup failure.
        main.get_token_liquidity_usd = lambda symbol: None
        main.estimate_price_impact_pct = lambda f, t, a: 0.5
        ok, reason = _enforce_token_gate("BNB", "Jupiter", "m", "t", 1_000_000)
        self.assertFalse(ok)

    def test_unknown_token_missing_liquidity_still_refused(self):
        # Unknown token stays fail-CLOSED on lookup failure.
        main.get_token_liquidity_usd = lambda symbol: None
        main.estimate_price_impact_pct = lambda f, t, a: 0.5
        ok, reason = _enforce_token_gate("NOTATOKEN", "Jupiter", "m", "t", 1_000_000)
        self.assertFalse(ok)
        self.assertIn("not in the token registry", reason)

    def test_route_restriction_enforced_through_gate(self):
        self._stub(liquidity=100_000, impact=0.5)
        # SPCX only allows Raydium; Jupiter must be refused.
        ok, reason = _enforce_token_gate("SPCX", "Jupiter", "m", "t", 1_000_000)
        self.assertFalse(ok)
        self.assertIn("route", reason)

    def test_unknown_symbol_refused_through_gate(self):
        self._stub()
        ok, reason = _enforce_token_gate("NOTATOKEN", "Jupiter", "m", "t", 1_000_000)
        self.assertFalse(ok)
        self.assertIn("not in the token registry", reason)

    def test_gate_passes_live_liquidity_and_impact_to_authorize(self):
        # Verify the helper forwards the fetched live numbers (not just status).
        self._stub(liquidity=200_000, impact=0.7)
        with mock.patch.object(main, "authorize_trade", wraps=main.authorize_trade) as at:
            ok, _ = _enforce_token_gate("SOL", "Jupiter", "m", "t", 1_000_000)
            self.assertTrue(ok)
            at.assert_called_once_with("SOL", liquidity_usd=200_000,
                                       price_impact_pct=0.7, route="Jupiter")


def test_token_registry_all():
    suite = unittest.TestLoader().loadTestsFromTestCase(TestAuthorizeTrade)
    suite.addTests(unittest.TestLoader().loadTestsFromTestCase(TestEnforceTokenGate))
    res = unittest.TextTestRunner(verbosity=0).run(suite)
    if not res.wasSuccessful():
        raise AssertionError("Token-registry unit tests failed")


if __name__ == "__main__":
    unittest.main()
