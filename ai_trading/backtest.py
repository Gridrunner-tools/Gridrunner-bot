from typing import List, Dict, Any
from ai_trading.regime import detect_market_regime
from ai_trading.strategies import generate_signals_and_score
from ai_trading.risk import RiskEngine
from ai_trading.journal import TradeJournal

class BacktestEngine:
    def __init__(self, initial_equity: float = 1000.0, risk_pct: float = 1.0, max_leverage: float = 3.0):
        self.initial_equity = initial_equity
        self.risk_pct = risk_pct
        self.max_leverage = max_leverage
        self.journal = TradeJournal()

    def run_backtest(self, symbol: str, highs: List[float], lows: List[float], closes: List[float], volumes: List[float]) -> Dict[str, Any]:
        """
        Run backtest over historical bar series.
        Walk-forward simulation with NO future information/lookahead.
        """
        n = len(closes)
        if n < 50:
            return {"error": "Inadequate data series length for backtesting (< 50 bars)"}
            
        equity = self.initial_equity
        risk_config = {
            "account_equity": equity,
            "risk_per_trade_pct": self.risk_pct,
            "max_leverage": self.max_leverage,
            "max_total_exposure": equity * 5.0,
            "max_per_asset_exposure": equity * 2.0,
            "max_simultaneous_positions": 3,
            "daily_loss_limit": equity * 0.1,
            "max_drawdown_limit_pct": 20.0,
            "circuit_breaker_active": False,
            "current_drawdown_pct": 0.0,
            "daily_loss_accrued": 0.0
        }
        risk_engine = RiskEngine(risk_config)
        
        # Simulating window traversal (walk-forward)
        # We start with 40 bars of history and roll forward bar-by-bar
        active_position = None  # we support one active position at a time per symbol for simple backtesting
        
        for i in range(40, n):
            h_slice = highs[:i]
            l_slice = lows[:i]
            c_slice = closes[:i]
            v_slice = volumes[:i]
            
            current_price = c_slice[-1]
            
            # 1. Manage Active Position Exit
            if active_position:
                direction = active_position["direction"]
                entry = active_position["entry"]
                stop = active_position["stop"]
                tp = active_position["take_profit"]
                size = active_position["size"]
                
                # Check exit conditions on the current bar
                is_exit = False
                pnl = 0.0
                exit_reason = ""
                
                if direction == "LONG":
                    if h_slice[-1] >= tp:
                        is_exit = True
                        pnl = size * (tp - entry)
                        exit_reason = "Take Profit Hit"
                    elif l_slice[-1] <= stop:
                        is_exit = True
                        pnl = size * (stop - entry)
                        exit_reason = "Stop Loss Hit"
                else:  # SHORT
                    if l_slice[-1] <= tp:
                        is_exit = True
                        pnl = size * (entry - tp)
                        exit_reason = "Take Profit Hit"
                    elif h_slice[-1] >= stop:
                        is_exit = True
                        pnl = size * (entry - stop)
                        exit_reason = "Stop Loss Hit"
                        
                if is_exit:
                    # Apply fee/slippage simulation (0.1% fee + 0.1% slippage)
                    fee_usd = size * entry * 0.001
                    slippage_usd = size * entry * 0.001
                    final_pnl = pnl - fee_usd - slippage_usd
                    
                    equity += final_pnl
                    risk_engine.config["account_equity"] = equity
                    risk_engine.record_trade_closed(symbol, final_pnl)
                    
                    # Record into journal
                    self.journal.record_trade({
                        "symbol": symbol,
                        "venue": "Solana",
                        "strategy": active_position["strategy"],
                        "regime": active_position["regime"],
                        "signal_score": active_position["score"],
                        "confidence": active_position["confidence"],
                        "entry": entry,
                        "stop": stop,
                        "target": tp,
                        "leverage": active_position["leverage"],
                        "position_size": size,
                        "exit_price": tp if "Take Profit" in exit_reason else stop,
                        "pnl_usd": final_pnl,
                        "fees_usd": fee_usd,
                        "slippage_usd": slippage_usd,
                        "duration_sec": 300.0,  # placeholder
                        "exit_reason": exit_reason
                    })
                    
                    active_position = None
                    continue
                    
            # 2. Search for New Signals if flat
            if not active_position:
                regime_info = detect_market_regime(h_slice, l_slice, c_slice, v_slice)
                signal = generate_signals_and_score(symbol, "Solana", h_slice, l_slice, c_slice, v_slice, regime_info)
                
                if signal.is_tradeable():
                    # Size trade via Risk Engine
                    sized_signal = risk_engine.evaluate_and_size_signal(signal)
                    if sized_signal.is_tradeable() and sized_signal.position_size > 0:
                        active_position = {
                            "direction": sized_signal.direction,
                            "strategy": sized_signal.strategy,
                            "regime": sized_signal.regime,
                            "score": sized_signal.signal_score,
                            "confidence": sized_signal.confidence,
                            "entry": sized_signal.entry,
                            "stop": sized_signal.stop,
                            "take_profit": sized_signal.take_profit,
                            "size": sized_signal.position_size,
                            "leverage": sized_signal.recommended_leverage
                        }
                        risk_engine.record_trade_opened(sized_signal)
                        
        stats = self.journal.calculate_performance_metrics()
        stats["initial_equity"] = self.initial_equity
        stats["final_equity"] = round(equity, 2)
        stats["total_pnl_usd"] = round(equity - self.initial_equity, 2)
        
        return stats
