import math
from typing import List, Tuple, Dict, Any

def ema(prices: List[float], period: int) -> List[float]:
    """Pure Python Exponential Moving Average (EMA)."""
    if not prices:
        return []
    if len(prices) < period:
        # Fallback to simple average or running average
        period = max(1, len(prices))
    
    alpha = 2 / (period + 1)
    ema_values = []
    
    # Simple average for the initial value
    current_ema = sum(prices[:period]) / period
    for _ in range(period):
        ema_values.append(current_ema)
        
    for p in prices[period:]:
        current_ema = (p * alpha) + (current_ema * (1 - alpha))
        ema_values.append(current_ema)
        
    return ema_values

def rsi(prices: List[float], period: int = 14) -> List[float]:
    """Pure Python Relative Strength Index (RSI)."""
    if not prices or len(prices) < 2:
        return [50.0] * len(prices)
    
    rsi_values = [50.0]  # First element has no change
    gains = []
    losses = []
    
    for i in range(1, len(prices)):
        diff = prices[i] - prices[i-1]
        if diff > 0:
            gains.append(diff)
            losses.append(0.0)
        else:
            gains.append(0.0)
            losses.append(abs(diff))
            
    if len(gains) < period:
        return [50.0] * len(prices)
        
    # Initial averages
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    
    if avg_loss == 0:
        rs = 100.0 if avg_gain > 0 else 0.0
    else:
        rs = avg_gain / avg_loss
    rsi_values.extend([100.0 - (100.0 / (1.0 + rs))] * period)
    
    # Wilder's smoothing
    for idx in range(period, len(gains)):
        g = gains[idx]
        l = losses[idx]
        avg_gain = (avg_gain * (period - 1) + g) / period
        avg_loss = (avg_loss * (period - 1) + l) / period
        
        if avg_loss == 0:
            current_rsi = 100.0 if avg_gain > 0 else 50.0
        else:
            rs = avg_gain / avg_loss
            current_rsi = 100.0 - (100.0 / (1.0 + rs))
        rsi_values.append(current_rsi)
        
    # Pad or slice to match input list length
    while len(rsi_values) < len(prices):
        rsi_values.append(50.0)
    return rsi_values[:len(prices)]

def macd(prices: List[float], fast: int = 12, slow: int = 26, signal_period: int = 9) -> Tuple[List[float], List[float], List[float]]:
    """Pure Python Moving Average Convergence Divergence (MACD)."""
    if len(prices) < slow:
        zero_list = [0.0] * len(prices)
        return zero_list, zero_list, zero_list
        
    fast_ema = ema(prices, fast)
    slow_ema = ema(prices, slow)
    
    macd_line = [f - s for f, s in zip(fast_ema, slow_ema)]
    signal_line = ema(macd_line, signal_period)
    histogram = [m - s for m, s in zip(macd_line, signal_line)]
    
    return macd_line, signal_line, histogram

def atr(highs: List[float], lows: List[float], closes: List[float], period: int = 14) -> List[float]:
    """Pure Python Average True Range (ATR)."""
    n = len(closes)
    if n < 2 or len(highs) != n or len(lows) != n:
        return [0.1] * n
        
    tr_values = [highs[0] - lows[0]]
    for i in range(1, n):
        h = highs[i]
        l = lows[i]
        prev_c = closes[i-1]
        tr = max(
            h - l,
            abs(h - prev_c),
            abs(l - prev_c)
        )
        tr_values.append(tr)
        
    if len(tr_values) < period:
        return [sum(tr_values) / len(tr_values)] * n
        
    atr_values = [sum(tr_values[:period]) / period] * period
    current_atr = atr_values[-1]
    
    # Wilder's smoothing
    for idx in range(period, n):
        current_atr = (current_atr * (period - 1) + tr_values[idx]) / period
        atr_values.append(current_atr)
        
    return atr_values

def bollinger_bands(prices: List[float], period: int = 20, num_std: float = 2.0) -> Tuple[List[float], List[float], List[float]]:
    """Pure Python Bollinger Bands."""
    n = len(prices)
    if n < period:
        # Fallback to mean and narrow bands
        mean = sum(prices) / max(1, n)
        return [mean] * n, [mean] * n, [mean] * n
        
    upper = []
    middle = []
    lower = []
    
    for i in range(n):
        if i < period - 1:
            window = prices[:i+1]
        else:
            window = prices[i - period + 1 : i + 1]
            
        mean = sum(window) / len(window)
        variance = sum((x - mean) ** 2 for x in window) / len(window)
        std_dev = math.sqrt(variance)
        
        middle.append(mean)
        upper.append(mean + (num_std * std_dev))
        lower.append(mean - (num_std * std_dev))
        
    return upper, middle, lower

