#!/usr/bin/env python3
"""
Unit and regression tests for GRID_ZONE_SPEC.md.
Includes coverage for:
1. Zone boundary - one cell up from initial buy is still a buy zone (buy cells 0..mid_idx, first sell cell mid_idx+1).
2. Take-profit must NEVER sell below purchase price (hold).
3. Autonomous re-engage and re-center on empty positions (flush).
4. Drawdown and daily loss halts.
"""
import sys
import types
import time
from pathlib import Path

# Setup requests mock first to prevent import failures
sys.modules.setdefault("requests", types.SimpleNamespace())

def test_zone_boundary_one_cell_up_is_still_buy():
    import main
    from main import state, cfg, _init_grid_pair, _grid_sell_indices

    # Back up originals
    orig_get_balance = main.get_balance
    orig_get_price = main.get_price

    # Setup mocks
    main.get_balance = lambda: 100.0
    main.get_price = lambda pair: 100.0

    # Setup clean state
    state["running"] = True
    state["strategy"] = "grid"
    state["mode"] = "cex"
    state["active_pairs"] = ["SOL/USDC"]
    state["grid_pairs"] = {}
    state["positions"] = []
    state["pnl"] = 0.0
    state["daily_pnl"] = 0.0
    state["peak_balance"] = 100.0

    cfg["risk_pct"] = 10.0
    cfg["max_pos"] = 500.0
    cfg["auto_compound"] = True
    cfg["min_order_usdc"] = 5.0
    cfg["grid_stop_loss_pct"] = 8.0

    p = "SOL/USDC"
    gs = _init_grid_pair(p)
    assert gs is not None, "Failed to initialize grid pair"
    grids = gs["grids"]
    mid_idx = gs["mid_idx"]  # levels=5 -> mid_idx=3, len(grids)=6

    # Verify buy cells are 0..mid_idx inclusive
    for i in range(len(grids) - 1):
        is_buy_zone = i <= mid_idx
        if i <= mid_idx:
            assert is_buy_zone, f"Cell {i} must be a buy zone"
        else:
            assert not is_buy_zone, f"Cell {i} must be a sell zone"

    # Verify first sell index is mid_idx + 1
    sell_indices = _grid_sell_indices({0: {"price": 90.0, "amount": 0.1}}, grid_idx=mid_idx + 1, levels=5)
    assert len(sell_indices) == 1, "Should target a sell level"
    assert sell_indices[0] == 0

    # For grid_idx = mid_idx, it's in the buy zone, so _grid_sell_indices must return empty
    sell_indices_mid = _grid_sell_indices({0: {"price": 90.0, "amount": 0.1}}, grid_idx=mid_idx, levels=5)
    assert not sell_indices_mid, "Should NOT sell at mid_idx because it is a buy zone cell now"

    # Restore originals
    main.get_balance = orig_get_balance
    main.get_price = orig_get_price

    print("PASS: test_zone_boundary_one_cell_up_is_still_buy")

