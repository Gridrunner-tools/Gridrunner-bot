#!/usr/bin/env python3
"""
Regression tests for Grid strategy behavior fixes.
Exercises the trailing arm/execute logic and one-sided dynamic grid shifting
as pure-function simulations -- no HTTP server, exchange, or license required.
"""

# 0.05% reversal constant (mirrors GRID_TRAILING_REVERSAL in main.py)
GRID_TRAILING_REVERSAL = 0.0005

# ──────────────────────────────────────────────────────────────────────────────
# Helpers: simulate the decision logic extracted from run_grid()
# ──────────────────────────────────────────────────────────────────────────────

def make_grids(center, spread=0.05, levels=5):
    return [round(center*(1-spread)+i*(center*spread*2/levels), 4) for i in range(levels+1)]

def grid_mid_idx(grids):
    return len(grids) // 2

def trailing_sell_should_execute(trailing_sell_active, trailing_high, price, trailing_pct=0.5):
    """Mirror of the sell-trigger condition in run_grid()."""
    reversal_sell = price <= trailing_high * (1 - GRID_TRAILING_REVERSAL)
    original_sell = price <= trailing_high * (1 - trailing_pct / 100)
    return trailing_sell_active and (reversal_sell or original_sell)

def trailing_buy_should_execute(trailing_buy_active, trailing_low, price, trailing_pct=0.5):
    """Mirror of the buy-trigger condition in run_grid()."""
    reversal_buy = price >= trailing_low * (1 + GRID_TRAILING_REVERSAL)
    original_buy = price >= trailing_low * (1 + trailing_pct / 100)
    return trailing_buy_active and (reversal_buy or original_buy)

def shift_buy_side(grids, price, spread, levels, mid_idx):
    """Simulate down-move one-sided shift."""
    new_grids = [round(price*(1-spread)+i*(price*spread*2/levels), 4) for i in range(levels+1)]
    result = grids[:]
    for i in range(mid_idx + 1):
        result[i] = new_grids[i]
    return result

def shift_sell_side(grids, price, spread, levels, mid_idx, filled=None):
    """Simulate up-move one-sided shift with no-loss guard."""
    new_grids = [round(price*(1-spread)+i*(price*spread*2/levels), 4) for i in range(levels+1)]
    result = grids[:]
    for i in range(mid_idx, levels + 1):
        result[i] = new_grids[i]
    # Enforce no sell level below any open position's entry price
    if filled:
        for idx, pos in filled.items():
            entry_price = pos["price"]
            for gi in range(mid_idx, levels + 1):
                if result[gi] < entry_price:
                    result[gi] = entry_price
    return result

# ──────────────────────────────────────────────────────────────────────────────
# Test 1: Trailing SELL – armed at sell grid touch, tracks high, executes on
#         0.05% pullback OR original configured-% pullback.
# ──────────────────────────────────────────────────────────────────────────────
def test_trailing_sell_armed_and_executes_on_reversal():
    # Arm at $100
    trailing_sell_active = True
    trailing_high = 100.0
    trailing_pct = 0.5  # configured 0.5%

    # Price rises to $105 — track new high
    trailing_high = 105.0

    # 0.05% reversal threshold: 105 * (1 - GRID_TRAILING_REVERSAL) = 104.9475
    threshold_reversal = trailing_high * (1 - GRID_TRAILING_REVERSAL)
    # Original 0.5% threshold: 105 * (1 - 0.005) = 104.475
    threshold_original = trailing_high * (1 - trailing_pct / 100)

    # Should NOT execute while above both thresholds
    assert not trailing_sell_should_execute(trailing_sell_active, trailing_high, 105.0, trailing_pct), \
        "Should NOT execute at peak"
    assert not trailing_sell_should_execute(trailing_sell_active, trailing_high, 104.96, trailing_pct), \
        "Should NOT execute just above 0.05% threshold"

    # Should execute on 0.05% reversal (104.9475 or below)
    price_at_reversal = threshold_reversal
    assert trailing_sell_should_execute(trailing_sell_active, trailing_high, price_at_reversal, trailing_pct), \
        f"Should execute at 0.05% reversal ({price_at_reversal})"

    # Should also execute when original configured% is hit (tighter fallback)
    assert trailing_sell_should_execute(trailing_sell_active, trailing_high, threshold_original, trailing_pct), \
        f"Should execute at original {trailing_pct}% trigger ({threshold_original})"

    print("PASS  test_trailing_sell_armed_and_executes_on_reversal")

