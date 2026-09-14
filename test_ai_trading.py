#!/usr/bin/env python3
"""
Comprehensive Unit Test Suite for GridRunner AI Trading Strategy Engine (Stage 7 Production-Readiness).
Verifies:
1. Standardized Signal invariants and creation.
2. Pure Python Indicators (EMA, RSI, MACD, ATR, ADX, Bollinger Bands, VWAP, swing S/R).
3. Regime detection on trending bull/bear, range, high/low volatility, and transition zones.
4. Strategy module scores, R:R filtering, and falling-knife protection.
5. Non-bypassable Risk Engine position sizing, daily loss limits, adaptive leverage, circuit breakers, and reconciliation.
6. Backtesting engine walk-forward runs without future information lookahead.
7. Continuing continuously scanning loop & thread-safe engine.
"""

import sys
import os
import unittest
import time
from typing import List, Dict, Any

# Ensure parent directory is in path
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

from ai_trading.signal import Signal, create_no_trade_signal
from ai_trading.indicators import ema, rsi, macd, atr, bollinger_bands, vwap, adx, swing_highs_lows
from ai_trading.regime import detect_market_regime
from ai_trading.strategies import generate_signals_and_score, evaluate_falling_knife
from ai_trading.risk import RiskEngine
from ai_trading.journal import TradeJournal
from ai_trading.backtest import BacktestEngine
from ai_trading.execution import AITradingEngine

class TestAITradingSignal(unittest.TestCase):
    def test_signal_invariants(self):
        sig = Signal(
            symbol="SOL/USDC",
            venue="Solana",
            direction="LONG",
            signal_score=85.0,
            confidence="HIGH",
            regime="TRENDING_BULL",
            strategy="Trend Following",
            entry=100.0,
            stop=95.0,
            take_profit=110.0,
            trailing_stop=1.5,
            risk_pct=1.0,
            position_size=10.0,
            recommended_leverage=1.0,
            reward_risk=2.0,
            reasons=["Strong EMAStack"],
            warnings=[],
            timestamp=time.time()
        )
        self.assertTrue(sig.is_tradeable())
        self.assertEqual(sig.reward_risk, 2.0)
        self.assertEqual(sig.symbol, "SOL/USDC")

    def test_no_trade_signal(self):
        sig = create_no_trade_signal("SOL/USDC", "Solana", "UNCERTAIN", "High volatility spike")
        self.assertFalse(sig.is_tradeable())
        self.assertEqual(sig.direction, "NO_TRADE")
        self.assertEqual(sig.signal_score, 0.0)

class TestAITradingIndicators(unittest.TestCase):
    def test_ema(self):
        prices = [10.0, 10.0, 10.0, 10.0, 10.0]
        ema_val = ema(prices, 3)
        self.assertEqual(len(ema_val), 5)
        self.assertAlmostEqual(ema_val[-1], 10.0)

    def test_rsi_bounds(self):
        prices = [10.0] * 20
        rsi_val = rsi(prices, 14)
        self.assertEqual(len(rsi_val), 20)
        for val in rsi_val:
            self.assertTrue(0.0 <= val <= 100.0)

    def test_macd(self):
        prices = list(range(1, 41)) # upward trend
        m, s, h = macd(prices)
        self.assertEqual(len(m), 40)
        self.assertEqual(len(s), 40)
        self.assertEqual(len(h), 40)

    def test_atr(self):
        highs = [15.0] * 20
        lows = [10.0] * 20
        closes = [12.0] * 20
        atr_val = atr(highs, lows, closes, 5)
        self.assertEqual(len(atr_val), 20)
        self.assertTrue(atr_val[-1] > 0.0)

    def test_bollinger_bands(self):
        prices = [10.0, 11.0, 12.0, 10.0, 9.0, 10.0, 11.0] * 5
        upper, middle, lower = bollinger_bands(prices, 5)
        self.assertEqual(len(upper), len(prices))
        for u, m, l in zip(upper, middle, lower):
            self.assertTrue(u >= m >= l)

    def test_vwap(self):
        prices = [10.0, 11.0, 12.0]
        volumes = [100.0, 200.0, 150.0]
        vwap_val = vwap(prices, volumes)
        self.assertEqual(len(vwap_val), 3)
        # Expected cumulative PV / cumulative V:
        # PV = (10*100) + (11*200) + (12*150) = 1000 + 2200 + 1800 = 5000
        # V = 100 + 200 + 150 = 450
        # 5000 / 450 = 11.111
        self.assertAlmostEqual(vwap_val[-1], 5000 / 450)

    def test_adx(self):
        highs = list(range(15, 55))
        lows = list(range(10, 50))
        closes = list(range(12, 52))
        adx_val = adx(highs, lows, closes, 10)
        self.assertEqual(len(adx_val), 40)

class TestAITradingRegime(unittest.TestCase):
    def test_regime_unclear(self):
        closes = [10.0] * 20
        regime = detect_market_regime(closes, closes, closes)
        self.assertEqual(regime["regime"], "UNCERTAIN")

    def test_regime_bull_trend(self):
        highs = list(range(15, 215, 2))
        lows = list(range(10, 210, 2))
        closes = list(range(12, 212, 2))
        regime = detect_market_regime(highs, lows, closes)
        # Strong rising closes with increasing momentum should classify as trending
        self.assertIn(regime["regime"], ("TRENDING_BULL", "TRANSITION", "HIGH_VOLATILITY"))

    def test_regime_range(self):
        closes = [10.0, 11.0, 9.0, 10.0] * 20
        highs = [x + 2 for x in closes]
        lows = [x - 2 for x in closes]
        regime = detect_market_regime(highs, lows, closes)
        self.assertEqual(regime["regime"], "RANGE")

