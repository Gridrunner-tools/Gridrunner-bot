from dataclasses import dataclass, field
from typing import List, Optional
import time

@dataclass
class Signal:
    symbol: str
    venue: str  # "Solana" or "Kalshi"
    direction: str  # "LONG", "SHORT", or "NO_TRADE"
    signal_score: float  # 0 to 100
    confidence: str  # "LOW", "MEDIUM", "HIGH", "VERY_HIGH"
    regime: str  # "TRENDING_BULL", "TRENDING_BEAR", "RANGE", "HIGH_VOLATILITY", "LOW_VOLATILITY", "TRANSITION", "UNCERTAIN"
    strategy: str  # "Trend Following", "Mean Reversion", "Breakout", "Adaptive Grid", "None"
    entry: float
    stop: float
    take_profit: float
    trailing_stop: float
    risk_pct: float  # e.g., 1.0 for 1%
    position_size: float  # computed position size in tokens or units
    recommended_leverage: float  # e.g., 1.0 to 5.0
    reward_risk: float  # R:R ratio
    reasons: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    timestamp: float = field(default_factory=time.time)

    def is_tradeable(self) -> bool:
        return self.direction in ("LONG", "SHORT") and self.signal_score >= 40.0

def create_no_trade_signal(symbol: str, venue: str, regime: str, reason: str, warnings: List[str] = None) -> Signal:
    return Signal(
        symbol=symbol,
        venue=venue,
        direction="NO_TRADE",
        signal_score=0.0,
        confidence="LOW",
        regime=regime,
        strategy="None",
        entry=0.0,
        stop=0.0,
        take_profit=0.0,
        trailing_stop=0.0,
        risk_pct=0.0,
        position_size=0.0,
        recommended_leverage=1.0,
        reward_risk=0.0,
        reasons=[reason],
        warnings=warnings or [],
        timestamp=time.time()
    )