# ──────────────────────────────────────────────────────────────────────────────
# Test 2: Trailing BUY – armed at buy grid touch, tracks low, executes on
#         0.05% bounce OR original configured-% bounce.
# ──────────────────────────────────────────────────────────────────────────────
def test_trailing_buy_armed_and_executes_on_bounce():
    trailing_buy_active = True
    trailing_low = 100.0
    trailing_pct = 0.5

    # Price drops to $95 — track new low
    trailing_low = 95.0

    threshold_reversal = trailing_low * (1 + GRID_TRAILING_REVERSAL)   # 95.0475
    threshold_original = trailing_low * (1 + trailing_pct / 100)  # 95.475

    # Should NOT execute while below both thresholds
    assert not trailing_buy_should_execute(trailing_buy_active, trailing_low, 95.0, trailing_pct), \
        "Should NOT execute at trough"
    assert not trailing_buy_should_execute(trailing_buy_active, trailing_low, 95.04, trailing_pct), \
        "Should NOT execute just below 0.05% threshold"

    # Should execute on 0.05% bounce
    assert trailing_buy_should_execute(trailing_buy_active, trailing_low, threshold_reversal, trailing_pct), \
        f"Should execute at 0.05% bounce ({threshold_reversal})"

    # Should also execute at original configured% bounce
    assert trailing_buy_should_execute(trailing_buy_active, trailing_low, threshold_original, trailing_pct), \
        f"Should execute at original {trailing_pct}% bounce ({threshold_original})"

    print("PASS  test_trailing_buy_armed_and_executes_on_bounce")

# ──────────────────────────────────────────────────────────────────────────────
# Test 3: Down-move shifts buy levels only; sell levels stay anchored.
# ──────────────────────────────────────────────────────────────────────────────
def test_down_move_shifts_buy_only():
    center = 100.0
    spread = 0.05
    levels = 5
    grids = make_grids(center, spread, levels)
    mid = grid_mid_idx(grids)

    # Save original sell-side levels
    original_sell_side = grids[mid:]

    # Price drops well below grid[0]
    new_price = grids[0] * 0.97
    new_grids = shift_buy_side(grids, new_price, spread, levels, mid)

    # Buy side (indices 0..mid-1) should have shifted down
    assert new_grids[0] < grids[0], "Buy floor should have shifted lower"

    # Sell side (indices mid+1..levels) must remain unchanged
    for i in range(mid + 1, levels + 1):
        assert new_grids[i] == grids[i], \
            f"Sell level grids[{i}] changed from {grids[i]} to {new_grids[i]}"

    print("PASS  test_down_move_shifts_buy_only")

# ──────────────────────────────────────────────────────────────────────────────
# Test 4: Up-move shifts sell levels only; buy levels stay anchored.
# ──────────────────────────────────────────────────────────────────────────────
def test_up_move_shifts_sell_only():
    center = 100.0
    spread = 0.05
    levels = 5
    grids = make_grids(center, spread, levels)
    mid = grid_mid_idx(grids)

    # Save original buy-side levels
    original_buy_side = grids[:mid]

    # Price spikes well above grid[-1]
    new_price = grids[-1] * 1.03
    new_grids = shift_sell_side(grids, new_price, spread, levels, mid)

    # Sell side (indices mid+1..levels) should have shifted up
    assert new_grids[-1] > grids[-1], "Sell ceiling should have shifted higher"

    # Buy side (indices 0..mid-1) must remain unchanged
    for i in range(mid):
        assert new_grids[i] == grids[i], \
            f"Buy level grids[{i}] changed from {grids[i]} to {new_grids[i]}"

    print("PASS  test_up_move_shifts_sell_only")

# ──────────────────────────────────────────────────────────────────────────────
# Test 5: No sell target may be below any open position's entry price.
# ──────────────────────────────────────────────────────────────────────────────
def test_no_sell_below_buy_entry():
    center = 100.0
    spread = 0.05
    levels = 5
    grids = make_grids(center, spread, levels)
    mid = grid_mid_idx(grids)

    # Simulate a filled buy position at a relatively high entry price
    filled = {0: {"price": grids[-1] * 0.98, "amount": 1.0}}  # entry near current sell ceiling

    # Spike price so sell side shifts above current grid
    new_price = grids[-1] * 1.01
    new_grids = shift_sell_side(grids, new_price, spread, levels, mid, filled=filled)

    # Every sell level must be >= the entry price of the open position
    entry_price = filled[0]["price"]
    for i in range(mid, levels + 1):
        assert new_grids[i] >= entry_price, \
            f"Sell level grids[{i}]={new_grids[i]} is below entry price {entry_price}"

    # Also verify the no-loss guard in execution
    # (simulates the `if price <= buy_price: continue` guard added to run_grid)
    sell_would_execute = lambda price, bp: price > bp
    assert not sell_would_execute(entry_price * 0.99, entry_price), "Should NOT sell below entry price"
    assert sell_would_execute(entry_price * 1.01, entry_price), "Should sell above entry price"

    print("PASS  test_no_sell_below_buy_entry")

