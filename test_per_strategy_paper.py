#!/usr/bin/env python3
"""Per-strategy paper/live mode regression tests.

Cover the owner-reported bug: a single global ``state["paper_trading"]`` flag
was overwritten by every /start and read by the shared execution path, so a
paper AI start silently flipped a live grid (and manual sells) into paper.

Spec: PAPER_MODE_PER_STRATEGY_SPEC.md
- place_order/jupiter_swap/dex_swap/cex_place_order carry an explicit
  ``paper`` kwarg (None = fall back to state["paper_trading"]).
- Strategy loops thread their own config's paper flag into every order.
- Manual trades / grid webhooks / /kill follow the running grid strategy's
  mode (fallback: the global flag).
- No /start handler overwrites the global flag anymore.
"""
import io
import threading
import unittest
from pathlib import Path

import main
from main import (state, _strategy_paper, _grid_paper, _state_payload,
                  ThreadSafeState, place_order, LiveExecutionAdapter)


def _install_state_backup(testcase):
    testcase._paper_backup = {
        "strategies": {k: dict(v) for k, v in state.get("strategies", {}).items()},
        "paper_trading": state.get("paper_trading", True),
        "running": state.get("running", False),
        "strategy": state.get("strategy"),
    }
    state["strategies"] = {}
    state["paper_trading"] = True
    state["running"] = False
    state["strategy"] = None


def _restore_state_backup(testcase):
    state["strategies"] = testcase._paper_backup["strategies"]
    state["paper_trading"] = testcase._paper_backup["paper_trading"]
    state["running"] = testcase._paper_backup["running"]
    state["strategy"] = testcase._paper_backup["strategy"]


class TestPaperKwargThreading(unittest.TestCase):
    def setUp(self):
        _install_state_backup(self)
        self._mode_bak = state.get("mode")
        self._chain_bak = state.get("chain")
        # Route every swap call through a recorder so we can assert the paper
        # value that reaches the execution path.
        self.calls = []
        self._orig_jup = main.jupiter_swap
        self._orig_dex = main.dex_swap
        self._orig_cex = main.cex_place_order

        def fake_jupiter(from_token, to_token, amount_input, price, dex=None, paper=None):
            self.calls.append(("jupiter", paper))
            return (True, amount_input / price)

        def fake_dex(chain, from_token, to_token, amount_usd, price, target_symbol=None, paper=None):
            self.calls.append(("dex", paper))
            return True

        def fake_cex(pair, side, amount, paper=None):
            self.calls.append(("cex", paper))
            return True

        main.jupiter_swap = fake_jupiter
        main.dex_swap = fake_dex
        main.cex_place_order = fake_cex
        main.get_price = lambda pair: 100.0
        # The token-registry gate would block unregistered symbols; this fix
        # is about the paper/live mode, so let every symbol through.
        main.authorize_trade = lambda *a, **k: (True, None)
        state["mode"] = "dex"
        state["chain"] = "solana"
        # place_order keeps a module-global 5s duplicate-order guard; reset it
        # so tests that reuse (pair, side, amount) are not blocked by a prior test.
        main._last_order_key = None
        main._last_order_time = 0.0

    def tearDown(self):
        main.jupiter_swap = self._orig_jup
        main.dex_swap = self._orig_dex
        main.cex_place_order = self._orig_cex
        _restore_state_backup(self)
        # restore any pre-existing mode/chain rather than leaking our values into
        # whichever test module runs after this one in the aggregate suite
        if self._mode_bak is None:
            state.pop("mode", None)
        else:
            state["mode"] = self._mode_bak
        if self._chain_bak is None:
            state.pop("chain", None)
        else:
            state["chain"] = self._chain_bak

    def test_place_order_forwards_paper_to_all_venues(self):
        state["mode"] = "dex"
        state["chain"] = "solana"
        # Solana buy -> jupiter_swap
        self.assertTrue(place_order("BTC/USDC", "buy", 1.0, paper=True))
        self.assertEqual(self.calls[-1], ("jupiter", True))
        # Solana sell -> jupiter_swap with the same flag
        self.assertTrue(place_order("BTC/USDC", "sell", 1.0, paper=False))
        self.assertEqual(self.calls[-1], ("jupiter", False))

    def test_place_order_forwards_paper_to_cex_path(self):
        state["mode"] = "cex"
        self.assertTrue(place_order("BTC/USDT", "buy", 1.0, paper=True))
        self.assertEqual(self.calls[-1], ("cex", True))

    def test_place_order_paper_none_defaults_unchanged(self):
        # No explicit paper -> the global fallback is forwarded untouched so
        # legacy callers keep their exact prior behavior.
        state["mode"] = "dex"
        state["chain"] = "solana"
        self.assertTrue(place_order("BTC/USDC", "buy", 2.0))
        self.assertEqual(self.calls[-1], ("jupiter", None))

    def test_grid_live_ai_paper_isolation(self):
        # The headline regression: a grid running LIVE and an AI strategy
        # running PAPER must route through different execution paths.
        state["strategies"] = {
            "grid_SOL/USDC": {"type": "grid", "config": {"paper_trading": False}, "running": True},
            "ai_trading_BTC/USDC": {"type": "ai_trading", "config": {"paper_trading": True}, "running": True},
        }
        state["paper_trading"] = True  # hostile global: former bug source
        results = {}

        def grid_thread():
            results["grid_paper"] = _strategy_paper("grid_SOL/USDC")
            results["grid_state_read"] = state["paper_trading"]
            results["grid_order"] = place_order("SOL/USDC", "buy", 3.0, paper=_strategy_paper("grid_SOL/USDC"))

        def ai_thread():
            results["ai_paper"] = _strategy_paper("ai_trading_BTC/USDC")
            results["ai_state_read"] = state["paper_trading"]
            results["ai_order"] = place_order("BTC/USDC", "buy", 4.0, paper=_strategy_paper("ai_trading_BTC/USDC"))

        tg = threading.Thread(target=grid_thread, name="grid_SOL/USDC")
        ta = threading.Thread(target=ai_thread, name="ai_trading_BTC/USDC")
        tg.start(); ta.start(); tg.join(); ta.join()

        # Grid stays LIVE even though the global is True: its real order hits
        # the live (paper=False) jupiter path.
        self.assertFalse(results["grid_paper"])
        self.assertFalse(results["grid_state_read"])
        self.assertFalse(results["grid_order"] is None)
        # AI is PAPER and routes to the paper jupiter path.
        self.assertTrue(results["ai_paper"])
        self.assertTrue(results["ai_state_read"])
        grid_route = [c for c in self.calls if c[1] is False]
        ai_route = [c for c in self.calls if c[1] is True]
        self.assertTrue(grid_route)
        self.assertTrue(ai_route)

    def test_ai_adapter_threads_its_paper_flag(self):
        state["strategies"] = {
            "ai_trading_SOL/USDC": {"type": "ai_trading", "config": {"paper_trading": True}, "running": True},
        }
        adapter = LiveExecutionAdapter(paper=True)
        self.assertTrue(adapter.execute_swap("SOL/USDC", "LONG", 5.0, 100.0))
        self.assertEqual(self.calls[-1], ("jupiter", True))
        adapter2 = LiveExecutionAdapter(paper=False)
        self.assertTrue(adapter2.execute_swap("SOL/USDC", "LONG", 6.0, 100.0))
        self.assertEqual(self.calls[-1], ("jupiter", False))


