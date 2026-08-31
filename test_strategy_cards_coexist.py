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
