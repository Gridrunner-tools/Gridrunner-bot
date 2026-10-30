#!/usr/bin/env python3
"""
Regression tests: the grid strategy must NEVER sell a position at a loss.

Owner report (2026-10-25, explicitly authorized): a live per-level stop-loss
sold a filled level below its entry and logged "STOP-LOSS". The owner's rule is
absolute - the grid NEVER sells below entry, PERIOD. The stop-loss block in
run_grid() was deleted outright (not gated behind a flag), leaving the trailing
take-profit as the only grid exit, which is hard-guarded by
`if price <= buy_price:` (HOLDING).

Static tests mirror test_grid_no_auto_stop.py (the earlier grid auto-stop
removal regression). The behavioural tests drive the REAL run_grid() against a
scripted price feed with a mocked order router and assert, end to end, that no
sell order is ever sent below the position's entry price.
"""
import sys
import types

# Ensure requests module is mocked for clean imports
sys.modules.setdefault("requests", types.SimpleNamespace())


def _run_grid_body():
    import main
    src = open(main.__file__, encoding="utf-8").read()
    start = src.index("def run_grid(")
    end = src.index("def run_scalp(")
    return src, src[start:end]


# ─────────────────────────── static / structural ───────────────────────────

def test_grid_stop_loss_block_removed():
    """The per-level stop-loss sell must be gone entirely, not flag-gated."""
    _, body = _run_grid_body()
    assert "STOP-LOSS" not in body, "grid stop-loss sell must be removed"
    assert "grid_stop_loss_pct" not in body, "stop-loss threshold must not be read in run_grid"
    assert "sl_buy_idx" not in body and "sl_loss" not in body and "sl_pnl" not in body, \
        "stop-loss variables must be gone"
    assert "stop_pct" not in body, "stop-loss percentage must not be computed in run_grid"


def test_grid_has_exactly_one_sell_path():
    """run_grid must contain exactly one sell order: the take-profit exit."""
    _, body = _run_grid_body()
    sells = [ln for ln in body.splitlines()
             if 'place_order(pair,"sell"' in ln or 'place_order(pair, "sell"' in ln]
    assert len(sells) == 1, "expected exactly one sell path in run_grid, found %d: %r" % (len(sells), sells)
    assert "sell_amt" in sells[0], "the only grid sell must be the take-profit sell"


def test_grid_sell_guarded_above_entry_price():
    """The take-profit sell must stay behind the `price <= buy_price` HOLDING guard."""
    _, body = _run_grid_body()
    lines = body.splitlines()
    guard_i = next(i for i, ln in enumerate(lines) if "if price <= buy_price:" in ln)
    sell_i = next(i for i, ln in enumerate(lines)
                  if 'place_order(pair,"sell"' in ln or 'place_order(pair, "sell"' in ln)
    assert guard_i < sell_i, "the entry-price guard must precede the sell"
    # The guard must bail out (hold) rather than fall through to the sell.
    guard_block = "\n".join(lines[guard_i:sell_i])
    assert "HOLDING" in guard_block, "guard must log HOLDING"
    assert "break" in guard_block, "guard must break out (hold), not sell"
    # A short-circuit guard immediately before the sell is acceptable too, but
    # the guard above must be the one that reads the entry price.
    assert 'buy_price = filled[buy_idx]["price"]' in body, \
        "sell must compare against the level's real entry price"


def test_grid_never_overwrites_entry_price_of_filled_level():
    """Entry price of a filled level is never rewritten (partial sells keep it)."""
    _, body = _run_grid_body()
    assert 'filled[buy_idx]["amount"] = keep_amt' in body, \
        "partial sell must only shrink the amount"
    for ln in body.splitlines():
        assert '["price"] =' not in ln or 'filled' not in ln, \
            "run_grid must never rewrite a filled level's entry price: %r" % ln


def test_grid_authoritative_loop_guard_untouched():
    """Guard rail: the authoritative grid loop guard must be unchanged."""
    src, _ = _run_grid_body()
    assert 'while state["running"] and state["strategy"]=="grid":' in src


# ─────────────────────────── behavioural (real run_grid) ───────────────────────────

class _Clock:
    """Stands in for the time module inside main: no real sleeping."""

    def __init__(self, cycles):
        import time as _t
        self._t = _t
        self.cycles = cycles
        self.sleeps = 0

    def __getattr__(self, name):
        return getattr(self._t, name)

    def sleep(self, _secs=0):
        self.sleeps += 1


