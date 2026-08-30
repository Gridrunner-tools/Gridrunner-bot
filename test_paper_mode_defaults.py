import unittest
from main import (
    state, default_strategy_paper, get_available_balance,
    check_capital_reservation, get_reserved_capital, DASHBOARD,
)


class TestPaperModeDefaults(unittest.TestCase):
    def setUp(self):
        self._backup = {
            "strategies": state.get("strategies", {}).copy(),
            "running": state.get("running", False),
            "strategy": state.get("strategy"),
            "paper_trading": state.get("paper_trading", True),
            "license_valid": state.get("license_valid", True),
        }
        state["strategies"] = {}
        state["running"] = False
        state["strategy"] = None

    def tearDown(self):
        state["strategies"] = self._backup["strategies"]
        state["running"] = self._backup["running"]
        state["strategy"] = self._backup["strategy"]
        state["paper_trading"] = self._backup["paper_trading"]
        state["license_valid"] = self._backup["license_valid"]

    # ── Defaults: grid/live, AI/paper ────────────────────────────────────────
    def test_grid_defaults_live_ai_defaults_paper(self):
        state["license_valid"] = True
        self.assertFalse(default_strategy_paper("grid"))
        self.assertFalse(default_strategy_paper("dca"))
        self.assertTrue(default_strategy_paper("ai_trading"))

    def test_invalid_license_forces_paper_even_for_grid(self):
        state["license_valid"] = False
        self.assertTrue(default_strategy_paper("grid"))
        self.assertTrue(default_strategy_paper("ai_trading"))

    # ── Mode-aware available balance ────────────────────────────────────────
    def test_available_balance_paper_vs_live(self):
        self.assertEqual(get_available_balance(True), 10000.0)
        # Live balance resolves through get_balance(); with no wallet configured
        # it must be 0.0, never the paper balance.
        state["mode"] = "dex"
        state["chain"] = "solana"
        self.assertEqual(get_available_balance(False), 0.0)

    # ── Reserved capital, pooled per mode ────────────────────────────────────
    def test_paper_pool_rejects_over_budget(self):
        state["strategies"] = {
            "ai_trading_SOL/USDC": {
                "type": "ai_trading",
                "config": {"paper_trading": True, "max_total_exposure": 6000.0},
                "running": True,
            }
        }
        ok, err = check_capital_reservation(
            "ai_trading", {"paper_trading": True, "max_total_exposure": 5000.0}
        )
        self.assertFalse(ok)
        self.assertIn("Insufficient balance", err)

    def test_live_grid_reserves_against_real_balance(self):
        state["license_valid"] = True
        # Force a known real balance.
        import main
        orig = main.get_balance
        main.get_balance = lambda: 1000.0
        try:
            ok, err = check_capital_reservation(
                "grid", {"paper_trading": False, "risk_pct": 2.0, "max_pos": 500.0}
            )
        finally:
            main.get_balance = orig
        self.assertTrue(ok, err)
        self.assertEqual(
            get_reserved_capital("grid", {"risk_pct": 2.0, "max_pos": 500.0}, 1000.0),
            20.0,
        )

    def test_paper_ai_does_not_consume_live_grid_capital(self):
        import main
        orig = main.get_balance
        main.get_balance = lambda: 1000.0
        try:
            # A large paper AI strategy is running; it must not block a live grid.
            state["strategies"] = {
                "ai_trading_SOL/USDC": {
                    "type": "ai_trading",
                    "config": {"paper_trading": True, "max_total_exposure": 6000.0},
                    "running": True,
                }
            }
            ok, err = check_capital_reservation(
                "grid", {"paper_trading": False, "risk_pct": 2.0, "max_pos": 500.0}
            )
        finally:
            main.get_balance = orig
        self.assertTrue(ok, err)

    # ── Dashboard JS: the malformed Stop-button onclick must be gone ─────────
    def test_dashboard_stop_button_onclick_is_valid(self):
        self.assertNotIn("stopStrategy('' + sid + '')", DASHBOARD)
        self.assertIn("stopStrategy('${sid}')", DASHBOARD)


def test_paper_mode_defaults_all():
    suite = unittest.TestLoader().loadTestsFromTestCase(TestPaperModeDefaults)
    res = unittest.TextTestRunner(verbosity=0).run(suite)
    if not res.wasSuccessful():
        raise AssertionError("Paper-mode-defaults unit tests failed")


if __name__ == "__main__":
    unittest.main()
