import os
import sys
import time
import json
import hmac
import hashlib
import base64
import re
import subprocess
import threading
import traceback
import requests
import sqlite3
from datetime import datetime, timedelta, timezone
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
import logging

logging.basicConfig(level=logging.ERROR)

def log(msg, level="INFO"):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    lvl_str = level.upper().ljust(6)
    print(f"[{now}] {lvl_str} {msg}", flush=True)

# ── License validation ─────────────────────────────────────────────────────────
LICENSE_URL = "https://atlas-trading-tau.vercel.app/api/validate"
GRACE_HOURS = 24

def _cache_write(data):
    try:
        with open(".license_cache", "w") as f:
            json.dump(data, f)
    except Exception as e:
        pass

def _cache_read():
    try:
        with open(".license_cache", "r") as f:
            return json.load(f)
    except Exception:
        return None

def validate_license():
    license_key = os.environ.get("LICENSE_KEY", "").strip()
    if not license_key:
        print("WARNING: No LICENSE_KEY set. Running in demo mode (limited).")
        return True, {"valid": True, "type": "demo", "expires": None, "days_remaining": None}

    # Validate against the private license API — this checks ONLY this one key
    # server-side and never transmits or exposes any other customer's key,
    # unlike the old approach of publishing every key in a public JSON file.
    resp_data = None
    fetch_ok = False
    for attempt in range(3):
        try:
            r = requests.post(LICENSE_URL, json={"key": license_key}, timeout=10)
            resp_data = r.json()
            fetch_ok = True
            break
        except Exception as e:
            wait = 2 ** attempt
            print(f"License check attempt {attempt+1} failed: {e} — retrying in {wait}s")
            time.sleep(wait)

    if not fetch_ok or resp_data is None:
        # Grace period: check cache
        cache = _cache_read()
        if cache and cache.get("key") == license_key:
            last_ok = cache.get("last_checked")
            if last_ok:
                try:
                    last_dt = datetime.fromisoformat(last_ok)
                    if (datetime.now(timezone.utc) - last_dt) < timedelta(hours=GRACE_HOURS):
                        print(f"License: using cached validation (last check: {last_ok})")
                        return True, cache.get("info", {"valid": True, "type": "cached", "expires": None, "days_remaining": None})
                except Exception:
                    pass
        print("License validation failed — network error and no valid cache. Restart when online.")
        return False, {"valid": False, "type": "error", "expires": None, "days_remaining": None, "error": "Cannot reach license server"}

    if not resp_data.get("valid"):
        err = resp_data.get("error", "invalid")
        print(f"License check failed: {err}")
        return False, {
            "valid": False,
            "type": resp_data.get("type", "invalid"),
            "expires": resp_data.get("expires"),
            "days_remaining": 0,
            "error": err,
        }

    expires_str = resp_data.get("expires")
    days_left = resp_data.get("days_remaining")

    # Valid — cache and return
    info = {
        "valid": True,
        "type": resp_data.get("type", "full"),
        "expires": expires_str,
        "days_remaining": days_left,
    }
    _cache_write({"key": license_key, "last_checked": datetime.now(timezone.utc).isoformat(), "info": info})

    if expires_str:
        print(f"License valid until {expires_str[:10]} ({days_left} days remaining)")
    else:
        print("License valid (full — no expiry)")
    return True, info

# ── Config from environment ───────────────────────────────────────────────────
cfg = {
    # CEX
    "api_key":      os.environ.get("API_KEY", ""),
    "api_secret":   os.environ.get("API_SECRET", ""),
    "exchange":     os.environ.get("EXCHANGE", "bybit"),
    # DEX/EVM
    "wallet":       os.environ.get("WALLET_ADDRESS", ""),
    "private_key":  os.environ.get("PRIVATE_KEY", ""),
    # Solana
    "sol_wallet":   os.environ.get("SOL_WALLET_ADDRESS", ""),
    "sol_key":      os.environ.get("SOL_PRIVATE_KEY", ""),
    "sol_rpc":      os.environ.get("SOL_RPC", "https://api.mainnet-beta.solana.com"),
    # Strategy
    "pair":         os.environ.get("PAIR", "SOL/USDC"),
    "strategy":     os.environ.get("STRATEGY", "dca"),
    "mode":         os.environ.get("MODE", "dex"),
    "base_spread":  float(os.environ.get("BASE_SPREAD", "0.05")),
    "grid_levels":  int(os.environ.get("GRID_LEVELS", "5")),
    "risk_pct":     float(os.environ.get("RISK_PCT", "2")),
    "max_pos":      float(os.environ.get("MAX_POS", "500")),
    "leverage":     int(os.environ.get("LEVERAGE", "3")),
}

