#!/usr/bin/env python3
"""Realized-fill PnL + the three review fixes (owner order 2026-11-27).

A. The recorded buy price, the PnL ledger and the never-sell-at-loss guard book
   off the amounts the Raydium (slippage_bps 300) and Jupiter (slippageBps 100)
   swaps actually executed, fees included - never the observed mark.
B. A shift that cannot happen at the 100-level cap says so instead of claiming
   a shift.
D. The 5-second duplicate guard keys on the grid level, so two sells at
   different levels in one tick are both allowed.
"""
import sys
import types

sys.modules.setdefault("requests", types.SimpleNamespace())


# ───────────────────────── A. realized fill price ──────────────────────────

def test_realized_price_buy_and_sell_include_fees():
    from main import _realized_price
    # BUY: USDC in / token out. The venue's 300bps slippage and fees are already
    # inside the out amount, so the price is simply in/out.
    assert round(_realized_price("BUY", 100.0, 0.0016490), 6) == round(100.0 / 0.0016490, 6)
    # SELL: token in / USDC out.
    assert round(_realized_price("SELL", 0.0016490, 98.5), 6) == round(98.5 / 0.0016490, 6)
    # Fees are inside the amounts: a worse fill gives a worse price, no double count.
    assert _realized_price("BUY", 100.0, 0.0016) > _realized_price("BUY", 100.0, 0.00165)
    assert _realized_price("SELL", 1.0, 99.0) < _realized_price("SELL", 1.0, 101.0)
    # Unusable amounts never invent a price.
    for bad in ((0, 1.0), (1.0, 0), (None, 1.0), ("x", 1.0)):
        assert _realized_price("BUY", bad[0], bad[1]) is None
    print("PASS  test_realized_price_buy_and_sell_include_fees")


def test_venue_amounts_prefer_wallet_deltas_both_venues():
    import main
    logs = []
    saved = (main.log, main._token_balance_for_mint)
    main.log = lambda msg, level="INFO": logs.append((msg, level))
    try:
        # Raydium response shape: quote said 100 USDC -> 0.00165 BTC (slippage_bps
        # 300); the wallet actually moved 100 USDC out and 0.0016490 BTC in.
        # (_post_swap_amounts re-reads the balances AFTER the swap.)
        seq = {"USDC": [100.0], "BTC": [0.0016490]}
        main._token_balance_for_mint = lambda mint: seq[mint].pop(0)
        moved_in, moved_out = main._venue_swap_amounts(
            "Raydium", 200.0, 0.0, "USDC", "BTC", 100.0, 0.00165)
        assert round(moved_in, 6) == 100.0
        assert round(moved_out, 7) == 0.0016490
        assert logs == [], "deltas are realized - no WARN expected"

        # Jupiter response shape: outAmount 0.00165 for 50 USDC, but the wallet
        # only received 0.00082 - the delta wins again.
        seq = {"USDC": [50.0], "BTC": [0.50082]}
        main._token_balance_for_mint = lambda mint: seq[mint].pop(0)
        moved_in, moved_out = main._venue_swap_amounts(
            "Jupiter", 100.0, 0.5, "USDC", "BTC", 50.0, 0.00082)
        assert round(moved_in, 6) == 50.0
        assert round(moved_out, 6) == 0.00082

        # No balance data at all: use the quote, and say it is quoted-not-realized.
        main._token_balance_for_mint = lambda mint: None
        quote = main._venue_swap_amounts("Jupiter", None, None, "USDC", "BTC", 100.0, 0.00165)
        assert quote == (100.0, 0.00165)
        assert any("quoted-not-realized" in m and lvl == "WARN" for m, lvl in logs)
    finally:
        main.log, main._token_balance_for_mint = saved
    print("PASS  test_venue_amounts_prefer_wallet_deltas_both_venues")


