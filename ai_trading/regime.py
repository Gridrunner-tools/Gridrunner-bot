from typing import List, Dict, Any
from ai_trading.indicators import ema, adx, atr, bollinger_bands

def detect_market_regime(highs: List[float], lows: List[float], closes: List[float], volumes: List[float] = None) -> Dict[str, Any]:
    """
    Detect the current market regime based on multi-indicator quantitative rules.
    Returns a dict with:
        "regime": TRENDING_BULL, TRENDING_BEAR, RANGE, HIGH_VOLATILITY, LOW_VOLATILITY, TRANSITION, UNCERTAIN
        "adx": float
        "atr": float
        "bb_width_pct": float
        "ema_trend_score": int
        "reasons": List[str]
    """
    n = len(closes)
    default_res = {
        "regime": "UNCERTAIN",
        "adx": 20.0,
        "atr": 0.1,
        "bb_width_pct": 2.0,
        "ema_trend_score": 0,
        "reasons": ["Inadequate data history"]
    }
    if n < 50:
        return default_res

    # 1. Compute Indicators
    ema9 = ema(closes, 9)[-1]
    ema21 = ema(closes, 21)[-1]
    ema50 = ema(closes, 50)[-1]
    ema200 = ema(closes, 200)[-1] if n >= 200 else ema(closes, 50)[-1]
    
    adx_series = adx(highs, lows, closes, 14)
    adx_val = adx_series[-1]
    
    atr_series = atr(highs, lows, closes, 14)
    atr_val = atr_series[-1]
    
    upper, middle, lower = bollinger_bands(closes, 20, 2.0)
    bb_width = upper[-1] - lower[-1]
    bb_width_pct = (bb_width / middle[-1]) * 100 if middle[-1] > 0 else 0.0
    
    # 2. EMA Trend Alignment Score
    # Simple score: +1 for each faster EMA above slower EMA, -1 for opposite
    ema_trend_score = 0
    if ema9 > ema21: ema_trend_score += 1
    else: ema_trend_score -= 1
    if ema21 > ema50: ema_trend_score += 1
    else: ema_trend_score -= 1
    if ema50 > ema200: ema_trend_score += 1
    else: ema_trend_score -= 1
    
    # 3. historical Volatility Averages for context
    historical_atr_avg = sum(atr_series[-min(n, 50):]) / min(n, 50)
    historical_bb_avg = sum((u - l) / m * 100 for u, m, l in zip(upper[-min(n, 50):], middle[-min(n, 50):], lower[-min(n, 50):]) if m > 0) / min(n, 50)
    
    relative_volatility = atr_val / historical_atr_avg if historical_atr_avg > 0 else 1.0
    relative_bb_width = bb_width_pct / historical_bb_avg if historical_bb_avg > 0 else 1.0

    reasons = []
    
    # 4. Classification Rules
    # Extreme Volatility check first
    if relative_volatility > 2.5 or relative_bb_width > 2.5:
        regime = "HIGH_VOLATILITY"
        reasons.append(f"Volatility is exploding (relative ATR: {relative_volatility:.2f}x, relative BB width: {relative_bb_width:.2f}x)")
    elif relative_volatility < 0.4 and relative_bb_width < 0.4:
        regime = "LOW_VOLATILITY"
        reasons.append(f"Market volatility is extremely compressed (relative ATR: {relative_volatility:.2f}x)")
    
    # Trend vs Range check if volatility isn't breaking circuit breaker thresholds
    else:
        if adx_val >= 25.0:
            if ema_trend_score >= 2:
                regime = "TRENDING_BULL"
                reasons.append(f"ADX ({adx_val:.1f}) is strong and EMAs are in bullish alignment (EMA 9 > 21 > 50)")
            elif ema_trend_score <= -2:
                regime = "TRENDING_BEAR"
                reasons.append(f"ADX ({adx_val:.1f}) is strong and EMAs are in bearish alignment (EMA 9 < 21 < 50)")
            else:
                regime = "TRANSITION"
                reasons.append(f"ADX is high ({adx_val:.1f}), but EMA structures are crossing or contradictory")
        elif adx_val < 20.0:
            regime = "RANGE"
            reasons.append(f"ADX ({adx_val:.1f}) is low, indicating a trendless, bound range")
        else:
            # ADX is between 20 and 25
            if ema_trend_score == 3:
                regime = "TRENDING_BULL"
                reasons.append("Moderate trend with strong EMA alignment")
            elif ema_trend_score == -3:
                regime = "TRENDING_BEAR"
                reasons.append("Moderate trend with strong bearish EMA alignment")
            else:
                regime = "TRANSITION"
                reasons.append("Market is in a transition zone (ADX between 20-25 with mixed indicators)")
                
    return {
        "regime": regime,
        "adx": adx_val,
        "atr": atr_val,
        "bb_width_pct": bb_width_pct,
        "ema_trend_score": ema_trend_score,
        "reasons": reasons
    }