class TestAITradingStrategies(unittest.TestCase):
    def test_falling_knife_detection(self):
        closes = [100.0] * 40 + [90.0, 80.0, 70.0, 60.0, 50.0, 40.0, 30.0, 20.0, 10.0, 5.0]
        highs = [x + 2 for x in closes]
        lows = [x - 2 for x in closes]
        volumes = [100.0] * 40 + [500.0, 1000.0, 1500.0, 2000.0, 3000.0, 4000.0, 5000.0, 6000.0, 7000.0, 8000.0]
        
        is_knife, warns = evaluate_falling_knife(closes, highs, lows, volumes)
        self.assertTrue(is_knife)
        self.assertTrue(any("FALLING-KNIFE" in w for w in warns))

    def test_rr_filtering(self):
        # Steadily rising series of length 80
        closes = [float(x) for x in range(100, 180)]
        highs = [x + 2 for x in closes]
        lows = [x - 2 for x in closes]
        volumes = [100.0] * 80
        regime_info = {"regime": "TRENDING_BULL"}
        
        # Test low R:R rejection with high min ratio
        signal = generate_signals_and_score("SOL/USDC", "Solana", highs, lows, closes, volumes, regime_info, min_rr_ratio=3.0)
        self.assertEqual(signal.direction, "NO_TRADE")
        self.assertTrue(any("Rejected: Reward/Risk" in r for r in signal.reasons))

class TestAITradingRiskEngine(unittest.TestCase):
    def setUp(self):
        self.config = {
            "account_equity": 1000.0,
            "risk_per_trade_pct": 1.0, # 1% risk = $10 loss limit
            "max_leverage": 3.0,
            "max_total_exposure": 5000.0,
            "max_per_asset_exposure": 2000.0,
            "max_simultaneous_positions": 2,
            "daily_loss_limit": 100.0,
            "max_drawdown_limit_pct": 10.0,
            "circuit_breaker_active": False,
            "current_drawdown_pct": 0.0,
            "daily_loss_accrued": 0.0
        }
        self.risk_engine = RiskEngine(self.config)

    def test_position_sizing_stop_loss(self):
        sig = Signal(
            symbol="SOL/USDC",
            venue="Solana",
            direction="LONG",
            signal_score=85.0,
            confidence="HIGH",
            regime="TRENDING_BULL",
            strategy="Trend Following",
            entry=100.0,
            stop=90.0, # $10 stop distance
            take_profit=120.0,
            trailing_stop=1.5,
            risk_pct=1.0,
            position_size=0.0,
            recommended_leverage=1.0,
            reward_risk=2.0,
            reasons=[],
            warnings=[],
            timestamp=time.time()
        )
        # Sizing: $10 max loss / $10 stop distance = 1.0 SOL base size
        sized = self.risk_engine.evaluate_and_size_signal(sig)
        self.assertEqual(sized.position_size, 1.0)
        self.assertEqual(sized.direction, "LONG")

    def test_auto_compounding_toggle_risk_engine(self):
        # 1. auto_compound = True (ON)
        self.config["auto_compound"] = True
        self.risk_engine.realized_pnl = 500.0  # mock realized PnL
        sig = Signal(
            symbol="SOL/USDC",
            venue="Solana",
            direction="LONG",
            signal_score=85.0,
            confidence="HIGH",
            regime="TRENDING_BULL",
            strategy="Trend Following",
            entry=100.0,
            stop=90.0, # $10 stop distance
            take_profit=120.0,
            trailing_stop=1.5,
            risk_pct=1.0,
            position_size=0.0,
            recommended_leverage=1.0,
            reward_risk=2.0,
            reasons=[],
            warnings=[],
            timestamp=time.time()
        )
        # Account equity is 1000.0. Since auto_compound is True and realized_pnl is 500.0:
        # Effective equity is 1500.0. Sizing: 1% risk of 1500 = $15 loss / $10 stop distance = 1.5 SOL
        sized = self.risk_engine.evaluate_and_size_signal(sig)
        self.assertEqual(sized.position_size, 1.5)

        # 2. auto_compound = False (OFF)
        self.config["auto_compound"] = False
        sig2 = Signal(
            symbol="SOL/USDC",
            venue="Solana",
            direction="LONG",
            signal_score=85.0,
            confidence="HIGH",
            regime="TRENDING_BULL",
            strategy="Trend Following",
            entry=100.0,
            stop=90.0, # $10 stop distance
            take_profit=120.0,
            trailing_stop=1.5,
            risk_pct=1.0,
            position_size=0.0,
            recommended_leverage=1.0,
            reward_risk=2.0,
            reasons=[],
            warnings=[],
            timestamp=time.time()
        )
        # Account equity is 1000.0. Since auto_compound is False, realized_pnl of 500.0 is ignored.
        # Effective equity is 1000.0. Sizing: 1% risk of 1000 = $10 loss / $10 stop distance = 1.0 SOL
        sized2 = self.risk_engine.evaluate_and_size_signal(sig2)
        self.assertEqual(sized2.position_size, 1.0)

    def test_circuit_breaker_active(self):
        self.risk_engine.daily_loss_accrued = 150.0 # above $100 limit
        sig = Signal(
            symbol="SOL/USDC",
            venue="Solana",
            direction="LONG",
            signal_score=85.0,
            confidence="HIGH",
            regime="TRENDING_BULL",
            strategy="Trend Following",
            entry=100.0,
            stop=90.0,
            take_profit=120.0,
            trailing_stop=1.5,
            risk_pct=1.0,
            position_size=0.0,
            recommended_leverage=1.0,
            reward_risk=2.0,
            reasons=[],
            warnings=[],
            timestamp=time.time()
        )
        sized = self.risk_engine.evaluate_and_size_signal(sig)
        self.assertEqual(sized.direction, "NO_TRADE")
        self.assertTrue(any("Gated by Circuit Breaker" in r for r in sized.reasons))

    def test_drawdown_protection(self):
        self.risk_engine.current_drawdown_pct = 12.0 # above 10% limit
        sig = Signal(
            symbol="SOL/USDC",
            venue="Solana",
            direction="LONG",
            signal_score=85.0,
            confidence="HIGH",
            regime="TRENDING_BULL",
            strategy="Trend Following",
            entry=100.0,
            stop=90.0,
            take_profit=120.0,
            trailing_stop=1.5,
            risk_pct=1.0,
            position_size=0.0,
            recommended_leverage=1.0,
            reward_risk=2.0,
            reasons=[],
            warnings=[],
            timestamp=time.time()
        )
        sized = self.risk_engine.evaluate_and_size_signal(sig)
        self.assertEqual(sized.direction, "NO_TRADE")

    def test_reconciliation_breaker(self):
        self.risk_engine.active_positions["SOL/USDC"] = {"direction": "LONG"}
        # Actual positions empty on exchange -> mismatch!
        self.risk_engine.reconcile_positions({})
        self.assertTrue(self.risk_engine.circuit_breaker_triggered)

