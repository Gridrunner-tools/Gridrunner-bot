#!/usr/bin/env python3
"""
Regression tests for /state JSON serialization.

Root cause: ``json.dumps(state)`` in the /state handler raised TypeError
("Object of type Thread is not JSON serializable") whenever a strategy was
running, because ``state["strategies"][sid]["thread"]`` held a threading.Thread
and (for AI Trading) ``state["ai_engine"]`` held an AITradingEngine instance.
The dashboard's refresh() then failed to parse the response and froze on its
static defaults ("analyzing" / "Analyzing markets...") — the owner-facing
"AI Trading stuck on Analyzing..." symptom.

These tests pin the fix: ``_state_payload()`` returns a JSON-serializable
snapshot that strips only the non-serializable runtime objects while keeping
every dashboard field (running/status/ai_*/log_tail/config) intact, and never
mutates shared state.
"""
import json
import threading
import unittest

from main import state, _state_payload


def _make_thread():
    return threading.Thread(target=lambda: None)


class TestStateSerialization(unittest.TestCase):
    def setUp(self):
        self.old_strategies = state.get("strategies", {}).copy()
        self.old_ai_engine = state.get("ai_engine", None)
        state["strategies"] = {}
        state.pop("ai_engine", None)
        state["running"] = False
        state["strategy"] = None

    def tearDown(self):
        state["strategies"] = self.old_strategies
        if self.old_ai_engine is None:
            state.pop("ai_engine", None)
        else:
            state["ai_engine"] = self.old_ai_engine

    def _register(self, sid, stype="ai_trading", config=None):
        strat = {
            "sid": sid,
            "type": stype,
            "pair": "SOL/USDC",
            "running": True,
            "paused": False,
            "config": config or {"paper_trading": True},
            "status": "RUNNING",
            "last_trade": None,
            "log_tail": ["AI Trading engine started - scanning market every 10s."],
            "started_at": 1,
            "thread": _make_thread(),
        }
        state["strategies"][sid] = strat
        return strat

    def test_ai_payload_serializes_and_keeps_display_fields(self):
        from ai_trading.execution import AITradingEngine
        self._register("ai_trading_SOL/USDC")
        state["ai_engine"] = AITradingEngine({"account_equity": 1000}, ["SOL/USDC"])
        state["running"] = True
        state["strategy"] = "ai_trading"
        state["ai_status"] = "analyzing"
        state["ai_explain"] = "Scanning whitelisted assets for high-confidence trading setups."

        payload = _state_payload()
        # Non-serializable runtime objects must be stripped.
        self.assertNotIn("ai_engine", payload)
        self.assertNotIn("thread", payload["strategies"]["ai_trading_SOL/USDC"])
        # json.dumps must succeed (this used to raise TypeError).
        out = json.dumps(payload)
        d = json.loads(out)
        # Dashboard fields survive.
        self.assertTrue(d["running"])
        self.assertEqual(d["strategy"], "ai_trading")
        self.assertEqual(d["ai_status"], "analyzing")
        self.assertEqual(d["strategies"]["ai_trading_SOL/USDC"]["status"], "RUNNING")
        self.assertEqual(len(d["strategies"]["ai_trading_SOL/USDC"]["log_tail"]), 1)

    def test_grid_payload_serializes(self):
        self._register("grid_SOL/USDC", stype="grid", config={"paper_trading": False, "risk_pct": 2, "max_pos": 500})
        state["running"] = True
        state["strategy"] = "grid"
        payload = _state_payload()
        self.assertNotIn("thread", payload["strategies"]["grid_SOL/USDC"])
        out = json.dumps(payload)
        d = json.loads(out)
        self.assertTrue(d["running"])
        self.assertEqual(d["strategy"], "grid")
        self.assertEqual(d["strategies"]["grid_SOL/USDC"]["type"], "grid")
        self.assertFalse(d["strategies"]["grid_SOL/USDC"]["config"]["paper_trading"])

    def test_payload_does_not_mutate_shared_state(self):
        from ai_trading.execution import AITradingEngine
        self._register("ai_trading_SOL/USDC")
        engine = AITradingEngine({"account_equity": 1000}, ["SOL/USDC"])
        state["ai_engine"] = engine
        _state_payload()
        # Shared state is untouched by the snapshot.
        self.assertIs(state["ai_engine"], engine)
        self.assertIsInstance(state["strategies"]["ai_trading_SOL/USDC"]["thread"], threading.Thread)

    def test_default_str_covers_stray_objects(self):
        # A defensive default=str must keep the dashboard alive even if a new
        # non-serializable object sneaks into state.
        self._register("ai_trading_SOL/USDC")
        state["strategies"]["ai_trading_SOL/USDC"]["mystery"] = object()
        out = json.dumps(_state_payload(), default=str)
        self.assertIn("mystery", out)


def test_state_serialization_all():
    suite = unittest.TestLoader().loadTestsFromTestCase(TestStateSerialization)
    res = unittest.TextTestRunner(verbosity=0).run(suite)
    if not res.wasSuccessful():
        raise AssertionError("State serialization regression tests failed")
