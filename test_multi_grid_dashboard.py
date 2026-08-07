"""Regression coverage for per-card multi-grid dashboard rendering.

These checks intentionally inspect the embedded dashboard script: the app is
served as a single-page HTML response, so this catches regressions without
requiring a browser or live exchange credentials.
"""
from pathlib import Path
import re


SOURCE = Path(__file__).with_name("main.py").read_text()


def test_multi_grid_cards_use_grid_layout_and_right_edge_anchor():
    assert 'id="charts-container"' in SOURCE
    assert 'grid-template-columns:repeat(auto-fit,minmax(320px,1fr))' in SOURCE
    assert 'chartsWrap.style.display = "grid"' in SOURCE
    assert 'rightOffset:0' in SOURCE


def test_each_pair_closure_owns_history_and_chart_dom():
    # The deferred callback must capture pair/card/history, rather than using
    # refresh-loop variables that can point at the next pair (BTC -> SPCX).
    assert '(function(ownerCard, ownerPair, ownerHistory)' in SOURCE
    assert '})(card, pair, ph.slice());' in SOURCE
    assert 'setMultiPairChartData(ownerCard, ownerHistory, chartEl)' in SOURCE
    assert 'document.getElementById(ownerCard.id + "-chart")' in SOURCE


def test_history_is_replayed_after_async_chart_creation():
    callback = SOURCE[SOURCE.index('(function(ownerCard'):SOURCE.index('})(card, pair, ph.slice());')]
    assert callback.index('ownerCard._series') < callback.index('setMultiPairChartData(ownerCard')
    assert re.search(r'setVisibleLogicalRange\(\{from:.*to:chartData.length\}', SOURCE)


def test_no_unresolved_merge_markers_in_dashboard_source():
    assert not re.search(r'^<<<<<<<|^=======|^>>>>>>>', SOURCE, re.MULTILINE)
