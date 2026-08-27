import time
from typing import List, Dict, Any, Tuple, Optional
from ai_trading.signal import Signal, create_no_trade_signal

class RiskEngine:
    def __init__(self, config: Dict[str, Any]):
        """
        config contains:
            "account_equity": float,
            "risk_per_trade_pct": float (e.g. 1.0 for 1%),
            "max_leverage": float (e.g. 3.0),
            "max_total_exposure": float (e.g. 5000.0),
            "max_per_asset_exposure": float (e.g. 2000.0),
            "max_simultaneous_positions": int (e.g. 3),
            "daily_loss_limit": float (e.g. 100.0),
            "max_drawdown_limit_pct": float (e.g. 10.0),
            "circuit_breaker_active": bool,
            "current_drawdown_pct": float,
            "daily_loss_accrued": float
        """
        self.config = config
        self.active_positions: Dict[str, Dict[str, Any]] = {}  # symbol -> position info
        self.circuit_breaker_triggered = config.get("circuit_breaker_active", False)
        self.daily_loss_accrued = config.get("daily_loss_accrued", 0.0)
        self.daily_loss_limit = config.get("daily_loss_limit", 100.0)
        self.current_drawdown_pct = config.get("current_drawdown_pct", 0.0)
        self.max_drawdown_limit_pct = config.get("max_drawdown_limit_pct", 10.0)
        
        # Track simulated daily loss reset time (e.g., 24h rolling or midnight)
        self.last_loss_reset = time.time()
        self.realized_pnl = 0.0
        from ai_trading.journal import TradeJournal
        self.journal = TradeJournal()

    def check_circuit_breakers(self, market_data: Dict[str, Any] = None) -> Tuple[bool, str]:
        """Verify circuit breaker thresholds and conditions."""
        if self.circuit_breaker_triggered:
            return True, "Circuit breaker manually activated"
            
        # 1. Daily Loss Limit Breaker
        if self.daily_loss_accrued >= self.daily_loss_limit:
            self.circuit_breaker_triggered = True
            return True, f"Circuit breaker: Daily loss limit (${self.daily_loss_accrued:.2f} >= ${self.daily_loss_limit:.2f}) exceeded"
            
        # 2. Drawdown Protection
        if self.current_drawdown_pct >= self.max_drawdown_limit_pct:
            self.circuit_breaker_triggered = True
            return True, f"Circuit breaker: Max drawdown limit ({self.current_drawdown_pct:.1f}% >= {self.max_drawdown_limit_pct:.1f}%) exceeded"
            
        # 3. Market Volatility Check
        if market_data and market_data.get("relative_volatility", 1.0) > 3.0:
            return True, "Circuit breaker: Excessive market volatility detected (> 3.0x standard)"
            
        return False, ""

    def reset_daily_loss(self):
        """Reset daily accrued loss."""
        self.daily_loss_accrued = 0.0
        self.circuit_breaker_triggered = False

    def get_correlation_group(self, symbol: str) -> str:
        """Categorize core assets into correlation buckets."""
        symbol_base = symbol.split("/")[0].upper()
        if symbol_base in ("BTC", "ETH", "SOL", "BNB", "XRP"):
            return "MAJOR_CRYPTO"
        return "ALTCOINS"

    def check_portfolio_exposure(self, signal: Signal) -> Tuple[bool, str]:
        """
        Validate portfolio-level exposure, direction, correlation, and position limits.
        """
        symbol = signal.symbol
        direction = signal.direction
        
        # 1. Max Simultaneous Positions Limit
        if len(self.active_positions) >= self.config.get("max_simultaneous_positions", 3):
            if symbol not in self.active_positions:
                return False, f"Max simultaneous positions cap ({self.config['max_simultaneous_positions']}) reached"
                
        # 2. Directional concentration risk
        same_direction_count = sum(1 for p in self.active_positions.values() if p["direction"] == direction)
        if same_direction_count >= 2:
            return False, f"Directional concentration risk: already have {same_direction_count} {direction} positions"
            
        # 3. Symbol specific limits
        if symbol in self.active_positions and self.active_positions[symbol]["direction"] != direction:
            return False, f"Contradictory trade: already in a {self.active_positions[symbol]['direction']} position on {symbol}"
            
        return True, ""

    def determine_adaptive_leverage(self, signal: Signal) -> float:
        """
        Calculate safe adaptive leverage based on regime, volatility, confidence, and drawdown.
        """
        regime = signal.regime
        confidence = signal.confidence
        
        # Start with standard defaults
        # Strong trend+low vol: 3-5x; normal: 2-3x; high vol: 1-2x; extreme: NO_TRADE
        if regime in ("TRENDING_BULL", "TRENDING_BEAR"):
            base_lev = 3.0
            if confidence == "VERY_HIGH":
                base_lev = 4.0
        elif regime == "RANGE":
            base_lev = 2.0
        else:
            base_lev = 1.0  # HIGH_VOLATILITY / TRANSITION
            
        # Reduce based on current drawdown
        if self.current_drawdown_pct > self.max_drawdown_limit_pct * 0.5:
            base_lev *= 0.5  # half leverage in drawdown
            
        # Cap by configured max leverage
        max_allowed = self.config.get("max_leverage", 3.0)
        return max(1.0, min(base_lev, max_allowed))

    def evaluate_and_size_signal(self, signal: Signal) -> Signal:
        """
        The critical Risk Gate. Sizes and decides if trade can proceed.
        Returns the sized Signal or a NO_TRADE signal if risk limits are breached.
        """
        if not signal.is_tradeable():
            return signal

        # 1. Circuit breaker checks
        breaker_fired, breaker_reason = self.check_circuit_breakers()
        if breaker_fired:
            return create_no_trade_signal(signal.symbol, signal.venue, signal.regime, f"Gated by Circuit Breaker: {breaker_reason}")

        # 2. Portfolio Exposure Checks
        portfolio_ok, portfolio_reason = self.check_portfolio_exposure(signal)
        if not portfolio_ok:
            return create_no_trade_signal(signal.symbol, signal.venue, signal.regime, f"Gated by Portfolio Risk Engine: {portfolio_reason}")

        # 3. Position Sizing Sourced strictly from Risk per Trade (not leverage multiplier)
        # Sizing formula: Position Loss = Account Equity * Risk %
        # Position Size = Position Loss / Stop Distance
        account_equity = self.config.get("account_equity", 1000.0)
        auto_compound = self.config.get("auto_compound", True)
        if auto_compound:
            account_equity += self.realized_pnl
            
        risk_pct = self.config.get("risk_per_trade_pct", 1.0)
        
        risk_dollar = account_equity * (risk_pct / 100.0)
        stop_distance = abs(signal.entry - signal.stop)
        
        if stop_distance == 0:
            return create_no_trade_signal(signal.symbol, signal.venue, signal.regime, "Invalid stop loss distance (zero)")
            
        # Determine target position size in base tokens
        raw_size = risk_dollar / stop_distance
        position_value_usd = raw_size * signal.entry
        
        # Determine Adaptive Leverage
        leverage = self.determine_adaptive_leverage(signal)
        
        # Cap exposure values
        max_asset_usd = self.config.get("max_per_asset_exposure", 2000.0)
        if position_value_usd > max_asset_usd:
            position_value_usd = max_asset_usd
            raw_size = position_value_usd / signal.entry
            
        # Hard limits on portfolio-wide exposure
        current_total_exposure = sum(p["exposure_usd"] for p in self.active_positions.values())
        max_total_exposure = self.config.get("max_total_exposure", 5000.0)
        if current_total_exposure + position_value_usd > max_total_exposure:
            remaining_allocation = max_total_exposure - current_total_exposure
            if remaining_allocation < 10.0:  # less than $10 remaining
                return create_no_trade_signal(signal.symbol, signal.venue, signal.regime, "Portfolio total exposure limit exceeded")
            position_value_usd = remaining_allocation
            raw_size = position_value_usd / signal.entry

        signal.position_size = round(raw_size, 6)
        signal.recommended_leverage = round(leverage, 1)
        
        return signal

    def record_trade_opened(self, signal: Signal):
        """Log trade opening inside the active position manager."""
        self.active_positions[signal.symbol] = {
            "symbol": signal.symbol,
            "direction": signal.direction,
            "entry": signal.entry,
            "stop": signal.stop,
            "take_profit": signal.take_profit,
            "size": signal.position_size,
            "exposure_usd": signal.position_size * signal.entry,
            "leverage": signal.recommended_leverage,
            "timestamp": time.time()
        }

    def record_trade_closed(self, symbol: str, profit_loss_usd: float):
        """Log trade closure and accrue performance tracking metrics."""
        if symbol in self.active_positions:
            del self.active_positions[symbol]
        self.realized_pnl += profit_loss_usd
        if self.journal:
            self.journal.record_trade({
                "symbol": symbol,
                "pnl_usd": profit_loss_usd,
                "timestamp": time.time()
            })
        if profit_loss_usd < 0:
            self.daily_loss_accrued += abs(profit_loss_usd)

    def reconcile_positions(self, actual_positions: Dict[str, Dict[str, Any]]):
        """Reconcile internal position tracking against actual exchange/RPC states."""
        for symbol, actual in actual_positions.items():
            if symbol not in self.active_positions:
                log(f"Reconciliation Mismatch: unexpected active position found for {symbol} on venue", "WARN")
                # Trigger circuit breaker on unreconciled position mismatch
                self.circuit_breaker_triggered = True
                
        for symbol in list(self.active_positions.keys()):
            if symbol not in actual_positions:
                log(f"Reconciliation Mismatch: position for {symbol} missing on venue", "WARN")
                self.circuit_breaker_triggered = True

def log(msg: str, level: str = "INFO"):
    print(f"[{level}] [RiskEngine] {msg}")
