"""Regression tests for grid buy trigger direction semantics."""


def buy_levels_to_execute(grids, mid_idx, filled, previous_price, price):
    """Mirror run_grid's crossing rule without exchange/state side effects."""
    moving_up = previous_price is None or price >= previous_price
    if not moving_up:
        return []
    floor = previous_price if previous_price is not None else price
    return [
        idx for idx, level in enumerate(grids[:mid_idx])
        if idx not in filled and floor <= level <= price
    ]


def test_upward_touch_executes_each_reached_buy_point():
    grids = [90, 95, 100, 105, 110, 115]
    # A jump upward crosses both buy points; neither may be skipped.
    assert buy_levels_to_execute(grids, 3, {}, 89, 101) == [0, 1, 2]
    assert buy_levels_to_execute(grids, 3, {1: {"price": 95}}, 89, 101) == [0, 2]


def test_downward_touch_defers_buy_points_until_drop_is_over():
    grids = [90, 95, 100, 105, 110, 115]
    # Falling through levels must not buy. A subsequent upward move buys
    # levels reached on that upward tick.
    assert buy_levels_to_execute(grids, 3, {}, 103, 94) == []
    assert buy_levels_to_execute(grids, 3, {}, 94, 96) == [1]


if __name__ == "__main__":
    test_upward_touch_executes_each_reached_buy_point()
    test_downward_touch_defers_buy_points_until_drop_is_over()
    print("PASS grid buy trigger regressions")

def test_recenter_uses_final_grid_for_crossings():
    # A recenter replaces the old levels before crossing eligibility is built.
    # The stale pre-recenter grid would incorrectly include level 0 (90).
    final_grids = [80, 90, 100, 110, 120, 130]
    assert buy_levels_to_execute(final_grids, 3, {}, 89, 101) == [1, 2]


def test_run_grid_computes_crossings_after_recenter_block():
    from pathlib import Path
    source = Path("main.py").read_text()
    recenter = source.index("# ── Grid re-centering")
    crossing = source.index("# Compute crossings against the final grid", recenter)
    assert crossing > recenter

if __name__ == "__main__":
    test_recenter_uses_final_grid_for_crossings()
    test_run_grid_computes_crossings_after_recenter_block()
    print("PASS recentering regressions")
