#!/usr/bin/env python3
"""
Regression tests for Base Buy Safety Fixes (Task 3df66386).
Includes coverage for:
1. Bug 1: Silent-failure "seeded" flag (seeded remains False if place_order fails).
2. Bug 2: Balance guard self-disables at 0 (fires even when usdc_bal is 0).
3. Bug 3: Sizing off USDC-only balance on Solana DEX mode.
"""
import sys
import types

# Ensure requests module is mocked for clean imports
sys.modules.setdefault("requests", types.SimpleNamespace())

def test_bug1_seeded_flag_only_on_success():
    import main
    from main import state, cfg, _execute_base_buy_if_needed

    orig_get_balance = main.get_balance
    orig_place_order = main.place_order
    orig_get_price = main.get_price
    orig_sol_get_balance = main.sol_get_balance

    state["running"] = True
    state["strategy"] = "grid"
    state["active_pairs"] = ["SOL/USDC"]
    state["grid_pairs"] = {}
    state["positions"] = []
    state["mode"] = "cex"
    state["paper_trading"] = True
    state["compound_profit"] = 0.0

    cfg["risk_pct"] = 10.0
    cfg["max_pos"] = 500.0
    cfg["auto_compound"] = True
    cfg["min_order_usdc"] = 5.0

    main.get_balance = lambda: 100.0
    main.get_price = lambda pair: 100.0
    main.sol_get_balance = lambda: 100.0

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

    # Case A: place_order fails -> seeded should stay False
    main.place_order = lambda pair, side, amount, grid_idx=None: False
    _execute_base_buy_if_needed(p, gs, 100.0)
    assert gs["seeded"] is False, "seeded flag must stay False when order placement fails"

    # Case B: place_order succeeds -> seeded should become True
    main.place_order = lambda pair, side, amount, grid_idx=None: True
    _execute_base_buy_if_needed(p, gs, 100.0)
    assert gs["seeded"] is True, "seeded flag must become True when order placement succeeds"

    # Restore originals
    main.get_balance = orig_get_balance
    main.place_order = orig_place_order
    main.get_price = orig_get_price
    main.sol_get_balance = orig_sol_get_balance
    print("PASS: test_bug1_seeded_flag_only_on_success")

def test_bug2_balance_guard_fires_at_zero():
    import main
    from main import state, cfg, jupiter_swap, _raydium_execute_swap

    # Setup config/wallet address so check proceeds to the balance guard
    cfg["sol_wallet"] = "GgXXXXXXXXXXXXXX"
    state["secrets"] = {"SOL_PRIVATE_KEY": "5" * 44}  # dummy key
    state["sol_usdc"] = 0.0  # balance is EXACTLY 0

    # Ensure paper_trading is False so balance check is not bypassed
    orig_paper_trading = state.get("paper_trading", False)
    state["paper_trading"] = False

    # Jupiter Swap Balance Guard Check
    # We attempt to swap 10.0 USDC (amount_input > usdc_bal)
    success, amt = jupiter_swap("USDC", "SOL", 10.0, 100.0)
    assert success is False, "Jupiter swap must fail when usdc_bal is 0 and amount_input > 0"
    assert amt == 0.0

    # Raydium Swap Balance Guard Check
    success, amt = _raydium_execute_swap(
        "USDC", "SOL", "mint_from", "mint_to", 10.0,
        0.0, 100.0, "buy", "", 0, {}, 9
    )
    assert success is False, "Raydium swap must fail when usdc_bal is 0 and amount_input > 0"
    assert amt == 0.0

    # Restore originals
    state["paper_trading"] = orig_paper_trading

    print("PASS: test_bug2_balance_guard_fires_at_zero")

def test_bug3_usdc_only_sizing_in_solana():
    import main
    from main import state, cfg, _execute_base_buy_if_needed

    orig_get_balance = main.get_balance
    orig_place_order = main.place_order
    orig_get_price = main.get_price
    orig_sol_get_balance = main.sol_get_balance

    # Setup simulated Solana DEX mode
    state["running"] = True
    state["strategy"] = "grid"
    state["active_pairs"] = ["SOL/USDC"]
    state["grid_pairs"] = {}
    state["positions"] = []
    state["mode"] = "dex"
    state["chain"] = "solana"
    state["paper_trading"] = True
    state["compound_profit"] = 0.0

    # Portfolio has $500 total (e.g. in SOL), but only $2 in USDC
    state["sol_balance"] = 500.0
    state["sol_usdc"] = 2.0
    state["sol_usdt"] = 0.0

    cfg["sol_wallet"] = "GgXXXXXXXXXXXXXX"
    cfg["risk_pct"] = 10.0
    cfg["max_pos"] = 500.0
    cfg["auto_compound"] = True
    cfg["min_order_usdc"] = 5.0

    # Mock sol_get_balance to do nothing (prevent RPC calls) but keep state
    main.sol_get_balance = lambda: 500.0
    main.get_price = lambda pair: 100.0

    placed_sizes = []
    def mock_place_order(pair, side, amount, grid_idx=None):
        placed_sizes.append(amount * 100.0) # size = amount * price (100.0)
        return True
    main.place_order = mock_place_order

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

    # Under old logic: bal = get_balance() = 500.0
    #                 size = max(5.0, min(500 * 10% / 100, 500) / 5) = max(5.0, 10.0) = 10.0 USD
    # Under new logic: bal = sol_usdc = 2.0
    #                 size = max(5.0, min(2 * 10% / 100, 500) / 5) = max(5.0, 0.04) = 5.0 USD
    _execute_base_buy_if_needed(p, gs, 100.0)

    assert len(placed_sizes) == 1, "Order should be placed"
    assert round(placed_sizes[0], 2) == 5.0, f"Expected size 5.0 USD, got {placed_sizes[0]}"
    assert gs["seeded"] is True, "seeded flag should be True on success"

    # Reset seeded flag and verify with high USDC (e.g. 500.0 USDC)
    gs["seeded"] = False
    placed_sizes.clear()
    state["sol_usdc"] = 500.0
    # Sizing: (500 * 10%) / 5 = 10.0 USD level size. 10.0 > 5.0 -> 10.0 USD size!
    _execute_base_buy_if_needed(p, gs, 100.0)
    assert len(placed_sizes) == 1, "Order should be placed"
    assert round(placed_sizes[0], 2) == 10.0, f"Expected size 10.0 USD, got {placed_sizes[0]}"
    assert gs["seeded"] is True, "seeded flag should be True on success"

    # Restore originals
    main.get_balance = orig_get_balance
    main.place_order = orig_place_order
    main.get_price = orig_get_price
    main.sol_get_balance = orig_sol_get_balance
    print("PASS: test_bug3_usdc_only_sizing_in_solana")

if __name__ == "__main__":
    test_bug1_seeded_flag_only_on_success()
    test_bug2_balance_guard_fires_at_zero()
    test_bug3_usdc_only_sizing_in_solana()
