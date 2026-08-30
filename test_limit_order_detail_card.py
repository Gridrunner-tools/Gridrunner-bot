"""Regression coverage for the owner-requested limit-order detail card
(ARMED/FILLED/REJECTED) + honest /start error handling.

Source-pattern suite mirroring test_pair_chart_history.py: the app is served as
a single-page HTML response, so the checks inspect the embedded dashboard script
(no browser / exchange / server required).

Required by the task:
  A. startBot() surfaces d.error and never toasts success on error.
  B. Detail card renders ARMED / FILLED / REJECTED from /state fields.
  C. Already-running warning source present (log-tail scan).
  D. PR #100 multi-pair card replay path untouched.
  E. No trading/license/webhook/DB changes; the only server change is the
     one-line "Limit order armed" log text (limit price appended).
"""
from pathlib import Path
import re


SOURCE = Path(__file__).with_name("main.py").read_text()


def _slice(start_marker, end_marker):
    i = SOURCE.index(start_marker)
    j = SOURCE.index(end_marker, i)
    return SOURCE[i:j]


# ── A. startBot() honest /start errors ────────────────────────────────────────

def test_startBot_surfaces_server_error_and_never_claims_success():
    fn = _slice('function startBot() {', 'function stopBot() {')
    # Reads the HTTP status as well as the body.
    assert 'return r.json().then(function(d) { return {ok: r.ok, body: d}; });' in fn
    # Non-OK status OR an error body -> error toast, no success claim.
    assert 'if (!res.ok || d.error) {' in fn
    assert 'showToast(d.error || "Failed to start", "error");' in fn
    # 200 but no ok:true (strategy did not start) -> error toast, not success.
    assert 'if (!d.ok) {' in fn
    assert 'showToast("Bot did not start' in fn
    # Success toast only after the ok:true gate.
    i_err = fn.index('if (!res.ok || d.error)')
    i_ok = fn.index('showToast("Bot started: " + sel.strat.toUpperCase(), "info");')
    assert i_err < i_ok, "success toast must come after the error gates"
    assert '"error"' in fn


def test_startBot_catch_handles_unreachable_server():
    fn = _slice('function startBot() {', 'function stopBot() {')
    assert '.catch(function() {' in fn
    assert 'showToast("Failed to start: server unreachable", "error");' in fn


# ── B. Detail card: ARMED / FILLED / REJECTED from state fields ───────────────

def test_status_element_exists_under_config_form():
    # The live status line renders under the (still-visible) config form.
    assert 'id="limit-order-status"' in SOURCE
    assert 'id="limit-order-summary"' in SOURCE
    i_status = SOURCE.index('id="limit-order-status"')
    i_summary = SOURCE.index('id="limit-order-summary"')
    assert i_summary < i_status


def test_detail_card_renders_armed_from_state_fields():
    fn = _slice('function updateLimitOrderStatus(d) {', 'function checkAlreadyRunning(d) {')
    assert 'sel.strat === "limit_buy" || sel.strat === "limit_sell"' in fn
    assert 'ARMED' in fn
    assert 'd.limit_side' in fn and 'd.limit_amount_usdc' in fn
    assert 'd.limit_price' in fn and 'd.limit_order_type' in fn
    assert 'd.paper_trading' in fn
    assert 'd.running' in fn and 'd.strategy' in fn
    # Side-aware comparator: buy waits for price <= limit, sell for >=.
    assert '"\\u2264"' in fn and '"\\u2265"' in fn
    assert 'side === "buy"' in fn


def test_detail_card_renders_filled_and_rejected():
    fn = _slice('function updateLimitOrderStatus(d) {', 'function checkAlreadyRunning(d) {')
    assert 'lt.status === "confirmed"' in fn
    assert 'FILLED @' in fn
    assert 'lt.status === "rejected"' in fn
    assert 'REJECTED:' in fn
    assert 'lt.error' in fn


