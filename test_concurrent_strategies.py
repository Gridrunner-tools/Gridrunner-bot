#!/usr/bin/env python3
import unittest
import time
import threading
from main import state, start_bot, stop_bot, stop_strategy, stop_all, get_available_balance, get_reserved_capital, ThreadSafeState, cfg

class TestConcurrentStrategies(unittest.TestCase):
    def setUp(self):
        # Backup and reset state before each test
        self.old_state_strategies = state.get("strategies", {}).copy()
        self.old_state_running = state.get("running", False)
        self.old_state_strategy = state.get("strategy")
        self.old_state_pair = state.get("pair")
        self.old_state_paper = state.get("paper_trading", True)
        
        state["strategies"] = {}
        state["running"] = False
        state["strategy"] = None
        state["paper_trading"] = True  # force paper mode for safety

    def tearDown(self):
        # Stop everything
        stop_all()
        # Restore state
        state["strategies"] = self.old_state_strategies
        state["running"] = self.old_state_running
        state["strategy"] = self.old_state_strategy
        state["pair"] = self.old_state_pair
        state["paper_trading"] = self.old_state_paper

    def test_concurrent_start(self):
        # 1. Start a grid strategy on SOL/USDC
        grid_cfg = {"risk_pct": 2.0, "max_pos": 500.0, "paper_trading": True}
        start_bot("grid", "SOL/USDC", "dex", "jupiter", "solana", grid_cfg)
        
        # 2. Start an AI Trading strategy on BTC/USDC concurrently
        ai_cfg = {"risk_pct": 1.0, "max_total_exposure": 1000.0, "paper_trading": True}
        start_bot("ai_trading", "BTC/USDC", "dex", "jupiter", "solana", ai_cfg)
        
        # Verify both are registered and marked running in state["strategies"]
        self.assertIn("grid_SOL/USDC", state["strategies"])
        self.assertIn("ai_trading_BTC/USDC", state["strategies"])
        self.assertTrue(state["strategies"]["grid_SOL/USDC"]["running"])
        self.assertTrue(state["strategies"]["ai_trading_BTC/USDC"]["running"])
        self.assertTrue(state["running"])

    def test_reserved_capital_rejection_over_budget(self):
        # Available balance in paper mode is 10000.0
        # Start a strategy that reserves a lot of capital, e.g. 6000.0
        ai_cfg1 = {"risk_pct": 1.0, "max_total_exposure": 6000.0, "paper_trading": True}
        start_bot("ai_trading", "SOL/USDC", "dex", "jupiter", "solana", ai_cfg1)
        
        self.assertIn("ai_trading_SOL/USDC", state["strategies"])
        self.assertTrue(state["strategies"]["ai_trading_SOL/USDC"]["running"])
        
        # Total reservation is 6000.0. Trying to start another strategy with 5000.0 reservation
        # should exceed 10000.0 available balance and must NOT be started.
        ai_cfg2 = {"risk_pct": 1.0, "max_total_exposure": 5000.0, "paper_trading": True}
        
        # We simulate the HTTP endpoint check:
        current_balance = get_available_balance()
        sum_reserved = sum(get_reserved_capital(s["type"], s["config"], current_balance) for s in state["strategies"].values() if s["running"])
        new_reserved = get_reserved_capital("ai_trading", ai_cfg2, current_balance)
        
        # Enforce rejection
        overdrawn = (sum_reserved + new_reserved > current_balance)
        self.assertTrue(overdrawn, "Rejection over budget should be true")

    def test_per_strategy_stop_releases_capital(self):
        # Start strategy 1 (reserves 6000.0)
        ai_cfg1 = {"risk_pct": 1.0, "max_total_exposure": 6000.0, "paper_trading": True}
        start_bot("ai_trading", "SOL/USDC", "dex", "jupiter", "solana", ai_cfg1)
        self.assertTrue(state["strategies"]["ai_trading_SOL/USDC"]["running"])
        
        # Strategy 2 (reserves 5000.0) would overdraw
        ai_cfg2 = {"risk_pct": 1.0, "max_total_exposure": 5000.0, "paper_trading": True}
        current_balance = get_available_balance()
        sum_reserved_before = sum(get_reserved_capital(s["type"], s["config"], current_balance) for s in state["strategies"].values() if s["running"])
        self.assertTrue(sum_reserved_before + 5000.0 > current_balance)
        
        # Stop Strategy 1
        stop_strategy("ai_trading_SOL/USDC")
        self.assertFalse(state["strategies"]["ai_trading_SOL/USDC"]["running"])
        
        # Now Strategy 2 should be within budget
        sum_reserved_after = sum(get_reserved_capital(s["type"], s["config"], current_balance) for s in state["strategies"].values() if s["running"])
        self.assertEqual(sum_reserved_after, 0.0)
        self.assertTrue(sum_reserved_after + 5000.0 <= current_balance)

    def test_stop_all(self):
        grid_cfg = {"risk_pct": 2.0, "max_pos": 500.0, "paper_trading": True}
        start_bot("grid", "SOL/USDC", "dex", "jupiter", "solana", grid_cfg)
        
        ai_cfg = {"risk_pct": 1.0, "max_total_exposure": 1000.0, "paper_trading": True}
        start_bot("ai_trading", "BTC/USDC", "dex", "jupiter", "solana", ai_cfg)
        
        # Stop all
        stop_all()
        
        self.assertFalse(state["strategies"]["grid_SOL/USDC"]["running"])
        self.assertFalse(state["strategies"]["ai_trading_BTC/USDC"]["running"])
        self.assertFalse(state["running"])

    def test_backward_compat_single_strategy(self):
        grid_cfg = {"risk_pct": 2.0, "max_pos": 500.0, "paper_trading": True}
        start_bot("grid", "SOL/USDC", "dex", "jupiter", "solana", grid_cfg)
        
        self.assertTrue(state["running"])
        self.assertEqual(state["strategy"], "grid")
        self.assertEqual(state["pair"], "SOL/USDC")
        
        stop_bot()
        self.assertFalse(state["running"])
        self.assertIsNone(state["strategy"])

    def test_real_concurrency_decoupling_loop(self):
        # Start a grid strategy on SOL/USDC
        grid_cfg = {"risk_pct": 2.0, "max_pos": 500.0, "paper_trading": True}
        start_bot("grid", "SOL/USDC", "dex", "jupiter", "solana", grid_cfg)
        
        # Verify grid strategy is registered and running
        self.assertTrue(state["strategies"]["grid_SOL/USDC"]["running"])
        
        # Start an AI strategy concurrently
        ai_cfg = {"risk_pct": 1.0, "max_total_exposure": 1000.0, "paper_trading": True}
        start_bot("ai_trading", "BTC/USDC", "dex", "jupiter", "solana", ai_cfg)
        
        # Verify both strategies are registered and running
        self.assertTrue(state["strategies"]["grid_SOL/USDC"]["running"])
        self.assertTrue(state["strategies"]["ai_trading_BTC/USDC"]["running"])
        
        # Let the background threads process a bit
        time.sleep(0.5)
        
        # Verify the grid strategy's thread and loop are STILL active and running
        # (even though global state["strategy"] is now "ai_trading")
        self.assertTrue(state["strategies"]["grid_SOL/USDC"]["running"])
        self.assertTrue(state["strategies"]["grid_SOL/USDC"]["thread"].is_alive())
        
        # Stop AI Trading
        stop_strategy("ai_trading_BTC/USDC")
        self.assertFalse(state["strategies"]["ai_trading_BTC/USDC"]["running"])
        
        # Grid must survive and remain running
        self.assertTrue(state["strategies"]["grid_SOL/USDC"]["running"])
        self.assertTrue(state["strategies"]["grid_SOL/USDC"]["thread"].is_alive())
        
        # Stop Grid
        stop_strategy("grid_SOL/USDC")
        self.assertFalse(state["strategies"]["grid_SOL/USDC"]["running"])

def test_concurrent_strategies_all():
    import unittest
    suite = unittest.TestLoader().loadTestsFromTestCase(TestConcurrentStrategies)
    res = unittest.TextTestRunner(verbosity=0).run(suite)
    if not res.wasSuccessful():
        raise AssertionError("Concurrent strategies unit tests failed")

if __name__ == '__main__':
    unittest.main()