# ── Global state ──────────────────────────────────────────────────────────────
state = {
    "running": False,
    "strategy": None,
    "pair": cfg.get("pair", "SOL/USDC"),
    "mode": cfg.get("mode", "dex"),
    "chain": None,
    "price": 0.0,
    "price_history": [],
    "trades": [],
    "positions": [],
    "grid_pairs": {},
    "active_pairs": [],
    "balance": 0.0,
    "paper_trading": True,
    "engine_running": False,
    "license_valid": True,
    "license_type": "demo",
}

def get_price(pair, exchange=None):
    """Get current price from Kraken"""
    try:
        kraken_pair = pair.replace("/", "")
        r = requests.get("https://api.kraken.com/0/public/Ticker", 
                        params={"pair": kraken_pair}, timeout=5)
        data = r.json()
        if data.get("result"):
            for key in data["result"]:
                if "c" in data["result"][key]:
                    return float(data["result"][key]["c"][0])
    except Exception as e:
        log(f"Price fetch error for {pair}: {e}", "WARN")
    return 0.0

def fetch_historical_candles(pair, hours=6):
    """Fetch 6 hours of historical OHLC candles from Kraken"""
    try:
        kraken_pair = pair.replace("/", "")
        # 5-minute candles: 6 hours = 72 candles
        r = requests.get("https://api.kraken.com/0/public/OHLC",
                        params={"pair": kraken_pair, "interval": 5},
                        timeout=10)
        data = r.json()
        if data.get("result"):
            candles = []
            for key in data["result"]:
                if key != "last":
                    for ohlc in data["result"][key][-72:]:  # Last 72 5-min candles = 6 hours
                        candles.append({
                            "time": int(ohlc[0]),
                            "value": float(ohlc[4])  # close price
                        })
            log(f"Loaded {len(candles)} historical candles for {pair}")
            return candles
    except Exception as e:
        log(f"Historical candles fetch error: {e}", "WARN")
    return []

def place_order(pair, side, amount):
    """Placeholder for order placement"""
    return True

def start_bot(strategy="dca", pair="SOL/USDC", mode="dex", exchange="", chain=""):
    state["running"] = True
    state["strategy"] = strategy
    state["pair"] = pair
    state["mode"] = mode
    state["chain"] = chain or "solana"
    log(f"Bot started: {strategy.upper()} on {pair} via {mode.upper()}")

def stop_bot():
    state["running"] = False
    log("Bot stopped")

def start_background_loops():
    """Start continuous price + balance + arb scanning regardless of strategy"""
    def price_loop():
        # Pre-populate price_history with 6 hours of historical candles
        if not state.get("price_history"):
            state["price_history"] = []
        init_pair = state.get("pair", "ETH/USDT")
        hist = fetch_historical_candles(init_pair)
        if hist:
            state["price_history"] = hist
            log("Loaded " + str(len(hist)) + " historical candles for " + init_pair)
        while True:
            try:
                pair = state.get("pair","ETH/USDT")
                p = get_price(pair)
                if p > 0:
                    state["price"] = p
                    if not state.get("price_history"):
                        state["price_history"] = []
                    state["price_history"].append({"time": int(time.time()), "value": p})
                    if len(state["price_history"]) > 1440:
                        state["price_history"] = state["price_history"][-1440:]
            except Exception as e:
                log("price loop error: "+str(e), "WARN")
            time.sleep(5)

    def balance_loop():
        time.sleep(3)
        # Set initial mode hint from config, but dashboard selection overrides
        if state["mode"] is None:
            state["mode"] = "dex"
        if state["chain"] is None or state["chain"] == "ethereum":
            state["chain"] = "solana"
        while True:
            try:
                m = state.get("mode", "dex")
                # Placeholder for balance updates
                state["balance"] = 1000.0
            except Exception as ex:
                log("Balance loop error: "+str(ex), "ERROR")
            time.sleep(120)

    def arb_loop():
        # Disabled — DexPaprika deprecated (410)
        pass

    threading.Thread(target=price_loop, daemon=True).start()
    threading.Thread(target=balance_loop, daemon=True).start()
    threading.Thread(target=arb_loop, daemon=True).start()
    log("Background price feed, balance and arb scanner started")

