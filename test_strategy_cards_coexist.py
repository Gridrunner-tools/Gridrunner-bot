"""Regression coverage: one details card per running strategy (grid + AI coexist).

The owner reported that when a grid is running and AI Trading is then opened
(paper), the AI-trading card REPLACES the grid's details card instead of
showing both. The dashboard must render a details card for every running
strategy in `state["strategies"]`, driven by per-strategy running flags — not
by the global "most recently started" `d.strategy` field.

These source-pattern checks inspect the embedded dashboard script in main.py
(the app is served as a single-page HTML response), mirroring
test_multi_grid_dashboard.py / test_limit_order_detail_card.py.
"""
from pathlib import Path
import re


SOURCE = Path(__file__).with_name("main.py").read_text()


def _slice(start_marker, end_marker):
    i = SOURCE.index(start_marker)
    j = SOURCE.index(end_marker, i)
    return SOURCE[i:j]


def test_selectStrat_does_not_hide_grid_card_for_ai():
    # Selecting AI Trading must not toggle the grid details card to "none".
    fn = _slice("function selectStrat(s) {", "function selectPair(p) {")
    assert 'document.getElementById("grid-details-card").style.display = "block";' in fn
    # The old single-slot toggle is gone.
    assert 'grid-details-card").style.display = s=="ai_trading"?"none":"block"' not in fn
    # The AI live-status card is still surfaced during AI configuration.
    assert 'document.getElementById("ai-trading-status-card").style.display = "block"' in fn


def test_grid_running_flag_computed_from_registry():
    refresh = _slice("function refresh() {", "setInterval(refresh, 3000);")
    assert "var gridRunning = false;" in refresh
    assert 'st.type === "grid" && st.running' in refresh
    assert "gridRunning = true;" in refresh
    # The grid card gate uses the registry flag, not the most-recent strategy.
    assert "if (gridRunning && d.grid_levels && d.grid_levels.length >= 2)" in refresh
    assert 'if (d.strategy === "grid" && d.grid_levels' not in refresh


def test_grid_card_prefers_running_grid_own_pair_data():
    refresh = _slice("function refresh() {", "setInterval(refresh, 3000);")
    assert "var gridStrategyPair = null;" in refresh
    assert "gridStrategyPair = st.pair;" in refresh
    assert "var gridPairData = (gridStrategyPair && d.grid_pairs && d.grid_pairs[gridStrategyPair]) || null;" in refresh
    assert "d.grid_levels = gridPairData.grids;" in refresh
    assert "d.grid_filled = gridPairData.filled || {};" in refresh
    assert "d.grid_trailing_active = gridPairData.trailing_sell_active || false;" in refresh


def test_ai_card_still_driven_by_running_flag_pr127():
    # PR #127 behavior is preserved: the AI status card follows aiRunning.
    refresh = _slice("function refresh() {", "setInterval(refresh, 3000);")
    assert 'st.type === "ai_trading" && st.running' in refresh
    assert 'aiStatusCard.style.display = (aiRunning || sel.strat === "ai_trading") ? "block" : "none"' in refresh


def test_pr100_multi_pair_replay_path_unchanged():
    assert "(function(ownerCard, ownerPair, ownerHistory)" in SOURCE
    assert "})(card, pair, ph.slice());" in SOURCE
    assert "setMultiPairChartData(ownerCard, ownerCard._history || ownerHistory, chartEl)" in SOURCE
    assert "card._history = ph.slice();" in SOURCE
    assert "var chart = card._chart" in SOURCE


def test_no_unresolved_merge_markers():
    assert not re.search(r"^<<<<<<<|^=======|^>>>>>>>", SOURCE, re.MULTILINE)

def test_per_strategy_panel_grid_layout():
    """Each running strategy owns a panel: grid chart+card is one grid cell,
    AI chart+card is the sibling cell. Grid panel comes first (left), AI panel
    second (right); extras (summary cards) stay below the panel grid."""
    assert '<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(340px,1fr));gap:16px;align-items:start;width:100%" id="single-chart-row">' in SOURCE
    assert 'id="grid-panel"' in SOURCE
    assert 'id="ai-panel"' in SOURCE
    # Grid panel (chart + card) precedes the AI panel in DOM order
    assert SOURCE.index('id="grid-panel"') < SOURCE.index('id="ai-panel"')
    # The AI details card lives INSIDE the AI panel, after the AI chart
    assert SOURCE.index('id="ai-panel"') < SOURCE.index('id="ai-chart-container"') < SOURCE.index('id="ai-trading-status-card"')
    # Grid details card stays inside the grid panel, after the grid chart
    assert SOURCE.index('id="grid-panel"') < SOURCE.index('id="chart-container"') < SOURCE.index('id="grid-details-card"')
    # Summary cards (extras) come after both panels
    assert SOURCE.index('id="ai-trading-status-card"') < SOURCE.index('id="summary-cards"')
def test_ai_panel_has_own_chart_container():
    """The AI card must NOT reuse the grid chart: it has its own chart container
    (#ai-chart-container) that is hidden unless AI Trading is running."""
    assert '<div id="ai-chart-container" style="display:none;' in SOURCE
    refresh = _slice("function refresh() {", "setInterval(refresh, 3000);")
    assert 'var aiChartEl = document.getElementById("ai-chart-container");' in refresh
    assert 'if (aiChartEl) aiChartEl.style.display = aiRunning ? "block" : "none";' in refresh
    assert 'var aiPanel = document.getElementById("ai-panel");' in refresh
    assert 'if (aiPanel) aiPanel.style.display = (aiRunning || sel.strat === "ai_trading") ? "flex" : "none";' in refresh
def test_ai_chart_fed_from_running_ai_pair():
    """The AI chart is fed from the running AI strategy's own pair history
    (price_history_pairs), not from the grid's chart feed."""
    refresh = _slice("function refresh() {", "setInterval(refresh, 3000);")
    assert "var aiStrategyPair = null;" in refresh
    assert 'if (st && st.type === "ai_trading" && st.running && !aiStrategyPair) aiStrategyPair = st.pair;' in refresh
    assert "var aiChartPair = aiStrategyPair || d.pair || \"SOL/USDC\";" in refresh
    assert "(d.price_history_pairs && d.price_history_pairs[aiChartPair])" in refresh
    assert "updateAiChart(aiChartHist, aiChartPair);" in refresh
def test_updateAiChart_renders_into_ai_chart_container():
    assert "function updateAiChart(data, pair) {" in SOURCE
    assert 'document.getElementById("ai-chart-container")' in SOURCE
    assert "LightweightCharts.createChart(el, {" in SOURCE
    assert "aggregateCandles(hist, 60)" in SOURCE
    assert "aiCandleSeries.setData(candles)" in SOURCE
def test_selectStrat_still_surfaces_ai_panel():
    fn = _slice("function selectStrat(s) {", "function selectPair(p) {")
    assert 'document.getElementById("ai-trading-status-card").style.display = "block"' in fn
    assert 'document.getElementById("ai-panel")' in fn
