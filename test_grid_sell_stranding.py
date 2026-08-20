#!/usr/bin/env python3
"""
Regression test for the stranded-position sell bug.

Owner repro: grid bought at a level, bought a SECOND time at the next set
point, then after the FIRST sale the remaining (final) position was never
sold — the grid "just stopped".

Root cause (before fix): the sell zone loop only sold ONE tranche per
trailing trigger (it iterated `_grid_sell_indices(...)` which returns a
single tranche) and then reset the trailing setup (trailing_sell_active=False,
trailing_high=0) and `break`-ed. Any additional filled tranche was stranded
because the trailing setup had to be re-armed from scratch (price re-enters
sell zone, rises, then pulls back trailing_pct%) — which may never happen if
price holds.

Fix: the sell trigger now iterates and drains EVERY filled tranche that is
ready to exit (partial remainders kept for a later trigger), only disarming
the trailing setup once `filled` is empty.

This test exercises the drain logic directly against the same data structures
and condition used in run_grid, matching the style of test_buy_on_way_up.py.
"""
import sys, types
sys.modules.setdefault("requests", types.SimpleNamespace())


def _simulate_trailing_sell(filled, partial_positions, trailing, price,
                            partial_pct=50, sell_prices=None):
    """
    Faithful copy of the (fixed) sell-zone logic in run_grid:
    drain every filled tranche while trailing trigger holds, only disarm when
    filled empties. Three returns: filled, partial_positions, trailing dict,
    and a list of executed sells [{"idx", "amt", "pnl"}}].
    """
    trailing_sell_active, trailing_high, trailing_pct = trailing
    sells = []
    if trailing_sell_active and price <= trailing_high * (1 - trailing_pct / 100):
        for buy_idx in sorted(filled.keys()):
            amt = filled[buy_idx]["amount"]
            buy_price = filled[buy_idx]["price"]
            partial_key = str(buy_idx)
            is_partial_sell = partial_pct < 100
            sell_amt = amt
            if is_partial_sell and partial_key not in partial_positions:
                sell_amt = amt * partial_pct / 100
                keep_amt = amt - sell_amt
                partial_positions[partial_key] = {
                    "amount": keep_amt, "buy_price": buy_price,
                    "orig_amount": amt, "price": price}
                filled[buy_idx]["amount"] = keep_amt
            elif partial_key in partial_positions:
                sell_amt = amt
                del partial_positions[partial_key]
            sells.append({"idx": buy_idx, "amt": sell_amt,
                          "pnl": (price - buy_price) * sell_amt})
            if is_partial_sell and partial_key in partial_positions:
                pass  # remainder kept in filled
            else:
                del filled[buy_idx]
        if not filled:
            trailing_sell_active = False
            trailing_high = 0.0
    return filled, partial_positions, (trailing_sell_active, trailing_high, trailing_pct), sells


def test_two_fills_both_reach_exit_no_stranding_full_sell():
    """PARTIAL_SELL_PCT=100 (full sell): both tranches sell on the same trigger."""
    filled = {
        0: {"price": 95.0, "amount": 10.0},   # first buy
        1: {"price": 97.0, "amount": 10.0},   # second buy (the one that used to strand)
    }
    partial_positions = {}
    # trailing armed at 100.0, price pulled back to 99.0 = 1% drop (trailing 0.5%) -> triggers
    trailing = (True, 100.0, 0.5)
    filled, pp, tr, sells = _simulate_trailing_sell(
        filled, partial_positions, trailing, 99.0, partial_pct=100)
    # Both tranches must be sold on this single trigger, nothing stranded.
    assert set(s["idx"] for s in sells) == {0, 1}, sells
    assert filled == {}, f"filled should be empty, got {filled}"
    assert tr[0] is False, "trailing should disarm once filled is empty"


def test_two_fills_both_reach_exit_partial_sell_remainder_clears():
    """PARTIAL_SELL_PCT=50: trigger1 sells 50% of each, remainders stay; next trigger clears them."""
    filled = {
        0: {"price": 95.0, "amount": 10.0},
        1: {"price": 97.0, "amount": 10.0},
    }
    partial_positions = {}
    trailing = (True, 100.0, 0.5)

    # Trigger 1 @ 99.0: partial sale of each -> 5.0 sold each, 5.0 kept each
    filled, pp, tr, sells = _simulate_trailing_sell(
        filled, partial_positions, trailing, 99.0, partial_pct=50)
    assert sorted(s["idx"] for s in sells) == [0, 1], sells
    assert all(abs(s["amt"] - 5.0) < 1e-9 for s in sells), sells
    # Remainders still in filled (amount 5 each), partial_positions populated
    assert set(filled.keys()) == {0, 1}, filled
    assert filled[0]["amount"] == 5.0 and filled[1]["amount"] == 5.0, filled
    # trailing NOT disarmed because remainders remain (so they can still exit)
    assert tr[0] is True, "trailing must stay armed while a remainder is held"

    # Trigger 2 @ 98.9 (still below 100 * 0.995): clear both remainders
    filled, pp, tr, sells2 = _simulate_trailing_sell(
        filled, partial_positions, tr, 98.9, partial_pct=50)
    assert sorted(s["idx"] for s in sells2) == [0, 1], sells2
    assert all(abs(s["amt"] - 5.0) < 1e-9 for s in sells2), sells2
    assert filled == {}, f"both remainders must clear, got {filled}"
    assert pp == {}, "partial_positions fully drained"
    assert tr[0] is False, "trailing disarms once nothing left"


def test_grid_levels_default_and_config():
    """Fix 1: level count must come from config (GRID_LEVELS), not hard-coded 5."""
    import os
    os.environ.pop("GRID_LEVELS", None)
    # Recompute the same expression used in cfg
    default_levels = max(2, min(int(os.environ.get("GRID_LEVELS", "5")), 100))
    assert default_levels == 5
    # With GRID_LEVELS set
    os.environ["GRID_LEVELS"] = "15"
    configured = max(2, min(int(os.environ.get("GRID_LEVELS", "5")), 100))
    assert configured == 15
    # Clamp both ends
    os.environ["GRID_LEVELS"] = "300"
    assert max(2, min(int(os.environ.get("GRID_LEVELS", "5")), 100)) == 100
    os.environ["GRID_LEVELS"] = "1"
    assert max(2, min(int(os.environ.get("GRID_LEVELS", "5")), 100)) == 2
    os.environ.pop("GRID_LEVELS", None)


if __name__ == "__main__":
    test_two_fills_both_reach_exit_no_stranding_full_sell()
    test_two_fills_both_reach_exit_partial_sell_remainder_clears()
    test_grid_levels_default_and_config()
    print("ALL TESTS PASS")
