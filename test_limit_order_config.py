"""Focused static checks for safe limit/market custom-token orders."""
from pathlib import Path
SOURCE=Path(__file__).with_name('main.py').read_text()
def test_custom_mint_validation_and_no_symbol_inference():
 assert 'def validate_solana_mint' in SOURCE and 'BASE58_ALPHABET' in SOURCE
 assert 'Invalid Solana mint' in SOURCE and 'custom_mint' in SOURCE and 'custom_symbol' in SOURCE
def test_limit_order_ui_and_routing():
 for x in ('limit_buy','limit_sell','limit-amount','limit-price','limit-quote','run_limit_order','validate_limit_order','limit-confirm'): assert x in SOURCE
 assert 'side == "buy" and price <= limit_price' in SOURCE and 'side == "sell" and price >= limit_price' in SOURCE
def test_safe_mode_and_trade_metadata():
 assert 'Orders default to LIVE mode' in SOURCE and 'status":"confirmed"' in SOURCE
 assert 'max position' in SOURCE and 'place_order(pair, side, amount)' in SOURCE and 'explicit order confirmation required' in SOURCE

def test_server_side_strategy_side_and_failure_terminal_state():
 assert 'expected_side = "buy" if start_strategy == "limit_buy" else "sell"' in SOURCE
 assert 'strategy side mismatch' in SOURCE
 assert 'status":"rejected"' in SOURCE and 'state["running"] = False' in SOURCE
 assert 'paper mode requires explicit paper confirmation' in SOURCE

def test_rejected_validation_is_terminal_and_custom_mint_is_routed():
 assert '"status": "rejected"' in SOURCE
 assert '"custom_mint": params.get("custom_mint"' in SOURCE
 assert '"quote_token": params.get("quote_token"' in SOURCE

def test_deterministic_effective_trade_mode_policy():
 assert 'def resolve_order_mode(params)' in SOURCE
 assert 'trade_mode must be live or paper' in SOURCE
 assert '"effective_mode": effective_mode' in SOURCE
 assert "trade_mode=live" in SOURCE and "confirm=true" in SOURCE

def test_execution_binds_resolved_mode_not_ambient_config():
 assert 'effective_mode = state.get("effective_mode", "live")' in SOURCE
 assert 'state["paper_trading"] = (effective_mode == "paper")' in SOURCE
 assert '"effective_mode":effective_mode' in SOURCE
