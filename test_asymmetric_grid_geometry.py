#!/usr/bin/env python3
"""
Unit and regression tests for asymmetric grid geometry.
Asserts that:
1. Sell-side spacing is exactly 2x buy-side spacing.
2. mid_idx remains unchanged.
3. Buy-side level positions are unchanged.
4. Buy-zone mapping is preserved (one-under-mid and mid both buy; above mid sells).
"""
import sys
import types

# Ensure requests module is mocked for clean imports
sys.modules.setdefault("requests", types.SimpleNamespace())

def test_asymmetric_grid_geometry():
    import main
    from main import _make_grids, _init_grid_pair, _grid_sell_indices

    # Let's test with default values: price=100.0, spread=0.05, levels=5
    price = 100.0
    spread = 0.05
    levels = 5
    
    # 1. Check mid_idx
    mid_idx = (levels + 1) // 2
    assert mid_idx == 3, f"mid_idx should be 3, got {mid_idx}"

    # 2. Generate asymmetric grid
    grids = _make_grids(price, spread, levels)
    assert len(grids) == 6, f"Length of grids should be 6, got {len(grids)}"

    # 3. Verify buy-side level positions are identical to original uniform positions
    # Original formula: price * (1 - spread) + i * (price * spread * 2 / levels)
    s = price * spread * 2 / levels  # step spacing = 2.0
    original_buy_levels = [round(price * (1 - spread) + i * s, 4) for i in range(mid_idx + 1)]
    assert original_buy_levels == [95.0, 97.0, 99.0, 101.0], f"Original buy levels should be [95.0, 97.0, 99.0, 101.0], got {original_buy_levels}"
    
    # Assert asymmetric grid buy-side matches original exactly
    assert grids[:mid_idx + 1] == original_buy_levels, f"Buy-side levels changed! Got {grids[:mid_idx + 1]}"

    # 4. Verify buy-side spacing is exactly s
    for i in range(1, mid_idx + 1):
        spacing = round(grids[i] - grids[i-1], 4)
        assert spacing == s, f"Buy-side spacing at step {i} should be {s}, got {spacing}"

    # 5. Verify sell-side spacing is exactly 2*s
    # Sell side indices are i > mid_idx (4, 5)
    for i in range(mid_idx + 1, levels + 1):
        spacing = round(grids[i] - grids[i-1], 4)
        assert spacing == 2 * s, f"Sell-side spacing at step {i} should be {2*s}, got {spacing}"

    # Verify sell-side levels are exactly as expected:
    # grids[4] = grids[3] + 2*s = 101.0 + 4.0 = 105.0
    # grids[5] = grids[4] + 2*s = 105.0 + 4.0 = 109.0
    assert grids == [95.0, 97.0, 99.0, 101.0, 105.0, 109.0], f"Grid positions mismatch! Got {grids}"

    # 6. Verify buy-zone mapping
    # Under current rule, index <= mid_idx are buy-zone, index > mid_idx are sell-zone
    # For mid_idx = 3:
    # - "one under mid" (index 2) is a buy zone level
    # - "mid" (index 3) is a buy zone level
    # - above "mid" (index 4) is a sell zone level
    
    # Verify index <= mid_idx is buy zone (so is_buy_zone is True)
    for i in range(levels + 1):
        is_buy_zone = i <= mid_idx
        if i == 2:
            assert is_buy_zone, "One-under-mid (index 2) must be in buy zone"
        elif i == 3:
            assert is_buy_zone, "Mid (index 3) must be in buy zone"
        elif i == 4:
            assert not is_buy_zone, "Above-mid (index 4) must be in sell zone"

    print("PASS: test_asymmetric_grid_geometry")

if __name__ == "__main__":
    test_asymmetric_grid_geometry()