# ── Solana ────────────────────────────────────────────────────────────────────
SOL_RPC = "https://api.mainnet-beta.solana.com"
JUPITER_API     = "https://api.jup.ag/swap/v1"
JUPITER_API_KEY = os.environ.get("JUPITER_API_KEY", "")

SOL_TOKENS = {
    "SOL": "So11111111111111111111111111111111111111112",
    "USDC": "EPjFWaLb3hyccqJ12DDjV4RVqaAP5gJY7SEcbVfLu7X",
    "USDT": "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenEb9",
}

# ── HTTP Dashboard ───────────────────────────────────────────────────────────

html_template = '''<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>Gridrunner Bot Dashboard</title><style>* { box-sizing: border-box; margin: 0; padding: 0; } body { background: #0f1419; color: #e0e0e0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; line-height: 1.6; } header { background: #1a1f2e; padding: 16px 24px; border-bottom: 1px solid #2a3142; display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 16px; } h1 { font-size: 24px; background: linear-gradient(135deg, #0ea5e9, #8b5cf6); -webkit-background-clip: text; -webkit-text-fill-color: transparent; } .dot { width: 12px; height: 12px; border-radius: 50%; background: #dc2626; display: inline-block; margin-right: 8px; } .dot.on { background: #10b981; } .controls { display: flex; gap: 8px; flex-wrap: wrap; } button { padding: 8px 16px; border: none; border-radius: 6px; background: #334155; color: #e0e0e0; cursor: pointer; font-weight: 600; font-size: 12px; transition: all 0.2s; } button:hover { background: #475569; } button.primary { background: #0ea5e9; } button.danger { background: #dc2626; } #chart-container { width: 100%; height: 400px; background: #1a1f2e; border-radius: 8px; margin-top: 16px; } .stats { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 16px; padding: 24px; } .card { background: #1a1f2e; border: 1px solid #2a3142; border-radius: 8px; padding: 16px; } .card-label { font-size: 12px; color: #94a3b8; text-transform: uppercase; } .card-value { font-size: 24px; font-weight: 700; color: #e0e0e0; margin-top: 8px; } .error { color: #fca5a5; } .success { color: #86efac; } #toast { position: fixed; bottom: 20px; right: 20px; background: #1f2937; border: 1px solid #4b5563; border-radius: 6px; padding: 16px; color: #e0e0e0; max-width: 300px; } </style></head><body><header><div><h1>⚙️ Gridrunner Bot</h1><div style="font-size: 12px; color: #94a3b8; margin-top: 4px;">Autonomous Trading Engine</div></div><div class="controls"><span><span class="dot" id="dot"></span><span id="status-text">Loading...</span></span><button class="primary" onclick="startBot()">▶ Start</button><button class="primary" onclick="stopBot()">⏸ Stop</button><button class="danger" onclick="killBot()">🛑 Kill</button><button onclick="togglePaper()">📄 Paper</button></div></header><div class="stats"><div class="card"><div class="card-label">Price</div><div class="card-value" id="s-price">—</div></div><div class="card"><div class="card-label">Balance</div><div class="card-value" id="s-balance">$0.00</div></div><div class="card"><div class="card-label">Active Pairs</div><div class="card-value" id="s-pairs">0</div></div><div class="card"><div class="card-label">Trades</div><div class="card-value" id="s-trades">0</div></div></div><div id="single-chart-row"><div id="chart-container"></div></div><div id="charts-container" style="display: none; flex-wrap: wrap; gap: 16px; padding: 24px;"></div><div id="toast" style="display: none;"></div><script src="https://cdn.jsdelivr.net/npm/lightweight-charts@4.1.0/dist/lightweight-charts.standalone.production.js"></script><script>
var chart = null;
var candleSeries = null;
var gridLines = [];
var isDark = true;

// API_SECRET - retrieved once from localStorage or prompt
var API_SECRET = localStorage.getItem("gr_api_secret") || "";
if (!API_SECRET) {
  API_SECRET = prompt("Enter your API_SECRET (set in Render env vars):") || "";
  if (API_SECRET) localStorage.setItem("gr_api_secret", API_SECRET);
}

// apiFetch - MUST be defined BEFORE any calls to it
function apiFetch(url, opts) {
  opts = opts || {};
  opts.headers = opts.headers || {};
  if (API_SECRET) opts.headers["X-API-Secret"] = API_SECRET;
  return fetch(url, opts);
}

function showToast(msg, type) {
  var t = document.getElementById("toast");
  t.textContent = msg;
  t.style.color = type === "error" ? "#fca5a5" : type === "info" ? "#93c5fd" : "#86efac";
  t.style.display = "block";
  setTimeout(function() { t.style.display = "none"; }, 3000);
}

function startBot() {
  apiFetch("/start?strategy=dca&pair=SOL/USDC&mode=dex").then(function(r) { return r.json(); }).then(function(d) {
    showToast(d.ok ? "Bot started" : "Error starting bot", d.ok ? "success" : "error");
  }).catch(function(e) { showToast("Start error: " + e.message, "error"); });
}

function stopBot() {
  apiFetch("/stop").then(function(r) { return r.json(); }).then(function(d) {
    showToast(d.ok ? "Bot stopped" : "Error", d.ok ? "success" : "error");
  }).catch(function(e) { showToast("Stop error: " + e.message, "error"); });
}

function killBot() {
  if (confirm("Kill all positions immediately?")) {
    apiFetch("/kill", {method:"POST"}).then(function(r) { return r.json(); }).then(function(d) {
      showToast("Killed " + (d.closed || 0) + " positions", "success");
    }).catch(function(e) { showToast("Kill error: " + e.message, "error"); });
  }
}

function togglePaper() {
  apiFetch("/toggle_paper").then(function(r) { return r.json(); }).then(function(d) {
    showToast("Paper trading: " + (d.paper_trading ? "ON" : "OFF"), "info");
  }).catch(function(e) { showToast("Toggle error: " + e.message, "error"); });
}

function aggregateCandles(data, intervalSec) {
  var candles = [], current = null;
  data.forEach(function(d) {
    var bucket = Math.floor(d.time / intervalSec) * intervalSec;
    if (!current || current.time !== bucket) {
      if (current) candles.push(current);
      current = {time: bucket, open: d.value, high: d.value, low: d.value, close: d.value};
    } else {
      current.high = Math.max(current.high, d.value);
      current.low = Math.min(current.low, d.value);
      current.close = d.value;
    }
  });
  if (current) candles.push(current);
  return candles;
}

function initChart() {
  try {
    chart = LightweightCharts.createChart(document.getElementById("chart-container"), {
      width: document.getElementById("chart-container").clientWidth || 600,
      height: 400,
      layout: {
        background: {type: "solid", color: "#0f1419"},
        textColor: "#94a3b8",
      },
      grid: {
        vertLines: {color: "#1a1f2e"},
        horzLines: {color: "#1a1f2e"},
      },
      timeScale: {
        borderColor: "#2a3142",
        timeVisible: true,
        secondsVisible: false,
      },
      rightPriceScale: {
        borderColor: "#2a3142",
      },
    });
    candleSeries = chart.addSeries(LightweightCharts.CandlestickSeries, {
      upColor: "#10b981",
      downColor: "#ef4444",
      borderUpColor: "#10b981",
      borderDownColor: "#ef4444",
      wickUpColor: "#10b981",
      wickDownColor: "#ef4444",
    });
  } catch(e) { console.log("Chart init error:", e); }
}

function updateChart(data) {
  if (!chart || !candleSeries) return;
  if (!data || data.length < 2) return;
  var candles = aggregateCandles(data, 60);
  candleSeries.setData(candles);
  if (candles.length > 60) {
    chart.timeScale().setVisibleLogicalRange({
      from: candles.length - 60,
      to: candles.length + 1
    });
  }
}

function refresh() {
  try {
    apiFetch("/state").then(function(r) { return r.json(); }).then(function(d) {
      try {
        var on = d.running;
        document.getElementById("dot").className = "dot" + (on ? " on" : "");
        document.getElementById("status-text").textContent = on ? "Running — " + (d.strategy || "").toUpperCase() : "Stopped";
        document.getElementById("s-price").textContent = d.price > 0 ? "$" + d.price.toFixed(4) : "—";
        document.getElementById("s-balance").textContent = "$" + (d.balance || 0).toFixed(2);
        document.getElementById("s-pairs").textContent = (d.active_pairs ? d.active_pairs.length : 0);
        document.getElementById("s-trades").textContent = (d.trades ? d.trades.length : 0);
        if (d.price_history && d.price_history.length > 0) {
          updateChart(d.price_history);
        }
      } catch(e) { console.error("refresh parse error:", e); }
    }).catch(function(e) { console.error("refresh fetch error:", e); });
  } catch(e) { console.error("refresh error:", e); }
}

window.addEventListener("resize", function() {
  if (chart) {
    var w = document.getElementById("chart-container").clientWidth || 600;
    chart.applyOptions({width: w});
  }
});

setInterval(refresh, 3000);
refresh();
setTimeout(function() { initChart(); }, 100);
</script></body></html>'''