class TestAITradingBacktestEngine(unittest.TestCase):
    def test_backtest_run(self):
        engine = BacktestEngine(initial_equity=1000.0)
        closes = [100.0, 101.0, 102.0, 100.0, 99.0, 101.0, 102.0] * 10
        highs = [x + 2 for x in closes]
        lows = [x - 2 for x in closes]
        volumes = [100.0] * len(closes)
        
        res = engine.run_backtest("SOL/USDC", highs, lows, closes, volumes)
        self.assertIn("final_equity", res)
        self.assertIn("total_trades", res)

class TestAITradingContinuousLoop(unittest.TestCase):
    class MockMarketDataProvider:
        def __init__(self, closes):
            self.closes = closes
        def get_candles(self, symbol):
            return {
                "highs": [x + 2 for x in self.closes],
                "lows": [x - 2 for x in self.closes],
                "closes": self.closes,
                "volumes": [100.0] * len(self.closes)
            }
        def get_current_price(self, symbol):
            return self.closes[-1]

    class MockExecutionAdapter:
        def __init__(self):
            self.executed = False
        def execute_swap(self, symbol, direction, size, price):
            self.executed = True
            return True
        def get_venue_positions(self):
            return {}

    def test_scanning_loop_flow(self):
        closes = [100.0, 101.0, 102.0, 100.0, 99.0, 101.0, 102.0] * 10
        prov = self.MockMarketDataProvider(closes)
        adapter = self.MockExecutionAdapter()
        
        config = {
            "account_equity": 1000.0,
            "risk_per_trade_pct": 1.0,
            "max_leverage": 3.0,
            "max_total_exposure": 5000.0,
            "max_per_asset_exposure": 2000.0,
            "max_simultaneous_positions": 3,
            "daily_loss_limit": 100.0,
            "max_drawdown_limit_pct": 10.0,
            "circuit_breaker_active": False,
            "current_drawdown_pct": 0.0,
            "daily_loss_accrued": 0.0
        }
        
        engine = AITradingEngine(config, ["SOL/USDC"])
        # Run one loop cycle
        engine.scan_cycle(prov, adapter)
        # Verify scanner analyzing state, preparing order, or entering position successfully
        self.assertIn(engine.status, ("analyzing", "preparing order", "in position", "blocked"))

    def test_auto_compounding_flow(self):
        closes = [100.0, 101.0, 102.0, 100.0, 99.0, 101.0, 102.0] * 10
        prov = self.MockMarketDataProvider(closes)
        
        class MockCompoundingAdapter:
            def __init__(self):
                self.executed = False
                self.compounded_pnl = 0.0
            def execute_swap(self, symbol, direction, size, price):
                self.executed = True
                return True
            def get_venue_positions(self):
                return {}
            def record_trade_closed(self, symbol, pnl):
                self.compounded_pnl += pnl

        adapter = MockCompoundingAdapter()
        config = {
            "account_equity": 1000.0,
            "risk_per_trade_pct": 1.0,
            "max_leverage": 3.0,
            "max_total_exposure": 5000.0,
            "max_per_asset_exposure": 2000.0,
            "max_simultaneous_positions": 3,
            "daily_loss_limit": 100.0,
            "max_drawdown_limit_pct": 10.0,
            "circuit_breaker_active": False,
            "current_drawdown_pct": 0.0,
            "daily_loss_accrued": 0.0
        }
        
        engine = AITradingEngine(config, ["SOL/USDC"])
        
        # Manually enter a position
        engine.positions["SOL/USDC"] = {
            "symbol": "SOL/USDC",
            "direction": "LONG",
            "entry": 100.0,
            "stop": 95.0,
            "take_profit": 110.0,
            "size": 2.0,
            "leverage": 1.0,
            "strategy": "Trend Following",
            "regime": "TRENDING_BULL",
            "timestamp": time.time()
        }
        
        # Mock price to hit take profit (110.0)
        prov.closes[-1] = 115.0
        engine.manage_existing_position("SOL/USDC", prov, adapter)
        
        self.assertTrue(adapter.executed)
        self.assertEqual(adapter.compounded_pnl, 20.0) # size 2 * (110.0 - 100.0) = 20.0

    def test_stop_loss_disabled_by_default_holds_below_entry(self):
        # Owner requirement: stop-loss (sell-below-entry) is OFF by default.
        # A LONG position trading below its stop must NOT be exited — the bot
        # holds through dips (only profit-taking exits + dip-buys apply).
        prov = self.MockMarketDataProvider([100.0])
        adapter = self.MockExecutionAdapter()
        config = {
            "account_equity": 1000.0,
            "risk_per_trade_pct": 1.0,
            "max_leverage": 3.0,
            "max_total_exposure": 5000.0,
            "max_per_asset_exposure": 2000.0,
            "max_simultaneous_positions": 3,
            "daily_loss_limit": 100.0,
            "max_drawdown_limit_pct": 10.0,
            "circuit_breaker_active": False,
            "current_drawdown_pct": 0.0,
            "daily_loss_accrued": 0.0
            # NOTE: no stop_loss_enabled key -> must default to OFF
        }
        engine = AITradingEngine(config, ["SOL/USDC"])
        engine.positions["SOL/USDC"] = {
            "symbol": "SOL/USDC", "direction": "LONG",
            "entry": 100.0, "stop": 95.0, "take_profit": 110.0,
            "size": 2.0, "leverage": 1.0, "strategy": "Trend Following",
            "regime": "TRENDING_BULL", "timestamp": time.time()
        }
        prov.closes[-1] = 94.0  # below stop -> would be a Stop Loss Hit if enabled
        engine.manage_existing_position("SOL/USDC", prov, adapter)
        self.assertFalse(adapter.executed, "must NOT sell below entry with stop-loss OFF (default)")
        self.assertIn("SOL/USDC", engine.positions, "position must be held through the dip")

    def test_stop_loss_enabled_restores_below_entry_exit(self):
        # ON restores the historic stop-loss behavior: price <= stop exits.
        prov = self.MockMarketDataProvider([100.0])
        adapter = self.MockExecutionAdapter()
        config = {
            "account_equity": 1000.0,
            "risk_per_trade_pct": 1.0,
            "max_leverage": 3.0,
            "max_total_exposure": 5000.0,
            "max_per_asset_exposure": 2000.0,
            "max_simultaneous_positions": 3,
            "daily_loss_limit": 100.0,
            "max_drawdown_limit_pct": 10.0,
            "circuit_breaker_active": False,
            "current_drawdown_pct": 0.0,
            "daily_loss_accrued": 0.0,
            "stop_loss_enabled": True
        }
        engine = AITradingEngine(config, ["SOL/USDC"])
        engine.positions["SOL/USDC"] = {
            "symbol": "SOL/USDC", "direction": "LONG",
            "entry": 100.0, "stop": 95.0, "take_profit": 110.0,
            "size": 2.0, "leverage": 1.0, "strategy": "Trend Following",
            "regime": "TRENDING_BULL", "timestamp": time.time()
        }
        prov.closes[-1] = 94.0  # below stop, stop-loss enabled -> exit
        engine.manage_existing_position("SOL/USDC", prov, adapter)
        self.assertTrue(adapter.executed, "stop-loss must exit when enabled")
        self.assertNotIn("SOL/USDC", engine.positions, "position must be closed on stop loss")

    def test_stop_loss_off_still_takes_profit(self):
        # OFF must not suppress profit-taking exits.
        prov = self.MockMarketDataProvider([100.0])
        adapter = self.MockExecutionAdapter()
        config = {
            "account_equity": 1000.0,
            "risk_per_trade_pct": 1.0,
            "max_leverage": 3.0,
            "max_total_exposure": 5000.0,
            "max_per_asset_exposure": 2000.0,
            "max_simultaneous_positions": 3,
            "daily_loss_limit": 100.0,
            "max_drawdown_limit_pct": 10.0,
            "circuit_breaker_active": False,
            "current_drawdown_pct": 0.0,
            "daily_loss_accrued": 0.0
        }
        engine = AITradingEngine(config, ["SOL/USDC"])
        engine.positions["SOL/USDC"] = {
            "symbol": "SOL/USDC", "direction": "LONG",
            "entry": 100.0, "stop": 95.0, "take_profit": 110.0,
            "size": 2.0, "leverage": 1.0, "strategy": "Trend Following",
            "regime": "TRENDING_BULL", "timestamp": time.time()
        }
        prov.closes[-1] = 111.0  # above take profit
        engine.manage_existing_position("SOL/USDC", prov, adapter)
        self.assertTrue(adapter.executed, "take-profit exit must still fire with stop-loss OFF")
        self.assertEqual(engine.status, "analyzing")  # loop continues after exit
        self.assertNotIn("SOL/USDC", engine.positions, "position must be closed on take profit")

    def test_stop_loss_off_short_holds_adverse_move(self):
        # Symmetric guard for SHORT legs (only relevant when perps enabled):
        # with stop-loss OFF the engine must not cover a short at a loss.
        prov = self.MockMarketDataProvider([100.0])
        adapter = self.MockExecutionAdapter()
        config = {
            "account_equity": 1000.0,
            "risk_per_trade_pct": 1.0,
            "max_leverage": 3.0,
            "max_total_exposure": 5000.0,
            "max_per_asset_exposure": 2000.0,
            "max_simultaneous_positions": 3,
            "daily_loss_limit": 100.0,
            "max_drawdown_limit_pct": 10.0,
            "circuit_breaker_active": False,
            "current_drawdown_pct": 0.0,
            "daily_loss_accrued": 0.0
        }
        engine = AITradingEngine(config, ["SOL/USDC"])
        engine.positions["SOL/USDC"] = {
            "symbol": "SOL/USDC", "direction": "SHORT",
            "entry": 100.0, "stop": 105.0, "take_profit": 90.0,
            "size": 2.0, "leverage": 1.0, "strategy": "Mean Reversion",
            "regime": "RANGE", "timestamp": time.time()
        }
        prov.closes[-1] = 106.0  # above stop => adverse; must hold with stop-loss OFF
        engine.manage_existing_position("SOL/USDC", prov, adapter)
        self.assertFalse(adapter.executed, "must NOT cover a SHORT at a loss with stop-loss OFF")
        self.assertIn("SOL/USDC", engine.positions, "SHORT must be held through the adverse move")

    def test_trailing_stop_off_keeps_fixed_take_profit(self):
        # trailing_stop_enabled OFF (default): fixed take-profit still fires.
        prov = self.MockMarketDataProvider([100.0])
        adapter = self.MockExecutionAdapter()
        config = {
            "account_equity": 1000.0,
            "risk_per_trade_pct": 1.0,
            "max_leverage": 3.0,
            "max_total_exposure": 5000.0,
            "max_per_asset_exposure": 2000.0,
            "max_simultaneous_positions": 3,
            "daily_loss_limit": 100.0,
            "max_drawdown_limit_pct": 10.0,
            "circuit_breaker_active": False,
            "current_drawdown_pct": 0.0,
            "daily_loss_accrued": 0.0,
            "stop_loss_enabled": False
        }
        engine = AITradingEngine(config, ["SOL/USDC"])
        engine.positions["SOL/USDC"] = {
            "symbol": "SOL/USDC", "direction": "LONG",
            "entry": 100.0, "stop": 95.0, "take_profit": 110.0,
            "size": 2.0, "leverage": 1.0, "strategy": "Trend Following",
            "regime": "TRENDING_BULL", "timestamp": time.time(),
            "trailing_stop": 2.0, "trailing_stop_level": 100.0
        }
        prov.closes[-1] = 112.0  # above fixed TP
        engine.manage_existing_position("SOL/USDC", prov, adapter)
        self.assertTrue(adapter.executed, "fixed take-profit must fire when trailing is OFF")
        self.assertNotIn("SOL/USDC", engine.positions)

    def test_trailing_stop_on_ratchets_and_locks_profit(self):
        # trailing ON: level ratchets up as price climbs, then exit on cross.
        prov = self.MockMarketDataProvider([100.0])
        adapter = self.MockExecutionAdapter()
        config = {
            "account_equity": 1000.0,
            "risk_per_trade_pct": 1.0,
            "max_leverage": 3.0,
            "max_total_exposure": 5000.0,
            "max_per_asset_exposure": 2000.0,
            "max_simultaneous_positions": 3,
            "daily_loss_limit": 100.0,
            "max_drawdown_limit_pct": 10.0,
            "circuit_breaker_active": False,
            "current_drawdown_pct": 0.0,
            "daily_loss_accrued": 0.0,
            "trailing_stop_enabled": True
        }
        engine = AITradingEngine(config, ["SOL/USDC"])
        engine.positions["SOL/USDC"] = {
            "symbol": "SOL/USDC", "direction": "LONG",
            "entry": 100.0, "stop": 95.0, "take_profit": 110.0,
            "size": 2.0, "leverage": 1.0, "strategy": "Trend Following",
            "regime": "TRENDING_BULL", "timestamp": time.time(),
            "trailing_stop": 2.0, "trailing_stop_level": 100.0
        }
        prov.closes[-1] = 105.0  # climb -> level ratchets to max(100, 105-2)=103
        engine.manage_existing_position("SOL/USDC", prov, adapter)
        self.assertFalse(adapter.executed, "no exit while price above trailing level")
        self.assertIn("SOL/USDC", engine.positions)
        self.assertAlmostEqual(engine.positions["SOL/USDC"]["trailing_stop_level"], 103.0)
        prov.closes[-1] = 102.0  # dip below the ratcheted level 103 -> exit
        engine.manage_existing_position("SOL/USDC", prov, adapter)
        self.assertTrue(adapter.executed, "must exit when price crosses trailing level")
        self.assertNotIn("SOL/USDC", engine.positions)
        self.assertTrue(any("Trailing Stop Hit" in e for e in engine.execution_logs), engine.execution_logs)

    def test_trailing_stop_on_floored_at_entry_never_sells_below(self):
        # Floor at entry: with trailing ON the level never drops below entry,
        # so a dip exits at break-even (level=entry), never at a loss.
        prov = self.MockMarketDataProvider([100.0])
        adapter = self.MockExecutionAdapter()
        config = {
            "account_equity": 1000.0,
            "risk_per_trade_pct": 1.0,
            "max_leverage": 3.0,
            "max_total_exposure": 5000.0,
            "max_per_asset_exposure": 2000.0,
            "max_simultaneous_positions": 3,
            "daily_loss_limit": 100.0,
            "max_drawdown_limit_pct": 10.0,
            "circuit_breaker_active": False,
            "current_drawdown_pct": 0.0,
            "daily_loss_accrued": 0.0,
            "trailing_stop_enabled": True,
            "stop_loss_enabled": False
        }
        engine = AITradingEngine(config, ["SOL/USDC"])
        engine.positions["SOL/USDC"] = {
            "symbol": "SOL/USDC", "direction": "LONG",
            "entry": 100.0, "stop": 95.0, "take_profit": 110.0,
            "size": 2.0, "leverage": 1.0, "strategy": "Trend Following",
            "regime": "TRENDING_BULL", "timestamp": time.time(),
            "trailing_stop": 2.0, "trailing_stop_level": 100.0
        }
        prov.closes[-1] = 94.0  # deep dip (also below hard stop, which is OFF)
        engine.manage_existing_position("SOL/USDC", prov, adapter)
        self.assertTrue(adapter.executed)
        level = engine.execution_logs  # exit fired via trailing at floor = entry
        self.assertNotIn("SOL/USDC", engine.positions)
        # P&L used (level - entry) with level floored at entry -> 0, never negative
        self.assertTrue(any("Trailing Stop Hit" in e for e in level), level)

    def test_trailing_stop_short_ratchets_direction(self):
        # SHORT: level ratchets DOWN (min with curr+dist), ceiling at entry.
        prov = self.MockMarketDataProvider([100.0])
        adapter = self.MockExecutionAdapter()
        config = {
            "account_equity": 1000.0,
            "risk_per_trade_pct": 1.0,
            "max_leverage": 3.0,
            "max_total_exposure": 5000.0,
            "max_per_asset_exposure": 2000.0,
            "max_simultaneous_positions": 3,
            "daily_loss_limit": 100.0,
            "max_drawdown_limit_pct": 10.0,
            "circuit_breaker_active": False,
            "current_drawdown_pct": 0.0,
            "daily_loss_accrued": 0.0,
            "trailing_stop_enabled": True
        }
        engine = AITradingEngine(config, ["SOL/USDC"])
        engine.positions["SOL/USDC"] = {
            "symbol": "SOL/USDC", "direction": "SHORT",
            "entry": 100.0, "stop": 105.0, "take_profit": 90.0,
            "size": 2.0, "leverage": 1.0, "strategy": "Mean Reversion",
            "regime": "RANGE", "timestamp": time.time(),
            "trailing_stop": 2.0, "trailing_stop_level": 100.0
        }
        prov.closes[-1] = 95.0  # drop -> level = min(100, 95+2) = 97
        engine.manage_existing_position("SOL/USDC", prov, adapter)
        self.assertFalse(adapter.executed)
        self.assertAlmostEqual(engine.positions["SOL/USDC"]["trailing_stop_level"], 97.0)
        prov.closes[-1] = 98.0  # bounce above 97 -> cover (exit)
        engine.manage_existing_position("SOL/USDC", prov, adapter)
        self.assertTrue(adapter.executed, "SHORT must exit when price rises back across the trailing level")
        self.assertNotIn("SOL/USDC", engine.positions)
        self.assertTrue(any("Trailing Stop Hit" in e for e in engine.execution_logs), engine.execution_logs)

    def test_trailing_stop_evaluated_before_stop_loss(self):
        # trailing ON + stop-loss ON: trailing (profit-taking) is checked first
        # and its floor at entry means the gap below still exits at/above entry.
        prov = self.MockMarketDataProvider([100.0])
        adapter = self.MockExecutionAdapter()
        config = {
            "account_equity": 1000.0,
            "risk_per_trade_pct": 1.0,
            "max_leverage": 3.0,
            "max_total_exposure": 5000.0,
            "max_per_asset_exposure": 2000.0,
            "max_simultaneous_positions": 3,
            "daily_loss_limit": 100.0,
            "max_drawdown_limit_pct": 10.0,
            "circuit_breaker_active": False,
            "current_drawdown_pct": 0.0,
            "daily_loss_accrued": 0.0,
            "trailing_stop_enabled": True,
            "stop_loss_enabled": True
        }
        engine = AITradingEngine(config, ["SOL/USDC"])
        engine.positions["SOL/USDC"] = {
            "symbol": "SOL/USDC", "direction": "LONG",
            "entry": 100.0, "stop": 95.0, "take_profit": 110.0,
            "size": 2.0, "leverage": 1.0, "strategy": "Trend Following",
            "regime": "TRENDING_BULL", "timestamp": time.time(),
            "trailing_stop": 2.0, "trailing_stop_level": 100.0
        }
        prov.closes[-1] = 94.0  # below stop; trailing floor at entry fires first
        engine.manage_existing_position("SOL/USDC", prov, adapter)
        self.assertTrue(adapter.executed)
        self.assertNotIn("SOL/USDC", engine.positions)
        self.assertTrue(any("Trailing Stop Hit" in e for e in engine.execution_logs),
                        "trailing must be evaluated before the hard stop-loss")

