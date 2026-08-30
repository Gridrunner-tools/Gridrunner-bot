import unittest
from main import state, start_bot, stop_all, _state_payload


class TestAICardPersistsWithGrid(unittest.TestCase):
    """Regression: starting a grid must not drop the running AI Trading card.

    The dashboard's strategy list and AI live-status card render from
    state["strategies"]. The owner reported that starting a GRID while an
    AI Trading strategy was already running made the AI Trading card vanish.
    The backend contract the dashboard depends on is: after start_bot() for
    grid, the AI strategy entry stays present with running=True and
    type="ai_trading" (so the frontend can keep rendering its card and status
    panel). This test pins that contract for the exact owner order
    (AI first, then grid).
    """

    def setUp(self):
        self._old = {
            "strategies": dict(state.get("strategies", {})),
            "running": state.get("running", False),
            "strategy": state.get("strategy"),
            "pair": state.get("pair"),
            "paper_trading": state.get("paper_trading", True),
        }
        state["strategies"] = {}
        state["running"] = False
        state["strategy"] = None
        state["paper_trading"] = True  # force paper for safety

    def tearDown(self):
        stop_all()
        state["strategies"] = self._old["strategies"]
        state["running"] = self._old["running"]
        state["strategy"] = self._old["strategy"]
        state["pair"] = self._old["pair"]
        state["paper_trading"] = self._old["paper_trading"]

    def test_ai_card_persists_after_grid_start(self):
        # Owner order: AI Trading first (paper), then GRID (paper for the test).
        ai_cfg = {"paper_trading": True, "risk_pct": 1.0, "max_total_exposure": 1000.0,
                  "max_leverage": 3.0, "max_simultaneous_positions": 3,
                  "auto_compound": True, "ai_whitelist": ["BTC/USDC"]}
        start_bot("ai_trading", "BTC/USDC", "dex", "jupiter", "solana", ai_cfg)

        grid_cfg = {"paper_trading": True, "risk_pct": 2.0, "max_pos": 500.0}
        start_bot("grid", "BTC/USDC", "dex", "jupiter", "solana", grid_cfg)

        # The payload served to the dashboard must keep BOTH strategies.
        payload = _state_payload()
        strats = payload.get("strategies", {})

        self.assertIn("ai_trading_BTC/USDC", strats)
        self.assertIn("grid_BTC/USDC", strats)

        ai = strats["ai_trading_BTC/USDC"]
        grid = strats["grid_BTC/USDC"]

        # The AI entry must still look like a running AI strategy so the card
        # and the AI live-status panel keep rendering.
        self.assertTrue(ai.get("running"))
        self.assertEqual(ai.get("type"), "ai_trading")
        self.assertTrue(grid.get("running"))
        self.assertEqual(grid.get("type"), "grid")


def test_ai_card_persists_with_grid_all():
    suite = unittest.TestLoader().loadTestsFromTestCase(TestAICardPersistsWithGrid)
    res = unittest.TextTestRunner(verbosity=0).run(suite)
    if not res.wasSuccessful():
        raise AssertionError("AI card persists with grid tests failed")


if __name__ == "__main__":
    unittest.main()
