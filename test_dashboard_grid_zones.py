"""Regression tests for showing buy/sell grid zones on the dashboard.

These checks verify that the frontend dashboard in main.py has been correctly
updated to render the grid levels with clear, color-coded buy/sell zone markers
matching our asymmetric grid geometry, and clearly displays the 2x sell gap.
"""
from pathlib import Path
import re

SOURCE = Path(__file__).with_name("main.py").read_text()


def test_dashboard_contains_asymmetric_zone_labels_and_colors():
    # Verify the updated mid and buy zone classification logic in the grid card infoEl
    assert "var isMid = i === midIdx;" in SOURCE or "var isMid = i === gp.mid_idx;" in SOURCE or "i === midIdx" in SOURCE
    assert "isMidMinusOne = i === (midIdx - 1)" in SOURCE
    assert "isBuyZone = i <= midIdx" in SOURCE

    # Verify that the color-coding uses distinct values for sell (red), mid (gold/yellow),
    # mid-1 (lighter orange/gold), and lower buy (green).
    assert "#ffd43b" in SOURCE  # Gold/Yellow for MID
    assert "#ffbc42" in SOURCE  # Lighter Orange/Gold for MID-1
    assert "#00ff9d" in SOURCE  # Green for BUY
    assert "#ff6b6b" in SOURCE  # Red for SELL


def test_dashboard_contains_sell_side_gap_display():
    # Verify we calculate and display the 2x sell gap in the multi-grid card
    assert "buySpacing * 2" in SOURCE or "gl[1] - gl[0]" in SOURCE
    assert "2× sell gap" in SOURCE

    # Verify that we show the Gap column in the Grid Details card table header
    assert "grid-template-columns:80px 80px 80px 1fr 80px" in SOURCE
    assert "<span>Gap</span>" in SOURCE


def test_dashboard_contains_per_level_spacing_multiplier():
    # Verify we display the individual level spacings with multiplier (1x vs 2x)
    assert 'multiplier = isSellGap ? " (2x)" : " (1x)"' in SOURCE or "isSellGap ? \" (2x)\" : \" (1x)\"" in SOURCE
    assert 'gapStr = "$" + gapVal.toFixed(2) + multiplier' in SOURCE


def test_no_unresolved_merge_markers():
    assert not re.search(r"^<<<<<<<|^=======|^>>>>>>>", SOURCE, re.MULTILINE)
