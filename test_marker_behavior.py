"""Behavioral test for dashboard strategy markers (JS executed in Node).

Feeds an `ai_trades`-shaped payload (unix seconds `time` + `side` buy/sell,
exactly as produced by the /state _state_payload() ai_trades block) through the
REAL `buildStrategyMarkers` and `applyChartMarkers` functions extracted from
main.py, and asserts correct buy/sell classification and 60s time bucketing.

Runs only the two marker functions in a Node sandbox with a stubbed series --
no browser, no server, no trading code. Skips cleanly (pytest.skip-style) only
if node is unavailable, but the canonical runner (run_all_tests.py) requires
node, so a missing binary is reported as a failure.
"""
import json
import shutil
import subprocess
import tempfile
from pathlib import Path

SOURCE = Path(__file__).with_name("main.py").read_text(encoding="utf-8")


def _extract_js_function(src, name):
    """Return the source text of `function <name>(...) { ... }` (brace-matched)."""
    start = src.find("function %s(" % name)
    assert start != -1, "function %s not found in main.py" % name
    open_idx = src.find("{", start)
    assert open_idx != -1, "no body brace for %s" % name
    depth = 0
    in_str = None
    i = open_idx
    while i < len(src):
        c = src[i]
        if in_str:
            if c == "\\":
                i += 2
                continue
            if c == in_str:
                in_str = None
        else:
            if c in ('"', "'", "`"):
                in_str = c
            elif c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    return src[start:i + 1]
        i += 1
    raise AssertionError("unbalanced braces for %s" % name)


def _run_marker_harness():
    """Execute buildStrategyMarkers + applyChartMarkers in node, return parsed JSON."""
    node = shutil.which("node")
    if not node:
        raise AssertionError("node binary not found; cannot run behavioral marker test")
    bsm = _extract_js_function(SOURCE, "buildStrategyMarkers")
    acm = _extract_js_function(SOURCE, "applyChartMarkers")

    # Sample payload faithful to the _state_payload() ai_trades block:
    #   {"time": unix seconds, "price": float, "side": "buy"|"sell",
    #    "pair": str, "strategy": "ai_trading", "state": ...}
    harness = r"""
var __out = { markers: null, placed: null };
{BSM}
{ACM}
var series = { setMarkers: function (m) { __out.placed = m; } };
var candles = [ { time: 960 }, { time: 1020 } ];   // 60s-aligned grid
var trades = [
  { time: 1005, price: 100.0, side: "buy",  pair: "BTC/USDC", strategy: "ai_trading", state: "open" },
  { time: 1065, price: 101.0, side: "sell", pair: "BTC/USDC", strategy: "ai_trading", state: "closed" },
  { time: 1090, price: 102.0, side: "buy",  pair: "BTC/USDC", strategy: "ai_trading", state: "open" },
  { time: "10:00:05", price: 103.0, side: "buy", pair: "BTC/USDC", strategy: "ai_trading", state: "closed" },
  { time: 1005, price: 104.0, side: "AI-LONG", pair: "BTC/USDC", strategy: "ai_trading", state: "closed" }
];
__out.markers = buildStrategyMarkers(trades, "ai_trading", "BTC/USDC");
applyChartMarkers(series, __out.markers, candles);
process.stdout.write(JSON.stringify(__out));
""".replace("{BSM}", bsm).replace("{ACM}", acm)

    with tempfile.TemporaryDirectory() as d:
        jsfile = Path(d) / "harness.js"
        jsfile.write_text(harness, encoding="utf-8")
        r = subprocess.run([node, str(jsfile)], capture_output=True, text=True, timeout=60)
    if r.returncode != 0:
        raise AssertionError("node harness failed: %s" % r.stderr[-2000:])
    return json.loads(r.stdout)


def test_marker_buckets_unix_times_to_60s_and_classifies_buy_sell():
    out = _run_marker_harness()
    mk = out["markers"]
    # 1005 -> 960, 1065 -> 1020, 1090 -> 1080; "10:00:05" (NaN) dropped.
    assert len(mk) == 4, "expected 4 markers, got %s" % (mk,)
    by_time = {m["time"]: m for m in mk}
    assert by_time[960]["isBuy"] is True, "unix 1005 (buy) must bucket to 960 as BUY"
    assert by_time[1020]["isBuy"] is False, "unix 1065 (sell) must bucket to 1020 as SELL"
    assert by_time[1080]["isBuy"] is True, "unix 1090 (buy) must bucket to 1080 as BUY"
    # Two markers bucket to 960 (buy@100 and AI-LONG@104); just assert the buy@100 exists.
    p960 = [m for m in mk if m["time"] == 960]
    assert any(m["price"] == 100.0 and m["isBuy"] for m in p960), "price 100.0 buy at 960 missing: %s" % (p960,)
    # AI-LONG classifies as buy.
    longs = [m for m in mk if m["price"] == 104.0]
    assert len(longs) == 1 and longs[0]["isBuy"] is True, "AI-LONG side must classify as BUY"


def test_apply_chart_markers_places_buy_sell_aligned_to_candle_times():
    out = _run_marker_harness()
    placed = out["placed"]
    # Candle grid only has 960 and 1020 -> marker at 1080 is dropped.
    assert placed is not None and len(placed) == 3, "expected 3 placed markers, got %s" % (placed,)
    by_time = {}
    for p in placed:
        by_time.setdefault(p["time"], []).append(p)
    buys = by_time.get(960, [])
    sells = by_time.get(1020, [])
    assert len(buys) == 2, "two BUY markers at 960 expected, got %s" % (buys,)
    assert len(sells) == 1, "one SELL marker at 1020 expected, got %s" % (sells,)
    b = buys[0]
    s = sells[0]
    assert b["position"] == "belowBar" and b["shape"] == "arrowUp" and b["color"] == "#00ff9d" and b["text"] == "BUY"
    assert s["position"] == "aboveBar" and s["shape"] == "arrowDown" and s["color"] == "#ff6b6b" and s["text"] == "SELL"
    assert 1080 not in by_time, "marker with no matching candle must be dropped"


def test_xspanel_chart_divs_have_card_chrome():
    # The dynamically created extra-panel chart divs must carry the same card
    # chrome as #chart-container / #ai-chart-container.
    assert '[id^="xspanel-chart-"]' in SOURCE
    idx = SOURCE.index('[id^="xspanel-chart-"]')
    rule = SOURCE[idx:idx + 200]
    for token in ("background:var(--card)", "border:1px solid var(--border)", "border-radius:10px"):
        assert token in rule, "xspanel chrome rule missing %s: %s" % (token, rule)
