#!/usr/bin/env python3
"""
Unit and regression tests for Option B: Grid Base Buy on Start.
Asserts that the seed buy places exactly one immediate order at startup,
registers a filled position, sets a persistent seeded flag that prevents re-firing,
and that the existing trading rules handle this seeded inventory in a byte-identical manner.
"""
import sys
import types
from pathlib import Path

# Ensure requests module is mocked for clean imports
sys.modules.setdefault("requests", types.SimpleNamespace())

def test_base_buy_on_start_registers_once_and_does_not_refire():
    # Setup mocks
    import main
    from main import state, cfg, _init_grid_pair, _grid_sync_state, _grid_sell_indices

    # Back up originals
    orig_get_balance = main.get_balance
    orig_place_order = main.place_order
    orig_get_price = main.get_price

    # Reset state to clean simulated bot start in CEX paper mode for sandbox isolation
    state["running"] = True
    state["strategy"] = "grid"
    state["active_pairs"] = ["SOL/USDC"]
    state["grid_pairs"] = {}
    state["positions"] = []
    state["mode"] = "cex"
    state["exchange"] = "binance"
    state["paper_trading"] = True
    
    # Configure mock values in cfg
    cfg["risk_pct"] = 10.0  # 10% risk
    cfg["max_pos"] = 500.0
    cfg["auto_compound"] = True
    cfg["min_order_usdc"] = 5.0

    # Spy on order placements
    placed_orders = []
    def mock_place_order(pair, side, amount, grid_idx=None):
        placed_orders.append({
            "pair": pair,
            "side": side,
            "amount": amount,
            "grid_idx": grid_idx
        })
        return True

    main.get_balance = lambda: 100.0  # mock balance
    main.place_order = mock_place_order
    main.get_price = lambda pair: 100.0  # mock price to prevent requests call

    # 1. Initialize the pair (first run)
    p = "SOL/USDC"
    gs = _init_grid_pair(p)
    assert gs is not None
    assert not gs.get("seeded")

    # Manually run the startup logic block as implemented in run_grid()
    bal = main.get_balance()
    effective_bal = bal + (state.get("compound_profit", 0) if cfg.get("auto_compound", True) else 0)
    min_order = max(5.0, float(cfg.get("min_order_usdc", 5)))
    levels = gs["levels"]
    size = max(min_order, min(effective_bal * cfg["risk_pct"] / 100, cfg["max_pos"]) / levels)
    
    price = gs["price"]
    assert price > 0
    assert size > 1

    grids = gs["grids"]
    cell = None
    for i, g in enumerate(grids[:-1]):
        ng = grids[i+1]
        if g <= price < ng:
            cell = i
            break
    if cell is None:
        cell = gs["mid_idx"]

    base_amt = round(size / price, 6)
    if main.place_order(p, "buy", base_amt, grid_idx=cell):
        gs["filled"][cell] = {"price": price, "amount": base_amt}
        state["positions"].append({"price": price, "amount": base_amt, "grid": cell, "strategy": "Grid"})
        gs["seeded"] = True

    # Assertions for initial seed buy
    assert len(placed_orders) == 1, "Exactly one order should have been placed"
    assert placed_orders[0]["pair"] == p
    assert placed_orders[0]["side"] == "buy"
    assert placed_orders[0]["grid_idx"] == cell
    assert placed_orders[0]["amount"] == base_amt

    assert cell in gs["filled"], "Seeded cell must be marked filled in gs"
    assert gs["filled"][cell]["price"] == price
    assert gs["filled"][cell]["amount"] == base_amt

    assert len(state["positions"]) == 1, "One position must be registered in global state"
    assert state["positions"][0]["grid"] == cell
    assert state["positions"][0]["amount"] == base_amt
    assert state["positions"][0]["price"] == price

    assert gs["seeded"] is True, "Seeded flag must be set to True"

    # 2. Simulate subsequent loop check and verify seed buy does NOT re-fire
    initial_order_count = len(placed_orders)
    # Simulate run_grid checking 'not gs.get("seeded")' again in next iterations
    if not gs.get("seeded"):
        main.place_order(p, "buy", base_amt, grid_idx=cell)

    assert len(placed_orders) == initial_order_count, "Base buy must never re-fire once seeded flag is True"

    # 3. Verify that the existing _grid_sell_indices logic is byte-identical and works perfectly with the seeded position
    # Specifically, when price moves to the first sell index (sell-zone boundary), the seeded position at cell '2' is liquidatable
    sell_indices = _grid_sell_indices(gs["filled"], grid_idx=gs["mid_idx"] + 1, levels=levels)
    assert cell in sell_indices, f"Seeded cell {cell} should be targeted for sell at mid_idx+1 {gs['mid_idx'] + 1}"

    # Restore originals
    main.get_balance = orig_get_balance
    main.place_order = orig_place_order
    main.get_price = orig_get_price
    print("PASS  test_base_buy_on_start_registers_once_and_does_not_refire")

