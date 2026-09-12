"""Regression test: the FULL dashboard <script> block must parse as valid JS.

Why this exists: PR #138's per-strategy panels introduced a Python-string
escaping bug in the JS stop-button expression inside main.py's DASHBOARD
template. DASHBOARD is a normal (non-raw, non-f) Python triple-quoted string,
so `\\` and `\\'` are reduced by the Python lexer BEFORE the browser sees them.
The authored line looked like valid JS but the SERVED bytes were a SyntaxError,
which killed the ENTIRE dashboard script: no refresh()/setInterval, no
initChart(), and manualSell was never defined — blank dashboard + dead manual
Sell control. The previous marker test only ran two isolated JS functions, so
it could not see the page-load failure.

This test extracts the dashboard <script> content exactly as the server serves
it (Python escape processing applied via ast.literal_eval) and requires
`node --check` to accept it as valid JS. It catches any future page-load parse
error in the whole dashboard script, not just one extracted function.
"""
import ast
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

MAIN_PY = Path(__file__).with_name("main.py").read_text(encoding="utf-8")


def _served_dashboard_js():
    """Return the dashboard <script> content exactly as served (escapes decoded)."""
    m = re.search(r"DASHBOARD = ('''|\"\"\")(.*?)\1", MAIN_PY, re.S)
    assert m, "DASHBOARD template not found in main.py"
    # Re-wrap the literal in its original delimiter so Python decodes the
    # backslash escapes exactly as it does when main.py is imported.
    literal = m.group(1) + m.group(2) + m.group(1)
    # Evaluate the Python string literal so backslash escapes are processed
    # exactly as they are when main.py is imported and the server serves it.
    dashboard = ast.literal_eval(literal)
    scripts = re.findall(r"<script>(.*?)</script>", dashboard, re.S)
    assert scripts, "no <script> block found in DASHBOARD"
    return scripts[-1]


def test_full_dashboard_script_is_valid_javascript():
    """The entire served dashboard script must pass node --check (no parse death)."""
    node = shutil.which("node")
    assert node, "node binary not found; cannot validate dashboard script"
    js = _served_dashboard_js()
    with tempfile.TemporaryDirectory() as d:
        f = Path(d) / "dashboard.js"
        f.write_text(js, encoding="utf-8")
        p = subprocess.run(
            [node, "--check", str(f)],
            capture_output=True, text=True,
        )
    assert p.returncode == 0, (
        "SERVED dashboard <script> fails JS syntax check — the entire "
        "dashboard script is dead (no refresh, no chart, no manual sell). "
        "SyntaxError: %s" % p.stderr.strip()
    )


def test_dashboard_script_has_init_and_manual_sell_wiring():
    """Sanity: the script still contains the page-load init path and controls."""
    js = _served_dashboard_js()
    for needle in (
        "function refresh(",
        "setInterval(refresh, 3000)",
        "initChart();",
        "function manualSell(",
    ):
        assert needle in js, "dashboard script is missing %r" % needle

def test_dashboard_seeds_chart_on_load_and_strips_credential_url():
    """Regression: page-load seed for the active pair + boot URL-credential strip.

    Root cause found 2026-09-12: a dashboard URL that embeds credentials
    (http://user:pass@host — shared links/bookmarks) makes every relative
    fetch() throw a SecurityError ("URL that includes credentials"), so the
    first refresh() dies silently: price stays '—', the chart is never seeded
    and renders EMPTY. Fix = strip userinfo once at boot via history.replaceState
    so refresh/seeding run, plus an explicit seedHistoryOnLoad() timed call so a
    stopped bot's selected pair gets chart data on page load.
    """
    js = _served_dashboard_js()
    for needle in (
        'apiFetch("/chart_history?pair=" + encodeURIComponent(pair))',
        "function seedHistoryOnLoad(pair)",
        "seedHistoryOnLoad((selEl2 && selEl2.value) ? selEl2.value : \"SOL/USDC\")",
        "window._cleanBase",
        "url = new URL(url, window._cleanBase);",
    ):
        assert needle in js, "dashboard script is missing seed/URL-strip wiring %r" % needle


def _served_dashboard_html():
    """Return the full DASHBOARD HTML/JS string exactly as served."""
    m = re.search(r"DASHBOARD = ('''|\"\"\")(.*?)\1", MAIN_PY, re.S)
    assert m, "DASHBOARD template not found in main.py"
    literal = m.group(1) + m.group(2) + m.group(1)
    return ast.literal_eval(literal)


def test_dashboard_ai_row_below_grid_with_own_chart():
    """Regression: grid + AI concurrently must keep BOTH charts visible.
    Owner's target layout (2026-09-12): one row per running strategy, stacked
    top-to-bottom. Grid row stays EXACTLY as it is ([grid chart | grid details
    card]); the AI strategy renders the same way UNDER it as its own row
    ([AI chart | AI status card]).
    Root cause of the original bug: #ai-trading-status-card was a THIRD flex
    child of #single-chart-row (flex-shrink:0, 420px) alongside the grid
    details card, leaving the grid chart (flex:1; min-width:0) only residual
    width when AI ran — chart collapsed to 0/88px and vanished. The fix moves
    the AI card OUT of the grid row into its own #ai-chart-row with its own
    chart container fed from the AI pair's own history (/chart_history).
    """
    html = _served_dashboard_html()
    # Superseded cards-column approach must be gone entirely.
    assert 'id="cards-column"' not in html, "cards-column approach must be gone"
    # DOM order: grid row [chart-container, grid-details-card] -> ai-chart-row
    # [ai-chart-container, ai-trading-status-card], all siblings.
    i_row = html.index('id="single-chart-row"')
    i_cc = html.index('id="chart-container"')
    i_gd = html.index('id="grid-details-card"')
    i_airow = html.index('id="ai-chart-row"')
    i_aicc = html.index('id="ai-chart-container"')
    i_ai = html.index('id="ai-trading-status-card"')
    assert i_row < i_cc < i_gd < i_airow < i_aicc < i_ai, (
        "expected: grid row [chart | grid card] then AI row [ai chart | ai card]"
    )
    # Grid row untouched: chart keeps flex:1;min-width:0, card keeps 420px fixed.
    assert "flex:1;min-width:0" in html[i_cc:i_cc + 60]
    assert "width:420px;flex-shrink:0" in html[i_gd:i_gd + 120]
    # AI chart container carries its own 320px floor so it can never collapse.
    assert "flex:1;min-width:320px" in html[i_aicc:i_aicc + 60]
    # The AI card is no longer nested inside the grid card / grid row.
    assert html.count('id="ai-chart-row"') == 1
    assert html.count('id="ai-chart-container"') == 1
    js = _served_dashboard_js()
    for needle in (
        "var aiChart = null;",
        "function initAIChart()",
        "function updateAIChart(data, pair)",
        "function fetchAIChartHistory(pair)",
        'aiChartRow.style.display = (aiRunning || sel.strat === "ai_trading") ? "flex" : "none"',
        "st.type === \"ai_trading\" && st.running",
        '"/chart_history?pair=" + encodeURIComponent(pair)',
        "if (aw > 0) aiChart.applyOptions({width: aw});",
    ):
        assert needle in js, "dashboard script is missing AI-row wiring %r" % needle