def test_detail_card_includes_current_market_price():
    fn = _slice('function updateLimitOrderStatus(d) {', 'function checkAlreadyRunning(d) {')
    assert 'd.price' in fn
    assert 'd.price_history_pairs[viewPair]' in fn
    assert 'ph[ph.length - 1].value' in fn
    assert "Current ' + pair" in fn


def test_detail_card_uses_only_state_fields_no_api_calls():
    fn = _slice('function updateLimitOrderStatus(d) {', 'function checkAlreadyRunning(d) {')
    # No server round-trips from the detail card: everything comes from /state.
    assert 'apiFetch(' not in fn
    assert 'fetch(' not in fn


# ── C. Already-running visibility ─────────────────────────────────────────────

def test_already_running_warning_source_present():
    fn = _slice('function checkAlreadyRunning(d) {', 'function refresh() {')
    assert 'd.log' in fn
    assert 'indexOf("Already running")' in fn
    assert 'window._alreadyRunningWarned' in fn
    assert 'showToast("Already running' in fn
    # And the warning line also renders inside the limit-order card.
    card = _slice('function updateLimitOrderStatus(d) {', 'function checkAlreadyRunning(d) {')
    assert 'indexOf("Already running")' in card
    assert 'stop first' in card


def test_refresh_calls_the_new_helpers():
    refresh = _slice('function refresh() {', 'setInterval(refresh, 3000);')
    assert 'updateLimitOrderStatus(d);' in refresh
    assert 'checkAlreadyRunning(d);' in refresh


# ── D. PR #100 multi-pair replay path untouched ───────────────────────────────

def test_pr100_replay_markers_still_present():
    assert '(function(ownerCard, ownerPair, ownerHistory)' in SOURCE
    assert '})(card, pair, ph.slice());' in SOURCE
    assert 'setMultiPairChartData(ownerCard, ownerCard._history || ownerHistory, chartEl)' in SOURCE
    assert 'card._history = ph.slice();' in SOURCE
    assert 'var chart = card._chart' in SOURCE


# ── E. No trading/license/webhook/DB changes; one-line server log change ──────

def test_server_and_trading_core_unchanged():
    # Core server/strategy definitions must still be present and intact.
    assert 'def run_limit_order():' in SOURCE
    assert 'def start_bot(' in SOURCE
    assert 'def do_GET(self):' in SOURCE
    assert 'def run_grid():' in SOURCE
    assert 'def place_order(' in SOURCE
    assert 'def validate_limit_order(' in SOURCE


def test_armed_log_includes_limit_price():
    # Owner follow-up: the "Limit order armed" log line must show the limit
    # price. Log-text-only change — no logic/validation/order-handling edits.
    line = [l for l in SOURCE.splitlines() if 'Limit order armed' in l]
    assert len(line) == 1, f"expected exactly one armed-log line, got {len(line)}"
    assert 'limit=$' in line[0]
    assert 'str(limit_price)' in line[0]
    # limit_price is read from state in run_limit_order (in scope for the log).
    assert 'limit_price = float(state.get("limit_price", 0))' in SOURCE
    # No order-handling lines were altered: the armed/filled/rejected markers
    # that follow the armed log line are all still present.
    assert 'Limit order filled: ' in SOURCE
    assert 'Limit order rejected after execution failure: ' in SOURCE
    assert 'Limit order rejected: ' in SOURCE


def test_no_new_server_endpoint_or_dependency():
    # startBot still talks to the pre-existing endpoints only, and the
    # dependency set is unchanged.
    assert 'apiFetch("/start?' in SOURCE
    assert '"/chart_history"' in SOURCE  # pre-existing endpoint (PR #103)
    # Unchanged stdlib+requests import line (no new dependencies added).
    assert ('import os, json, time, hmac, hashlib, threading, requests, '
            'logging, base64, random, string, math') in SOURCE
    # The detail card makes no server round-trips (verified separately).


def test_no_unresolved_merge_markers():
    assert not re.search(r'^<<<<<<<|^=======|^>>>>>>>', SOURCE, re.MULTILINE)
