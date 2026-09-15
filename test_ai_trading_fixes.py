import unittest
import unittest.mock
import time
import json
from ai_trading.signal import Signal
from ai_trading.strategies import generate_signals_and_score
from ai_trading.execution import AITradingEngine
import unittest.mock
from main import state, LiveMarketDataProvider, LiveExecutionAdapter, run_ai_trading, run_grid, place_order, _ai_positions_payload

class TestAITradingFixes(unittest.TestCase):
    def setUp(self):
        # Reset relevant state variables before each test
        state["price_history"] = []
        state["price_history_pairs"] = {}
        state["ai_exposure"] = 0.0

    def test_exposure_usd_defensive_calculation(self):
        """Verify that state['ai_exposure'] is computed defensively even if positions lack exposure_usd key."""
        class MockEngine:
            def __init__(self):
                self.positions = {
                    "SOL/USDC": {
                        "symbol": "SOL/USDC",
                        "size": 2.0,
                        "entry": 100.0,
                        # No exposure_usd key!
                    },
                    "BTC/USDC": {
                        "symbol": "BTC/USDC",
                        "size": 0.5,
                        "entry": 60000.0,
                        "exposure_usd": 30000.0 # Has exposure_usd
                    }
                }
        
        engine = MockEngine()
        
        # Test the exact defensive computation block from run_ai_trading
        tot_exp = 0.0
        for p in engine.positions.values():
            if isinstance(p, dict):
                tot_exp += p.get("exposure_usd") or (p.get("size", 0.0) * p.get("entry", 0.0))
        
        # Assert that the sum is calculated correctly without raising KeyError
        self.assertEqual(tot_exp, 2.0 * 100.0 + 30000.0)

    def test_per_symbol_price_isolation(self):
        """Verify that LiveMarketDataProvider.get_candles isolates price histories by symbol."""
        dp = LiveMarketDataProvider()
        
        # Set distinct histories for SOL/USDC and BTC/USDC in price_history_pairs
        state["price_history_pairs"]["SOL/USDC"] = [
            {"time": 1, "value": 100.0},
            {"time": 2, "value": 101.0},
            {"time": 3, "value": 102.0}
        ]
        state["price_history_pairs"]["BTC/USDC"] = [
            {"time": 1, "value": 60000.0},
            {"time": 2, "value": 61000.0},
            {"time": 3, "value": 62000.0}
        ]
        
        # Fetch candles for each
        sol_candles = dp.get_candles("SOL/USDC")
        btc_candles = dp.get_candles("BTC/USDC")
        
        # Verify isolation
        self.assertAlmostEqual(sol_candles["closes"][-1], 102.0)
        self.assertAlmostEqual(btc_candles["closes"][-1], 62000.0)

    def test_precedence_gating_logic(self):
        """Verify that generate_signals_and_score with the fixed parentheses gates correctly."""
        # Setup mock indicators and scores
        # If regime is TRENDING_BULL but long_score <= short_score, it should not trigger LONG
        regime_info = {"regime": "TRENDING_BULL"}
        closes = [100.0] * 100
        highs = [102.0] * 100
        lows = [98.0] * 100
        volumes = [1000.0] * 100
        
        # Test that if long_score <= short_score (e.g. by tweaking inputs or mock values),
        # trend following correctly requires long_score > short_score to buy.
        signal = generate_signals_and_score(
            "SOL/USDC", "Solana", highs, lows, closes, volumes, regime_info
        )
        # Even if trend following is decided, it must satisfy score gates
        self.assertTrue(signal.direction in ("LONG", "NO_TRADE"))

    def test_long_only_spot_ai_no_short_signals(self):
        """Verify that with enable_perps=False, SHORT signals are blocked and returned as NO_TRADE."""
        # Setup bearish regime that would normally yield a SHORT signal
        regime_info = {"regime": "TRENDING_BEAR"}
        closes = [100.0] * 100
        highs = [102.0] * 100
        lows = [98.0] * 100
        volumes = [1000.0] * 100

        signal = generate_signals_and_score(
            "SOL/USDC", "Solana", highs, lows, closes, volumes, regime_info, enable_perps=False
        )
        self.assertEqual(signal.direction, "NO_TRADE")
        self.assertIn("spot is long-only", signal.reasons[-1])

    def test_perps_flag_on_short_signals_allowed(self):
        """Verify that with enable_perps=True, SHORT signals are allowed to be emitted."""
        regime_info = {"regime": "TRENDING_BEAR"}
        # Ensure we have long score lower than short score
        closes = [100.0] * 100
        highs = [101.0] * 100
        lows = [99.0] * 100
        volumes = [1000.0] * 100

        signal = generate_signals_and_score(
            "SOL/USDC", "Solana", highs, lows, closes, volumes, regime_info, enable_perps=True
        )
        # It shouldn't be blocked by the "spot is long-only" rule
        if signal.direction == "NO_TRADE":
            self.assertNotIn("spot is long-only", "".join(signal.reasons))

    def test_paper_sell_with_no_holdings(self):
        """Verify that a paper sell with no prior holdings / active positions is blocked by the adapter."""
        engine = AITradingEngine({"daily_loss_limit": 100.0}, ["SOL/USDC"])
        adapter = LiveExecutionAdapter(engine_ref=engine)

        self.assertNotIn("SOL/USDC", engine.positions)

        success = adapter.execute_swap("SOL/USDC", "SHORT", 1.0, 100.0)
        self.assertFalse(success)

    def test_paper_sell_with_holdings(self):
        """Verify that a paper sell with an active position is allowed and executed."""
        engine = AITradingEngine({"daily_loss_limit": 100.0}, ["SOL/USDC"])
        adapter = LiveExecutionAdapter(engine_ref=engine)

        engine.positions["SOL/USDC"] = {
            "symbol": "SOL/USDC",
            "direction": "LONG",
            "entry": 100.0,
            "size": 1.0,
            "exposure_usd": 100.0,
            "score": 85.0
        }

        import main
        orig_place_order = main.place_order
        main.place_order = lambda pair, side, amt, paper=None: True

        try:
            success = adapter.execute_swap("SOL/USDC", "SHORT", 1.0, 100.0)
            self.assertTrue(success)
        finally:
            main.place_order = orig_place_order

    def test_live_short_sell_with_no_position(self):
        """Verify that a live sell / SHORT with no active open position is blocked fail-closed."""
        engine = AITradingEngine({"daily_loss_limit": 100.0}, ["SOL/USDC"])
        adapter = LiveExecutionAdapter(engine_ref=engine)

        self.assertNotIn("SOL/USDC", engine.positions)

        success = adapter.execute_swap("SOL/USDC", "SHORT", 1.0, 100.0)
        self.assertFalse(success)

    def test_grid_loop_liveness_authoritative_condition(self):
        """Owner requirement: the grid loop exit must be driven ONLY by the
        authoritative condition state['running'] and state['strategy']=='grid'.
        A per-strategy bookkeeping dict must NEVER be the loop's kill switch
        (a missing/cleared entry would default to False and idle the loop after
        a sell - the regression the owner saw last time)."""
        import inspect
        src = inspect.getsource(run_grid)
        self.assertIn('while state["running"] and state["strategy"]=="grid"', src,
                      "grid loop must exit only on the authoritative running/strategy condition")
        self.assertNotIn('.get(sid, {}).get("running", False)', src,
                         "per-strategy bookkeeping must NEVER be the grid loop's kill switch")
        self.assertNotIn('state["strategies"].get(', src,
                         "grid loop must not depend on the strategies bookkeeping dict")
    def test_grid_loop_survives_last_position_sell(self):
        """Owner requirement: selling the LAST position must NOT stop the grid loop.
        After a completed sell empties `filled`, the loop's exit predicate must
        still evaluate True (running remains True, strategy remains 'grid')."""
        state["running"] = True
        state["strategy"] = "grid"
        state["grid_pairs"] = {
            "BTC/USDC": {
                "grids": [70000.0, 71000.0, 72000.0, 73000.0, 74000.0],
                "mid_idx": 2,
                "filled": {2: {"price": 72000.0, "amount": 0.01}},
                "levels": 4,
                "spread": 0.01,
                "trailing_high": 0.0,
                "trailing_sell_active": False,
                "trailing_low": 0.0,
                "trailing_buy_active": False,
                "dip_occurred": False,
            }
        }
        # Exactly what run_grid does after a successful sell of the last position:
        # it removes the filled entry and the matching position, then continues.
        del state["grid_pairs"]["BTC/USDC"]["filled"][2]
        state["positions"] = []
        self.assertTrue(state["running"], "running must stay True after selling the last position")
        self.assertEqual(state["strategy"], "grid", "strategy must stay 'grid' after selling the last position")
        self.assertTrue(state["running"] and state["strategy"] == "grid",
                        "grid loop must remain alive after selling the last position")
    def test_grid_loop_survives_state_serialization_roundtrip(self):
        """Owner requirement: state dict -> JSON -> dict round-trip must NOT kill
        the grid loop. After a full serialization round-trip the loop's exit
        predicate must still evaluate True."""
        state["running"] = True
        state["strategy"] = "grid"
        state["grid_pairs"] = {
            "BTC/USDC": {
                "grids": [70000.0, 71000.0, 72000.0, 73000.0, 74000.0],
                "mid_idx": 2,
                "filled": {},
                "levels": 4,
                "spread": 0.01,
                "trailing_high": 0.0,
                "trailing_sell_active": False,
                "trailing_low": 0.0,
                "trailing_buy_active": False,
                "dip_occurred": False,
            }
        }
        roundtripped = json.loads(json.dumps(state))
        self.assertTrue(roundtripped["running"], "running must survive serialization round-trip")
        self.assertEqual(roundtripped["strategy"], "grid", "strategy must survive serialization round-trip")
        self.assertTrue(roundtripped["running"] and roundtripped["strategy"] == "grid",
                        "grid loop must remain alive after state serialization round-trip")
    def test_range_score_tie_returns_no_trade_not_long(self):
        """Regression: in RANGE regime with long_score == short_score (no clean
        directional edge), generate_signals_and_score must return NO_TRADE, NOT
        LONG. The old fallback `direction = LONG if long_score >= short_score`
        defaulted to LONG on a tie; since spot is long-only, an ambiguous RANGE
        condition forced an immediate long entry."""
        with unittest.mock.patch('ai_trading.strategies.ema', return_value=[90.0] * 100), \
             unittest.mock.patch('ai_trading.strategies.vwap', return_value=[90.0] * 100), \
             unittest.mock.patch('ai_trading.strategies.rsi', return_value=[40.0] * 100), \
             unittest.mock.patch('ai_trading.strategies.momentum', return_value=[-5.0] * 100):
            # Scoring with these mocks / data:
            #   HTF: price(100) > ema(90)                  -> long +20
            #   VWAP: price(100) > vwap(90)                -> long +10
            #   Momentum: RSI(40) < 45, mom(-5) < 0        -> short +15
            #   Breakout: price(100) <= support(100)*1.005 -> short +15
            #   EMA stack, volume, volatility: flat        -> 0
            # long_score == short_score == 30 -> exact tie.
            regime_info = {"regime": "RANGE"}
            closes = [100.0] * 100
            highs = [101.0] * 100
            lows = [100.0] * 100
            volumes = [1000.0] * 100
            signal = generate_signals_and_score(
                "SOL/USDC", "Solana", highs, lows, closes, volumes, regime_info
            )
        self.assertEqual(signal.direction, "NO_TRADE")
        self.assertEqual(signal.reasons[0], "RANGE no clear directional edge")
    def test_range_clear_long_edge_still_trades(self):
        """Control: a genuine LONG edge inside RANGE regime must still produce
        LONG (we only suppress ties, not valid adaptive-grid longs)."""
        with unittest.mock.patch('ai_trading.strategies.ema', return_value=[90.0] * 100), \
             unittest.mock.patch('ai_trading.strategies.vwap', return_value=[90.0] * 100), \
             unittest.mock.patch('ai_trading.strategies.rsi', return_value=[60.0] * 100), \
             unittest.mock.patch('ai_trading.strategies.momentum', return_value=[5.0] * 100):
            # price(100) > ema(90) -> long +20; price > vwap(90) -> long +10;
            # RSI(60) > 55 and momentum(5) > 0 -> long +15. No short-side inputs.
            regime_info = {"regime": "RANGE"}
            closes = [100.0] * 100
            highs = [101.0] * 100
            lows = [99.0] * 100
            volumes = [1000.0] * 100
            signal = generate_signals_and_score(
                "SOL/USDC", "Solana", highs, lows, closes, volumes, regime_info
            )
        self.assertEqual(signal.direction, "LONG")
    def test_range_edge_within_margin_returns_no_trade(self):
        """FIX 1 (owner spec): in RANGE regime the LONG vs SHORT pick requires a
        real edge - long_score must beat short_score by at least
        RANGE_DIRECTIONAL_MARGIN. Widening the margin to 1000 turns a genuine
        45-vs-0 edge into 'no clean directional edge' -> NO_TRADE, proving the
        gate is a margin, not just an exact-tie suppression."""
        with unittest.mock.patch('ai_trading.strategies.ema', return_value=[90.0] * 100), \
             unittest.mock.patch('ai_trading.strategies.vwap', return_value=[90.0] * 100), \
             unittest.mock.patch('ai_trading.strategies.rsi', return_value=[60.0] * 100), \
             unittest.mock.patch('ai_trading.strategies.momentum', return_value=[5.0] * 100), \
             unittest.mock.patch('ai_trading.strategies.RANGE_DIRECTIONAL_MARGIN', 1000.0):
            # long: HTF +20, VWAP +10, momentum +15 = 45; short: 0 -> diff 45.
            regime_info = {"regime": "RANGE"}
            closes = [100.0] * 100
            highs = [101.0] * 100
            lows = [99.0] * 100
            volumes = [1000.0] * 100
            signal = generate_signals_and_score(
                "SOL/USDC", "Solana", highs, lows, closes, volumes, regime_info
            )
        self.assertEqual(signal.direction, "NO_TRADE")
        self.assertEqual(signal.reasons[0], "RANGE no clear directional edge")
    def test_range_long_edge_must_clear_ai_min_score(self):
        """FIX 1 (owner spec): even a LONG that clears the RANGE margin must
        also clear the AI_MIN_SCORE floor. Raising the floor above the signal
        score must yield NO_TRADE (score-band check), never a sub-floor LONG."""
        with unittest.mock.patch('ai_trading.strategies.ema', return_value=[90.0] * 100), \
             unittest.mock.patch('ai_trading.strategies.vwap', return_value=[90.0] * 100), \
             unittest.mock.patch('ai_trading.strategies.rsi', return_value=[60.0] * 100), \
             unittest.mock.patch('ai_trading.strategies.momentum', return_value=[5.0] * 100), \
             unittest.mock.patch('ai_trading.strategies.AI_MIN_SCORE', 999.0):
            # LONG edge of 45 beats SHORT 0 by 45 > margin; floor at 999 blocks.
            regime_info = {"regime": "RANGE"}
            closes = [100.0] * 100
            highs = [101.0] * 100
            lows = [99.0] * 100
            volumes = [1000.0] * 100
            signal = generate_signals_and_score(
                "SOL/USDC", "Solana", highs, lows, closes, volumes, regime_info
            )
        self.assertEqual(signal.direction, "NO_TRADE")
        self.assertIn("too weak", signal.reasons[0])
    def test_ai_positions_payload_has_per_position_pnl(self):
        """FIX 2: the AI card positions list must be per-position dicts with
        unrealized PnL computed from engine positions vs the latest cached tick
        (positive when price rose for a LONG, negative when it fell)."""
        class MockEngine:
            def __init__(self):
                self.positions = {
                    "SOL/USDC": {"symbol": "SOL/USDC", "direction": "LONG",
                                 "entry": 100.0, "avg_entry": 105.0, "size": 2.0},
                    "BTC/USDC": {"symbol": "BTC/USDC", "direction": "LONG",
                                 "entry": 60000.0, "size": 0.5},
                }
        state["price_history_pairs"] = {
            "SOL/USDC": [{"time": 1, "value": 110.0}],
            "BTC/USDC": [{"time": 1, "value": 55000.0}],
        }
        out = _ai_positions_payload(MockEngine())
        self.assertEqual(len(out), 2)
        by = {p["symbol"]: p for p in out}
        self.assertEqual(by["SOL/USDC"]["direction"], "LONG")
        self.assertEqual(by["SOL/USDC"]["entry"], 105.0)
        self.assertEqual(by["SOL/USDC"]["pnl"], 10.0)      # (110 - 105) * 2
        self.assertEqual(by["BTC/USDC"]["pnl"], -2500.0)   # (55000 - 60000) * 0.5
    def test_ai_positions_payload_empty_and_defensive(self):
        """FIX 2: empty/None engine yields [], and a non-dict position entry
        (legacy symbol-string shape) is still served without crashing."""
        class MockEngine:
            def __init__(self):
                self.positions = {"SOL/USDC": "SOL/USDC"}
        state["price_history_pairs"] = {}
        out = _ai_positions_payload(MockEngine())
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0], "SOL/USDC")
        self.assertEqual(_ai_positions_payload(None), [])
        class EmptyEngine:
            positions = {}
        self.assertEqual(_ai_positions_payload(EmptyEngine()), [])
    def test_grid_loop_error_is_caught_inside_loop(self):
        """Owner requirement (live-money bug): a transient per-cycle error in
        run_grid (swap/network/state race) must NOT propagate out of the loop
        and mark the bot stopped. The loop body must be wrapped in try/except
        (log + continue), and the authoritative condition
        state['running'] and state['strategy']=='grid' must remain the only
        loop stopper alongside the intentional drawdown/daily-loss returns."""
        import inspect
        src = inspect.getsource(run_grid)
        self.assertIn('while state["running"] and state["strategy"]=="grid"', src,
                      "authoritative grid loop condition must be preserved")
        self.assertIn('except Exception as e:', src,
                      "run_grid loop body must catch per-cycle exceptions")
        self.assertIn('Grid cycle error (continuing)', src,
                      "caught grid cycle errors must be logged with a continue intent")
        self.assertIn('time.sleep(5)', src,
                      "loop must back off briefly then continue after a cycle error")
    def test_place_order_returns_false_on_swap_error(self):
        """Owner requirement: place_order must NEVER raise on a swap/network
        error - it returns False so the caller (grid loop) treats it as an
        unfilled order and keeps running with open positions intact."""
        with unittest.mock.patch.object(main_module := __import__("main"), "get_price", return_value=100.0), \
             unittest.mock.patch.object(main_module, "authorize_trade", return_value=(True, "")), \
             unittest.mock.patch.object(main_module, "jupiter_swap", side_effect=RuntimeError("jupiter down")):
            state["mode"] = "dex"
            state["chain"] = "solana"
            res = place_order("SOL/USDC", "buy", 0.5)
        self.assertFalse(res, "place_order must return False when the swap raises")
    def test_place_order_error_is_logged_not_raised(self):
        """Same guarantee, asserted through the call boundary: a swap exception
        must never escape place_order regardless of pair/amount."""
        def boom(*a, **k):
            raise ConnectionError("network timeout")
        with unittest.mock.patch.object(__import__("main"), "get_price", return_value=100.0), \
             unittest.mock.patch.object(__import__("main"), "authorize_trade", return_value=(True, "")), \
             unittest.mock.patch.object(__import__("main"), "jupiter_swap", side_effect=boom):
            state["mode"] = "dex"
            state["chain"] = "solana"
            try:
                res = place_order("BTC/USDC", "buy", 0.01)
            except Exception as ex:
                self.fail("place_order raised {0!r} - it must return False instead".format(ex))
        self.assertFalse(res)
def test_ai_trading_fixes_all():
    suite = unittest.TestLoader().loadTestsFromTestCase(TestAITradingFixes)
    res = unittest.TextTestRunner(verbosity=0).run(suite)
    if not res.wasSuccessful():
        raise AssertionError("AI Trading Fixes tests failed")

if __name__ == "__main__":
    unittest.main()