def test_record_realized_fill_books_amounts_and_falls_back_loudly():
    import main
    logs = []
    saved_log = main.log
    saved_fill = dict(main._LAST_FILL)
    main.log = lambda msg, level="INFO": logs.append((msg, level))
    try:
        # Raydium live buy, fees inside: 100 USDC in, 0.0016490 BTC out.
        price = main._record_realized_fill("BUY", "USDC", "BTC", 100.0, 0.0016490, 100.0, "Raydium")
        assert round(price, 6) == round(100.0 / 0.0016490, 6)
        assert main._LAST_FILL["source"] == "realized"
        booked_price, booked_amt = main._booked_fill("BUY", 100.0, 0.00165)
        assert round(booked_price, 6) == round(100.0 / 0.0016490, 6), "must book the fill, not the mark"
        assert round(booked_amt, 7) == 0.0016490, "must book the tokens actually received"

        # Jupiter live sell: 0.0016490 BTC in, 98.5 USDC out.
        price = main._record_realized_fill("SELL", "BTC", "USDC", 0.0016490, 98.5, 100.0, "Jupiter")
        assert round(price, 4) == round(98.5 / 0.0016490, 4)
        sell_price, sold_amt = main._booked_fill("SELL", 100.0, 0.0016490)
        assert round(sell_price, 4) == round(98.5 / 0.0016490, 4)
        assert round(sold_amt, 7) == 0.0016490

        # A response with no amounts keeps the observed price - loudly.
        logs[:] = []
        price = main._record_realized_fill("BUY", "USDC", "BTC", None, None, 123.0, "Raydium")
        assert price == 123.0
        assert main._LAST_FILL["source"] == "observed"
        assert any("realized price unavailable" in m and lvl == "WARN" for m, lvl in logs)

        # Paper mode keeps the observed price and says so.
        logs[:] = []
        price = main._record_realized_fill("BUY", "USDC", "BTC", 100.0, 0.0016490, 123.0,
                                          "Raydium", paper=True)
        assert price == 123.0
        assert any("realized price unavailable" in m and lvl == "WARN" for m, lvl in logs)

        # A stale fill from an earlier order is never reused.
        main._LAST_FILL["time"] = 0
        assert main._booked_fill("BUY", 55.0, 1.0) == (55.0, 1.0)
    finally:
        main.log = saved_log
        main._LAST_FILL.clear()
        main._LAST_FILL.update(saved_fill)
    print("PASS  test_record_realized_fill_books_amounts_and_falls_back_loudly")


# ─────────────── A. behavioural: booking + the no-loss guard ───────────────

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


def _drive(prices, cycles, buy_fill=None, sell_fill=None, preset_gs=None, spread=0.05):
    """Run the real run_grid() over a scripted price feed.

    `buy_fill` / `sell_fill` are (in_amount, out_amount) tuples handed to
    _record_realized_fill for the matching side, so the strategy books a REAL
    venue fill. Returns (orders, logs, result) with orders as
    (side, price, amount, grid) and result a snapshot taken before state is
    restored.
    """
    import main

    pair = "SOL/USDC"
    orders, logs = [], []
    saved_state = dict(main.state)
    saved_cfg = dict(main.cfg)
    saved_fill = dict(main._LAST_FILL)
    saved = (main.get_price, main.place_order, main.get_balance, main.send_telegram,
             main.record_trade, main.seed_history, main.log, main.time)

    script = list(prices)
    clock = _Clock(cycles)
    pos = {"i": 0}
    result = {}

    def fake_get_price(_pair):
        i = min(pos["i"], len(script) - 1)
        pos["i"] += 1
        return script[i]

    def current_price():
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
            "config": {"trailing_pct": 0.5, "partial_sell_pct": 100,
                       "base_spread": spread, "dynamic_spread": False,
                       "auto_compound": False, "risk_pct": 2, "max_pos": 500,
                       "min_order_usdc": 5, "grid_level_count": 5},
        })
        main.cfg.clear()
        main.cfg.update({
            "trailing_pct": 0.5, "partial_sell_pct": 100, "base_spread": spread,
            "dynamic_spread": False, "auto_compound": False, "risk_pct": 2,
            "max_pos": 500, "min_order_usdc": 5, "grid_level_count": 5,
        })
        main._LAST_FILL.clear()
        if preset_gs:
            main.state["grid_pairs"][pair] = preset_gs

        def _place_order(pair_, side, amount, grid_idx=None, paper=None):
            price = current_price()
            orders.append((side, price, amount, grid_idx))
            fill = buy_fill if side == "buy" else sell_fill
            if fill:
                main._record_realized_fill(side.upper(), "USDC" if side == "buy" else "SOL",
                                           "SOL" if side == "buy" else "USDC",
                                           fill[0], fill[1], price, "Raydium")
            else:
                main._LAST_FILL.clear()
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
        gp = main.state["grid_pairs"].get(pair, {})
        result.update({
            "positions": [dict(p) for p in main.state.get("positions", [])],
            "pnl": main.state.get("pnl", 0.0),
            "filled": {k: dict(v) for k, v in (gp.get("filled") or {}).items()},
            "grids": list(gp.get("grids") or []),
        })
    finally:
        (main.get_price, main.place_order, main.get_balance, main.send_telegram,
         main.record_trade, main.seed_history, main.log, main.time) = saved
        main._LAST_FILL.clear()
        main._LAST_FILL.update(saved_fill)
        main.state.clear()
        main.state.update(saved_state)
        main.cfg.clear()
        main.cfg.update(saved_cfg)

    return orders, logs, result


