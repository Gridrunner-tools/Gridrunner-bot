import time
from typing import List, Tuple, Dict, Any
from ai_trading.indicators import ema, rsi, macd, atr, bollinger_bands, vwap, swing_highs_lows, momentum
from ai_trading.signal import Signal, create_no_trade_signal, AI_MIN_SCORE

def evaluate_falling_knife(closes: List[float], highs: List[float], lows: List[float], volumes: List[float]) -> Tuple[bool, List[str]]:
    """
    Check for the classic falling-knife trap:
    RSI oversold + volume exploding + below VWAP + bearish EMA + ATR expanding + support broken.
    Returns (is_knife: bool, warnings: list).
    """
    warnings = []
    if len(closes) < 50:
        return False, []

    # Indicators
    rsi_val = rsi(closes, 14)[-1]
    ema21 = ema(closes, 21)[-1]
    ema50 = ema(closes, 50)[-1]
    atr_series = atr(highs, lows, closes, 14)
    atr_expanding = atr_series[-1] > atr_series[-5]
    vwap_val = vwap(closes, volumes)[-1]
    
    current_vol = volumes[-1]
    avg_vol = sum(volumes[-20:]) / 20
    vol_exploding = current_vol > avg_vol * 1.8
    
    price_below_vwap = closes[-1] < vwap_val
    bearish_ema = ema21 < ema50 and closes[-1] < ema21
    
    # Simple support check (lowest low of last 20 periods except current)
    support_level = min(lows[-21:-1])
    support_broken = closes[-1] < support_level

    knife_conditions = []
    if rsi_val < 30: knife_conditions.append("RSI is extremely oversold")
    if vol_exploding: knife_conditions.append("Volume is exploding")
    if price_below_vwap: knife_conditions.append("Price is below VWAP")
    if bearish_ema: knife_conditions.append("Bearish EMA alignment")
    if atr_expanding: knife_conditions.append("ATR volatility is expanding")
    if support_broken: knife_conditions.append("Recent support level broken")

    # If at least 4 of these are true, it is a falling-knife trap!
    if len(knife_conditions) >= 4:
        warnings.append(f"FALLING-KNIFE DETECTED: {', '.join(knife_conditions)}")
        return True, warnings
    return False, []

def calculate_rr_ratio(direction: str, entry: float, stop: float, take_profit: float) -> float:
    """Calculate reward-risk ratio."""
    risk = abs(entry - stop)
    reward = abs(take_profit - entry)
    if risk == 0:
        return 0.0
    return reward / risk