# ──────────────────────────────────────────────────────────────────────────────
# Test 6: Neither trailing sell nor trailing buy fires when not armed.
# ──────────────────────────────────────────────────────────────────────────────
def test_trailing_not_execute_when_not_armed():
    """Armed flag must be True for either trigger to fire."""
    # Sell: never fires when not armed, even if price satisfies both thresholds
    assert not trailing_sell_should_execute(False, 105.0, 104.9, 0.5), \
        "Should NOT execute sell when trailing_sell_active is False"

    # Buy: never fires when not armed, even if price satisfies both thresholds
    assert not trailing_buy_should_execute(False, 95.0, 95.1, 0.5), \
        "Should NOT execute buy when trailing_buy_active is False"

    print("PASS  test_trailing_not_execute_when_not_armed")

# ──────────────────────────────────────────────────────────────────────────────
# Test 7: A sell cell liquidates only its mirrored buy tranche.
# ──────────────────────────────────────────────────────────────────────────────
def test_sell_cell_targets_only_paired_tranche():
    # Exercise production geometry: levels=5 has sell cells 3 and 4.
    import sys, types
    sys.modules.setdefault("requests", types.SimpleNamespace())
    from main import _grid_sell_indices
    levels = 5
    filled = {idx: {"amount": float(idx + 1)} for idx in range(3)}
    # Each sell-cell event selects exactly one tranche (never a cascade).
    first = _grid_sell_indices(filled, 3, levels)
    assert len(first) == 1 and first == [2]
    del filled[first[0]]
    second = _grid_sell_indices(filled, 3, levels)
    assert len(second) == 1 and second == [1]
    del filled[second[0]]
    # The upper actual sell cell reaches the remaining lowest buy tranche.
    assert _grid_sell_indices(filled, 4, levels) == [0]
    # Every buy tranche is reachable through actual sell cells, with no cell 5.
    assert all(any(buy_idx in _grid_sell_indices({buy_idx: {}}, cell, levels)
                   for cell in (3, 4)) for buy_idx in range(3))
    print("PASS  test_sell_cell_targets_only_paired_tranche")

# ──────────────────────────────────────────────────────────────────────────────
# Entry point
# ──────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    test_trailing_sell_armed_and_executes_on_reversal()
    test_trailing_buy_armed_and_executes_on_bounce()
    test_down_move_shifts_buy_only()
    test_up_move_shifts_sell_only()
    test_no_sell_below_buy_entry()
    test_trailing_not_execute_when_not_armed()
    test_sell_cell_targets_only_paired_tranche()
    print("\nAll 7 regression tests passed.")
"""Regression tests for run_grid buy direction and recentering semantics."""
import sys, types
# main imports requests; tests stub it so strategy logic runs in minimal environments.
sys.modules.setdefault("requests", types.SimpleNamespace())
from main import _grid_crossed_buy_indices


def test_upward_touch_executes_each_reached_buy_point():
    grids = [90, 95, 100, 105, 110, 115]
    assert _grid_crossed_buy_indices(grids, 3, {}, 89, 101) == {0, 1, 2}
    assert _grid_crossed_buy_indices(grids, 3, {1: {"price": 95}}, 89, 101) == {0, 2}


def test_downward_touch_defers_buy_points_until_drop_is_over():
    grids = [90, 95, 100, 105, 110, 115]
    assert _grid_crossed_buy_indices(grids, 3, {}, 103, 94) == set()
    assert _grid_crossed_buy_indices(grids, 3, {}, 94, 96) == {1}


def test_recenter_uses_final_grid_for_crossings():
    # This is the grid after run_grid's recenter block. A stale old grid
    # must not be used to derive index eligibility for these final levels.
    final_grids = [80, 90, 100, 110, 120, 130]
    assert _grid_crossed_buy_indices(final_grids, 3, {}, 89, 101) == {1, 2}


def test_run_grid_computes_crossings_after_recenter_block():
    from pathlib import Path
    source = Path("main.py").read_text()
    recenter = source.index("# ── Grid re-centering")
    crossing = source.index("_grid_crossed_buy_indices(", recenter)
    assert crossing > recenter
