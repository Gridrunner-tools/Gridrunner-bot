"""Regression coverage for limit-order PnL (buy→sell round trip).

Owner-reported live bug on release 53dc8ee: a LIMIT-BUY fill followed by a
LIMIT-SELL fill showed no PnL anywhere (total PnL $0.00, trade row PnL "—")
because run_limit_order() recorded fills without pnl and never updated
state["pnl"]/state["daily_pnl"].

Fix (limit orders ONLY, mirrors the GRID-SELL convention):
1. LIMIT-BUY fill stores a per-pair position: state["limit_positions"][pair]
   = {"price", "amount", "time"}; buy record_trade unchanged (pnl None).
2. LIMIT-SELL fill pops the pair position; if found, computes
   pnl = round((sell_price - pos["price"]) * amount, 2), adds to
   state["pnl"] and state["daily_pnl"], stamps record_trade with pnl, and
   logs a GRID-SELL-style line.
3. No position on sell: today's exact behavior (pnl None) + WARN log —
   never invent a PnL.

Constraints: PR #100 multi-pair replay path byte-identical; grid/dca/scalp/
arb untouched; no license/webhook/DB/notification changes.
"""
from pathlib import Path
import re


SOURCE = Path(__file__).with_name("main.py").read_text()


def _slice(start_marker, end_marker):
    i = SOURCE.index(start_marker)
    j = SOURCE.index(end_marker, i)
    return SOURCE[i:j]


FILL_BLOCK = _slice('if place_order(pair, side, amount):',
                    'state["running"] = False; state["strategy"] = None')
RUN = _slice('def run_limit_order():', 'STRATEGIES = {')


# ── 1. Buy fill creates a per-pair position ───────────────────────────────────

def test_buy_fill_creates_per_pair_position():
    assert 'positions = state.setdefault("limit_positions", {})' in FILL_BLOCK
    assert 'if side == "buy":' in FILL_BLOCK
    assert 'positions[pair] = {"price": price, "amount": amount, "time": int(time.time())}' in FILL_BLOCK
    # Buy keeps today's record_trade with pnl None (correct for a buy).
    assert 'record_trade("LIMIT-BUY", price, amount, pair=pair)' in FILL_BLOCK
    assert 'state["pnl"] += pnl' not in FILL_BLOCK.split('elif side == "sell":')[0]


# ── 2. Sell fill with position computes and stamps PnL ────────────────────────

def test_sell_fill_with_position_computes_pnl():
    sell = FILL_BLOCK.split('elif side == "sell":')[1]
    assert 'pos = positions.pop(pair, None)' in sell
    assert 'pnl = round((price - pos["price"]) * amount, 2)' in sell
    assert 'state["pnl"] += pnl' in sell
    assert 'state["daily_pnl"] = state.get("daily_pnl", 0) + pnl' in sell
    # record_trade receives the pnl so trades_list/trade row shows it.
    assert 'record_trade("LIMIT-SELL", price, amount, pnl, pair=pair)' in sell
    # GRID-SELL-style log line with bought price + PnL.
    assert 'LIMIT-SELL ' in sell and '(bought $' in sell and 'PnL $' in sell


# ── 3. Sell fill WITHOUT position: unchanged behavior + WARN ──────────────────

def test_sell_fill_without_position_warns_and_never_invents_pnl():
    sell = FILL_BLOCK.split('elif side == "sell":')[1]
    assert 'pos = positions.pop(pair, None)' in sell
    # Isolate the else-branch of `if pos:` (the no-position path).
    no_pos_branch = sell.split('if pos:')[1].split('else:')[1].split('else:')[0]
    assert 'record_trade("LIMIT-SELL", price, amount, pair=pair)' in no_pos_branch  # pnl None
    assert 'no matching limit-buy position to compute PnL for ' in no_pos_branch
    assert '"WARN"' in no_pos_branch
    # Never touch PnL accumulators in the no-position path.
    assert 'state["pnl"]' not in no_pos_branch
    assert 'state["daily_pnl"]' not in no_pos_branch


# ── 4. Backward-compat solo run / unknown side fallback ───────────────────────

def test_legacy_generic_record_kept_for_unknown_side():
    # The generic LIMIT-<SIDE> record remains for any non-buy/sell side.
    assert 'record_trade("LIMIT-"+side.upper(), price, amount, pair=pair)' in FILL_BLOCK
    # The generic filled log line (existing behavior) is preserved.
    assert 'log("Limit order filled: "+side+" "+pair+" @ $"+str(price))' in RUN


# ── 5. Grid path byte-unchanged (owner: stay away from grid) ─────────────────

def test_grid_pnl_convention_untouched():
    # GRID-SELL PnL convention still present verbatim.
    assert 'pnl=(price-buy_price)*sell_amt' in SOURCE
    assert 'state["pnl"]+=pnl' in SOURCE
    assert 'state["daily_pnl"] = state.get("daily_pnl",0)+pnl' in SOURCE
    assert 'record_trade(tag,price,sell_amt,round(pnl,2), pair=pair)' in SOURCE
    # Exactly one set of grid PnL mutations exists (untouched, not duplicated).
    assert SOURCE.count('pnl=(price-buy_price)*sell_amt') == 1


# ── 6. PR #100 multi-pair replay path untouched ───────────────────────────────

def test_pr100_replay_markers_still_present():
    assert '(function(ownerCard, ownerPair, ownerHistory)' in SOURCE
    assert '})(card, pair, ph.slice());' in SOURCE
    assert 'setMultiPairChartData(ownerCard, ownerCard._history || ownerHistory, chartEl)' in SOURCE
    assert 'card._history = ph.slice();' in SOURCE


# ── 7. No license/webhook/DB/notification changes ─────────────────────────────

def test_no_license_webhook_db_or_notification_changes():
    # run_limit_order() contains no send_telegram call (notifications untouched).
    assert 'send_telegram' not in RUN
    # No new server endpoints and no dependency changes.
    assert '"/start?"' in SOURCE and '"/chart_history"' in SOURCE
    assert ('import os, json, time, hmac, hashlib, threading, requests, '
            'logging, base64, random, string, math') in SOURCE


def test_no_unresolved_merge_markers():
    assert not re.search(r'^<<<<<<<|^=======|^>>>>>>>', SOURCE, re.MULTILINE)
