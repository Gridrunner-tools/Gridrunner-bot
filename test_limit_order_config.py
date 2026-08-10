"""Static regression checks for limit-order configuration and routing."""
from pathlib import Path
SOURCE = Path(__file__).with_name("main.py").read_text()
def test_limit_order_validation_and_ui():
    assert 'def validate_limit_order' in SOURCE
    assert 'value="limit_buy"' in SOURCE and 'value="limit_sell"' in SOURCE
    assert 'id="limit-amount"' in SOURCE and 'id="limit-price"' in SOURCE
    assert 'amount must be positive' in SOURCE and 'limit price must be positive' in SOURCE

def test_limit_order_routing_semantics():
    assert '"limit_buy":run_limit_order' in SOURCE and '"limit_sell":run_limit_order' in SOURCE
    assert 'side == "buy" and price <= limit_price' in SOURCE
    assert 'side == "sell" and price >= limit_price' in SOURCE
    assert 'place_order(pair, side, amount)' in SOURCE

def test_limit_order_trade_details_and_risk_visibility():
    assert 'order_type' in SOURCE and 'limit_price' in SOURCE
    assert 'status":"confirmed"' in SOURCE
    assert 'Orders are subject to risk limits and paper/live mode.' in SOURCE