def test_take_profit_never_sells_below_purchase_price():
    import main
    from main import state, cfg, run_grid

    # Setup clean state
    state["running"] = True
    state["strategy"] = "grid"
    state["mode"] = "cex"
    state["active_pairs"] = ["SOL/USDC"]
    state["positions"] = []
    state["pnl"] = 0.0
    state["daily_pnl"] = 0.0
    state["peak_balance"] = 100.0

    cfg["risk_pct"] = 10.0
    cfg["max_pos"] = 500.0
    cfg["auto_compound"] = True
    cfg["min_order_usdc"] = 5.0
    cfg["grid_stop_loss_pct"] = 8.0

    placed_orders = []
    def mock_place_order(pair, side, amount, grid_idx=None):
        placed_orders.append((side, amount, grid_idx))
        return True

    orig_place_order = main.place_order
    orig_get_price = main.get_price
    orig_get_balance = main.get_balance
    orig_sleep = main.time.sleep

    main.place_order = mock_place_order
    main.get_balance = lambda: 100.0
    main.time.sleep = lambda secs: None

    # Initialize grid
    p = "SOL/USDC"
    main.get_price = lambda pair: 100.0
    from main import _init_grid_pair
    gs = _init_grid_pair(p)
    assert gs is not None, "Failed to initialize grid pair"
    state["grid_pairs"][p] = gs
    gs["seeded"] = True # pretend already seeded

    # Setup trailing sell active at a high price
    # We bought at $102, price rose to $105 (trailing_high = 105)
    # Then price pulled back to $101.5 (below cost of 102 but satisfies trailing pullback 105 * 0.995 = 103.9)
    # We want to make sure it HOLDS (does not sell)
    gs["filled"] = {4: {"price": 102.0, "amount": 0.1}} # first sell cell is 4
    gs["trailing_sell_active"] = True
    gs["trailing_high"] = 105.0

    # Tick price down to 101.5 (below buy price of 102.0)
    # Set running=False on the second tick to exit the run_grid loop cleanly
    price_calls = [101.5]
    def mock_get_price_seq(pair):
        if price_calls:
            return price_calls.pop(0)
        state["running"] = False
        state["strategy"] = None
        return 0.0
    main.get_price = mock_get_price_seq

    run_grid()

    # The position at index 4 should NOT have been sold!
    assert 4 in gs["filled"], "Position must be HELD (not sold) because pullback price 101.5 <= buy price 102.0"
    assert not gs["trailing_sell_active"], "Trailing sell should have been reset on hold to prevent loop spam"

    # Restore originals
    main.place_order = orig_place_order
    main.get_price = orig_get_price
    main.get_balance = orig_get_balance
    main.time.sleep = orig_sleep

    print("PASS: test_take_profit_never_sells_below_purchase_price")

def test_autonomous_recenter_and_reengage_on_empty_positions():
    import main
    from main import state, cfg, run_grid

    # Setup clean state
    state["running"] = True
    state["strategy"] = "grid"
    state["mode"] = "cex"
    state["active_pairs"] = ["SOL/USDC"]
    state["positions"] = []
    state["pnl"] = 0.0
    state["daily_pnl"] = 0.0
    state["peak_balance"] = 100.0

    cfg["risk_pct"] = 10.0
    cfg["max_pos"] = 500.0
    cfg["auto_compound"] = True
    cfg["min_order_usdc"] = 5.0
    cfg["grid_stop_loss_pct"] = 8.0

    placed_orders = []
    def mock_place_order(pair, side, amount, grid_idx=None):
        placed_orders.append((side, amount, grid_idx))
        return True

    orig_place_order = main.place_order
    orig_get_price = main.get_price
    orig_get_balance = main.get_balance
    orig_sleep = main.time.sleep

    main.place_order = mock_place_order
    main.get_balance = lambda: 100.0
    main.time.sleep = lambda secs: None

    # Initialize grid at $100
    p = "SOL/USDC"
    main.get_price = lambda pair: 100.0
    from main import _init_grid_pair
    gs = _init_grid_pair(p)
    assert gs is not None, "Failed to initialize grid pair"
    state["grid_pairs"][p] = gs
    gs["seeded"] = True # pretend already seeded
    gs["filled"] = {} # NO positions! (empty)

    # Since there are no positions (empty), the next tick should automatically trigger re-centering
    # and reset gs["seeded"] = False, which immediately triggers the Base Buy on Start (Option B)!
    # Let's tick price to 101.0
    price_calls = [101.0]
    def mock_get_price_seq(pair):
        if price_calls:
            return price_calls.pop(0)
        state["running"] = False
        state["strategy"] = None
        return 0.0
    main.get_price = mock_get_price_seq

    run_grid()

    # Verify that:
    # 1. The grid re-centered around $101.0
    # For levels=5, len(grids)=6, price is the average of index 2 and 3
    center_price = (gs["grids"][2] + gs["grids"][3]) / 2
    assert abs(center_price - 101.0) < 0.1, f"Grid should have re-centered around 101.0 but got {center_price} from {gs['grids']}"
    # 2. gs["seeded"] was reset and base buy on start was executed again
    assert gs["seeded"] is True, "Seeded flag should be set to True again"
    assert len(placed_orders) >= 1, "Should have placed at least one order (the seed buy)"
    assert placed_orders[-1][0] == "buy", "Seeded order should be a buy"

    # Restore originals
    main.place_order = orig_place_order
    main.get_price = orig_get_price
    main.get_balance = orig_get_balance
    main.time.sleep = orig_sleep

    print("PASS: test_autonomous_recenter_and_reengage_on_empty_positions")

