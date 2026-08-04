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