class TestSwapPaperBranch(unittest.TestCase):
    """The mode check inside the swap functions must honor the passed-in flag."""

    def setUp(self):
        _install_state_backup(self)
        self._orig_raydium = main.raydium_get_quote
        self._orig_requests = main.requests
        self._orig_gate = main._enforce_token_gate
        self._orig_secret = main._secret
        main.raydium_get_quote = lambda *a, **k: None  # skip Raydium path
        main._enforce_token_gate = lambda *a, **k: (True, None)
        main._secret = lambda name: None  # no wallet keys in tests
        state["trades"] = []

        class FakeResp:
            def json(self):
                return {"outAmount": "1000000000", "error": None}

        class FakeRequests:
            @staticmethod
            def get(*a, **k):
                return FakeResp()

        main.requests = FakeRequests

    def tearDown(self):
        main.raydium_get_quote = self._orig_raydium
        main.requests = self._orig_requests
        main._enforce_token_gate = self._orig_gate
        main._secret = self._orig_secret
        _restore_state_backup(self)

    def test_jupiter_paper_true_records_paper_trade(self):
        state["paper_trading"] = False  # global hostile: must NOT leak in
        ok, amt = main.jupiter_swap("USDC", "BTC", 10.0, 100.0, paper=True)
        self.assertTrue(ok)
        self.assertGreater(amt, 0)
        self.assertTrue(any("[PAPER]" in t["side"] for t in state["trades"]))

    def test_jupiter_paper_false_takes_live_path(self):
        state["paper_trading"] = True  # global hostile the other way
        ok, amt = main.jupiter_swap("USDC", "BTC", 10.0, 100.0, paper=False)
        # No keys + live path -> blocked before any on-chain send, no paper trade.
        self.assertFalse(ok)
        self.assertEqual(amt, 0.0)
        self.assertFalse(any("[PAPER]" in t["side"] for t in state["trades"]))

    def test_cex_place_order_honors_paper_param(self):
        state["exchange"] = "bybit"
        raised = []

        class BoomRequests:
            @staticmethod
            def post(*a, **k):
                raise RuntimeError("network must not be touched in paper mode")

        post_calls = []
        called = []

        class BoomRequests:
            @staticmethod
            def post(*a, **k):
                post_calls.append(1)
                raise RuntimeError("network must not be touched in paper mode")

        main.requests = BoomRequests
        # The bybit branch signs with the API secret before posting, so give it
        # a stub secret so the crash lands on the network call, not the signing.
        orig_secret = main._secret
        main._secret = lambda name: "test-secret-value"
        try:
            # paper=True -> paper branch, never touches the network
            self.assertTrue(main.cex_place_order("BTC/USDT", "buy", 1.0, paper=True))
            self.assertEqual(post_calls, [])
            # paper=False -> the real CEX path is attempted; the network error
            # is swallowed by cex_place_order's outer guard and returns falsy.
            self.assertFalse(main.cex_place_order("BTC/USDT", "buy", 1.0, paper=False))
            self.assertEqual(len(post_calls), 1)
        finally:
            main._secret = orig_secret
        # paper=None -> falls back to the global
        state["paper_trading"] = True
        self.assertTrue(main.cex_place_order("BTC/USDT", "buy", 1.0))


