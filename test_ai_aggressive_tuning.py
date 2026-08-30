import inspect
import unittest

from ai_trading.signal import Signal, AI_MIN_SCORE
from ai_trading.strategies import generate_signals_and_score
from ai_trading.execution import AITradingEngine
from ai_trading.risk import RiskEngine


def _signal(direction: str, score: float) -> Signal:
    return Signal(
        symbol="SOL/USDC",
        venue="Solana",
        direction=direction,
        signal_score=score,
        confidence="MEDIUM",
        regime="TRENDING_BULL",
        strategy="Trend Following",
        entry=100.0,
        stop=95.0,
        take_profit=110.0,
        trailing_stop=98.0,
        risk_pct=1.0,
        position_size=10.0,
        recommended_leverage=1.0,
        reward_risk=2.0,
    )


class TestAggressiveAITuning(unittest.TestCase):
    """Pins the owner-approved aggressive tuning thresholds.

    Only SIGNAL-GENERATION thresholds change (score floor, min R:R, scan speed).
    The Risk Engine sizing/exposure/circuit-breaker defaults and the regime
    "stay flat on UNCERTAIN" filter must remain untouched.
    """

    def test_score_floor_constant_is_25(self):
        self.assertEqual(AI_MIN_SCORE, 25.0)

    def test_signal_score_30_is_now_tradeable(self):
        self.assertTrue(_signal("LONG", 30.0).is_tradeable())

    def test_signal_score_24_still_not_tradeable(self):
        self.assertFalse(_signal("LONG", 24.0).is_tradeable())
        self.assertFalse(_signal("SHORT", 24.0).is_tradeable())

    def test_no_trade_direction_is_never_tradeable(self):
        self.assertFalse(_signal("NO_TRADE", 85.0).is_tradeable())

    def test_min_rr_ratio_default_is_1_2(self):
        params = inspect.signature(generate_signals_and_score).parameters
        self.assertEqual(params["min_rr_ratio"].default, 1.2)

    def test_scan_interval_default_is_5(self):
        params = inspect.signature(AITradingEngine.start).parameters
        self.assertEqual(params["interval_sec"].default, 5.0)

    def test_regime_uncertain_still_flat(self):
        # Aggressive tuning must NOT loosen the regime filter.
        closes = [100.0] * 200
        signal = generate_signals_and_score(
            "SOL/USDC", "Solana",
            highs=closes, lows=closes, closes=closes, volumes=[1000.0] * len(closes),
            regime_info={"regime": "UNCERTAIN", "confidence": "LOW"},
        )
        self.assertEqual(signal.direction, "NO_TRADE")

    def test_risk_engine_defaults_untouched(self):
        # risk.py must remain byte-identical: drawdown limit default still 10.0,
        # and a config-driven drawdown limit is still honored as-is.
        engine = RiskEngine({})
        self.assertEqual(engine.max_drawdown_limit_pct, 10.0)


def test_ai_aggressive_tuning_all():
    suite = unittest.TestLoader().loadTestsFromTestCase(TestAggressiveAITuning)
    res = unittest.TextTestRunner(verbosity=0).run(suite)
    if not res.wasSuccessful():
        raise AssertionError("AI aggressive tuning tests failed")


if __name__ == "__main__":
    unittest.main()
