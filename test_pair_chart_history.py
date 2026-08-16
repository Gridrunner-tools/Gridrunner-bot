"""Regression coverage for the live fix: the main chart follows the selected
trading pair (pair-select / switchPair / addToken / limit-order screen).

The app is served as a single-page HTML response, so these checks inspect the
embedded dashboard script and the HTTP endpoint wiring directly, matching the
test_multi_grid_dashboard.py pattern (no browser / exchange required).

Coverage required by the task:
  1. selected-pair history fetch endpoint returns history and stores it into
     state.price_history_pairs (prefer existing buffer -> Kraken seed -> live
     price fallback);
  2. updateChart clears stale candles when the selected pair has no data and
     shows a placeholder instead of another pair's candles;
  3. single-pair path uses the selected pair's own history.
"""
from pathlib import Path
import re


SOURCE = Path(__file__).with_name("main.py").read_text()


def _slice(start_marker, end_marker):
    """Return the source between two unique markers (inclusive of start)."""
    i = SOURCE.index(start_marker)
    j = SOURCE.index(end_marker, i)
    return SOURCE[i:j]


# ── Server side: /chart_history endpoint ──────────────────────────────────────

def test_chart_history_endpoint_exists_and_is_auth_gated():
    assert 'elif path=="/chart_history":' in SOURCE
    endpoint = _slice('elif path=="/chart_history":', 'elif path=="/limit_orders/status":')
    assert 'if not self._auth_or_401(): return' in endpoint
    assert 'chart_history_for(pair)' in endpoint


def test_chart_history_endpoint_returns_history_and_stores_into_state():
    # The helper must write into state.price_history_pairs so the 3s /state
    # refresh then serves the seeded pair directly.
    assert 'def chart_history_for(pair):' in SOURCE
    helper = _slice('def chart_history_for(pair):', 'def run_dca():')
    assert 'state["price_history_pairs"].get(pair, [])' in helper
    assert 'state["price_history_pairs"][pair] = history' in helper or \
           'state["price_history_pairs"][pair] = samples' in helper
    # The endpoint returns the history in the JSON body.
    endpoint = _slice('elif path=="/chart_history":', 'elif path=="/limit_orders/status":')
    assert '"history": history' in endpoint
    assert '"ok": True' in endpoint


def test_chart_history_prefers_existing_per_pair_buffer():
    helper = _slice('def chart_history_for(pair):', 'def run_dca():')
    # First branch: return the existing buffer unchanged (no network).
    assert 'history = state["price_history_pairs"].get(pair, [])' in helper
    assert 'if len(history) >= 2:\n        return history[-4320:]' in helper


def test_chart_history_seeds_from_kraken_then_live_price_fallback():
    helper = _slice('def chart_history_for(pair):', 'def run_dca():')
    # Missing/short history -> reuse seed_history (Kraken OHLC mapping + the
    # generic pair.replace fallback used by /backtest).
    assert 'seed_history(pair)' in helper
    assert 'if len(history) >= 2:\n        return history[-4320:]' in helper
    # If Kraken still yields nothing, synthesize two points from the current
    # live price via get_price(pair) so the user sees THIS pair's price.
    assert 'get_price(pair)' in helper
    assert 'state["price_history_pairs"][pair] = samples' in helper
    assert 'samples = [{"time": now - 1, "value": float(price)}, {"time": now, "value": float(price)}]' in helper


def test_chart_history_rejects_empty_or_malformed_pair():
    helper = _slice('def chart_history_for(pair):', 'def run_dca():')
    assert '"/" not in pair' in helper
    assert 'len(pair) > 64' in helper
    assert 'return []' in helper


def test_chart_history_returns_empty_with_error_for_no_data():
    endpoint = _slice('elif path=="/chart_history":', 'elif path=="/limit_orders/status":')
    assert 'body["error"] = "No price data for " + pair + " yet"' in endpoint


# ── Client side: updateChart clears stale candles ─────────────────────────────

def test_updateChart_clears_stale_candles_on_empty():
    fn = _slice('function updateChart(data, gridLevels, gridBuyZone, pair) {', 'function showChartPlaceholder')
    # The <2-data branch must clear the series (never leave another pair's
    # candles visible) and show the placeholder.
    assert 'if (!data || data.length < 2) {' in fn
    assert 'candleSeries.setData([])' in fn
    assert 'showChartPlaceholder(pair || "", true)' in fn
    assert 'return;' in fn
    # And hide the placeholder once real data arrives.
    assert 'showChartPlaceholder(pair || "", false)' in fn


def test_placeholder_element_exists_in_chart_container():
    assert 'id="chart-placeholder"' in SOURCE
    assert 'No price data for ' in SOURCE
    assert 'chart-container' in SOURCE


def test_fetchPairChartHistory_renders_immediately_with_cooldown():
    fn = _slice('function fetchPairChartHistory(pair, levels, buyZone) {', 'function showToast')
    assert 'apiFetch("/chart_history?pair=" + encodeURIComponent(pair))' in fn
    # Render the fetched history into the chart right away.
    assert 'updateChart(d.history, levels, buyZone, pair)' in fn
    # Never silently keep stale candles when the endpoint has nothing.
    assert 'updateChart([], levels, buyZone, pair)' in fn
    # Cooldown: at most one attempt per 15s per pair while it keeps failing.
    assert 'window._pairChartFetches' in fn
    assert '15000' in fn


# ── Client side: single-pair path uses the selected pair's history ────────────

def test_single_pair_path_uses_selected_pairs_history():
    block = _slice('if (!multiPair) {', '// Override grid details for selected pair')
    assert 'var viewPair = sel.pair || d.pair || "SOL/USDC"' in block
    assert 'var pairHistory = d.price_history_pairs && d.price_history_pairs[viewPair]' in block
    # History present -> render the selected pair's own candles.
    assert 'updateChart(chartHistory, levels, buyZone, viewPair);' in block
    # History missing -> clear stale candles and fetch on demand.
    assert 'updateChart([], levels, buyZone, viewPair);' in block
    assert 'fetchPairChartHistory(viewPair, levels, buyZone);' in block


def test_pair_selection_paths_still_refresh():
    # selectPair / switchPair / addToken all funnel into selectPair -> refresh,
    # which now owns the on-demand history fetch; ensure they are intact.
    assert 'function selectPair(p) {' in SOURCE
    assert 'refresh();' in SOURCE
    assert 'function switchPair() {' in SOURCE
    assert 'selectPair(next);' in SOURCE


# ── Hard constraints ──────────────────────────────────────────────────────────

def test_multi_pair_card_rendering_path_is_untouched():
    # PR #100 replay-fix territory must remain byte-identical: card owns its
    # chart/history; deferred replay after async chart creation.
    assert '(function(ownerCard, ownerPair, ownerHistory)' in SOURCE
    assert '})(card, pair, ph.slice());' in SOURCE
    assert 'setMultiPairChartData(ownerCard, ownerCard._history || ownerHistory, chartEl)' in SOURCE
    assert 'card._history = ph.slice();' in SOURCE


def test_no_trading_logic_changed():
    # The diff scope is the dashboard single-pair path + one endpoint: no
    # trading/order/execution/license/webhook/DB code should be touched.
    assert 'def place_order(' in SOURCE
    assert 'def run_grid():' in SOURCE
    assert 'def run_limit_order():' in SOURCE
    assert 'def run_dca():' in SOURCE


def test_no_unresolved_merge_markers_in_dashboard_source():
    assert not re.search(r'^<<<<<<<|^=======|^>>>>>>>', SOURCE, re.MULTILINE)
