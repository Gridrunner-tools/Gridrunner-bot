#!/usr/bin/env python3
"""Regression tests for per-strategy card log + running/status reconciliation.

Covers the owner-reported concurrent-build bug: the Active Strategies card
always showed an empty log (log_tail was never populated) and the page-level
"running" indicator could diverge from a strategy's actual stopped/running
state.
"""
import unittest
import threading
import time

from main import (
    state, log, _mark_strategy_stopped, _safe_strategy_runner, DASHBOARD,
)


class TestStrategyLogTail(unittest.TestCase):
    def setUp(self):
        self.old_strategies = state.get("strategies", {}).copy()
        self.old_running = state.get("running", False)
        self.old_strategy = state.get("strategy")
        self.old_pair = state.get("pair")
        state["strategies"] = {}
        state["running"] = False
        state["strategy"] = None

    def tearDown(self):
        for sid in list(state.get("strategies", {}).keys()):
            state["strategies"][sid]["running"] = False
        state["strategies"] = self.old_strategies
        state["running"] = self.old_running
        state["strategy"] = self.old_strategy
        state["pair"] = self.old_pair

    def _register(self, sid, running=True):
        state["strategies"][sid] = {
            "sid": sid, "type": "ai_trading", "pair": "SOL/USDC",
            "running": running, "status": "RUNNING", "log_tail": [],
        }
        return state["strategies"][sid]

    def _run_in_strategy_thread(self, sid, fn, *args):
        """Run fn in a thread whose name == sid (like a real strategy thread)."""
        holder = {}
        def target():
            try:
                holder["ret"] = fn(*args)
            except Exception as e:  # pragma: no cover - surfaced via holder
                holder["exc"] = e
        t = threading.Thread(target=target, name=sid)
        t.start()
        t.join(timeout=5)
        return holder

    def test_log_routes_to_strategy_log_tail_and_global(self):
        sid = "ai_trading_SOL/USDC"
        self._register(sid)
        self._run_in_strategy_thread(sid, log, "hello from strategy")
        tail = state["strategies"][sid]["log_tail"]
        self.assertTrue(any("hello from strategy" in e for e in tail),
                        "log() must route to the strategy's log_tail")
        self.assertTrue(any("hello from strategy" in e for e in state["log"]),
                        "log() must still write the global log")

    def test_log_tail_is_capped(self):
        sid = "ai_trading_SOL/USDC"
        self._register(sid)
        def spam():
            for i in range(45):
                log("line %d" % i)
        self._run_in_strategy_thread(sid, spam)
        tail = state["strategies"][sid]["log_tail"]
        self.assertLessEqual(len(tail), 40)
        self.assertTrue(tail, "log_tail must be populated")

    def test_non_strategy_thread_not_routed(self):
        # No strategy registered: log from the main thread must not create one.
        log("plain main-thread log")
        self.assertEqual(state["strategies"], {})
        self.assertTrue(any("plain main-thread log" in e for e in state["log"]))

    def test_mark_stopped_reconciles_global_single(self):
        sid = "ai_trading_SOL/USDC"
        self._register(sid)
        state["running"] = True
        _mark_strategy_stopped(sid)
        self.assertFalse(state["strategies"][sid]["running"])
        self.assertEqual(state["strategies"][sid]["status"], "STOPPED")
        self.assertFalse(state["running"])
        self.assertIsNone(state["strategy"])

    def test_mark_stopped_keeps_other_strategy_running(self):
        sid1 = "ai_trading_SOL/USDC"
        sid2 = "grid_BTC/USDC"
        self._register(sid1)
        self._register(sid2)
        state["running"] = True
        _mark_strategy_stopped(sid1)
        self.assertFalse(state["strategies"][sid1]["running"])
        self.assertTrue(state["strategies"][sid2]["running"])
        self.assertTrue(state["running"], "global running must stay True while another strategy runs")

    def test_safe_runner_normal_exit_marks_stopped(self):
        sid = "ai_trading_SOL/USDC"
        self._register(sid)
        state["running"] = True
        wrapper = _safe_strategy_runner(lambda: None)
        self._run_in_strategy_thread(sid, wrapper, sid)
        self.assertFalse(state["strategies"][sid]["running"])
        self.assertEqual(state["strategies"][sid]["status"], "STOPPED")
        self.assertFalse(state["running"])

    def test_safe_runner_exception_marks_rejected(self):
        sid = "ai_trading_SOL/USDC"
        self._register(sid)
        state["running"] = True
        def boom(sid=None):
            raise ValueError("boom")
        wrapper = _safe_strategy_runner(boom)
        self._run_in_strategy_thread(sid, wrapper, sid)
        self.assertFalse(state["strategies"][sid]["running"])
        self.assertEqual(state["strategies"][sid]["status"], "REJECTED")
        self.assertTrue(state["strategies"][sid].get("error"))
        self.assertFalse(state["running"], "global running must reconcile after a fatal strategy error")

    def test_dashboard_card_renders_log_tail(self):
        self.assertIn("s.log_tail", DASHBOARD)
        self.assertIn("logLines", DASHBOARD)


def test_strategy_log_tail_all():
    suite = unittest.TestLoader().loadTestsFromTestCase(TestStrategyLogTail)
    res = unittest.TextTestRunner(verbosity=0).run(suite)
    if not res.wasSuccessful():
        raise AssertionError("Strategy log-tail unit tests failed")


if __name__ == "__main__":
    unittest.main()