def test_buy_price_booked_from_realized_fill_not_mark():
    """The recorded buy price is the fill (fees in), never the observed mark."""
    # Base buy observed at $100; the venue filled 50.8 USDC for 0.5 token -> $101.6.
    orders, logs, result = _drive([100.0, 100.0], cycles=2, buy_fill=(50.8, 0.5))
    errors = [m for m, _ in logs if "Grid cycle error" in m]
    assert not errors, "run_grid raised: %r" % (errors,)
    assert orders and orders[0][0] == "buy"
    positions = result["positions"]
    assert positions, "the base buy must record a position"
    assert abs(positions[0]["price"] - 101.6) < 1e-6, \
        "position must be booked at the realized fill $101.6, got %r" % (positions[0]["price"],)
    assert abs(positions[0]["amount"] - 0.5) < 1e-9, "must book the tokens actually received"
    filled = result["filled"]
    assert filled and all(abs(pos["price"] - 101.6) < 1e-6 for pos in filled.values()), \
        "filled levels must carry the realized entry price: %r" % (filled,)
    print("PASS  test_buy_price_booked_from_realized_fill_not_mark")


def test_guard_holds_when_mark_says_profit_and_realized_fill_says_loss():
    """Observed entry $100, realized entry $101.6, pullback to $101.45: the mark
    looks profitable but the realized fill is underwater, so the guard HOLDs."""
    orders, logs, _ = _drive([100.0, 100.0, 102.0, 101.45], cycles=4,
                             buy_fill=(50.8, 0.5), spread=0.01)
    errors = [m for m, _ in logs if "Grid cycle error" in m]
    assert not errors, "run_grid raised: %r" % (errors,)
    sells = [o for o in orders if o[0] == "sell"]
    assert sells == [], "guard must HOLD - realized entry $101.6 is above the $101.45 sell: %r" % (sells,)
    assert any("HOLDING" in m for m, _ in logs), "the hold must be logged"
    print("PASS  test_guard_holds_when_mark_says_profit_and_realized_fill_says_loss")


def test_sell_pnl_booked_from_realized_sell_fill():
    """Same scenario with a profitable realized entry: the sell PnL comes from
    the realized sell price, not the mark."""
    # Buy realized at $99.0 (49.5 USDC for 0.5 token); mark pulls back to $101.45
    # but the venue pays $101.0 for the 0.5 token.
    orders, logs, result = _drive([100.0, 100.0, 102.0, 101.45], cycles=4,
                                  buy_fill=(49.5, 0.5), sell_fill=(0.5, 50.5), spread=0.01)
    errors = [m for m, _ in logs if "Grid cycle error" in m]
    assert not errors, "run_grid raised: %r" % (errors,)
    sells = [o for o in orders if o[0] == "sell"]
    assert sells, "a profitable realized entry must still sell (control)"
    realized_sell = 50.5 / 0.5          # $101.0, not the $101.45 mark
    expected = (realized_sell - 99.0) * 0.5
    assert abs(result["pnl"] - expected) < 1e-6, \
        "PnL must come from the realized fill ($%.4f), got $%.4f" % (expected, result["pnl"])
    print("PASS  test_sell_pnl_booked_from_realized_sell_fill")


# ─────────────── B. a shift that cannot happen says so ───────────────