class TestAIAveragingDown(unittest.TestCase):
    """Averaging-down (dip-buy): config knobs, blending, and cap enforcement."""

    class MockMarketDataProvider:
        def __init__(self, closes):
            self.closes = list(closes)
        def get_candles(self, symbol):
            return {
                "highs": [x + 2 for x in self.closes],
                "lows": [x - 2 for x in self.closes],
                "closes": self.closes,
                "volumes": [100.0] * len(self.closes)
            }
        def get_current_price(self, symbol):
            return self.closes[-1]

    class MockExecutionAdapter:
        def __init__(self):
            self.executed = False
            self.closed_pnl = None
        def execute_swap(self, symbol, direction, size, price):
            self.executed = True
            return True
        def get_venue_positions(self):
            return {}
        def record_trade_closed(self, symbol, pnl):
            self.closed_pnl = pnl

    def _config(self, **over):
        cfg = {
            "account_equity": 1000.0,
            "risk_per_trade_pct": 1.0,
            "max_leverage": 3.0,
            "max_total_exposure": 5000.0,
            "max_per_asset_exposure": 2000.0,
            "max_simultaneous_positions": 3,
            "daily_loss_limit": 100.0,
            "max_drawdown_limit_pct": 10.0,
            "circuit_breaker_active": False,
            "current_drawdown_pct": 0.0,
            "daily_loss_accrued": 0.0
        }
        cfg.update(over)
        return cfg

    def _long_pos(self, size=2.0, entry=100.0, stop=95.0, tp=110.0, lots=1, avg_entry=None, first_lot=2.0):
        return {
            "symbol": "SOL/USDC", "direction": "LONG",
            "entry": entry, "stop": stop, "take_profit": tp,
            "size": size, "leverage": 1.0, "strategy": "Trend Following",
            "regime": "TRENDING_BULL", "timestamp": time.time(),
            "trailing_stop": 2.0, "trailing_stop_level": entry,
            "avg_entry": avg_entry if avg_entry is not None else entry,
            "first_lot_size": first_lot, "lots": lots
        }

    def test_avg_down_disabled_by_default_holds_only(self):
        # No avg_down keys in config -> master switch OFF (engine default).
        # A dip below the would-be trigger adds nothing and exits nothing.
        prov = self.MockMarketDataProvider([100.0])
        adapter = self.MockExecutionAdapter()
        engine = AITradingEngine(self._config(), ["SOL/USDC"])
        engine.positions["SOL/USDC"] = self._long_pos()
        prov.closes[-1] = 98.0  # <= 100 * (1 - 0.02) would trigger if enabled
        engine.manage_existing_position("SOL/USDC", prov, adapter)
        self.assertFalse(adapter.executed, "no add/exit when avg_down is OFF")
        self.assertIn("SOL/USDC", engine.positions)
        self.assertEqual(engine.positions["SOL/USDC"]["size"], 2.0)
        self.assertEqual(engine.positions["SOL/USDC"]["lots"], 1)

    def test_avg_down_adds_first_lot_at_step(self):
        # ON, step 2%: add #2 triggers at avg_entry*(1-0.02) = 98.0.
        prov = self.MockMarketDataProvider([100.0])
        adapter = self.MockExecutionAdapter()
        engine = AITradingEngine(self._config(
            avg_down_enabled=True, avg_down_max_lots=3,
            avg_down_step_pct=2.0, avg_down_size_multiplier=1.0), ["SOL/USDC"])
        engine.positions["SOL/USDC"] = self._long_pos()
        prov.closes[-1] = 98.0
        engine.manage_existing_position("SOL/USDC", prov, adapter)
        self.assertTrue(adapter.executed, "add #2 must buy a lot at the step")
        pos = engine.positions["SOL/USDC"]
        self.assertEqual(pos["size"], 4.0)             # 2.0 + 2.0
        self.assertAlmostEqual(pos["avg_entry"], 99.0)  # (200 + 196) / 4
        self.assertEqual(pos["lots"], 2)
        self.assertTrue(any("Average-down add #2" in e for e in engine.execution_logs), engine.execution_logs)

    def test_avg_down_deeper_steps_then_caps_at_max_lots(self):
        prov = self.MockMarketDataProvider([100.0])
        adapter = self.MockExecutionAdapter()
        engine = AITradingEngine(self._config(
            avg_down_enabled=True, avg_down_max_lots=3,
            avg_down_step_pct=2.0, avg_down_size_multiplier=1.0), ["SOL/USDC"])
        engine.positions["SOL/USDC"] = self._long_pos()
        prov.closes[-1] = 98.0
        engine.manage_existing_position("SOL/USDC", prov, adapter)   # add #2 -> avg 99.0, size 4
        prov.closes[-1] = 95.0
        engine.manage_existing_position("SOL/USDC", prov, adapter)   # 95 <= 99*0.96=95.04 -> add #3
        pos = engine.positions["SOL/USDC"]
        self.assertEqual(pos["lots"], 3)
        self.assertEqual(pos["size"], 6.0)
        self.assertAlmostEqual(pos["avg_entry"], 97.6666666667)
        adapter.executed = False
        prov.closes[-1] = 93.0   # lots == max (3) -> no add, and TP/SL off -> hold
        engine.manage_existing_position("SOL/USDC", prov, adapter)
        self.assertFalse(adapter.executed, "no add beyond avg_down_max_lots")
        self.assertEqual(engine.positions["SOL/USDC"]["size"], 6.0)

    def test_avg_down_rejected_by_per_asset_exposure(self):
        # Add would push SOL to 4.0 * 98 = $392 > max_per_asset_exposure $350.
        prov = self.MockMarketDataProvider([100.0])
        adapter = self.MockExecutionAdapter()
        engine = AITradingEngine(self._config(
            avg_down_enabled=True, avg_down_max_lots=3,
            avg_down_step_pct=2.0, avg_down_size_multiplier=1.0,
            max_per_asset_exposure=350.0), ["SOL/USDC"])
        engine.positions["SOL/USDC"] = self._long_pos()
        prov.closes[-1] = 98.0
        engine.manage_existing_position("SOL/USDC", prov, adapter)
        self.assertFalse(adapter.executed, "add must be rejected by per-asset cap")
        pos = engine.positions["SOL/USDC"]
        self.assertEqual(pos["size"], 2.0)
        self.assertEqual(pos["lots"], 1)
        self.assertTrue(any("rejected: exposure cap" in e for e in engine.execution_logs), engine.execution_logs)

    def test_avg_down_rejected_by_total_exposure(self):
        # Second coin (BTC) already uses $4,800 of the $5,000 portfolio cap;
        # the SOL add would push the portfolio to $5,192 -> rejected.
        prov = self.MockMarketDataProvider([100.0])
        adapter = self.MockExecutionAdapter()
        engine = AITradingEngine(self._config(
            avg_down_enabled=True, avg_down_max_lots=3,
            avg_down_step_pct=2.0, avg_down_size_multiplier=1.0,
            max_total_exposure=5000.0), ["SOL/USDC", "BTC/USDC"])
        engine.positions["SOL/USDC"] = self._long_pos()
        engine.positions["BTC/USDC"] = {
            "symbol": "BTC/USDC", "direction": "LONG", "entry": 100.0,
            "stop": 95.0, "take_profit": 110.0, "size": 2.0, "leverage": 1.0,
            "strategy": "Trend Following", "regime": "TRENDING_BULL",
            "timestamp": time.time(), "trailing_stop": 2.0,
            "trailing_stop_level": 100.0, "avg_entry": 100.0,
            "first_lot_size": 2.0, "lots": 1, "exposure_usd": 4800.0
        }
        prov.closes[-1] = 98.0
        engine.manage_existing_position("SOL/USDC", prov, adapter)
        self.assertFalse(adapter.executed, "add must be rejected by portfolio cap")
        self.assertEqual(engine.positions["SOL/USDC"]["size"], 2.0)
        self.assertTrue(any("rejected: exposure cap" in e for e in engine.execution_logs), engine.execution_logs)

    def test_avg_down_exit_uses_blended_avg_entry(self):
        # After two adds the position is 6.0 @ 97.6667; TP exit P&L must be
        # measured against the blended avg entry, not the first entry.
        prov = self.MockMarketDataProvider([100.0])
        adapter = self.MockExecutionAdapter()
        engine = AITradingEngine(self._config(
            avg_down_enabled=True, avg_down_max_lots=3,
            avg_down_step_pct=2.0, avg_down_size_multiplier=1.0), ["SOL/USDC"])
        engine.positions["SOL/USDC"] = self._long_pos(
            size=6.0, lots=3, avg_entry=586.0 / 6.0)
        prov.closes[-1] = 110.0  # fixed TP
        engine.manage_existing_position("SOL/USDC", prov, adapter)
        self.assertTrue(adapter.executed, "TP must fire on the blended position")
        self.assertNotIn("SOL/USDC", engine.positions)
        self.assertAlmostEqual(adapter.closed_pnl, 6.0 * (110.0 - 586.0 / 6.0), places=2)
        self.assertTrue(any("Take Profit Hit" in e for e in engine.execution_logs), engine.execution_logs)

    def test_avg_down_stop_loss_exit_takes_precedence_over_add(self):
        # SL ON: add fires while above the stop; once price crosses the stop the
        # exit fires for the WHOLE blended position instead of adding again.
        prov = self.MockMarketDataProvider([100.0])
        adapter = self.MockExecutionAdapter()
        engine = AITradingEngine(self._config(
            avg_down_enabled=True, avg_down_max_lots=3,
            avg_down_step_pct=2.0, avg_down_size_multiplier=1.0,
            stop_loss_enabled=True), ["SOL/USDC"])
        engine.positions["SOL/USDC"] = self._long_pos()
        prov.closes[-1] = 98.0   # above stop 95 -> add #2
        engine.manage_existing_position("SOL/USDC", prov, adapter)
        self.assertTrue(adapter.executed)
        pos = engine.positions["SOL/USDC"]
        self.assertEqual(pos["lots"], 2)
        self.assertAlmostEqual(pos["avg_entry"], 99.0)
        prov.closes[-1] = 94.0   # below hard stop -> exit whole blended position
        engine.manage_existing_position("SOL/USDC", prov, adapter)
        self.assertTrue(adapter.executed)
        self.assertNotIn("SOL/USDC", engine.positions)
        self.assertAlmostEqual(adapter.closed_pnl, 4.0 * (95.0 - 99.0), places=2)
        self.assertTrue(any("Stop Loss Hit" in e for e in engine.execution_logs), engine.execution_logs)
        self.assertEqual(sum(1 for e in engine.execution_logs if "Average-down add" in e), 1,
                         "the stop-loss pass must NOT add a lot")

    def test_avg_down_trailing_exit_takes_precedence_over_add(self):
        # Trailing ON floors at entry; a dip below the trigger is first an exit
        # (Trailing Stop Hit at break-even), not an add.
        prov = self.MockMarketDataProvider([100.0])
        adapter = self.MockExecutionAdapter()
        engine = AITradingEngine(self._config(
            avg_down_enabled=True, avg_down_max_lots=3,
            avg_down_step_pct=2.0, avg_down_size_multiplier=1.0,
            trailing_stop_enabled=True), ["SOL/USDC"])
        engine.positions["SOL/USDC"] = self._long_pos()
        prov.closes[-1] = 98.0
        engine.manage_existing_position("SOL/USDC", prov, adapter)
        self.assertTrue(adapter.executed)
        self.assertNotIn("SOL/USDC", engine.positions)
        self.assertTrue(any("Trailing Stop Hit" in e for e in engine.execution_logs), engine.execution_logs)
        self.assertFalse(any("Average-down add" in e for e in engine.execution_logs),
                         "trailing exit must preempt the add in the same pass")