def vwap(prices: List[float], volumes: List[float]) -> List[float]:
    """Pure Python Volume Weighted Average Price (VWAP)."""
    n = len(prices)
    if n != len(volumes) or n == 0:
        return prices.copy()
        
    vwap_values = []
    cumulative_pv = 0.0
    cumulative_v = 0.0
    
    for p, v in zip(prices, volumes):
        cumulative_pv += p * v
        cumulative_v += v
        if cumulative_v == 0:
            vwap_values.append(p)
        else:
            vwap_values.append(cumulative_pv / cumulative_v)
            
    return vwap_values

def adx(highs: List[float], lows: List[float], closes: List[float], period: int = 14) -> List[float]:
    """Pure Python Average Directional Index (ADX)."""
    n = len(closes)
    if n < period * 2 or len(highs) != n or len(lows) != n:
        return [20.0] * n
        
    plus_dm = [0.0]
    minus_dm = [0.0]
    
    for i in range(1, n):
        up_move = highs[i] - highs[i-1]
        down_move = lows[i-1] - lows[i]
        
        if up_move > down_move and up_move > 0:
            plus_dm.append(up_move)
        else:
            plus_dm.append(0.0)
            
        if down_move > up_move and down_move > 0:
            minus_dm.append(down_move)
        else:
            minus_dm.append(0.0)
            
    tr_values = [highs[0] - lows[0]]
    for i in range(1, n):
        h = highs[i]
        l = lows[i]
        prev_c = closes[i-1]
        tr = max(h - l, abs(h - prev_c), abs(l - prev_c))
        tr_values.append(tr)
        
    # Smooth DM & TR
    smoothed_tr = [sum(tr_values[:period])]
    smoothed_plus_dm = [sum(plus_dm[:period])]
    smoothed_minus_dm = [sum(minus_dm[:period])]
    
    for i in range(period, n):
        smoothed_tr.append(smoothed_tr[-1] - (smoothed_tr[-1] / period) + tr_values[i])
        smoothed_plus_dm.append(smoothed_plus_dm[-1] - (smoothed_plus_dm[-1] / period) + plus_dm[i])
        smoothed_minus_dm.append(smoothed_minus_dm[-1] - (smoothed_minus_dm[-1] / period) + minus_dm[i])
        
    plus_di = []
    minus_di = []
    dx = []
    
    for i in range(len(smoothed_tr)):
        tr_val = smoothed_tr[i]
        if tr_val == 0:
            p_di = 0.0
            m_di = 0.0
        else:
            p_di = 100 * (smoothed_plus_dm[i] / tr_val)
            m_di = 100 * (smoothed_minus_dm[i] / tr_val)
            
        plus_di.append(p_di)
        minus_di.append(m_di)
        
        di_diff = abs(p_di - m_di)
        di_sum = p_di + m_di
        if di_sum == 0:
            dx.append(0.0)
        else:
            dx.append(100 * (di_diff / di_sum))
            
    # ADX smoothing
    if len(dx) < period:
        return [20.0] * n
        
    adx_values = [sum(dx[:period]) / period]
    for i in range(period, len(dx)):
        adx_values.append(((adx_values[-1] * (period - 1)) + dx[i]) / period)
        
    # Re-align lengths to match n
    final_adx = [20.0] * (n - len(adx_values)) + adx_values
    return final_adx

def swing_highs_lows(highs: List[float], lows: List[float], window: int = 5) -> Tuple[List[bool], List[bool]]:
    """Determine swing high and swing low points over a rolling window."""
    n = len(highs)
    sw_high = [False] * n
    sw_low = [False] * n
    
    if n < window * 2 + 1:
        return sw_high, sw_low
        
    for i in range(window, n - window):
        chunk_h = highs[i - window : i + window + 1]
        chunk_l = lows[i - window : i + window + 1]
        
        if highs[i] == max(chunk_h):
            sw_high[i] = True
        if lows[i] == min(chunk_l):
            sw_low[i] = True
            
    return sw_high, sw_low

def momentum(prices: List[float], period: int = 10) -> List[float]:
    """Calculate simple momentum difference."""
    n = len(prices)
    mom = [0.0] * n
    for i in range(period, n):
        mom[i] = prices[i] - prices[i - period]
    return mom
