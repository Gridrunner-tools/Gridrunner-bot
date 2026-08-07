"""Static regressions for isolated multi-pair chart cards."""
from pathlib import Path
SOURCE = Path(__file__).with_name("main.py").read_text()

def test_each_pair_has_owned_chart_container_and_series():
    assert 'card.id = cardId' in SOURCE
    assert 'card._chart = ch' in SOURCE
    assert 'card._series = ch.addSeries' in SOURCE
    assert 'setMultiPairChartData(card, chartHistoryAtInit, chartEl)' in SOURCE
    assert 'var chart = card._chart' in SOURCE

def test_multi_pair_cards_support_three_panel_layout():
    assert 'flex:1 1 calc(33.333% - 11px)' in SOURCE
    assert 'min-width:280px' in SOURCE
    assert 'max-width:none' in SOURCE

def test_history_is_pair_scoped_before_rendering():
    assert 'd.price_history_pairs && d.price_history_pairs[pair]' in SOURCE
    assert 'd.price_history_pairs && d.price_history_pairs[viewPair]' in SOURCE
