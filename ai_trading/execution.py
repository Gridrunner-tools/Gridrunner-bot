import time
import threading
from typing import List, Dict, Any, Tuple
from ai_trading.signal import Signal
from ai_trading.regime import detect_market_regime
from ai_trading.strategies import generate_signals_and_score
from ai_trading.risk import RiskEngine
from ai_trading.journal import TradeJournal

class AITradingEngine:
    def __init__(self, risk_config: Dict[str, Any], whitelisted_symbols: List[str]):
        """
        Coordinates signal generation, risk engine gating, and execution adapter triggers.
        """
        self.risk_engine = RiskEngine(risk_config)
        self.whitelisted_symbols = whitelisted_symbols
        self.running = False
        self.thread: Optional[threading.Thread] = None
        self.status = "analyzing"  # analyzing, waiting, preparing order, in position, managing, blocked, stopped
        self.explain_msg = "Engine initialized, waiting to analyze whitelisted assets."
        
        # In-memory database of active/historical trades
        self.positions: Dict[str, Dict[str, Any]] = {}  # symbol -> position data
        self.execution_logs: List[str] = []

    def log_event(self, msg: str):
        timestamp = time.strftime("%H:%M:%S")
        log_line = f"[{timestamp}] {msg}"
        self.execution_logs.append(log_line)
        print(f"[AITradingEngine] {log_line}")

    def update_configs(self, new_risk_config: Dict[str, Any], whitelisted_symbols: List[str]):
        """Dynamically update configs from the UI controls."""
        self.risk_engine.config.update(new_risk_config)
        self.whitelisted_symbols = whitelisted_symbols

    def scan_cycle(self, market_data_provider: Any, execution_adapter: Any):
        """
        One complete scan cycle of all whitelisted assets.
        """
        # 1. Circuit breaker health checks
        breaker_fired, breaker_reason = self.risk_engine.check_circuit_breakers()
        if breaker_fired:
            self.status = "stopped"
            self.explain_msg = f"Circuit breaker triggered: {breaker_reason}"
            self.log_event(self.explain_msg)
            return

        self.status = "analyzing"
        self.explain_msg = "Scanning whitelisted assets for high-confidence trading setups."
        
        # Reconcile positions before trading
        # actual_positions = execution_adapter.get_venue_positions()
        # self.risk_engine.reconcile_positions(actual_positions)

        for symbol in self.whitelisted_symbols:
            # Skip if we already have an active position on this asset
            if symbol in self.positions:
                self.manage_existing_position(symbol, market_data_provider, execution_adapter)
                continue

            # 2. Fetch market candles
            bars = market_data_provider.get_candles(symbol)
            if not bars or len(bars.get("closes", [])) < 50:
                self.log_event(f"Insufficient history or data stale for {symbol}, skipping.")
                continue

            # 3. Detect market regime
            highs = bars["highs"]
            lows = bars["lows"]
            closes = bars["closes"]
            volumes = bars["volumes"]
            
            regime_info = detect_market_regime(highs, lows, closes, volumes)
            
            # 4. Score signal
            signal = generate_signals_and_score(symbol, "Solana", highs, lows, closes, volumes, regime_info)
            
            if signal.is_tradeable():
                self.log_event(f"High score signal detected: {symbol} {signal.direction} score={signal.signal_score:.1f}")
                
                # 5. Risk Sizing Gate
                self.status = "preparing order"
                self.explain_msg = f"Sizing and validating setup for {symbol} through non-bypassable Risk Engine."
                sized_signal = self.risk_engine.evaluate_and_size_signal(signal)
                
                if sized_signal.is_tradeable() and sized_signal.position_size > 0:
                    # 6. Execute Order via Spot/Perp adapter
                    self.log_event(f"Risk Approved! Executing {sized_signal.direction} position on {symbol} with size={sized_signal.position_size}")
                    success = execution_adapter.execute_swap(
                        symbol=symbol,
                        direction=sized_signal.direction,
                        size=sized_signal.position_size,
                        price=sized_signal.entry
                    )
                    
                    if success:
                        self.positions[symbol] = {
                            "symbol": symbol,
                            "direction": sized_signal.direction,
                            "entry": sized_signal.entry,
                            "stop": sized_signal.stop,
                            "take_profit": sized_signal.take_profit,
                            "size": sized_signal.position_size,
                            "leverage": sized_signal.recommended_leverage,
                            "strategy": sized_signal.strategy,
                            "regime": sized_signal.regime,
                            "timestamp": time.time()
                        }
                        self.risk_engine.record_trade_opened(sized_signal)
                        self.status = "in position"
                        self.explain_msg = f"Successfully entered {sized_signal.direction} position on {symbol}."
                    else:
                        self.log_event(f"Execution adapter failed to fill {symbol} swap.")
                else:
                    self.status = "blocked"
                    self.explain_msg = f"Trade setup on {symbol} blocked by Risk Engine: {sized_signal.reasons[-1]}"
                    self.log_event(self.explain_msg)

    def manage_existing_position(self, symbol: str, market_data_provider: Any, execution_adapter: Any):
        """Manage stop losses, take profits, and exit parameters of open trades."""
        pos = self.positions[symbol]
        curr_price = market_data_provider.get_current_price(symbol)
        
        if not curr_price:
            self.log_event(f"Cannot fetch current price for {symbol} to manage position, skipped.")
            return

        direction = pos["direction"]
        entry = pos["entry"]
        stop = pos["stop"]
        tp = pos["take_profit"]
        size = pos["size"]
        
        is_exit = False
        pnl = 0.0
        exit_reason = ""
        
        if direction == "LONG":
            if curr_price >= tp:
                is_exit = True
                pnl = size * (tp - entry)
                exit_reason = "Take Profit Hit"
            elif curr_price <= stop:
                is_exit = True
                pnl = size * (stop - entry)
                exit_reason = "Stop Loss Hit"
        else:  # SHORT
            if curr_price <= tp:
                is_exit = True
                pnl = size * (entry - tp)
                exit_reason = "Take Profit Hit"
            elif curr_price >= stop:
                is_exit = True
                pnl = size * (entry - stop)
                exit_reason = "Stop Loss Hit"

        if is_exit:
            self.log_event(f"Position exit trigger for {symbol}: {exit_reason}. Executing sell/cover swap.")
            opp_direction = "SHORT" if direction == "LONG" else "LONG"
            
            success = execution_adapter.execute_swap(
                symbol=symbol,
                direction=opp_direction,
                size=size,
                price=curr_price
            )
            
            if success:
                self.risk_engine.record_trade_closed(symbol, pnl)
                if hasattr(execution_adapter, "record_trade_closed"):
                    execution_adapter.record_trade_closed(symbol, pnl)
                del self.positions[symbol]
                self.log_event(f"Successfully exited position on {symbol} with P&L: ${pnl:.2f} ({exit_reason})")
            else:
                self.log_event(f"Failed to execute exit trade for {symbol}!")

    def start(self, market_data_provider: Any, execution_adapter: Any, interval_sec: float = 10.0):
        """Start the background execution thread loop."""
        if self.running:
            return
        self.running = True
        
        def run_loop():
            while self.running:
                try:
                    self.scan_cycle(market_data_provider, execution_adapter)
                except Exception as e:
                    self.log_event(f"Unhandled exception in scanning loop: {e}")
                time.sleep(interval_sec)
                
        self.thread = threading.Thread(target=run_loop, daemon=True)
        self.thread.start()
        self.log_event("AI Trading background engine started successfully.")

    def stop(self):
        """Stop background execution loop."""
        self.running = False
        if self.thread:
            self.thread.join(timeout=2.0)
        self.log_event("AI Trading background engine stopped.")