class RequestHandler(BaseHTTPRequestHandler):
    API_SECRET = os.environ.get("API_SECRET", "")

    def _check_auth(self):
        return not self.API_SECRET or self.headers.get("X-API-Secret") == self.API_SECRET

    def _auth_or_401(self):
        if self._check_auth():
            return True
        self.respond(401, "application/json", b'{"error":"Unauthorized"}')
        return False

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        params = parse_qs(parsed.query)

        if path == "/":
            self.respond(200, "text/html", html_template.encode())
        elif path == "/state":
            state["trades_list"] = state.get("trades", [])[-50:]
            if not self._check_auth():
                self.respond(200, "application/json", json.dumps({
                    "price": state.get("price", 0),
                    "running": state.get("running", False),
                    "strategy": state.get("strategy", ""),
                    "pair": state.get("pair", ""),
                }).encode())
                return
            self.respond(200, "application/json", json.dumps(state).encode())
        elif path == "/start":
            if not self._auth_or_401(): return
            start_bot(
                params.get("strategy", ["dca"])[0],
                params.get("pair", [cfg["pair"]])[0],
                params.get("mode", ["dex"])[0],
                params.get("exchange", [cfg["exchange"]])[0],
                params.get("chain", ["solana"])[0],
            )
            self.respond(200, "application/json", b'{"ok":true}')
        elif path == "/stop":
            if not self._auth_or_401(): return
            stop_bot()
            self.respond(200, "application/json", b'{"ok":true}')
        elif path == "/kill":
            if not self._auth_or_401(): return
            state["running"] = False
            self.respond(200, "application/json", json.dumps({"closed": 0}).encode())
        elif path == "/toggle_paper":
            if not self._auth_or_401(): return
            state["paper_trading"] = not state.get("paper_trading", True)
            self.respond(200, "application/json", json.dumps({"paper_trading": state["paper_trading"]}).encode())
        else:
            self.respond(404, "text/plain", b"Not found")

    def do_POST(self):
        self.do_GET()

    def respond(self, code, content_type, body):
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", len(body))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        pass

def main():
    log("Gridrunner Bot starting...")
    valid, info = validate_license()
    state["license_valid"] = info.get("valid", True)
    state["license_type"] = info.get("type", "demo")

    if not valid:
        log("License invalid. Exiting.", "ERROR")
        return

    # Initialize price history
    if not state.get("price_history"):
        state["price_history"] = []

    # Start background loops
    start_background_loops()

    # Start HTTP server
    port = int(os.environ.get("PORT", 8000))
    server = HTTPServer(("0.0.0.0", port), RequestHandler)
    log(f"Dashboard running at http://localhost:{port}")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log("Shutting down...")
        server.shutdown()

if __name__ == "__main__":
    main()