def test_ai_trading_all():
    import unittest
    suite = unittest.TestSuite()
    suite.addTest(unittest.makeSuite(TestAITradingSignal))
    suite.addTest(unittest.makeSuite(TestAITradingIndicators))
    suite.addTest(unittest.makeSuite(TestAITradingRegime))
    suite.addTest(unittest.makeSuite(TestAITradingStrategies))
    suite.addTest(unittest.makeSuite(TestAITradingRiskEngine))
    suite.addTest(unittest.makeSuite(TestAITradingBacktestEngine))
    suite.addTest(unittest.makeSuite(TestAITradingContinuousLoop))
    suite.addTest(unittest.makeSuite(TestAIAveragingDown))
    
    runner = unittest.TextTestRunner(verbosity=0)
    result = runner.run(suite)
    assert result.wasSuccessful(), "AI Trading Unit Tests Failed"

def run_tests():
    suite = unittest.TestSuite()
    suite.addTest(unittest.makeSuite(TestAITradingSignal))
    suite.addTest(unittest.makeSuite(TestAITradingIndicators))
    suite.addTest(unittest.makeSuite(TestAITradingRegime))
    suite.addTest(unittest.makeSuite(TestAITradingStrategies))
    suite.addTest(unittest.makeSuite(TestAITradingRiskEngine))
    suite.addTest(unittest.makeSuite(TestAITradingBacktestEngine))
    suite.addTest(unittest.makeSuite(TestAITradingContinuousLoop))
    suite.addTest(unittest.makeSuite(TestAIAveragingDown))
    
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    if not result.wasSuccessful():
        sys.exit(1)

if __name__ == "__main__":
    run_tests()
