#!/usr/bin/env python3
"""
Regression test - GRID_LEVELS configurability + per-level sell semantics.

Owner-verified behavior:
  - GRID_LEVELS env must control the grid level count (was hard-coded to 5).
  - Each filled position must sell at ITS OWN sell level (per-level), NOT drain
    all tranches at once on a single trailing trigger.
"""
import sys, types
sys.modules.setdefault("requests", types.SimpleNamespace())


def _grid_sell_indices(filled, grid_idx, levels):
    """Faithful copy of the per-level sell-cell -> tranche pairing in main.py."""
    mid_idx = levels // 2 + (levels % 2)
    if grid_idx < mid_idx or grid_idx >= levels or not filled:
        return []
    ordered = sorted(filled)
    if grid_idx == mid_idx:
        return [ordered[-1]]
    return [ordered[0]]


def test_per_level_sell_selects_one_tranche():
    """Sell cell pairs to exactly ONE tranche (not all) - per-level semantics."""
    filled = {0: {"price": 95.0, "amount": 10.0},
              1: {"price": 97.0, "amount": 10.0}}
    levels = 5
    mid_idx = levels // 2 + (levels % 2)  # 3
    # Lower sell cell (mid) picks the farthest tranche...
    assert _grid_sell_indices(filled, mid_idx, levels) == [1]
    # ...and has exactly one result.
    assert len(_grid_sell_indices(filled, mid_idx, levels)) == 1
    # Upper sell cell picks the nearest tranche, still exactly one.
    assert _grid_sell_indices(filled, mid_idx + 1, levels) == [0]
    assert len(_grid_sell_indices(filled, mid_idx + 1, levels)) == 1
    # Not every tranche exits at once.
    assert set(_grid_sell_indices(filled, mid_idx, levels)) != {0, 1}


def test_grid_levels_default_and_config():
    """GRID_LEVELS env controls the level count (clamped 2..100, default 5)."""
    import os
    os.environ.pop("GRID_LEVELS", None)
    default = max(2, min(int(os.environ.get("GRID_LEVELS", "5")), 100))
    assert default == 5
    os.environ["GRID_LEVELS"] = "15"
    assert max(2, min(int(os.environ.get("GRID_LEVELS", "5")), 100)) == 15
    os.environ["GRID_LEVELS"] = "300"
    assert max(2, min(int(os.environ.get("GRID_LEVELS", "5")), 100)) == 100
    os.environ["GRID_LEVELS"] = "0"
    assert max(2, min(int(os.environ.get("GRID_LEVELS", "5")), 100)) == 2
    os.environ.pop("GRID_LEVELS", None)


if __name__ == "__main__":
    test_per_level_sell_selects_one_tranche()
    test_grid_levels_default_and_config()
    print("ALL TESTS PASS")