class TestGridContextPaper(unittest.TestCase):
    def setUp(self):
        _install_state_backup(self)

    def tearDown(self):
        _restore_state_backup(self)

    def test_manual_trade_follows_grid_mode(self):
        # grid LIVE + hostile global -> manual sell must stay LIVE
        state["strategies"] = {
            "grid_SOL/USDC": {"type": "grid", "config": {"paper_trading": False}, "running": True},
        }
        state["paper_trading"] = True
        self.assertFalse(_grid_paper())
        # grid PAPER -> manual sell goes paper
        state["strategies"]["grid_SOL/USDC"]["config"]["paper_trading"] = True
        self.assertTrue(_grid_paper())
        # no grid running -> global fallback
        state["strategies"] = {}
        self.assertTrue(_grid_paper())

    def test_strategy_paper_falls_back_to_global(self):
        self.assertTrue(_strategy_paper("no_such_sid"))
        state["paper_trading"] = False
        self.assertFalse(_strategy_paper("no_such_sid"))


class TestNoGlobalOverwrite(unittest.TestCase):
    def test_only_two_global_writes_remain(self):
        # The /start handlers and run_limit_order must no longer write the
        # global flag. Only the manual toggle and the license paper-lock do.
        src = io.open(Path(__file__).parent / "main.py", encoding="utf-8").read()
        writes = [ln for ln in src.split("\n") if 'state["paper_trading"] =' in ln]
        self.assertEqual(len(writes), 2, "unexpected global writes: %r" % writes)

    def test_start_handlers_keep_per_strategy_config(self):
        src = io.open(Path(__file__).parent / "main.py", encoding="utf-8").read()
        self.assertNotIn('state["paper_trading"] = ai_paper', src)
        self.assertNotIn('state["paper_trading"] = grid_paper', src)
        self.assertNotIn('effective_mode == "paper"))', src.replace("state[\"paper_trading\"] = (", ""))

    def test_state_payload_exposes_running_strategy_mode(self):
        state["strategies"] = {
            "grid_SOL/USDC": {"type": "grid", "config": {"paper_trading": False}, "running": True},
            "ai_trading_BTC/USDC": {"type": "ai_trading", "config": {"paper_trading": True}, "running": True},
        }
        state["paper_trading"] = True
        payload = _state_payload()
        # Most recently started running strategy is the AI one -> header shows PAPER
        self.assertTrue(payload.get("active_paper"))
        # global flag untouched by the payload builder
        self.assertTrue(payload.get("paper_trading"))
        # stop AI -> active becomes grid (LIVE)
        state["strategies"]["ai_trading_BTC/USDC"]["running"] = False
        payload2 = _state_payload()
        self.assertFalse(payload2.get("active_paper"))
        # no running strategies -> no active_paper key, global stays the default
        state["strategies"] = {}
        payload3 = _state_payload()
        self.assertNotIn("active_paper", payload3)


def test_per_strategy_paper_all():
    suite = unittest.TestLoader().loadTestsFromTestCase(TestPaperKwargThreading)
    suite.addTests(unittest.TestLoader().loadTestsFromTestCase(TestSwapPaperBranch))
    suite.addTests(unittest.TestLoader().loadTestsFromTestCase(TestGridContextPaper))
    suite.addTests(unittest.TestLoader().loadTestsFromTestCase(TestNoGlobalOverwrite))
    res = unittest.TextTestRunner(verbosity=0).run(suite)
    if not res.wasSuccessful():
        raise AssertionError("per-strategy paper tests failed")


if __name__ == "__main__":
    unittest.main()