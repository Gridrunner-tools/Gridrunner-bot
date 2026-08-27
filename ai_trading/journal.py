import time
import math
from typing import List, Dict, Any, Tuple

class TradeJournal:
    def __init__(self):
        self.trades: List[Dict[str, Any]] = []

    def record_trade(self, trade_data: Dict[str, Any]):
        """
        trade_data keys:
            symbol, venue, strategy, regime, signal_score, confidence,
            entry, stop, target, leverage, position_size, exit_price,
            pnl_usd, fees_usd, slippage_usd, mae_usd, mfe_usd,
            duration_sec, exit_reason
        """
        trade_data["timestamp"] = time.time()
        self.trades.append(trade_data)

    def calculate_performance_metrics(self, filtered_trades: List[Dict[str, Any]] = None) -> Dict[str, Any]:
        trades_to_analyze = filtered_trades if filtered_trades is not None else self.trades
        n = len(trades_to_analyze)
        
        default_metrics = {
            "total_trades": n,
            "win_rate": 0.0,
            "avg_win": 0.0,
            "avg_loss": 0.0,
            "expectancy": 0.0,
            "profit_factor": 0.0,
            "max_drawdown": 0.0,
            "consecutive_losses": 0,
            "sharpe_ratio": 0.0,
            "sortino_ratio": 0.0
        }
        
        if n == 0:
            return default_metrics

        wins = [t for p in trades_to_analyze if (t := p.get("pnl_usd", 0.0)) > 0]
        losses = [abs(t) for p in trades_to_analyze if (t := p.get("pnl_usd", 0.0)) < 0]
        
        total_pnl = sum(p.get("pnl_usd", 0.0) for p in trades_to_analyze)
        
        win_rate = len(wins) / n if n > 0 else 0.0
        avg_win = sum(wins) / len(wins) if wins else 0.0
        avg_loss = sum(losses) / len(losses) if losses else 0.0
        
        # Expectancy = (Win% * AvgWin) - (Loss% * AvgLoss)
        expectancy = (win_rate * avg_win) - ((1 - win_rate) * avg_loss)
        
        # Profit Factor = Sum(Wins) / Sum(Losses)
        sum_wins = sum(wins)
        sum_losses = sum(losses)
        profit_factor = sum_wins / sum_losses if sum_losses > 0 else (float('inf') if sum_wins > 0 else 1.0)
        
        # Calculate consecutive losses
        consec_losses = 0
        max_consec_losses = 0
        for p in trades_to_analyze:
            if p.get("pnl_usd", 0.0) < 0:
                consec_losses += 1
                max_consec_losses = max(max_consec_losses, consec_losses)
            else:
                consec_losses = 0
                
        # Simple Sharpe / Sortino approximation
        pnls = [p.get("pnl_usd", 0.0) for p in trades_to_analyze]
        mean_pnl = sum(pnls) / n
        variance = sum((x - mean_pnl) ** 2 for x in pnls) / n
        std_dev = math.sqrt(variance) if variance > 0 else 0.0
        
        sharpe = mean_pnl / std_dev if std_dev > 0 else 0.0
        
        downside_pnls = [x for x in pnls if x < 0]
        downside_variance = sum(x ** 2 for x in downside_pnls) / n if downside_pnls else 0.0
        downside_std_dev = math.sqrt(downside_variance) if downside_variance > 0 else 0.0
        
        sortino = mean_pnl / downside_std_dev if downside_std_dev > 0 else 0.0
        
        return {
            "total_trades": n,
            "win_rate": round(win_rate, 4),
            "avg_win": round(avg_win, 2),
            "avg_loss": round(avg_loss, 2),
            "expectancy": round(expectancy, 2),
            "profit_factor": round(profit_factor, 2) if profit_factor != float('inf') else 999.0,
            "consecutive_losses": max_consec_losses,
            "sharpe_ratio": round(sharpe, 2),
            "sortino_ratio": round(sortino, 2)
        }

    def get_strategy_health_rating(self, strategy_name: str) -> str:
        """
        Evaluate and return strategy health ratings (EXCELLENT, GOOD, DEGRADED).
        """
        strat_trades = [t for t in self.trades if t.get("strategy") == strategy_name]
        if len(strat_trades) < 5:
            return "GOOD"  # default rating
            
        metrics = self.calculate_performance_metrics(strat_trades)
        pf = metrics["profit_factor"]
        wr = metrics["win_rate"]
        
        if pf >= 1.5 and wr >= 0.55:
            return "EXCELLENT"
        elif pf < 1.0 or wr < 0.40:
            return "DEGRADED"
        return "GOOD"

    def get_summary_report(self) -> Dict[str, Any]:
        """Produce segment summaries (regime, asset, strategy)."""
        report = {
            "overall": self.calculate_performance_metrics(),
            "by_strategy": {},
            "by_regime": {},
            "by_symbol": {}
        }
        
        # Groupings
        strats = set(t.get("strategy", "Unknown") for t in self.trades)
        for s in strats:
            filtered = [t for t in self.trades if t.get("strategy") == s]
            report["by_strategy"][s] = {
                "metrics": self.calculate_performance_metrics(filtered),
                "health": self.get_strategy_health_rating(s)
            }
            
        regimes = set(t.get("regime", "Unknown") for t in self.trades)
        for r in regimes:
            filtered = [t for t in self.trades if t.get("regime") == r]
            report["by_regime"][r] = self.calculate_performance_metrics(filtered)
            
        symbols = set(t.get("symbol", "Unknown") for t in self.trades)
        for sym in symbols:
            filtered = [t for t in self.trades if t.get("symbol") == sym]
            report["by_symbol"][sym] = self.calculate_performance_metrics(filtered)
            
        return report
