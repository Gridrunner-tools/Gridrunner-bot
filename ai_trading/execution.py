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
            enable_perps = self.risk_engine.config.get("enable_perps", False)
            signal = generate_signals_and_score(symbol, "Solana", highs, lows, closes, volumes, regime_info, enable_perps=enable_perps)
            
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
                            "exposure_usd": sized_signal.position_size * sized_signal.entry,
                            "score": sized_signal.signal_score,
                            "leverage": sized_signal.recommended_leverage,
                            "trailing_stop": float(getattr(sized_signal, "trailing_stop", 0.0) or 0.0),
                            "trailing_stop_level": sized_signal.entry,
                            "avg_entry": sized_signal.entry,
                            "first_lot_size": sized_signal.position_size,
                            "lots": 1,
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
        # Blended average entry: equals the first entry until averaging-down
        # adds occur; all exit P&L / floors are measured against it.
        entry_basis = float(pos.get("avg_entry", entry))
        lots = int(pos.get("lots", 1))
        
        is_exit = False
        pnl = 0.0
        exit_reason = ""
        
        # Owner-gated exits (both default OFF):
        #  - stop_loss_enabled: OFF = never sell below entry (hold through
        #    dips; only profit-taking exits + dip-buys). ON restores the
        #    fixed stop-loss exit.
        #  - trailing_stop_enabled: OFF = fixed take-profit at pos["take_profit"].
        #    ON = trailing stop REPLACES the fixed take-profit; the trailing
        #    level ratchets in the profit direction and is floored at entry
        #    (LONG never below / SHORT never above), so trailing-ON can never
        #    exit below entry. Trailing (profit-taking) is evaluated before
        #    the hard stop-loss.
        sl_enabled = self.risk_engine.config.get("stop_loss_enabled", False)
        trail_enabled = self.risk_engine.config.get("trailing_stop_enabled", False)
        if trail_enabled:
            level = float(pos.get("trailing_stop_level", entry))
            trail_dist = float(pos.get("trailing_stop") or 0.0)
            if trail_dist <= 0.0:
                trail_dist = abs(entry - stop)
            if direction == "LONG":
                level = max(entry_basis, level, curr_price - trail_dist)
                pos["trailing_stop_level"] = level
                if curr_price <= level:
                    is_exit = True
                    pnl = size * (level - entry_basis)
                    exit_reason = "Trailing Stop Hit"
            else:  # SHORT
                level = min(entry_basis, level, curr_price + trail_dist)
                pos["trailing_stop_level"] = level
                if curr_price >= level:
                    is_exit = True
                    pnl = size * (entry_basis - level)
                    exit_reason = "Trailing Stop Hit"
        elif direction == "LONG":
            if curr_price >= tp:
                is_exit = True
                pnl = size * (tp - entry_basis)
                exit_reason = "Take Profit Hit"
        else:  # SHORT
            if curr_price <= tp:
                is_exit = True
                pnl = size * (entry_basis - tp)
                exit_reason = "Take Profit Hit"
        # Hard stop-loss stays active when enabled (evaluated after trailing /
        # fixed TP so profit-taking takes precedence). Only reachable below
        # entry for LONG / above entry for SHORT.
        if not is_exit and sl_enabled:
            if direction == "LONG" and curr_price <= stop:
                is_exit = True
                pnl = size * (stop - entry_basis)
                exit_reason = "Stop Loss Hit"
            elif direction == "SHORT" and curr_price >= stop:
                is_exit = True
                pnl = size * (entry_basis - stop)
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

        # Averaging-down (dip-buy): while LONG and enabled, buy additional
        # lots at step % below the blended average entry (spot only). Exit
        # conditions take precedence (evaluated with the pre-add blended
        # entry above); an add never fires in the same pass as an exit.
        avg_down_enabled = self.risk_engine.config.get("avg_down_enabled", False)
        if avg_down_enabled and not is_exit and direction == "LONG":
            max_lots = int(self.risk_engine.config.get("avg_down_max_lots", 3))
            if max_lots < 2:
                max_lots = 2
            if max_lots > 5:
                max_lots = 5
            step_pct = float(self.risk_engine.config.get("avg_down_step_pct", 2.0))
            size_mult = float(self.risk_engine.config.get("avg_down_size_multiplier", 1.0))
            if lots < max_lots and curr_price > 0:
                avg_entry = float(pos.get("avg_entry", entry))
                # Add #(lots+1) triggers at step_pct * lots % below avg entry
                trigger = avg_entry * (1.0 - (step_pct / 100.0) * lots)
                if curr_price <= trigger:
                    first_lot = float(pos.get("first_lot_size", size))
                    add_size = round(first_lot * size_mult, 6)
                    if add_size <= 0.0:
                        add_size = first_lot
                    max_asset = float(self.risk_engine.config.get("max_per_asset_exposure", 2000.0))
                    max_total = float(self.risk_engine.config.get("max_total_exposure", 5000.0))
                    new_size = size + add_size
                    new_exposure = new_size * curr_price
                    other_exposure = sum(
                        p.get("exposure_usd", p["size"] * p.get("avg_entry", p["entry"]))
                        for s, p in self.positions.items() if s != symbol
                    )
                    if new_exposure <= max_asset and (other_exposure + new_exposure) <= max_total:
                        success = execution_adapter.execute_swap(
                            symbol=symbol,
                            direction="LONG",
                            size=add_size,
                            price=curr_price
                        )
                        if success:
                            old_cost = size * avg_entry
                            new_avg = (old_cost + add_size * curr_price) / new_size
                            pos["size"] = new_size
                            pos["avg_entry"] = new_avg
                            pos["lots"] = lots + 1
                            pos["exposure_usd"] = new_exposure
                            # Spec: trailing stop is floored at the blended
                            # avg_entry, so reset the stored level to the NEW
                            # average on add (lets a small recovery above the
                            # new avg exit the whole blended position).
                            pos["trailing_stop_level"] = new_avg
                            # Keep the risk engine's portfolio ledger in sync:
                            # evaluate_and_size_signal sums active_positions
                            # exposure_usd to gate NEW entries per the total
                            # exposure cap. Without this update the add
                            # undercounts exposure and a later entry could be
                            # over-sized past max_total_exposure.
                            ra = self.risk_engine.active_positions.get(symbol)
                            if ra is not None:
                                ra["size"] = new_size
                                ra["exposure_usd"] = new_exposure
                                ra["entry"] = new_avg  # blended basis
                                ra["avg_entry"] = new_avg
                            self.log_event(
                                f"Average-down add #{lots + 1} for {symbol}: +{add_size} @ {curr_price:.4f}, "
                                f"avg entry ${new_avg:.4f} (lots {lots + 1}/{max_lots})"
                            )
                        else:
                            self.log_event(f"Execution adapter failed to fill average-down add for {symbol}.")
                    else:
                        self.log_event(
                            f"Average-down add for {symbol} rejected: exposure cap "
                            f"({new_exposure:.2f} > asset {max_asset:.0f} or total {max_total:.0f})."
                        )

    def start(self, market_data_provider: Any, execution_adapter: Any, interval_sec: float = 5.0, thread_name: str = None):
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
                
        self.thread = threading.Thread(target=run_loop, daemon=True, name=thread_name)
        self.thread.start()
        self.log_event("AI Trading background engine started successfully.")

    def stop(self):
        """Stop background execution loop."""
        self.running = False
        if self.thread:
            self.thread.join(timeout=2.0)
        self.log_event("AI Trading background engine stopped.")