def _drive_run_grid(prices, cycles):
    """Run the real run_grid() over a scripted price feed.

    Returns (orders, logs) where orders is a list of (side, price, amount) for
    every order the grid attempted, and logs is a list of (msg, level).
    """
    import main

    pair = "SOL/USDC"
    orders, logs = [], []
    saved_state = dict(main.state)
    saved_cfg = dict(main.cfg)
    saved = (main.get_price, main.place_order, main.get_balance, main.send_telegram,
             main.record_trade, main.seed_history, main.log, main.time)

    script = list(prices)
    clock = _Clock(cycles)
    pos = {"i": 0}

    def fake_get_price(_pair):
        i = min(pos["i"], len(script) - 1)
        pos["i"] += 1
        return script[i]

    def current_price():
        """The scripted price in force right now (last one handed out)."""
        return script[max(0, min(pos["i"] - 1, len(script) - 1))]

    def fake_log(msg, level="INFO"):
        logs.append((msg, level))

    try:
        main.state.clear()
        main.state.update({
            "running": True, "strategy": "grid", "paused": False,
            "pair": pair, "active_pairs": [pair], "grid_pairs": {},
            "positions": [], "pnl": 0.0, "daily_pnl": 0.0, "compound_profit": 0.0,
            "partial_positions": {}, "price_history_pairs": {}, "logs": [],
            "paper_trading": True, "mode": "dex", "strategies": {},
            "config": {"trailing_pct": 0.5, "partial_sell_pct": 50,
                       "base_spread": 0.05, "dynamic_spread": False,
                       "auto_compound": False, "risk_pct": 2, "max_pos": 500,
                       "min_order_usdc": 5, "grid_level_count": 5},
        })
        main.cfg.clear()
        main.cfg.update({
            "trailing_pct": 0.5, "partial_sell_pct": 50, "base_spread": 0.05,
            "dynamic_spread": False, "auto_compound": False, "risk_pct": 2,
            "max_pos": 500, "min_order_usdc": 5, "grid_level_count": 5,
        })
        # Record the price in force when each order is placed.
        def _place_order(pair_, side, amount, grid_idx=None, paper=None):
            orders.append((side, current_price(), amount))
            return True

        main.get_price = fake_get_price
        main.place_order = _place_order
        main.get_balance = lambda *a, **k: 1000.0
        main.send_telegram = lambda *a, **k: None
        main.record_trade = lambda *a, **k: None
        main.seed_history = lambda *a, **k: None
        main.log = fake_log

        def _sleep(_secs=0):
            clock.sleeps += 1
            if clock.sleeps >= cycles:
                main.state["running"] = False
        main.time = types.SimpleNamespace(
            time=clock.time, sleep=_sleep, strftime=clock.strftime,
            localtime=clock.localtime, gmtime=clock.gmtime)

        main.run_grid(sid="test")
    finally:
        (main.get_price, main.place_order, main.get_balance, main.send_telegram,
         main.record_trade, main.seed_history, main.log, main.time) = saved
        main.state.clear()
        main.state.update(saved_state)
        main.cfg.clear()
        main.cfg.update(saved_cfg)

    return orders, logs


def test_grid_holds_through_deep_drop_never_sells_at_loss():
    """Price falls 12% below the entry: the grid must HOLD, never sell at a loss."""
    # init price 100 (base buy @ 100), then a drop well past the old 8% stop.
    orders, logs = _drive_run_grid([100.0, 100.0, 90.0, 88.0, 86.0, 91.0], cycles=6)
    errors = [m for m, lvl in logs if "Grid cycle error" in m]
    assert not errors, "run_grid raised: %r" % errors
    buys = [o for o in orders if o[0] == "buy"]
    assert buys, "the scripted scenario must trade (base buy expected)"
    sells = [o for o in orders if o[0] == "sell"]
    assert sells == [], "grid sold while below entry - loss-taking must be impossible: %r" % (sells,)


def test_grid_still_sells_above_entry_when_trailing_fires():
    """Control: the take-profit exit still works - but only above entry."""
    # 100 entry, rise into the sell zone (>=105 on this grid), pull back >0.5%.
    orders, logs = _drive_run_grid([100.0, 102.0, 106.0, 108.0, 107.0], cycles=5)
    errors = [m for m, lvl in logs if "Grid cycle error" in m]
    assert not errors, "run_grid raised: %r" % errors
    sells = [o for o in orders if o[0] == "sell"]
    assert sells, "the trailing take-profit must still sell (control scenario)"
    for _side, price, _amt in sells:
        assert price > 100.0, "every sell must be above the 100.0 entry, got %r" % (price,)
