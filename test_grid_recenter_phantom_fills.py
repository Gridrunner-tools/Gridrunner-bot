#!/usr/bin/env python3
"""
Regression tests for the grid re-centering "phantom fill" fix.

Bug: run_grid's re-centering block rebuilt `grids` (a full rebuild, or the
old "lowered buy zone" partial in-place loop) but never remapped the `filled`
dict, which is keyed by GRID INDEX. Positions actually bought around $81k
stayed pinned to slot indices that now sit at ~$75k, so the dashboard showed
levels "filled" at prices BTC never traded.

Fix: every re-center rebuilds the whole (monotonic) grid, then remaps
`filled` by REAL buy price into the cell containing that price (clamping
out-of-range positions to the nearest edge cell), and re-keys
state['positions']['grid'] so sell / stop-loss bookkeeping stays consistent.

Pure-function tests only -- no HTTP server, exchange, or license required.
"""
import sys, types
sys.modules.setdefault("requests", types.SimpleNamespace())
from main import (
    _make_grids,
    _grid_find_cell,
    _grid_remap_filled,
    _grid_merge_filled,
    _grid_rekey_positions,
)


def test_find_cell_in_range():
    grids = [90, 95, 100, 105, 110, 115]
    assert _grid_find_cell(grids, 97) == 1     # 95 <= 97 < 100
    assert _grid_find_cell(grids, 90) == 0     # lower bound inclusive
    assert _grid_find_cell(grids, 114.99) == 4
    assert _grid_find_cell(grids, 105) == 3    # exact level -> its own cell


def test_find_cell_clamps_out_of_range():
    grids = [90, 95, 100, 105, 110, 115]
    assert _grid_find_cell(grids, 89.9) == 0   # below grid -> cell 0
    assert _grid_find_cell(grids, 120) == 4    # above grid -> last cell
    assert _grid_find_cell([90], 95) is None   # degenerate grid


def test_downward_recenter_no_phantom_fill_at_untraded_level():
    # Owner's real-world case: a position bought around $81k, then a downward
    # re-center to ~$75k. With the OLD code the filled entry stayed keyed by
    # its stale index, so it rendered at new_grids[stale_idx] == $74,250 -- a
    # price BTC never traded (owner confirmed BTC never went near $75k either).
    # With the fix the entry must land in the cell that CONTAINS its real buy
    # price, so the dashboard never marks an untraded level filled.
    old_filled = {2: {"price": 81000.0, "amount": 0.12}}   # bought at $81k
    new_grids = _make_grids(75000, 0.05, 5)               # downward re-center
    assert new_grids[2] != 81000.0        # stale index 2 sits far below $81k
    assert new_grids[0] <= 81000.0 < new_grids[-1]        # 81k inside new grid
    new_filled, index_map = _grid_remap_filled(new_grids, old_filled)
    assert len(new_filled) == 1
    cell = next(iter(new_filled))
    pos = new_filled[cell]
    assert pos == {"price": 81000.0, "amount": 0.12}
    assert index_map == {2: cell}
    # The displayed cell's price range CONTAINS the real buy price (no phantom).
    assert new_grids[cell] <= 81000.0 < new_grids[cell + 1]


def test_clamp_out_of_range_positions_to_nearest_edge_cell():
    new_grids = _make_grids(75000, 0.05, 5)
    # Above the top level -> clamped to the last (top) cell, deterministically.
    high_filled, high_map = _grid_remap_filled(new_grids, {5: {"price": 90000.0, "amount": 1.0}})
    assert high_filled == {4: {"price": 90000.0, "amount": 1.0}}
    assert high_map == {5: 4}
    # Below the bottom level -> clamped to cell 0, deterministically.
    low_filled, low_map = _grid_remap_filled(new_grids, {0: {"price": 50000.0, "amount": 2.0}})
    assert low_filled == {0: {"price": 50000.0, "amount": 2.0}}
    assert low_map == {0: 0}


def test_remap_places_position_in_cell_containing_real_buy_price():
    # In-range case: buy at $100, re-center grid centered at $105 with 5%
    # spread spans 99.75..110.25, so $100 belongs to cell 0 ([99.75, 101.85)).
    new_grids = _make_grids(105, 0.05, 5)
    assert new_grids[0] <= 100.0 < new_grids[1]
    filled = {4: {"price": 100.0, "amount": 0.25}}        # stale old index 4
    new_filled, index_map = _grid_remap_filled(new_grids, filled)
    assert index_map == {4: 0}
    assert 0 in new_filled
    assert new_filled[0] == {"price": 100.0, "amount": 0.25}
    # Requirement (b): remapped to the cell containing the real buy price.
    assert new_filled[0]["price"] >= new_grids[0] and new_filled[0]["price"] < new_grids[1]