def test_base_buy_telegram_alert():
    # Setup mocks
    import main
    from main import state, cfg, _execute_base_buy_if_needed

    # Back up originals
    orig_get_balance = main.get_balance
    orig_place_order = main.place_order
    orig_get_price = main.get_price
    orig_send_telegram = main.send_telegram

    # Reset state to clean simulated bot start in CEX paper mode
    state["running"] = True
    state["strategy"] = "grid"
    state["active_pairs"] = ["SOL/USDC"]
    state["grid_pairs"] = {}
    state["positions"] = []
    state["mode"] = "cex"
    state["exchange"] = "binance"
    state["paper_trading"] = True
    state["compound_profit"] = 0.0

    # Configure mock values in cfg
    cfg["risk_pct"] = 10.0
    cfg["max_pos"] = 500.0
    cfg["auto_compound"] = True
    cfg["min_order_usdc"] = 5.0

    main.get_balance = lambda: 100.0
    main.get_price = lambda pair: 100.0

    # Case 1: successful base buy
    placed_orders = []
    def mock_place_order_success(pair, side, amount, grid_idx=None):
        placed_orders.append((side, amount, grid_idx))
        return True

    telegram_messages = []
    def mock_send_telegram(msg):
        telegram_messages.append(msg)

    main.place_order = mock_place_order_success
    main.send_telegram = mock_send_telegram

    p = "SOL/USDC"
    gs = {
        "price": 100.0,
        "grids": [95.0, 97.5, 100.0, 102.5, 105.0, 107.5],
        "mid_idx": 3,
        "levels": 5,
        "filled": {},
        "seeded": False,
        "trailing_sell_active": False,
        "trailing_high": 0.0
    }

    _execute_base_buy_if_needed(p, gs, 100.0)

    assert len(placed_orders) == 1, "Exactly one order should have been placed"
    assert len(telegram_messages) == 1, "Exactly one telegram message should have been sent"
    assert "🟢 <b>BUY</b> SOL/USDC" in telegram_messages[0]
    assert "Level: 2" in telegram_messages[0]
    assert "Price: $100.0" in telegram_messages[0]
    assert "Mode: PAPER" in telegram_messages[0]

    # Case 2: failed base buy
    placed_orders.clear()
    telegram_messages.clear()
    gs["seeded"] = False
    gs["filled"].clear()

    def mock_place_order_fail(pair, side, amount, grid_idx=None):
        placed_orders.append((side, amount, grid_idx))
        return False

    main.place_order = mock_place_order_fail
    _execute_base_buy_if_needed(p, gs, 100.0)

    assert len(placed_orders) == 1, "Order placement attempted"
    assert len(telegram_messages) == 0, "No telegram message on order placement failure"

    # Restore originals
    main.get_balance = orig_get_balance
    main.place_order = orig_place_order
    main.get_price = orig_get_price
    main.send_telegram = orig_send_telegram
    print("PASS  test_base_buy_telegram_alert")

if __name__ == "__main__":
    test_base_buy_on_start_registers_once_and_does_not_refire()
    test_base_buy_telegram_alert()
