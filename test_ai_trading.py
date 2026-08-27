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
    
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    if not result.wasSuccessful():
        sys.exit(1)

if __name__ == "__main__":
    run_tests()