def test_ladder_cap_shift_logs_the_truth():
    import main
    # A full ladder with every buy level filled: nothing is recyclable, so no
    # shift is possible in either direction.
    levels = main.MAX_GRID_LEVELS - 1
    grids = [round(100.0 - 0.5 * i, 4) for i in range(levels + 1)]
    mid_idx = 5
    filled = {i: {"price": grids[i], "amount": 0.1} for i in range(mid_idx + 1)}
    base_gs = {
        "grids": grids, "mid_idx": mid_idx, "filled": dict(filled),
        "trailing_pct": 0.5, "trailing_high": 0.0, "trailing_sell_active": False,
        "trailing_low": 0.0, "trailing_buy_active": False, "dip_occurred": False,
        "price": 100.0, "previous_price": 100.0, "levels": levels, "spread": 0.05,
        "last_price": 100.0, "drop_through_active": False,
        "drop_through_low": 100.0, "drop_through_levels": [], "seeded": True,
    }
    # Downward: price far below grids[0] (94.5) and the ladder is at the cap.
    orders, logs, _ = _drive([80.0, 80.0], cycles=2, preset_gs=dict(base_gs))
    errors = [m for m, _ in logs if "Grid cycle error" in m]
    assert not errors, "run_grid raised: %r" % (errors,)
    assert any("no room to shift down" in m for m, _ in logs), \
        "the cap must be reported: %r" % ([m for m, _ in logs if "shift" in m.lower()],)
    assert not any("Grid shifted down" in m for m, _ in logs), \
        "a shift that did not happen must never be claimed"

    # Upward: price far above grids[-1] and the ladder is at the cap.
    up_gs = dict(base_gs)
    up_gs["filled"] = dict(filled)
    orders, logs, _ = _drive([130.0, 130.0], cycles=2, preset_gs=up_gs)
    errors = [m for m, _ in logs if "Grid cycle error" in m]
    assert not errors, "run_grid raised: %r" % (errors,)
    assert any("no room to shift up" in m for m, _ in logs), \
        "the cap must be reported: %r" % ([m for m, _ in logs if "shift" in m.lower()],)
    assert not any("Grid shifted up" in m for m, _ in logs), \
        "a shift that did not happen must never be claimed"
    print("PASS  test_ladder_cap_shift_logs_the_truth")


# ─────────────── D. duplicate guard keys on the level ───────────────

def test_duplicate_guard_allows_two_sells_at_different_levels():
    import main
    saved = (main.jupiter_swap, main.get_price, main.authorize_trade, main.log)
    calls = []
    main.jupiter_swap = lambda *a, **k: (calls.append(a), (True, 1.0))[1]
    main.get_price = lambda *a, **k: 100.0
    main.authorize_trade = lambda *a, **k: (True, "")
    main.log = lambda *a, **k: None
    saved_mode = main.state.get("mode")
    saved_chain = main.state.get("chain")
    main.state["mode"] = "dex"
    main.state["chain"] = "solana"
    main._last_order_key = None
    main._last_order_time = 0
    try:
        # Two sells at adjacent levels in the same tick: both must go through.
        assert main.place_order("SOL/USDC", "sell", 0.5, grid_idx=3, paper=True) is True
        assert main.place_order("SOL/USDC", "sell", 0.5, grid_idx=4, paper=True) is True
        assert len(calls) == 2, "a different level is not a duplicate: %r" % (calls,)
        # The same level twice inside 5s is still a duplicate.
        assert main.place_order("SOL/USDC", "sell", 0.5, grid_idx=4, paper=True) is False
        assert len(calls) == 2
    finally:
        (main.jupiter_swap, main.get_price, main.authorize_trade, main.log) = saved
        main.state["mode"] = saved_mode
        main.state["chain"] = saved_chain
        main._last_order_key = None
        main._last_order_time = 0
    print("PASS  test_duplicate_guard_allows_two_sells_at_different_levels")


def test_grid_sell_carries_its_level_index():
    import main
    src = open(main.__file__, encoding="utf-8").read()
    body = src[src.index("def run_grid("):src.index("def run_scalp(")]
    assert 'place_order(pair,"sell",sell_amt, grid_idx=buy_idx, paper=paper)' in body, \
        "the grid sell must pass its level index into the duplicate guard"
    print("PASS  test_grid_sell_carries_its_level_index")


def test_realized_fills_all():
    for fn in (test_realized_price_buy_and_sell_include_fees,
               test_venue_amounts_prefer_wallet_deltas_both_venues,
               test_record_realized_fill_books_amounts_and_falls_back_loudly,
               test_buy_price_booked_from_realized_fill_not_mark,
               test_guard_holds_when_mark_says_profit_and_realized_fill_says_loss,
               test_sell_pnl_booked_from_realized_sell_fill,
               test_ladder_cap_shift_logs_the_truth,
               test_duplicate_guard_allows_two_sells_at_different_levels,
               test_grid_sell_carries_its_level_index):
        fn()


if __name__ == "__main__":
    test_realized_fills_all()
