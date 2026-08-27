#!/usr/bin/env python3
"""
Regression tests for Grid Stuck Running and Startup Crash Fixes.
Includes coverage for:
1. _safe_strategy_runner catching exceptions, logging tracebacks, and resetting state variables.
2. _execute_base_buy_if_needed with null or missing config/state fields to ensure it handles them gracefully without TypeErrors or ValueErrors.
"""
import sys
import types

# Ensure requests module is mocked for clean imports
sys.modules.setdefault("requests", types.SimpleNamespace())

def test_safe_strategy_runner_exception_handling():
    import main
    from main import state, _safe_strategy_runner

    # Set initial "running" states
    state["running"] = True
    state["strategy"] = "grid"
    state["active_pairs"] = ["SOL/USDC"]
    state["error"] = None

    def crashing_func():
        raise ValueError("Simulated strategy crash")

    # Wrap the crashing function
    safe_func = _safe_strategy_runner(crashing_func)

    # Call it (it should catch the exception)
    safe_func()

    # Assert that states have been correctly reset
    assert state["running"] is False, "state['running'] must be reset to False on exception"
    assert state["strategy"] is None, "state['strategy'] must be reset to None on exception"
    assert state["active_pairs"] == [], "state['active_pairs'] must be reset to empty list on exception"
    assert "Simulated strategy crash" in (state["error"] or ""), "state['error'] must contain the error message"

    print("PASS: test_safe_strategy_runner_exception_handling")

def test_execute_base_buy_null_guards():
    import main
    from main import state, cfg, _execute_base_buy_if_needed

    # Save original globals
    orig_get_balance = getattr(main, "get_balance", None)
    orig_get_price = getattr(main, "get_price", None)
    orig_sol_get_balance = getattr(main, "sol_get_balance", None)
    orig_place_order = getattr(main, "place_order", None)

    # Setup mocked functions
    main.get_balance = lambda: None  # Check that returning None is guarded
    main.get_price = lambda pair: 100.0
    main.sol_get_balance = lambda: None
    main.place_order = lambda pair, side, amount, grid_idx=None: False

    # Setup state with missing/null values
    state["running"] = True
    state["strategy"] = "grid"
    state["active_pairs"] = ["SOL/USDC"]
    state["grid_pairs"] = {}
    state["positions"] = []
    state["mode"] = "cex"
    state["paper_trading"] = True
    state["compound_profit"] = None  # test None value
    state["sol_usdc"] = None  # test None value

    # Setup cfg with missing/null/empty/str values that could cause crash
    cfg["risk_pct"] = None  # test None value
    cfg["max_pos"] = "500"  # test string representation
    cfg["auto_compound"] = None  # test None value
    cfg["min_order_usdc"] = ""  # test empty string
    cfg["sol_wallet"] = "GgXXXXXXXXXXXXXX"

    p = "SOL/USDC"
    gs = {
        "price": 100.0,
        "grids": [95.0, 97.5, 100.0, 102.5, 105.0, 107.5],
        "mid_idx": 3,
        "levels": None,  # test None levels
        "filled": {},
        "seeded": False,
        "trailing_sell_active": False,
        "trailing_high": 0.0
    }

    # This call should execute without raising any exceptions (TypeError or ValueError)
    try:
        _execute_base_buy_if_needed(p, gs, 100.0)
    except Exception as e:
        assert False, f"_execute_base_buy_if_needed crashed with: {e}"

    # Restore originals
    main.get_balance = orig_get_balance
    main.get_price = orig_get_price
    main.sol_get_balance = orig_sol_get_balance
    main.place_order = orig_place_order

    print("PASS: test_execute_base_buy_null_guards")

if __name__ == "__main__":
    test_safe_strategy_runner_exception_handling()
    test_execute_base_buy_null_guards()