def test_remap_preserves_all_positions_and_index_map():
    grids = _make_grids(1000, 0.05, 5)
    filled = {0: {"price": 955.0, "amount": 1.0}, 3: {"price": 1010.0, "amount": 2.0}}
    new_filled, index_map = _grid_remap_filled(grids, filled)
    assert set(index_map.keys()) == {0, 3}
    assert len(new_filled) == 2
    for i, pos in new_filled.items():
        assert i == _grid_find_cell(grids, pos["price"])


def test_remap_empty_and_bad_entries():
    grids = _make_grids(100, 0.05, 5)
    assert _grid_remap_filled(grids, {}) == ({}, {})
    # Zero/negative price or amount entries are dropped, never displayed.
    new_filled, index_map = _grid_remap_filled(grids, {1: {"price": 0, "amount": 5}, 2: {"price": 100.0, "amount": 0}})
    assert new_filled == {} and index_map == {}


def test_collision_merges_with_price_weighted_basis():
    grids = [100, 120, 140, 160, 180, 200]     # one wide cell [100,120)
    filled = {0: {"price": 100.0, "amount": 0.1}, 1: {"price": 102.0, "amount": 0.2}}
    new_filled, index_map = _grid_remap_filled(grids, filled)
    assert len(new_filled) == 1
    cell = next(iter(new_filled))
    assert cell == 0
    pos = new_filled[cell]
    assert abs(pos["amount"] - 0.3) < 1e-9
    expect_price = (100.0 * 0.1 + 102.0 * 0.2) / 0.3
    assert abs(pos["price"] - expect_price) < 1e-6


def test_merge_filled_seed_entry_and_collision():
    base = {}
    _grid_merge_filled(base, {4: {"price": 95.0, "amount": 0.5}})
    assert base[4] == {"price": 95.0, "amount": 0.5}
    # Second entry onto the same cell merges weighted.
    _grid_merge_filled(base, {4: {"price": 105.0, "amount": 0.5}})
    assert abs(base[4]["amount"] - 1.0) < 1e-9
    assert abs(base[4]["price"] - 100.0) < 1e-6
    # Garbage entries ignored.
    _grid_merge_filled(base, {9: "not-a-dict", 10: {"price": 0, "amount": 3}})
    assert 9 not in base and 10 not in base


def test_rekey_positions_only_grid_strategy():
    positions = [
        {"price": 95, "amount": 1, "grid": 2, "strategy": "Grid"},
        {"price": 95, "amount": 1, "grid": 2, "strategy": "DCA"},
        {"price": 80.5, "amount": 1, "strategy": "AI"},
        {"price": 90, "amount": 1, "side": "buy", "router": "jup"},
    ]
    _grid_rekey_positions(positions, {2: 4, 3: 0})
    assert positions[0]["grid"] == 4          # Grid position re-keyed
    assert positions[1]["grid"] == 2          # non-Grid left untouched
    assert "grid" not in positions[2]
    assert "grid" not in positions[3]
    _grid_rekey_positions(positions, {})      # no-op on empty map


def test_grid_recenter_block_remaps_both_branches():
    # Structure guard (mirrors test_strategy's source-inspection style):
    # the re-centering block must remap `filled` in BOTH branches and must no
    # longer contain the disjoint "lowered buy zone" partial rebuild.
    from pathlib import Path
    source = Path("main.py").read_text()
    recenter = source.index("# ── Grid re-centering")
    crossing = source.index("_grid_crossed_buy_indices(", recenter)
    block = source[recenter:crossing]
    assert block.count("_grid_remap_filled(") == 2      # both branches
    assert block.count("_grid_rekey_positions(") == 2   # both branches
    assert "Grid buy zone lowered" not in block         # partial lowering removed
    assert "new_grids = _make_grids" not in block       # no partial rebuild left
    assert "grids = _make_grids(price, spread, levels)" in block
    # Authoritative grid-loop guard stays byte-identical.
    assert 'while state["running"] and state["strategy"]=="grid"' in source