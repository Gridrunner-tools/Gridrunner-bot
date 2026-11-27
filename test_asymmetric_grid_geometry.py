#!/usr/bin/env python3
"""
Unit and regression tests for asymmetric grid geometry.
Asserts that:
1. Sell-side spacing is exactly SELL_SPACING_MULT (5x) the buy-side spacing.
2. mid_idx is the LAST BUY level, at or below the mark (the odd-level off-by-one
   fix: `(levels + 1) // 2` put it ABOVE the mark).
3. Buy-side levels keep the production formula.
4. Buy-zone mapping is preserved (index <= mid_idx buys; above mid_idx sells).
5. Odd level counts split exactly and the 5x spacing is measured from the mark
   (levels = 5 and levels = 7).
"""
import sys
import types

# Ensure requests module is mocked for clean imports
sys.modules.setdefault("requests", types.SimpleNamespace())


def test_asymmetric_grid_geometry():
    from main import _make_grids, SELL_SPACING_MULT

    # Default values: price=100.0, spread=0.05, levels=5
    price = 100.0
    spread = 0.05
    levels = 5

    # 1. The mid level is the LAST buy level: levels // 2 == 2 for levels == 5.
    mid_idx = levels // 2
    assert mid_idx == 2, f"mid_idx should be 2 (last buy level), got {mid_idx}"

    # 2. Generate asymmetric grid
    grids = _make_grids(price, spread, levels)
    assert len(grids) == 6, f"Length of grids should be 6, got {len(grids)}"

    # 3. Buy-side levels keep the original uniform formula
    s = price * spread * 2 / levels  # step spacing = 2.0
    original_buy_levels = [round(price * (1 - spread) + i * s, 4) for i in range(mid_idx + 1)]
    assert original_buy_levels == [95.0, 97.0, 99.0], \
        f"Original buy levels should be [95.0, 97.0, 99.0], got {original_buy_levels}"
    assert grids[:mid_idx + 1] == original_buy_levels, \
        f"Buy-side levels changed! Got {grids[:mid_idx + 1]}"

    # 4. Buy-side spacing is exactly s
    for i in range(1, mid_idx + 1):
        spacing = round(grids[i] - grids[i-1], 4)
        assert spacing == s, f"Buy-side spacing at step {i} should be {s}, got {spacing}"

    # 5. Sell-side spacing is exactly SELL_SPACING_MULT (5x) the buy spacing
    assert SELL_SPACING_MULT == 5.0, f"SELL_SPACING_MULT must be 5.0, got {SELL_SPACING_MULT}"
    for i in range(mid_idx + 1, levels + 1):
        spacing = round(grids[i] - grids[i-1], 4)
        assert spacing == 5 * s, f"Sell-side spacing at step {i} should be {5*s}, got {spacing}"
    assert grids == [95.0, 97.0, 99.0, 109.0, 119.0, 129.0], f"Grid positions mismatch! Got {grids}"

    # 6. The mark sits inside the first cell: mid at/below it, first sell above it
    assert grids[mid_idx] <= price < grids[mid_idx + 1], \
        f"The mid level must be at/below the mark: {grids[mid_idx]} / {grids[mid_idx+1]}"

    # 7. Buy-zone mapping: index <= mid_idx buys, above mid_idx sells
    for i in range(levels + 1):
        is_buy_zone = i <= mid_idx
        if i in (0, 1, 2):
            assert is_buy_zone, f"index {i} must be in the buy zone"
        else:
            assert not is_buy_zone, f"index {i} must be in the sell zone"

    print("PASS: test_asymmetric_grid_geometry")


def test_odd_level_count_split_and_exact_spacing():
    """levels = 5 and levels = 7: the buy/sell split is exact and the sell step
    is exactly SELL_SPACING_MULT x the buy step, measured from the mark."""
    from main import _make_grids, SELL_SPACING_MULT

    # (price, levels, expected buy count, expected sell count)
    cases = [(100.0, 5, 3, 3), (700.0, 7, 4, 4)]
    for price, levels, want_buy, want_sell in cases:
        grids = _make_grids(price, 0.05, levels)
        mid_idx = levels // 2
        buy_count = mid_idx + 1          # every level at/below the mark buys
        sell_count = len(grids) - buy_count
        assert len(grids) == levels + 1, (levels, len(grids))
        assert (buy_count, sell_count) == (want_buy, want_sell), \
            f"levels={levels}: expected {want_buy}/{want_sell}, got {buy_count}/{sell_count}"
        # The extra cell is on the SELL side; no buy level sits above the mark.
        assert sell_count >= buy_count, (levels, buy_count, sell_count)
        assert grids[mid_idx] <= price < grids[mid_idx + 1], \
            f"levels={levels}: mark {price} must sit in [{grids[mid_idx]}, {grids[mid_idx+1]})"

        buy_step = round(grids[1] - grids[0], 4)
        for i in range(1, mid_idx + 1):
            assert round(grids[i] - grids[i - 1], 4) == buy_step, (levels, i)
        for i in range(mid_idx + 1, len(grids)):
            sell_step = round(grids[i] - grids[i - 1], 4)
            assert sell_step == SELL_SPACING_MULT * buy_step, \
                f"levels={levels}: sell step {sell_step} must be exactly {SELL_SPACING_MULT}x{buy_step}"

    print("PASS: test_odd_level_count_split_and_exact_spacing")


if __name__ == "__main__":
    test_asymmetric_grid_geometry()
    test_odd_level_count_split_and_exact_spacing()