def generate_signals_and_score(
    symbol: str, 
    venue: str,
    highs: List[float], 
    lows: List[float], 
    closes: List[float], 
    volumes: List[float],
    regime_info: Dict[str, Any],
    min_rr_ratio: float = 1.2,
    enable_perps: bool = False
) -> Signal:
    """
    Analyze market data and generate a standardized Signal with weighted scoring.
    """
    regime = regime_info["regime"]
    current_price = closes[-1]
    
    # 1. First-class NO_TRADE decision on regime
    if regime == "UNCERTAIN":
        return create_no_trade_signal(symbol, venue, regime, "Market regime is UNCERTAIN — staying flat")
    
    # Indicators
    ema9_series = ema(closes, 9)
    ema21_series = ema(closes, 21)
    ema50_series = ema(closes, 50)
    ema200_series = ema(closes, 200) if len(closes) >= 200 else ema50_series
    
    rsi_series = rsi(closes, 14)
    macd_line, sig_line, hist = macd(closes)
    atr_series = atr(highs, lows, closes, 14)
    upper, middle, lower = bollinger_bands(closes, 20, 2.0)
    vwap_series = vwap(closes, volumes)
    mom_series = momentum(closes, 10)
    
    # Swing S/R
    sw_high, sw_low = swing_highs_lows(highs, lows, 5)
    recent_resistance = max(highs[-20:])
    recent_support = min(lows[-20:])
    
    # Weights from Owner spec Prompt 3:
    # HTF trend +20, EMA structure +15, momentum +15, volume +15, breakout +15, volatility +10, order-flow +10 = 100
    long_score = 0.0
    short_score = 0.0
    reasons = []
    warnings = []
    
    # -- 1. HTF Trend / Alignment (+20) --
    if closes[-1] > ema200_series[-1]:
        long_score += 20
        reasons.append("Price is above the macro EMA 200 (Long-Term Bullish)")
    else:
        short_score += 20
        reasons.append("Price is below the macro EMA 200 (Long-Term Bearish)")
        
    # -- 2. EMA Structure (+15) --
    if ema9_series[-1] > ema21_series[-1] > ema50_series[-1]:
        long_score += 15
        reasons.append("EMAs in bullish stack (9 > 21 > 50)")
    elif ema9_series[-1] < ema21_series[-1] < ema50_series[-1]:
        short_score += 15
        reasons.append("EMAs in bearish stack (9 < 21 < 50)")
        
    # -- 3. Momentum (+15) --
    # RSI & Momentum diff
    if rsi_series[-1] > 55 and mom_series[-1] > 0:
        long_score += 15
        reasons.append("Momentum is positive (RSI > 55 and momentum positive)")
    elif rsi_series[-1] < 45 and mom_series[-1] < 0:
        short_score += 15
        reasons.append("Momentum is negative (RSI < 45 and momentum negative)")
        
    # -- 4. Volume (+15) --
    current_vol = volumes[-1]
    avg_vol = sum(volumes[-20:]) / 20
    if current_vol > avg_vol * 1.3:
        if closes[-1] > closes[-2]:
            long_score += 15
            reasons.append("Volume expanding on up-move")
        else:
            short_score += 15
            reasons.append("Volume expanding on down-move")
            
    # -- 5. Breakout / S-R (+15) --
    if closes[-1] >= recent_resistance * 0.995:
        long_score += 15
        reasons.append("Price breaking out near local resistance")
    elif closes[-1] <= recent_support * 1.005:
        short_score += 15
        reasons.append("Price breaking down near local support")
        
    # -- 6. Volatility (+10) --
    atr_expanding = atr_series[-1] > atr_series[-5]
    if atr_expanding:
        # Expanding volatility favors breakout strategy
        if regime in ("TRENDING_BULL", "TRENDING_BEAR"):
            long_score += 5
            short_score += 5
            reasons.append("Volatility (ATR) is expanding, supporting trends")
            
    # -- 7. Order-Flow / VWAP context (+10) --
    if closes[-1] > vwap_series[-1]:
        long_score += 10
        reasons.append("Price trades above VWAP (aggressive buying control)")
    else:
        short_score += 10
        reasons.append("Price trades below VWAP (aggressive selling control)")

    # Decide strategy and direction based on regime & scores
    direction = "NO_TRADE"
    strategy = "None"
    score = 0.0
    
    # 2. Falling Knife protection for LONGs
    is_knife, knife_warns = evaluate_falling_knife(closes, highs, lows, volumes)
    
    if (regime == "TRENDING_BULL" or regime == "HIGH_VOLATILITY") and long_score > short_score:
        direction = "LONG"
        strategy = "Trend Following"
        score = long_score
    elif (regime == "TRENDING_BEAR" or regime == "HIGH_VOLATILITY") and short_score > long_score:
        direction = "SHORT"
        strategy = "Trend Following"
        score = short_score
    elif regime == "RANGE":
        # Range favors Mean Reversion or Adaptive Grid
        if current_price < middle[-1] and rsi_series[-1] < 40:
            direction = "LONG"
            strategy = "Mean Reversion"
            score = long_score + 10 # bonus for oversold reversion in range
        elif current_price > middle[-1] and rsi_series[-1] > 60:
            direction = "SHORT"
            strategy = "Mean Reversion"
            score = short_score + 10
        else:
            direction = "LONG" if long_score >= short_score else "SHORT"
            strategy = "Adaptive Grid"
            score = max(long_score, short_score)
            
    if direction == "SHORT" and not enable_perps:
        return create_no_trade_signal(symbol, venue, regime, "spot is long-only — SHORT requires perps")
        
    # Apply Falling Knife filter
    if direction == "LONG" and is_knife:
        warnings.extend(knife_warns)
        return create_no_trade_signal(
            symbol=symbol,
            venue=venue,
            regime=regime,
            reason="LONG blocked by falling-knife filter (exploding bearish momentum/volatility)",
            warnings=warnings
        )
        
    # Invalidation check
    if direction == "LONG" and closes[-1] < recent_support:
        return create_no_trade_signal(symbol, venue, regime, "LONG signal invalidated — local support already broken")
    if direction == "SHORT" and closes[-1] > recent_resistance:
        return create_no_trade_signal(symbol, venue, regime, "SHORT signal invalidated — local resistance already broken")

    # Score Bands check: below the tradeable floor is NO TRADE
    if score < AI_MIN_SCORE:
        return create_no_trade_signal(symbol, venue, regime, f"Signal score ({score:.1f}) is too weak (< {AI_MIN_SCORE:.1f} floor)")

    # 3. Dynamic Stop Loss & Take Profit (ATR & Technical S/R Stops)
    atr_val = atr_series[-1]
    stop_dist = max(atr_val * 1.5, current_price * 0.005) # clamp min stop at 0.5%
    
    if direction == "LONG":
        entry_price = current_price
        # Set stop at recent support or ATR distance, whichever is closer to price (more conservative risk)
        stop_price = max(recent_support, entry_price - stop_dist)
        # Set target at recent resistance
        tp_price = max(recent_resistance, entry_price + (stop_dist * 1.5))
    else:
        entry_price = current_price
        # Set stop at recent resistance or ATR distance, whichever is closer
        stop_price = min(recent_resistance, entry_price + stop_dist)
        # Set target at recent support
        tp_price = min(recent_support, entry_price - (stop_dist * 1.5))

    # 4. Reward/Risk Filter
    rr = calculate_rr_ratio(direction, entry_price, stop_price, tp_price)
    if rr < min_rr_ratio:
        return create_no_trade_signal(
            symbol=symbol,
            venue=venue,
            regime=regime,
            reason=f"Rejected: Reward/Risk ratio ({rr:.2f}) is below minimum floor ({min_rr_ratio:.2f})"
        )

    # Confidence band mapping
    if score >= 90.0:
        confidence = "VERY_HIGH"
    elif score >= 75.0:
        confidence = "HIGH"
    elif score >= 60.0:
        confidence = "MEDIUM"
    else:
        confidence = "LOW"

    return Signal(
        symbol=symbol,
        venue=venue,
        direction=direction,
        signal_score=score,
        confidence=confidence,
        regime=regime,
        strategy=strategy,
        entry=entry_price,
        stop=stop_price,
        take_profit=tp_price,
        trailing_stop=atr_val * 1.5,
        risk_pct=1.0,  # defaults to 1% risk
        position_size=0.0,  # size and leverage are computed strictly by risk engine
        recommended_leverage=1.0,
        reward_risk=rr,
        reasons=reasons,
        warnings=warnings,
        timestamp=time.time()
    )
