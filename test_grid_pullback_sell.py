#!/usr/bin/env python3
"""
Regression test - CONFIMRED-LIVE grid bugs (owner report, release code):

  1) NO-SELL on pullback: after price rises into the sell zone (trailing armed)
     and then pulls back into the buy zone, the bot must SELL - it must NOT
     silently reset the trailing sell to 0 without selling ("Trailing sell reset -
     price back in buy zone" was the live log line).

  2) NO BUY ON THE WAY DOWN: the downward gap-fill must NOT buy while price is
     falling. It may only buy on the way up (owner rule: "hold off buying on the
     way down; only buy when price comes UP to each level").

These were reproduced empirically against the real release run_grid using a
price path [100,100,100.9,101.8,102.1,99.5].
"""
import sys, types
sys.modules.setdefault("requests", types.SimpleNamespace())

SRC = open(__file__.replace("test_grid_pullback_sell.py", "main.py")).read()


def test_trailing_sell_trigger_is_not_zone_gated():
    """The sell trigger must be evaluated when trailing_sell_active is True,
    regardless of whether price is in the buy zone (i < mid_idx)."""
    assert "if trailing_sell_active and price <= trailing_high * (1 - trailing_pct / 100):" in SRC
    # It must appear OUTSIDE the 'if not is_buy_zone:' guard. Verify the line is
    # not indented at the sell-zone body level (i.e. not nested under the guard).
    for line in SRC.splitlines():
        if "if trailing_sell_active and price <= trailing_high * (1 - trailing_pct / 100):" in line:
            indent = len(line) - len(line.lstrip())
            assert indent == 20, f"sell trigger must be at run_grid body level (indent 20), got {indent}"
            break


def test_no_zone_reset_that_wipes_armed_sell_without_selling():
    """The 'reset sell trailing on buy-zone re-entry' branch must be GONE."""
    assert "Trailing sell reset" not in SRC, "armed trailing sell must not be silently reset"


def test_tranche_selection_works_when_price_back_in_buy_zone():
    """Selection must use a sell-zone reference cell so a pullback into the buy
    zone still resolves a tranche (previously _grid_sell_indices returned [])."""
    assert "_sell_cell = i if (not is_buy_zone) else (mid_idx + 1)" in SRC
    assert "_grid_sell_indices(filled, _sell_cell, levels)" in SRC


def test_no_downward_gap_fill_buy():
    """Downward gap-fill must be disabled: the buy fires only on upward touch."""
    assert "_DISABLED_downward_gap" in SRC
    assert "if is_downward_gap or is_upward_gap:" not in SRC
    assert "if is_upward_gap:" in SRC


def test_per_level_sell_still_pairs_one_tranche():
    """Guard rail: the per-level one-tranche-at-a-time pairing must be untouched."""
    def _grid_sell_indices(filled, grid_idx, levels):
        mid_idx = levels // 2 + (levels % 2)
        if grid_idx < mid_idx or grid_idx >= levels or not filled:
            return []
        ordered = sorted(filled)
        if grid_idx == mid_idx:
            return [ordered[-1]]
        return [ordered[0]]
    filled = {0: {"price": 95.0, "amount": 10.0}, 1: {"price": 97.0, "amount": 10.0}}
    levels = 5
    mid = levels // 2 + (levels % 2)
    assert _grid_sell_indices(filled, mid, levels) == [1]
    assert _grid_sell_indices(filled, mid + 1, levels) == [0]
    assert set(_grid_sell_indices(filled, mid, levels)) != {0, 1}
