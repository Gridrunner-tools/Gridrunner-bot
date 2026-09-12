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