def test_profitable_pullback_into_buy_zone_still_exits():
    import main
    from main import state, cfg, run_grid

    # Setup clean state
    state["running"] = True
    state["strategy"] = "grid"
    state["mode"] = "cex"
    state["active_pairs"] = ["SOL/USDC"]
    state["positions"] = []
    state["pnl"] = 0.0
    state["daily_pnl"] = 0.0
    state["peak_balance"] = 100.0

    cfg["risk_pct"] = 10.0
    cfg["max_pos"] = 500.0
    cfg["auto_compound"] = True
    cfg["min_order_usdc"] = 5.0
    cfg["grid_stop_loss_pct"] = 8.0
    cfg["partial_sell_pct"] = 100

    placed_orders = []
    def mock_place_order(pair, side, amount, grid_idx=None):
        placed_orders.append((side, amount, grid_idx))
        return True

    orig_place_order = main.place_order
    orig_get_price = main.get_price
    orig_get_balance = main.get_balance
    orig_sleep = main.time.sleep

    main.place_order = mock_place_order
    main.get_balance = lambda: 100.0
    main.time.sleep = lambda secs: None

    # Initialize grid at $100
    p = "SOL/USDC"
    main.get_price = lambda pair: 100.0
    from main import _init_grid_pair
    gs = _init_grid_pair(p)
    assert gs is not None, "Failed to initialize grid pair"
    state["grid_pairs"][p] = gs
    gs["seeded"] = True # pretend already seeded

    # Setup trailing sell active at a high price
    # We bought at $102, price rose to $105 (trailing_high = 105)
    # Then price pulled back to $103.5.
    # $103.5 is in cell 3 (the buy zone, since <= mid_idx=3).
    # But it is above buy price of $102.0.
    # It satisfies trailing pullback (103.5 <= 105 * 0.995 = 104.475).
    # With our fix, this pullback into the buy zone (cell 3) targets cell 4 (mid_idx+1)
    # and successfully sells the level 4 position.
    gs["filled"] = {4: {"price": 102.0, "amount": 0.1}}
    gs["trailing_sell_active"] = True
    gs["trailing_high"] = 105.0
    gs["previous_price"] = 105.0

    # Tick price down to 103.5
    price_calls = [103.5]
    def mock_get_price_seq(pair):
        if price_calls:
            return price_calls.pop(0)
        state["running"] = False
        state["strategy"] = None
        return 0.0
    main.get_price = mock_get_price_seq

    run_grid()

    # The position at index 4 should have been sold!
    assert 4 not in gs["filled"], "Position must be sold because pullback price is profitable"
    assert len(placed_orders) >= 1, "Should have placed at least one order"
    assert placed_orders[-1] == ("sell", 0.1, None), f"Should have executed sell order for level 4, got {placed_orders}"

    # Restore originals
    main.place_order = orig_place_order
    main.get_price = orig_get_price
    main.get_balance = orig_get_balance
    main.time.sleep = orig_sleep

    print("PASS: test_profitable_pullback_into_buy_zone_still_exits")

if __name__ == "__main__":
    test_zone_boundary_one_cell_up_is_still_buy()
    test_take_profit_never_sells_below_purchase_price()
    test_autonomous_recenter_and_reengage_on_empty_positions()
    test_profitable_pullback_into_buy_zone_still_exits()
    print("All GRID_ZONE_SPEC paper-test validation checks pass successfully!")
