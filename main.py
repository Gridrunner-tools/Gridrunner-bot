#!/usr/bin/env python3
"""
Trading Bot — Full Dashboard
DEX mode: Wallet-based spot grid trading on Solana via Raydium/Jupiter
Price feeds: Kraken (no key needed)
Strategies: DCA, Grid, Scalping, Copy Trading, Arbitrage
"""
import os, json, time, hmac, hashlib, threading, requests, logging, base64, random, string, math

TRADE_LOG = "trade_history.log"  # persistent trade log
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import parse_qs, urlparse
from datetime import datetime, timezone, timedelta
os.environ["TZ"] = "US/Eastern"
time.tzset()

logging.basicConfig(level=logging.WARNING)
def _normalize_partial_sell_pct(value):
    """Return a safe partial-sell percentage (1-100, inclusive)."""
    try:
        return max(1, min(100, float(value)))
    except (TypeError, ValueError):
        return 50.0
TOKEN_DECIMALS = {"USDC": 6, "USDT": 6, "SOL": 9, "BTC": 8, "ETH": 8, "JUP": 6, "BONK": 5, "WIF": 6, "SPCX": 6}


# ── License Validation ──────────────────────────────────────────────────────────
# Paid licenses are stored in the private Neon/PostgreSQL registry. DATABASE_URL
# is a secret Render environment variable; no public keys.json URL is used.
from license_registry import LicenseRegistryUnavailable, lookup_license

LICENSE_CACHE_FILE = ".license_cache"
GRACE_HOURS = 48

def _cache_write(data):
    try:
        with open(LICENSE_CACHE_FILE, "w") as f:
            json.dump(data, f)
    except Exception:
        pass

def _cache_read():
    try:
        with open(LICENSE_CACHE_FILE) as f:
            return json.load(f)
    except Exception:
        return None

def _cached_license(license_key):
    """Return a matching, recently verified record or None.

    The cache is only an outage grace period. It cannot validate a new key and
    is ignored after GRACE_HOURS, so missing registry access fails closed.
    """
    cache = _cache_read()
    if not cache or cache.get("key") != license_key:
        return None
    last_ok = cache.get("last_checked")
    if not last_ok:
        return None
    try:
        last_dt = datetime.fromisoformat(last_ok)
        if last_dt.tzinfo is None:
            last_dt = last_dt.replace(tzinfo=timezone.utc)
        if (datetime.now(timezone.utc) - last_dt) < timedelta(hours=GRACE_HOURS):
            return cache.get("info")
    except (TypeError, ValueError):
        pass
    return None

def validate_license():
    """Validate LICENSE_KEY against the private Neon/PostgreSQL registry.

    Returns (valid: bool, info: dict). A registry outage gets a maximum
    48-hour grace period only for the exact key that was previously verified.
    Otherwise the caller receives an invalid result and forces paper mode.
    """
    license_key = os.environ.get("LICENSE_KEY", "").strip()
    if not license_key:
        # Auto-trial: 7 days free from first run
        trial_file = "trial.json"
        try:
            if os.path.exists(trial_file):
                with open(trial_file) as f:
                    trial = json.load(f)
                trial_start = datetime.fromisoformat(trial.get("start", "2000-01-01T00:00:00+00:00"))
                trial_end = trial_start + timedelta(days=7)
                # Verify hash hasn't been tampered with
                expected_hash = hashlib.sha256(trial_start.isoformat().encode()).hexdigest()[:16]
                if trial.get("hash") != expected_hash:
                    print("TRIAL DATA TAMPERED — forcing paper-only mode")
                    return False, {"valid": False, "type": "tampered", "expires": None, "days_remaining": 0, "error": "Trial data tampered. Purchase a license."}
                now = datetime.now(timezone.utc)
                days_left = (trial_end - now).days
                if now < trial_end:
                    print(f"TRIAL ACTIVE — {days_left} day(s) remaining (ends {trial_end.strftime('%Y-%m-%d')})")
                    return True, {"valid": True, "type": "trial", "expires": trial_end.isoformat(), "days_remaining": max(0, days_left)}
                else:
                    print(f"TRIAL EXPIRED — ended {trial_end.strftime('%Y-%m-%d')}. Buy a license to continue live trading.")
                    return False, {"valid": False, "type": "trial_expired", "expires": trial_end.isoformat(), "days_remaining": 0, "error": "Trial expired. Purchase a license key to continue."}
            else:
                # First run — start trial. Fetch remote time to prevent clock manipulation.
                trial_start = datetime.now(timezone.utc)
                try:
                    r = requests.get("https://api.kraken.com/0/public/Time", timeout=5)
                    if r.status_code == 200:
                        unixtime = r.json().get("result",{}).get("unixtime", 0)
                        if unixtime > 0:
                            trial_start = datetime.fromtimestamp(unixtime, tz=timezone.utc)
                except Exception:
                    pass  # fall back to local time if network fails
                trial_end = trial_start + timedelta(days=7)
                # Store obfuscated: hash the start time so tampering is detectable
                trial_hash = hashlib.sha256(trial_start.isoformat().encode()).hexdigest()[:16]
                with open(trial_file, "w") as f:
                    json.dump({"start": trial_start.isoformat(), "end": trial_end.isoformat(), "hash": trial_hash}, f)
                print(f"TRIAL STARTED — 7 days free. Expires {trial_end.strftime('%Y-%m-%d')}")
                return True, {"valid": True, "type": "trial", "expires": trial_end.isoformat(), "days_remaining": 7}
        except Exception as e:
            print(f"Trial error: {e}")
            return True, {"valid": True, "type": "demo", "expires": None, "days_remaining": None}

    try:
        match = lookup_license(license_key)
    except LicenseRegistryUnavailable:
        cached_info = _cached_license(license_key)
        if cached_info:
            print("License: using cached validation during registry outage")
            return True, cached_info
        print("License validation failed — registry unavailable and no valid cache. Restart when online.")
        return False, {"valid": False, "type": "error", "expires": None, "days_remaining": None, "error": "Cannot reach license registry"}

    if not match:
        print("Invalid license key — not present in private registry")
        return False, {"valid": False, "type": "invalid", "expires": None, "days_remaining": None, "error": "License key not found"}

    # Check expiry
    expires_str = match.get("expires")
    if expires_str:
        try:
            expires_dt = datetime.fromisoformat(expires_str)
            if expires_dt.tzinfo is None:
                expires_dt = expires_dt.replace(tzinfo=timezone.utc)
            now = datetime.now(timezone.utc)
            if now > expires_dt:
                print(f"License expired on {expires_str[:10]}")
                return False, {"valid": False, "type": match.get("type", "trial"), "expires": expires_str, "days_remaining": 0, "error": "License expired"}
            days_left = (expires_dt - now).days
        except (TypeError, ValueError):
            print("License validation failed — registry returned an invalid expiration.")
            return False, {"valid": False, "type": "error", "expires": None, "days_remaining": None, "error": "Invalid license expiration"}
    else:
        days_left = None  # Full key, no expiry

    # Valid — cache and return
    info = {
        "valid": True,
        "type": match.get("type", "full"),
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
PAPER_MODE_FILE = "paper_mode.json"
def _env_paper_mode():
    """Return explicit environment mode, or None when no valid override is set.

    PAPER_MODE is the public setting; PAPER_TRADING remains supported for
    existing installations. Invalid values are ignored so persisted state (or
    the wallet-based default) remains the safe fallback.
    """
    for name in ("PAPER_MODE", "PAPER_TRADING"):
        raw = os.environ.get(name)
        if raw is None:
            continue
        value = raw.strip().lower()
        if value in ("1", "true", "yes", "on"):
            return True
        if value in ("0", "false", "no", "off"):
            return False
    return None

def _load_paper_mode(default):
    env_mode = _env_paper_mode()
    if env_mode is not None:
        return env_mode
    try:
        with open(PAPER_MODE_FILE) as f:
            value = json.load(f).get("paper_trading")
            if isinstance(value, bool): return value
    except (OSError, ValueError, TypeError): pass
    return default
def _save_paper_mode(value):
    tmp = PAPER_MODE_FILE + ".tmp"
    with open(tmp, "w") as f: json.dump({"paper_trading": bool(value)}, f)
    os.replace(tmp, PAPER_MODE_FILE)

def _secret(name):
    """Read credentials only at point of use; never retain them in global config/state."""
    return os.environ.get(name, "")

cfg = {
    # CEX
    "exchange":     os.environ.get("EXCHANGE", "bybit"),
    # DEX/EVM
    "wallet":       os.environ.get("WALLET_ADDRESS", ""),
    # Solana
    "sol_wallet":   os.environ.get("SOL_WALLET_ADDRESS", ""),
    # Trading
    "pair":         os.environ.get("TRADING_PAIR", "SOL/USDC"),
    "risk_pct":     float(os.environ.get("RISK_PCT", "2")),
    "stop_loss":    float(os.environ.get("STOP_LOSS_PCT", "5")),
    "take_profit":  float(os.environ.get("TAKE_PROFIT_PCT", "15")),
    "max_pos":      float(os.environ.get("MAX_POSITION_USD", "500")),
    "max_loss":     float(os.environ.get("MAX_DAILY_LOSS_USD", "200")),
    "source_wallet":os.environ.get("SOURCE_WALLET", ""),
    "min_arb_spread":  float(os.environ.get("MIN_ARB_SPREAD", "1.5")),
    # Explicit env mode wins over persisted state; otherwise default to PAPER
    # trading — live trading only turns on via PAPER_MODE/PAPER_TRADING env
    # var or a manual dashboard toggle (which persists in paper_mode.json).
    "paper_trading":   (_env_paper_mode() if _env_paper_mode() is not None else True),
    "auto_compound":   os.environ.get("AUTO_COMPOUND", "true").lower() != "false",
    "partial_sell_pct":  _normalize_partial_sell_pct(os.environ.get("PARTIAL_SELL_PCT", "50")),
    "grid_level_count":  max(2, min(int(os.environ.get("GRID_LEVELS", "5")), 100)),
}


import threading
_state_lock = threading.Lock()

def get_reserved_capital(strategy_type, strategy_config, current_balance):
    if strategy_type == "grid":
        risk_pct = float(strategy_config.get("risk_pct", 2.0))
        max_pos = float(strategy_config.get("max_pos", 500.0))
        return min(current_balance * risk_pct / 100, max_pos) if current_balance > 0 else max_pos
    elif strategy_type in ("limit_buy", "limit_sell"):
        return float(strategy_config.get("limit_amount_usdc", 0.0))
    elif strategy_type == "ai_trading":
        return float(strategy_config.get("max_total_exposure", 5000.0) or 5000.0)
    else:
        risk_pct = float(strategy_config.get("risk_pct", 2.0))
        max_pos = float(strategy_config.get("max_pos", 500.0))
        return min(current_balance * risk_pct / 100, max_pos) if current_balance > 0 else max_pos

def get_available_balance(paper=None):
    if paper is None:
        paper = state.get("paper_trading", False)
    if paper:
        return 10000.0
    return get_balance() or 0.0

def check_capital_reservation(strategy_type, strategy_config):
    """Reserve capital per mode so the shared wallet is never double-spent.

    Live strategies reserve against the REAL wallet balance; paper strategies
    reserve against the simulated paper balance (10000). They are pooled
    separately, so a paper AI Trading strategy cannot consume the live-grid
    capital and vice versa. Returns (ok: bool, error_msg: str|None).
    """
    paper = bool(strategy_config.get("paper_trading", True))
    balance = get_available_balance(paper)
    reserved = 0.0
    for s in state.get("strategies", {}).values():
        if not s.get("running"):
            continue
        s_paper = bool((s.get("config") or {}).get("paper_trading", True))
        if s_paper != paper:
            continue
        reserved += get_reserved_capital(s["type"], s.get("config", {}), balance)
    new_reserved = get_reserved_capital(strategy_type, strategy_config, balance)
    if reserved + new_reserved > balance:
        pool = "paper" if paper else "live"
        return False, (f"Insufficient balance: strategy requires ${new_reserved:.2f} "
                       f"but only ${balance - reserved:.2f} remains available "
                       f"(total {pool} balance: ${balance:.2f})")
    return True, None

def default_strategy_paper(strategy_type):
    """Default paper flag for a strategy type.

    AI Trading is paper-by-default (never auto-enables live). All other
    strategies (grid, dca, limit, etc.) default to LIVE, unless the license is
    invalid — in which case a global paper-only lock forces paper everywhere.
    """
    if strategy_type == "ai_trading":
        return True
    return not state.get("license_valid", True)

def is_strategy_running(sid, expected_type=None):
    if sid:
        return state.get("strategies", {}).get(sid, {}).get("running", False)
    else:
        if expected_type:
            return state.get("running", False) and state.get("strategy") == expected_type
        return state.get("running", False)

class ThreadSafeState(dict):
    def __getitem__(self, key):
        thread_name = threading.current_thread().name
        strategies = dict.get(self, "strategies")
        if strategies and thread_name in strategies:
            strat = strategies[thread_name]
            if key == "running":
                return strat.get("running", False)
            elif key == "strategy":
                return strat.get("type")
            elif key == "pair":
                return strat.get("pair")
            elif key == "paper_trading":
                return strat.get("config", {}).get("paper_trading", True)
            elif key == "limit_side":
                return strat.get("config", {}).get("limit_side")
            elif key == "limit_amount_usdc":
                return strat.get("config", {}).get("limit_amount_usdc")
            elif key == "limit_price":
                return strat.get("config", {}).get("limit_price")
            elif key == "limit_order_type":
                return strat.get("config", {}).get("limit_order_type")
            elif key == "effective_mode":
                return strat.get("config", {}).get("effective_mode")
        return dict.__getitem__(self, key)

    def get(self, key, default=None):
        try:
            return self[key]
        except KeyError:
            return default

    def __setitem__(self, key, value):
        thread_name = threading.current_thread().name
        strategies = dict.get(self, "strategies")
        if strategies and thread_name in strategies:
            strat = strategies[thread_name]
            if key == "running":
                strat["running"] = value
                if not value:
                    if strat.get("status") not in ("FILLED", "REJECTED"):
                        strat["status"] = "STOPPED"
            elif key == "strategy":
                strat["type"] = value
            elif key == "error":
                strat["error"] = value
        dict.__setitem__(self, key, value)

# ── Bot State ─────────────────────────────────────────────────────────────────
state = ThreadSafeState({
    "running":       False,
    "strategy":      None,
    "mode":          None,
    "exchange":      cfg["exchange"],
    "chain":         "solana",
    "pair":          cfg["pair"],
    "price":         0.0,
    "balance":       0.0,
    "sol_balance":   0.0,
    "sol_usdc":      0.0,
    "sol_usdt":      0.0,
    "sol_native":    0.0,
    "positions":     [],
    "trades":        [],
    "pnl":           0.0,
    "daily_loss":    0.0,
    "log":           [],
    "error":         None,
    "arb_opps":      [],
    "paper_trading": _load_paper_mode(cfg["paper_trading"]),
    "license_valid":  True,
    "license_type":   "demo",
    "license_expires": None,
    "license_days_left": None,
    "trading_lock":  False,   # Prevent simultaneous trades
    "last_trade_time": 0,     # Cooldown between trades
    # Dashboard UI fields
    "paused":        False,
    "win_rate":      0,
    "avg_profit":    0.0,
    "trades_count":  0,
    
    "best_trade":    None,
    "trades_list":   [],
    # Per-pair completed-trade metrics used by dashboard summary cards.
    "pair_stats":    {},
    "positions_list": [],
    "config":        {"risk_pct": cfg.get("risk_pct",2), "max_pos": cfg.get("max_pos",500), "grid_stop_loss_pct": cfg.get("grid_stop_loss_pct",5), "trailing_pct": cfg.get("trailing_pct",0.5), "partial_sell_pct": cfg.get("partial_sell_pct",50), "base_spread": cfg.get("base_spread",0.05), "auto_compound": cfg.get("auto_compound",True), "dynamic_spread": cfg.get("dynamic_spread",True)},
    "last_trade":    None,
    "price_history": [],
    "price_history_pairs": {},
    "grid_levels":   [],
    "grid_buy_zone": 0.0,
    "grid_filled":   {},
    "grid_trailing_active": False,
    "grid_trailing_high": 0.0,
    "grid_mid_idx": 0,
    "positions_count": 0,
    "compound_profit":  0.0,
    "partial_positions": {},
    "active_pairs":   [],
    "grid_pairs":     {},
    "daily_pnl":      0.0,
    "peak_balance":   0.0,
    "dip_active":     False,
    "dip_24h_high":   0.0,
    "last_midnight":  0,
    "emergency_stop":  False,
    "strategies":      {},
})

def send_telegram(msg):
    # Telegram requires the bot token in its endpoint, but POST avoids token-bearing
    # GET requests and prevents credentials from being sent via query parameters.
    token = _secret("TG_BOT_TOKEN")
    chat_id = _secret("TG_CHAT_ID")
    if not token or not chat_id:
        return
    try:
        r = requests.post("https://api.telegram.org/bot" + token + "/sendMessage",
            json={"chat_id": chat_id, "text": msg, "parse_mode": "HTML"},
            timeout=5)
        if r.status_code != 200:
            log("Telegram error "+str(r.status_code)+": "+r.text[:200], "WARN")
    except Exception as e:
        log("Telegram send failed: "+str(e), "WARN")

def log(msg, level="INFO"):
    ts = time.strftime("%H:%M:%S")
    entry = "["+ts+"] ["+level+"] "+msg
    print(entry)
    with _state_lock:
        state["log"].insert(0, entry)
        if len(state["log"]) > 150:
            state["log"] = state["log"][:150]
        thread_name = threading.current_thread().name
        strategies = state.get("strategies")
        if strategies and thread_name in strategies:
            tail = strategies[thread_name].setdefault("log_tail", [])
            tail.insert(0, entry)
            if len(tail) > 40:
                del tail[40:]

# ── Price Feeds (Kraken — no API key needed) ──────────────────────────────────
KRAKEN_PAIRS = {
    "BTC/USDT": "XBTUSD", "ETH/USDT": "ETHUSD", "BNB/USDT": "BNBUSD",
    "SOL/USDT": "SOLUSD", "ARB/USDT": "ARBUSD", "MATIC/USDT": "MATICUSD",
    "AVAX/USDT": "AVAXUSD", "LINK/USDT": "LINKUSD", "UNI/USDT": "UNIUSD",
    "BTC/USDC": "XBTUSD", "ETH/USDC": "ETHUSD", "SOL/USDC": "SOLUSD",
    "BNB/USDC": "BNBUSD", "MATIC/USDC": "MATICUSD",
}

def get_price_kraken(pair):
    try:
        kraken_pair = KRAKEN_PAIRS.get(pair, pair.replace("/","").replace("USDT","USD").replace("USDC","USD"))
        r = requests.get("https://api.kraken.com/0/public/Ticker", params={"pair": kraken_pair}, timeout=5)
        data = r.json()
        if not data.get("error"):
            result = data.get("result", {})
            key = list(result.keys())[0] if result else None
            if key:
                return float(result[key]["c"][0])
    except Exception as ex:
        log("Kraken price error: "+str(ex), "ERROR")
    return 0.0

def get_price_coingecko(token):
    try:
        ids = {"BTC":"bitcoin","ETH":"ethereum","BNB":"binancecoin","SOL":"solana","MATIC":"matic-network","ARB":"arbitrum"}
        cid = ids.get(token.split("/")[0], token.split("/")[0].lower())
        r = requests.get("https://api.coingecko.com/api/v3/simple/price", params={"ids":cid,"vs_currencies":"usd"}, timeout=5)
        data = r.json()
        return float(data.get(cid,{}).get("usd",0))
    except Exception as e:
        log("CoinGecko error: "+str(e), "WARN")
        return 0.0

def get_price_raydium(pair):
    """Get price from Raydium pool (matches execution price)."""
    try:
        token = pair.split("/")[0]
        token_upper = token.upper()
        usdc_mint = SOL_TOKENS.get("USDC", "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v")
        token_mint = SOL_TOKENS.get(token_upper)
        if not token_mint:
            return 0.0
        # Quote a small amount (0.01 token) to avoid liquidity issues, then scale
        decimals = TOKEN_DECIMALS.get(token_upper, 6)
        small_amount = 10 ** (decimals - 2)  # 0.01 of the token
        if small_amount < 1000:
            small_amount = 10 ** decimals  # fallback to 1 full unit for low-dec tokens
        quote = raydium_get_quote(token_mint, usdc_mint, small_amount, "100")
        if quote and quote.get("data") and quote["data"].get("outputAmount"):
            out = int(quote["data"]["outputAmount"])  # USDC units (6 decimals)
            # Scale up: output for 0.01 token * 100 = price for 1 token
            scale = (10 ** decimals) / small_amount
            price = (out / 10**6) * scale
            if price > 0:
                return price
    except Exception as ex:
        log("Raydium price error: "+str(ex), "WARN")
    return 0.0

def get_price_jupiter(pair):
    """Get price from Jupiter quote API (works for any token on Solana)."""
    try:
        token = pair.split("/")[0].upper()
        token_mint = SOL_TOKENS.get(token)
        if not token_mint:
            try:
                r = requests.get("https://token.jup.ag/all", timeout=5)
                for t in r.json():
                    if t.get("symbol","").upper() == token:
                        token_mint = t["address"]
                        SOL_TOKENS[token] = token_mint
                        log(f"Found {token} mint via Jupiter: {token_mint[:8]}...")
                        break
            except: pass
        if not token_mint:
            return 0.0
        usdc_mint = SOL_TOKENS.get("USDC", "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v")
        decimals = TOKEN_DECIMALS.get(token, 6)
        amount = 10 ** decimals
        url = f"https://quote-api.jup.ag/v6/quote?inputMint={token_mint}&outputMint={usdc_mint}&amount={amount}&slippageBps=100"
        r = requests.get(url, timeout=5)
        data = r.json()
        if data.get("outAmount"):
            price = int(data["outAmount"]) / 10**6
            if price > 0:
                return price
    except Exception as ex:
        log("Jupiter price error: "+str(ex), "WARN")
    return 0.0

def get_price_dexscreener(pair):
    """Get price from DexScreener API (works for obscure meme coins)."""
    try:
        token = pair.split("/")[0].upper()
        token_mint = SOL_TOKENS.get(token)
        if not token_mint:
            return 0.0
        url = f"https://api.dexscreener.com/latest/dex/tokens/{token_mint}"
        r = requests.get(url, timeout=5)
        data = r.json()
        pairs_resp = data.get("pairs", [])
        if pairs_resp:
            best = None
            for p in pairs_resp:
                if p.get("quoteToken",{}).get("symbol","") in ("USDC","USDT","SOL"):
                    liq = float(p.get("liquidity",{}).get("usd",0))
                    if best is None or liq > float(best.get("liquidity",{}).get("usd",0)):
                        best = p
            if best:
                price = float(best.get("priceUsd", 0))
                if price > 0:
                    return price
    except Exception as ex:
        log("DexScreener price error: "+str(ex), "WARN")
    return 0.0

def get_price_jup_dex(pair):
    price = get_price_jupiter(pair)
    if price > 0:
        state["price"] = price
        return price
    return get_price_dexscreener(pair)
def get_price(pair):
    price = get_price_raydium(pair)
    if price > 0:
        state["price"] = price
        return price
    price = get_price_jup_dex(pair)
    if price > 0:
        state["price"] = price
        return price
    price = get_price_kraken(pair)
    if price <= 0:
        price = get_price_coingecko(pair)
    state["price"] = price
    return price

# ── CEX Trading ───────────────────────────────────────────────────────────────
CEX_CONFIGS = {
    "binance": {"base":"https://api.binance.com","sign":"sha256"},
    "bybit":   {"base":"https://api.bybit.com","sign":"sha256"},
    "okx":     {"base":"https://www.okx.com","sign":"sha256"},
    "kraken":  {"base":"https://api.kraken.com","sign":"sha512"},
    "kucoin":  {"base":"https://api.kucoin.com","sign":"sha256"},
    "lbank":   {"base":"https://api.lbank.info","sign":"md5"},
}

# Reusable ccxt exchange instances to avoid reloading markets on every call
_cex_exchanges = {}

def _get_cex_exchange(name):
    """Get or create a cached ccxt exchange instance."""
    global _cex_exchanges
    if name not in _cex_exchanges:
        import ccxt
        opts = {'apiKey': _secret("API_KEY"), 'secret': _secret("API_SECRET")}
        if name == 'lbank':
            opts['options'] = {
                'createMarketBuyOrderRequiresPrice': False,
            }
        ex = getattr(ccxt, name)(opts)
        ex.load_markets()
        # Force LBank to use HmacSHA256 regardless of secret length
        if name == 'lbank':
            ex.options['createOrder'] = ex.options.get('createOrder', {})
            ex.options['createOrder']['method'] = 'spotPrivatePostSupplementCreateOrder'
        _cex_exchanges[name] = ex
    return _cex_exchanges[name]

def cex_get_balance():
    exchange = state["exchange"]
    # Rate-limit self-protection: don't check more than once per 60s per exchange
    now = time.time()
    last_check = state.get("_last_balance_check", {})
    last_time = last_check.get(exchange, 0)
    if now - last_time < 60:
        return state.get("balance", 0.0)
    last_check[exchange] = now
    state["_last_balance_check"] = last_check

    try:
        if exchange == "binance":
            ts = str(int(time.time()*1000))
            params = "timestamp="+ts
            sig = hmac.new(_secret("API_SECRET").encode(), params.encode(), hashlib.sha256).hexdigest()
            r = requests.get("https://api.binance.com/api/v3/account",
                headers={"X-MBX-APIKEY": _secret("API_KEY")},
                params={"timestamp":ts,"signature":sig}, timeout=5)
            data = r.json()
            for b in data.get("balances",[]):
                if b["asset"] == "USDT":
                    state["balance"] = float(b["free"])
                    return float(b["free"])
        elif exchange == "bybit":
            ts = str(int(time.time()*1000))
            params = "timestamp="+ts+"&api_key="+_secret("API_KEY")
            sig = hmac.new(_secret("API_SECRET").encode(), params.encode(), hashlib.sha256).hexdigest()
            r = requests.get("https://api.bybit.com/v2/private/wallet/balance",
                params={"timestamp":ts,"api_key":_secret("API_KEY"),"sign":sig,"coin":"USDT"}, timeout=5)
            data = r.json()
            usdt = data.get("result",{}).get("USDT",{}).get("available_balance",0)
            state["balance"] = float(usdt)
            return float(usdt)
        elif exchange == "okx":
            import base64, datetime
            ts = datetime.datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%S.000Z')
            path = "/api/v5/account/balance"
            sign_str = ts+"GET"+path+""
            sig = base64.b64encode(hmac.new(_secret("API_SECRET").encode(),sign_str.encode(),hashlib.sha256).digest()).decode()
            r = requests.get("https://www.okx.com"+path,
                headers={"OK-ACCESS-KEY":_secret("API_KEY"),"OK-ACCESS-SIGN":sig,"OK-ACCESS-TIMESTAMP":ts,"OK-ACCESS-PASSPHRASE":os.environ.get("OKX_PASSPHRASE","")}, timeout=5)
            data = r.json()
            for d in data.get("data",[{}])[0].get("details",[]):
                if d.get("ccy")=="USDT":
                    state["balance"]=float(d.get("availBal",0)); return state["balance"]
        elif exchange == "lbank":
            ex = _get_cex_exchange('lbank')
            bal = ex.fetch_balance()
            usdt = bal.get('USDT', {}).get('free', 0)
            state['balance'] = usdt
            return usdt
        elif exchange == "kucoin":
            ts = str(int(time.time()*1000))
            path = "/api/v1/accounts"
            sign_str = ts+"GET"+path
            sig = hmac.new(_secret("API_SECRET").encode(), sign_str.encode(), hashlib.sha256).hexdigest()
            r = requests.get("https://api.kucoin.com"+path,
                headers={"KC-API-KEY":_secret("API_KEY"),"KC-API-SIGN":sig,"KC-API-TIMESTAMP":ts,"KC-API-PASSPHRASE":os.environ.get("KUCOIN_PASSPHRASE","")}, timeout=5)
            data = r.json()
            for a in data.get("data",[]):
                if a.get("currency")=="USDT" and a.get("type")=="trade":
                    state["balance"]=float(a.get("available",0)); return state["balance"]
        elif exchange == "kraken":
            ts = str(int(time.time()))
            path = "/0/private/Balance"
            sig_str = "/0/private/Balance"+hashlib.sha256((str(ts)+"nonce="+ts).encode()).hexdigest()
            sig = base64.b64encode(hmac.new(_secret("API_SECRET").encode(), sig_str.encode(), hashlib.sha512).digest()).decode()
            r = requests.post("https://api.kraken.com"+path,
                headers={"API-Key":_secret("API_KEY"),"API-Sign":sig},
                data={"nonce": ts}, timeout=5)
            data = r.json()
            if not data.get("error"):
                for asset, bal in data.get("result",{}).items():
                    if asset in ("USDT", "ZUSD"):
                        state["balance"] = float(bal)
                        return float(bal)
    except Exception as ex:
        log("Balance error ("+exchange+"): "+str(ex), "ERROR")
    return 0.0

def cex_place_order(pair, side, amount):
    if state.get("paper_trading", False):
        log("[CEX] PAPER MODE — skipping " + side + " " + pair + " " + str(amount))
        return True
    exchange = state["exchange"]
    try:
        sym = pair.replace("/","")
        if exchange == "binance":
            ts = str(int(time.time()*1000))
            params = "symbol="+sym+"&side="+side.upper()+"&type=MARKET&quantity="+str(amount)+"&timestamp="+ts
            sig = hmac.new(_secret("API_SECRET").encode(), params.encode(), hashlib.sha256).hexdigest()
            r = requests.post("https://api.binance.com/api/v3/order",
                headers={"X-MBX-APIKEY":_secret("API_KEY")},
                params={"symbol":sym,"side":side.upper(),"type":"MARKET","quantity":amount,"timestamp":ts,"signature":sig}, timeout=10)
            data = r.json()
            return data.get("orderId")
        elif exchange == "bybit":
            ts = str(int(time.time()*1000))
            body = json.dumps({"symbol":sym,"side":side.capitalize(),"orderType":"Market","qty":str(amount),"timeInForce":"GoodTillCancel"})
            sig = hmac.new(_secret("API_SECRET").encode(),(ts+_secret("API_KEY")+"5000"+body).encode(),hashlib.sha256).hexdigest()
            r = requests.post("https://api.bybit.com/v5/order/create",
                headers={"X-BAPI-API-KEY":_secret("API_KEY"),"X-BAPI-SIGN":sig,"X-BAPI-TIMESTAMP":ts,"X-BAPI-RECV-WINDOW":"5000","Content-Type":"application/json"},
                data=body, timeout=10)
            data = r.json()
            return data.get("result",{}).get("orderId")
        elif exchange == "lbank":
            lside = 'buy' if 'buy' in side.lower() else 'sell'
            try:
                ex = _get_cex_exchange('lbank')
                if lside == 'buy':
                    cost = amount * state.get("price", 1)
                    order = ex.create_order(pair, 'market', 'buy', cost, None, {
                        'createMarketBuyOrderRequiresPrice': False,
                    })
                else:
                    order = ex.create_order(pair, 'market', 'sell', amount, None)
                oid = order.get('id')
                if oid:
                    return oid
                info = order.get('info', {})
                log("LBank: " + str(info)[:200], "WARN")
            except Exception as e:
                log("LBank: " + str(e)[:200], "WARN")
        elif exchange == "okx":
            import base64, datetime
            ts = datetime.datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%S.000Z')
            path = "/api/v5/trade/order"
            body = json.dumps({
                "instId": sym + "-USDT" if not sym.endswith("USDT") else sym,
                "tdMode": "cash",
                "side": side.lower(),
                "ordType": "market",
                "sz": str(amount),
            })
            sign_str = ts+"POST"+path+body
            sig = base64.b64encode(hmac.new(_secret("API_SECRET").encode(),sign_str.encode(),hashlib.sha256).digest()).decode()
            r = requests.post("https://www.okx.com"+path,
                headers={"OK-ACCESS-KEY":_secret("API_KEY"),"OK-ACCESS-SIGN":sig,"OK-ACCESS-TIMESTAMP":ts,"OK-ACCESS-PASSPHRASE":os.environ.get("OKX_PASSPHRASE",""),"Content-Type":"application/json"},
                data=body, timeout=10)
            data = r.json()
            return data.get("data",[{}])[0].get("ordId")
        elif exchange == "kucoin":
            ts = str(int(time.time()*1000))
            path = "/api/v1/orders"
            body = json.dumps({
                "clientOid": ts,
                "side": side.lower(),
                "symbol": sym + "-USDT" if not sym.endswith("USDT") else sym,
                "type": "market",
                "size": str(amount),
            })
            sign_str = ts+"POST"+path+body
            sig = hmac.new(_secret("API_SECRET").encode(), sign_str.encode(), hashlib.sha256).hexdigest()
            r = requests.post("https://api.kucoin.com"+path,
                headers={"KC-API-KEY":_secret("API_KEY"),"KC-API-SIGN":sig,"KC-API-TIMESTAMP":ts,"KC-API-PASSPHRASE":os.environ.get("KUCOIN_PASSPHRASE",""),"Content-Type":"application/json"},
                data=body, timeout=10)
            data = r.json()
            return data.get("data",{}).get("orderId")
        elif exchange == "kraken":
            ts = str(int(time.time()))
            path = "/0/private/AddOrder"
            post_data = "pair="+sym+"&type="+("buy" if "buy" in side.lower() else "sell")+"&ordertype=market&volume="+str(amount)
            sig_str = "/0/private/AddOrder"+hashlib.sha256((str(ts)+post_data).encode()).hexdigest()
            sig = base64.b64encode(hmac.new(_secret("API_SECRET").encode(), sig_str.encode(), hashlib.sha512).digest()).decode()
            r = requests.post("https://api.kraken.com"+path,
                headers={"API-Key":_secret("API_KEY"),"API-Sign":sig},
                data=post_data, timeout=10)
            data = r.json()
            if not data.get("error"):
                return data.get("result",{}).get("txid",[None])[0]
    except Exception as ex:
        log("Order error ("+exchange+"): "+str(ex), "ERROR")
        return None

# ── DEX Trading ───────────────────────────────────────────────────────────────
ALCHEMY_KEY = os.environ.get("ALCHEMY_KEY", "")

def get_rpc(chain):
    alchemy_rpcs = {
        "ethereum": "https://eth-mainnet.g.alchemy.com/v2/"+ALCHEMY_KEY,
        "bsc":      "https://bnb-mainnet.g.alchemy.com/v2/"+ALCHEMY_KEY,
        "base":     "https://base-mainnet.g.alchemy.com/v2/"+ALCHEMY_KEY,
        "arbitrum": "https://arb-mainnet.g.alchemy.com/v2/"+ALCHEMY_KEY,
        "polygon":  "https://polygon-mainnet.g.alchemy.com/v2/"+ALCHEMY_KEY,
    }
    public_rpcs = {
        "ethereum": ["https://cloudflare-eth.com","https://rpc.ankr.com/eth"],
        "bsc":      ["https://bsc-dataseed1.binance.org","https://rpc.ankr.com/bsc"],
        "base":     ["https://mainnet.base.org","https://rpc.ankr.com/base"],
        "arbitrum": ["https://arb1.llamarpc.com","https://rpc.ankr.com/arbitrum"],
        "polygon":  ["https://polygon-rpc.com","https://rpc.ankr.com/polygon"],
    }
    if ALCHEMY_KEY:
        return [alchemy_rpcs.get(chain, alchemy_rpcs["ethereum"])]
    return public_rpcs.get(chain, public_rpcs["ethereum"])

CHAIN_CONFIG = {
    "ethereum": {"chain_id":1,    "name":"Ethereum"},
    "bsc":      {"chain_id":56,   "name":"BNB Chain"},
    "base":     {"chain_id":8453, "name":"Base"},
    "arbitrum": {"chain_id":42161,"name":"Arbitrum"},
    "polygon":  {"chain_id":137,  "name":"Polygon"},
    "monad":    {"chain_id":10143,"name":"Monad", "rpc":"https://rpc.monad.xyz", "native":"MON"},
}

TOKENS = {
    "ethereum": {"USDT":"0xdAC17F958D2ee523a2206206994597C13D831ec7","WETH":"0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2","WBTC":"0x2260FAC5E5542a773Aa44fBCfeDf7C193bc2C599"},
    "bsc":      {"USDT":"0x55d398326f99059fF775485246999027B3197955","WBNB":"0xbb4CdB9CBd36B01bD1cBaEBF2De08d9173bc095c","BTCB":"0x7130d2A12B9BCbFAe4f2634d864A1Ee1Ce3Ead9c"},
    "base":     {"USDT":"0xfde4C96c8593536E31F229EA8f37b2ADa2699bb2","WETH":"0x4200000000000000000000000000000000000006"},
    "arbitrum": {"USDT":"0xFd086bC7CD5C481DCC9C85ebE478A1C0b69FCbb9","WETH":"0x82aF49447D8a07e3bd95BD0d56f35241523fBab1"},
    "polygon":  {"USDT":"0xc2132D05D31c914a87C6611C10748AEb04B58e8F","WMATIC":"0x0d500B1d8E8eF31E21C99d1Db9A6444d3ADf1270"},
    "monad":    {},  # TODO: add Monad token addresses when available
}

def dex_get_quote_1inch(chain, from_token, to_token, amount_wei):
    try:
        chain_ids = {"ethereum":1,"bsc":56,"base":8453,"arbitrum":42161,"polygon":137,"monad":10143}
        cid = chain_ids.get(chain, 1)
        r = requests.get(
            "https://api.1inch.dev/swap/v6.0/"+str(cid)+"/quote",
            headers={"Authorization":"Bearer "+os.environ.get("ONEINCH_API_KEY","")},
            params={"src":from_token,"dst":to_token,"amount":str(amount_wei)}, timeout=5)
        data = r.json()
        return int(data.get("dstAmount", 0))
    except Exception as ex:
        log("1inch quote error: "+str(ex), "ERROR")
    return 0

def dex_get_quote_uniswap(chain, from_token, to_token, amount_wei):
    try:
        chain_ids = {"ethereum":1,"bsc":56,"base":8453,"arbitrum":42161,"polygon":137,"monad":10143}
        cid = chain_ids.get(chain, 1)
        r = requests.get(
            "https://api.uniswap.org/v1/quote",
            params={"protocols":"v2,v3","tokenInAddress":from_token,"tokenInChainId":cid,
                    "tokenOutAddress":to_token,"tokenOutChainId":cid,"amount":str(amount_wei),"type":"exactIn"}, timeout=5)
        data = r.json()
        return int(float(data.get("quote","0")) * 1e6)
    except Exception as ex:
        log("Uniswap quote error: "+str(ex), "ERROR")
    return 0

def dex_best_quote(chain, from_token, to_token, amount_wei):
    q1 = dex_get_quote_1inch(chain, from_token, to_token, amount_wei)
    q2 = dex_get_quote_uniswap(chain, from_token, to_token, amount_wei)
    if q1 >= q2:
        return q1, "1inch"
    return q2, "Uniswap"

def dex_swap(chain, from_token, to_token, amount_usd, price):
    try:
        amount_wei = int(amount_usd * 1e6)
        best_amount, router = dex_best_quote(chain, from_token, to_token, amount_wei)
        log("DEX swap via "+router+": $"+str(amount_usd)+" on "+CHAIN_CONFIG[chain]["name"])
        token_amount = amount_usd / price
        trade = {"time":time.strftime("%H:%M:%S"),"side":"DEX-BUY","price":price,"amount":round(token_amount,6),"router":router,"chain":chain}
        state["trades"].append(trade)
        state["positions"].append({"price":price,"amount":round(token_amount,6),"side":"buy","router":router,"chain":chain})
        log("Swap executed via "+router+" on "+CHAIN_CONFIG[chain]["name"])
        return True
    except Exception as ex:
        log("DEX swap error: "+str(ex), "ERROR")
    return False

def dex_get_balance():
    try:
        chain = state["chain"]
        wallet = cfg["wallet"]
        if not wallet:
            log("No wallet address — add WALLET_ADDRESS to Render environment", "WARN")
            return 0.0

        # Try Alchemy Token API first (most reliable)
        if ALCHEMY_KEY:
            try:
                chain_map = {
                    "ethereum":"eth-mainnet","bsc":"bnb-mainnet",
                    "base":"base-mainnet","arbitrum":"arb-mainnet","polygon":"polygon-mainnet"
                }
                network = chain_map.get(chain, "eth-mainnet")
                url = "https://"+network+".g.alchemy.com/v2/"+ALCHEMY_KEY
                payload = {
                    "jsonrpc":"2.0","method":"alchemy_getTokenBalances",
                    "params":[wallet,["0xdAC17F958D2ee523a2206206994597C13D831ec7"]],
                    "id":1
                }
                r = requests.post(url, json=payload, timeout=8)
                data = r.json()
                log("Alchemy token response: "+str(data)[:80])
                balances = data.get("result",{}).get("tokenBalances",[])
                if balances:
                    hex_val = balances[0].get("tokenBalance","0x0")
                    if hex_val and hex_val != "0x0" and hex_val != "0x":
                        balance = int(hex_val, 16) / 1e6
                        state["balance"] = balance
                        log("USDT Balance: $"+str(round(balance,2)))
                        return balance
            except Exception as ex:
                log("Alchemy token API error: "+str(ex), "WARN")

        # Fallback: native ETH balance
        rpcs = get_rpc(chain)
        for rpc in rpcs:
            try:
                payload = {"jsonrpc":"2.0","method":"eth_getBalance","params":[wallet,"latest"],"id":1}
                r = requests.post(rpc, json=payload, timeout=8)
                result = r.json().get("result","0x0")
                if result and result != "0x" and result != "0x0":
                    native = int(result, 16) / 1e18
                    price = get_price_kraken("ETH/USDT") or 3000
                    usd_val = round(native * price, 2)
                    state["balance"] = usd_val
                    log("ETH Balance: "+str(round(native,6))+" = $"+str(usd_val))
                    return usd_val
            except Exception as ex:
                log("Native balance failed: "+str(ex), "WARN")
                continue

        log("All balance checks failed", "WARN")
    except Exception as ex:
        log("DEX balance error: "+str(ex), "ERROR")
    return 0.0

def start_background_loops():
    """Start continuous price + balance + arb scanning regardless of strategy"""
    def price_loop():
        while True:
            try:
                pair = state.get("pair","ETH/USDT")
                p = get_price(pair)
                if p > 0:
                    state["price"] = p
                    now = int(time.time())
                    point = {"time": now, "value": p}
                    state["price_history"].append(point)
                    if len(state["price_history"]) > 4320:
                        state["price_history"] = state["price_history"][-4320:]
                    pair_history = state["price_history_pairs"].setdefault(pair, [])
                    # Keep chart data scoped to the selected pair; never mix BTC with another asset.
                    if not pair_history or pair_history[-1].get("time") != now:
                        pair_history.append(point)
                    else:
                        pair_history[-1] = point
                    if len(pair_history) > 4320:
                        state["price_history_pairs"][pair] = pair_history[-4320:]
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
                if cfg.get("wallet"):
                    dex_get_balance()
                if cfg.get("sol_wallet"):
                    sol_get_balance()
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
# Jupiter API — api.jup.ag/swap/v1 (current as of 2026)
# Set JUPITER_API_KEY in Render env vars if using paid tier
JUPITER_API     = "https://api.jup.ag/swap/v1"
JUPITER_API_KEY = os.environ.get("JUPITER_API_KEY", "")

from token_registry import get_registry_entry, is_approved, authorize_trade, add_pending_token, register_verified_token

# Solana token mints — presence here is NOT authorization to trade; every
# swap is gated through token_registry.authorize_trade() before execution.
SOL_TOKENS = {
    "USDC":  "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
    "USDT":  "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB",
    "SOL":   "So11111111111111111111111111111111111111112",
    "BTC":   "3NZ9JMVBmGAqocybic2c7LQCJScmgsAZ6vQqTDzcqmJh",
    "ETH":   "7vfCXTUXx5WJV5JADk17DUJ4ksgau7utNKj4b963voxs",
    "BNB":   "9gP2kCy3wA1ctvYWQk75guqXuzoJGLIDs5oPHkHGs89",
    "JUP":   "JUPyiwrYJFskUPiHa7hkeR8VUtAeFoSYbKedZNsDvCN",
    "BONK":  "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263",
    "WIF":   "EKpQGSJtjMFqKZ9KQanSqYXRcF8fBopzLHYxdM65zcjm",
    "MATIC": "Gz7VkD4MacbEB6yC5XD3HcumEiYx2EtDYYrfikGsvopG",
    "SPCX":  "SPCXxcqXj6e5dJDVNovHN8744zkbhM2bYudU45BimGb",
}

# Shared Solana RPC endpoints — set SOLANA_RPC env var to override all
BASE58_ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
def validate_solana_mint(mint):
    """Validate canonical base58 Solana public-key length without symbol inference."""
    if not isinstance(mint, str) or not (32 <= len(mint) <= 44) or any(c not in BASE58_ALPHABET for c in mint):
        return False
    n = 0
    for c in mint: n = n * 58 + BASE58_ALPHABET.index(c)
    raw = n.to_bytes((n.bit_length() + 7) // 8 or 1, "big")
    raw = b"\x00" * (len(mint) - len(mint.lstrip("1"))) + raw
    return len(raw) == 32
SOLANA_RPC = os.environ.get("SOLANA_RPC", "")
SOL_RPCS = [SOLANA_RPC] if SOLANA_RPC else [
    "https://api.mainnet-beta.solana.com",
    "https://rpc.ankr.com/solana",
    "https://solana-rpc.publicnode.com",
]

def verify_token_authenticity(mint, claimed_symbol=""):
    """
    Live authenticity check for a newly-supplied mint. Combines an on-chain
    lookup (does the mint exist, are mint/freeze authority revoked, real
    decimals) with DexScreener market data (liquidity, pool age, on-chain
    name/symbol) so a new token can be evaluated automatically instead of
    trusting a symbol label alone.

    Returns a dict consumed by token_registry.evaluate_verification():
        exists, decimals, mint_authority, freeze_authority, supply_raw,
        liquidity_usd, pool_age_hours, dex_symbol, dex_name
    Any field that couldn't be determined is left as None so the registry's
    evaluation logic treats it as a hard-fail rather than guessing.
    """
    result = {
        "exists": False, "decimals": None, "mint_authority": None,
        "freeze_authority": None, "supply_raw": None, "liquidity_usd": None,
        "pool_age_hours": None, "dex_symbol": None, "dex_name": None,
    }

    # ── On-chain mint account lookup ──
    payload = {
        "jsonrpc": "2.0", "id": 1,
        "method": "getAccountInfo",
        "params": [mint, {"encoding": "jsonParsed"}],
    }
    for rpc in SOL_RPCS:
        try:
            r = requests.post(rpc, json=payload, timeout=8)
            data = r.json().get("result", {}).get("value")
            if not data:
                continue
            parsed = data.get("data", {}).get("parsed", {})
            info = parsed.get("info", {})
            if parsed.get("type") != "mint" or not info:
                continue
            result["exists"] = True
            result["decimals"] = info.get("decimals")
            result["mint_authority"] = info.get("mintAuthority")
            result["freeze_authority"] = info.get("freezeAuthority")
            result["supply_raw"] = info.get("supply")
            break
        except Exception as e:
            log("verify_token_authenticity RPC error: " + str(e), "WARN")
            continue

    if not result["exists"]:
        log("Token verification: mint " + mint[:12] + "... not found on-chain", "WARN")
        return result

    # ── DexScreener liquidity, pool age, and name/symbol cross-check ──
    try:
        r = requests.get("https://api.dexscreener.com/latest/dex/tokens/" + mint, timeout=8)
        pairs = r.json().get("pairs", []) or []
        best = None
        for p in pairs:
            if p.get("quoteToken", {}).get("symbol", "") not in ("USDC", "USDT", "SOL"):
                continue
            liq = float(p.get("liquidity", {}).get("usd", 0) or 0)
            if best is None or liq > float(best.get("liquidity", {}).get("usd", 0) or 0):
                best = p
        if best:
            result["liquidity_usd"] = float(best.get("liquidity", {}).get("usd", 0) or 0)
            result["dex_symbol"] = best.get("baseToken", {}).get("symbol")
            result["dex_name"] = best.get("baseToken", {}).get("name")
            created_ms = best.get("pairCreatedAt")
            if created_ms:
                result["pool_age_hours"] = max(0.0, (time.time() * 1000 - created_ms) / 3_600_000)
    except Exception as e:
        log("verify_token_authenticity DexScreener error: " + str(e), "WARN")

    return result

def get_holder_concentration(mint, decimals, supply_raw):
    """
    Top-holder concentration via getTokenLargestAccounts (returns up to the
    20 largest token ACCOUNTS, not necessarily 20 unique wallets — a wallet
    can hold multiple accounts for the same mint, though that's uncommon in
    practice). Used as a heuristic, not an authoritative wallet-level count.
    """
    out = {"top1_holder_pct": None, "top10_holder_pct": None, "accounts_sampled": 0}
    if not decimals or not supply_raw:
        return out
    payload = {"jsonrpc": "2.0", "id": 1, "method": "getTokenLargestAccounts", "params": [mint]}
    for rpc in SOL_RPCS:
        try:
            r = requests.post(rpc, json=payload, timeout=8)
            accounts = r.json().get("result", {}).get("value", []) or []
            if not accounts:
                continue
            amounts = [float(a.get("uiAmount") or 0) for a in accounts]
            supply_ui = float(supply_raw) / (10 ** decimals)
            if supply_ui <= 0:
                continue
            out["top1_holder_pct"] = round(min((amounts[0] / supply_ui) * 100, 100.0), 2) if amounts else None
            out["top10_holder_pct"] = round(min((sum(amounts[:10]) / supply_ui) * 100, 100.0), 2)
            out["accounts_sampled"] = len(amounts)
            break
        except Exception as e:
            log("get_holder_concentration RPC error: " + str(e), "WARN")
            continue
    return out

def get_rugcheck_report(mint):
    """
    Best-effort pull from RugCheck.xyz's public report API — covers LP
    lock/burn status and an aggregate risk score, which is a specialized
    analysis this codebase doesn't reimplement itself. If the service is
    unreachable or its response shape has changed, this fails soft: the
    rest of the scan proceeds without it rather than blocking on it.
    """
    try:
        r = requests.get("https://api.rugcheck.xyz/v1/tokens/" + mint + "/report", timeout=10)
        if r.status_code != 200:
            return {"available": False, "reason": "HTTP " + str(r.status_code)}
        data = r.json()
        risks = data.get("risks", [])
        risk_names = [x.get("name") for x in risks if isinstance(x, dict) and x.get("name")] if isinstance(risks, list) else []
        return {
            "available": True,
            "score": data.get("score"),
            "risks": risk_names,
            "lp_locked_pct": data.get("markets", [{}])[0].get("lp", {}).get("lpLockedPct") if data.get("markets") else None,
        }
    except Exception as e:
        log("get_rugcheck_report error: " + str(e), "WARN")
        return {"available": False, "reason": str(e)[:100]}

def scan_token_full(mint, symbol=""):
    """
    Combined safety-scanner report for a single mint. Read-only — never
    writes to the token registry, unlike register_verified_token(). This
    is the diagnostic panel; /add_token remains the only path that actually
    makes a token tradeable.
    """
    verification = verify_token_authenticity(mint, symbol)
    holders = {"top1_holder_pct": None, "top10_holder_pct": None, "accounts_sampled": 0}
    rugcheck = {"available": False, "reason": "skipped — mint not found on-chain"}
    status, reg_warnings = "REJECTED", ["Mint does not exist on-chain"]

    if verification["exists"]:
        holders = get_holder_concentration(mint, verification.get("decimals"), verification.get("supply_raw"))
        rugcheck = get_rugcheck_report(mint)
        from token_registry import evaluate_verification
        status, reg_warnings = evaluate_verification(verification, symbol or (verification.get("dex_symbol") or ""))

    extra_flags = []
    if holders.get("top1_holder_pct") is not None and holders["top1_holder_pct"] >= 40:
        extra_flags.append(f"Top holder controls {holders['top1_holder_pct']}% of supply — high dump risk")
    elif holders.get("top1_holder_pct") is not None and holders["top1_holder_pct"] >= 20:
        extra_flags.append(f"Top holder controls {holders['top1_holder_pct']}% of supply — worth watching")
    if rugcheck.get("available") and rugcheck.get("lp_locked_pct") is not None and rugcheck["lp_locked_pct"] < 50:
        extra_flags.append(f"RugCheck reports only {rugcheck['lp_locked_pct']}% of LP locked/burned")
    if rugcheck.get("available") and rugcheck.get("risks"):
        extra_flags.append("RugCheck flags: " + ", ".join(rugcheck["risks"][:5]))

    all_flags = list(reg_warnings) + extra_flags
    if status == "REJECTED":
        rating = "CRITICAL"
    elif status == "APPROVED" and not all_flags:
        rating = "LOW RISK"
    elif status == "APPROVED":
        rating = "LOW-MEDIUM RISK"
    elif any("dump risk" in f or "unlimited" in f for f in all_flags):
        rating = "HIGH RISK"
    else:
        rating = "MEDIUM RISK"

    return {
        "mint": mint, "symbol": symbol.upper() if symbol else None,
        "rating": rating, "registry_status": status,
        "verification": verification, "holders": holders, "rugcheck": rugcheck,
        "flags": all_flags, "scanned_at": int(time.time()),
    }

def sol_get_balance():
    """Get SOL + USDC + USDT balance. Tries multiple RPC endpoints for reliability."""
    rpcs = list(SOL_RPCS)
    if ALCHEMY_KEY:
        rpcs = ["https://solana-mainnet.g.alchemy.com/v2/"+ALCHEMY_KEY] + rpcs

    wallet = cfg["sol_wallet"]
    if not wallet:
        return 0.0

    def rpc_call(method, params):
        payload = {"jsonrpc":"2.0","id":1,"method":method,"params":params}
        for rpc in rpcs:
            try:
                r = requests.post(rpc, json=payload, timeout=8)
                result = r.json()
                if "result" in result:
                    return result["result"]
            except Exception as e:
                log("RPC error: "+str(e), "WARN")
                continue
        return None

    def get_token_balance(mint):
        """Helper to get balance of any SPL token by mint address."""
        raw = rpc_call("getTokenAccountsByOwner",
            [wallet, {"mint": mint}, {"encoding": "jsonParsed"}])
        if raw and raw.get("value"):
            return float(
                raw["value"][0]
                .get("account",{}).get("data",{}).get("parsed",{})
                .get("info",{}).get("tokenAmount",{}).get("uiAmount", 0) or 0
            )
        return 0.0

    try:
        # Get SOL native balance
        sol_raw = rpc_call("getBalance", [wallet])
        sol_amt = (sol_raw.get("value", 0) / 1e9) if isinstance(sol_raw, dict) else 0.0
        sol_price = get_price_kraken("SOL/USDT") or get_price_coingecko("SOL/USDT") or 150

        # Get USDC and USDT balances
        usdc = get_token_balance(SOL_TOKENS["USDC"])
        usdt = get_token_balance(SOL_TOKENS["USDT"])

        stable_total = round(usdc + usdt, 2)
        total_usd = round(sol_amt * sol_price + stable_total, 2)
        state["sol_balance"] = total_usd
        state["sol_usdc"]    = usdc
        state["sol_usdt"]    = usdt
        state["sol_native"]  = round(sol_amt * sol_price, 2)
        log("Solana balance: "+str(round(sol_amt,4))+" SOL + $"+str(usdc)+" USDC + $"+str(usdt)+" USDT = $"+str(total_usd))
        return total_usd
    except Exception as ex:
        log("Solana balance error: "+str(ex), "ERROR")
    return 0.0

def jupiter_get_quote(from_mint, to_mint, amount_lamports):
    """Get best swap quote from Jupiter aggregator"""
    try:
        r = requests.get(JUPITER_API+"/quote", params={
            "inputMint": from_mint,
            "outputMint": to_mint,
            "amount": str(amount_lamports),
            "slippageBps": "50",
        }, timeout=8)
        data = r.json()
        return data
    except Exception as ex:
        log("Jupiter quote error: "+str(ex), "ERROR")
    return None

def raydium_get_quote(from_mint, to_mint, amount, slippage_bps="200"):
    """
    Get swap quote from Raydium Trade API.
    Confirmed endpoint: transaction-v1.raydium.io/compute/swap-base-in
    Returns full response object — swapResponse in the TX payload needs the complete object.
    Handles 429 with Retry-After backoff.
    """
    for attempt in range(3):
        try:
            r = requests.get(
                "https://transaction-v1.raydium.io/compute/swap-base-in",
                params={
                    "inputMint":   from_mint,
                    "outputMint":  to_mint,
                    "amount":      str(amount),
                    "slippageBps": slippage_bps,
                    "txVersion":   "V0",
                },
                timeout=10
            )
            if r.status_code == 429:
                wait = int(r.headers.get("Retry-After", 5))
                log("Raydium quote 429 — waiting "+str(wait)+"s", "WARN")
                time.sleep(wait)
                continue
            if r.status_code != 200:
                log("Raydium quote status: "+str(r.status_code), "WARN")
                return None
            data = r.json()
            if not data.get("success"):
                log("Raydium quote failed: "+str(data.get("msg","")), "WARN")
                return None
            return data  # Full response needed by transaction endpoint
        except Exception as ex:
            log("Raydium quote error (attempt "+str(attempt+1)+"): "+str(ex)[:80], "WARN")
            time.sleep(2)
    return None

def _raydium_execute_swap(from_token, to_token, from_mint, to_mint,
                          amount_input, out_human, price, side, via,
                          lamports, raydium_quote, to_dec):
    """Execute a Raydium swap using the quote from raydium_get_quote."""
    if state["paper_trading"]:
        trade = {"time":time.strftime("%H:%M:%S"),"side":"[PAPER] "+side+via,
                 "price":price,"amount":out_human,"router":"Raydium","chain":"solana"}
        state["trades"].append(trade)
        return True, out_human

    try:
        from solders.keypair import Keypair
        from solders.transaction import VersionedTransaction
        from solders import message as solders_message
        import base64 as b64

        private_key = _secret("SOL_PRIVATE_KEY")
        wallet      = cfg.get("sol_wallet","")
        if not private_key or not wallet:
            log("SOL_PRIVATE_KEY or SOL_WALLET_ADDRESS not set", "WARN")
            return False, 0.0
        # Check USDC balance before attempting swap
        usdc_bal = state.get("sol_usdc", 0)
        if from_token in ("USDC","USDT") and amount_input > usdc_bal:
            log(f"Insufficient USDC: need ${amount_input:.2f}, have ${usdc_bal:.2f}", "WARN")
            return False, 0.0

        keypair = Keypair.from_base58_string(private_key)

        # ── ATA helpers ────────────────────────────────────────────────────
        def get_ata(wallet_addr, mint_addr):
            """Find existing Associated Token Account for a wallet+mint."""
            rpcs = list(SOL_RPCS)
            if ALCHEMY_KEY:
                rpcs = ["https://solana-mainnet.g.alchemy.com/v2/"+ALCHEMY_KEY] + rpcs
            payload = {
                "jsonrpc":"2.0","id":1,
                "method":"getTokenAccountsByOwner",
                "params":[wallet_addr, {"mint":mint_addr}, {"encoding":"jsonParsed"}]
            }
            for rpc in rpcs:
                try:
                    r = requests.post(rpc, json=payload, timeout=8)
                    accs = r.json().get("result",{}).get("value",[])
                    if accs:
                        return accs[0].get("pubkey")
                except Exception as e:
                    log("ATA error: "+str(e), "WARN")
                    continue
            return None

        def create_ata_if_missing(wallet_addr, mint_addr):
            """Create ATA on-chain if missing, return its address."""
            existing = get_ata(wallet_addr, mint_addr)
            if existing:
                return existing
            log("Creating ATA for mint "+mint_addr[:8]+"...", "WARN")
            try:
                from solders.pubkey import Pubkey
                from solders.hash import Hash
                from solders.instruction import AccountMeta, Instruction
                from solders.message import MessageV0

                wallet_pk  = Pubkey.from_string(wallet_addr)
                mint_pk    = Pubkey.from_string(mint_addr)
                token_prog = Pubkey.from_string("TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA")
                ata_prog   = Pubkey.from_string("ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJe1bsU")
                sys_prog   = Pubkey.from_string("11111111111111111111111111111111")

                seeds = [bytes(wallet_pk), bytes(token_prog), bytes(mint_pk)]
                ata_pk, _ = Pubkey.find_program_address(seeds, ata_prog)

                # Get blockhash
                bh_rpc = SOLANA_RPC if SOLANA_RPC else (("https://solana-mainnet.g.alchemy.com/v2/"+ALCHEMY_KEY) if ALCHEMY_KEY else "https://api.mainnet-beta.solana.com")
                bh_r = requests.post(bh_rpc, json={
                    "jsonrpc":"2.0","id":1,"method":"getLatestBlockhash",
                    "params":[{"commitment":"confirmed"}]
                }, timeout=8)
                bh_json = bh_r.json()
                blockhash_str = bh_json.get("result",{}).get("value",{}).get("blockhash","")
                if not blockhash_str:
                    log("Could not get blockhash, response: "+str(bh_json)[:200], "WARN")
                    return None

                blockhash = Hash.from_string(blockhash_str)

                create_ix = Instruction(
                    program_id=ata_prog,
                    data=bytes(),
                    accounts=[
                        AccountMeta(wallet_pk, True, True),    # payer
                        AccountMeta(ata_pk,    False, True),   # ata
                        AccountMeta(wallet_pk, False, False),  # owner
                        AccountMeta(mint_pk,   False, False),  # mint
                        AccountMeta(sys_prog,  False, False),  # system program
                        AccountMeta(token_prog,False, False),  # token program
                    ]
                )
                msg = MessageV0.try_compile(wallet_pk, [create_ix], [], blockhash)
                tx = VersionedTransaction(msg, [keypair])

                send_payload = {
                    "jsonrpc":"2.0","id":1,"method":"sendTransaction",
                    "params":[b64.b64encode(bytes(tx)).decode(),
                              {"encoding":"base64","skipPreflight":False,
                               "preflightCommitment":"confirmed"}]
                }
                rpc = SOLANA_RPC if SOLANA_RPC else (("https://solana-mainnet.g.alchemy.com/v2/"+ALCHEMY_KEY) if ALCHEMY_KEY else "https://api.mainnet-beta.solana.com")
                rr = requests.post(rpc, json=send_payload, timeout=15)
                rr_json = rr.json()
                rr_result = rr_json.get("result","")
                rr_error = rr_json.get("error",{})
                log("ATA creation tx: "+str(rr_result)[:40]+" err="+str(rr_error)[:120], "INFO")
                if rr_result:
                    # Wait for confirmation and verify
                    time.sleep(3)
                    for _ in range(5):
                        existing = get_ata(wallet_addr, mint_addr)
                        if existing:
                            return existing
                        time.sleep(2)
                log("ATA not confirmed after creation, trying again...", "WARN")
                return get_ata(wallet_addr, mint_addr)
            except Exception as ate:
                log("ATA creation error: "+str(ate)[:80], "WARN")
                return get_ata(wallet_addr, mint_addr)  # might exist now

        # SOL swaps need a WSOL token account (Raydium wraps/unwraps via it)
        input_ata = get_ata(wallet, from_mint)
        if not input_ata:
            log("No ATA for input token "+from_token+", trying to create...", "WARN")
            input_ata = create_ata_if_missing(wallet, from_mint)
        if not input_ata:
            log("Cannot swap — no input ATA for "+from_token, "WARN")
            return False, 0.0

        output_ata = get_ata(wallet, to_mint)
        if not output_ata:
            output_ata = create_ata_if_missing(wallet, to_mint)
        if not output_ata:
            log("Cannot swap — no output ATA for "+to_token, "WARN")
            return False, 0.0

        # Build Raydium swap transaction payload
        swap_payload = {
            "computeUnitPriceMicroLamports": "10000",
            "swapResponse":  raydium_quote,
            "txVersion":     "V0",
            "wallet":        wallet,
            "wrapSol":       from_token == "SOL",
            "unwrapSol":     to_token   == "SOL",
            "inputAccount":  input_ata,
            "outputAccount": output_ata,
        }
        log("Raydium swap payload keys: "+str(list(swap_payload.keys())), "DEBUG")
        r = requests.post("https://transaction-v1.raydium.io/transaction/swap-base-in",
            json=swap_payload, timeout=15)
        log("Raydium TX status: "+str(r.status_code)+" body: "+r.text[:200])
        tx_data = r.json()
        if not tx_data.get("success") or not tx_data.get("data"):
            log("Raydium tx build failed: "+str(tx_data.get("msg",""))[:100], "WARN")
            return False, 0.0

        txs = tx_data.get("data", [])
        if isinstance(txs, list) and len(txs) > 0:
            tx_b64 = txs[0].get("transaction", "")
        elif isinstance(txs, dict):
            tx_b64 = txs.get("transaction", "")
        else:
            tx_b64 = ""
        if not tx_b64:
            log("No transaction in Raydium response", "WARN")
            return False, 0.0
        raw_tx = b64.b64decode(tx_b64)
        tx_obj = VersionedTransaction.from_bytes(raw_tx)
        sig = keypair.sign_message(solders_message.to_bytes_versioned(tx_obj.message))
        signed_tx = VersionedTransaction.populate(tx_obj.message, [sig])

        # Submit
        rpc = SOLANA_RPC if SOLANA_RPC else (("https://solana-mainnet.g.alchemy.com/v2/"+ALCHEMY_KEY) if ALCHEMY_KEY else "https://api.mainnet-beta.solana.com")
        r2 = requests.post(rpc, json={
            "jsonrpc":"2.0","id":1,"method":"sendTransaction",
            "params":[
                b64.b64encode(bytes(signed_tx)).decode(),
                {"encoding":"base64","skipPreflight":False,
                 "preflightCommitment":"confirmed","maxRetries":5}
            ]
        }, timeout=20)
        result = r2.json()

        tx_sig = result.get("result","")
        if tx_sig:
            # Retry confirmation up to 15s (Solana can take several seconds)
            tx_ok = False
            vresult = None
            for attempt in range(5):
                time.sleep(3)
                try:
                    verify_payload = {"jsonrpc":"2.0","id":1,"method":"getSignatureStatuses","params":[[tx_sig]]}
                    vr = requests.post(rpc, json=verify_payload, timeout=8)
                    vdata = vr.json()
                    vresult = vdata.get("result",{}).get("value",[{}])[0]
                    if vresult and vresult.get("confirmationStatus") in ("confirmed","finalized") and vresult.get("err") is None:
                        tx_ok = True
                        break
                except Exception:
                    pass
            if tx_ok:
                log("RAYDIUM SWAP CONFIRMED: "+tx_sig[:20]+"... "+from_token+"→"+to_token+via); log_trade_to_file({"event":"SWAP_OK","time":time.strftime("%H:%M:%S"),"router":"Raydium","pair":from_token+"/"+to_token,"side":side,"tx":tx_sig[:20]})
                trade = {"time":time.strftime("%H:%M:%S"),"side":"LIVE-"+side+via,
                         "price":price,"amount":out_human,"router":"Raydium",
                         "chain":"solana","tx":tx_sig[:20]}
                state["trades"].append(trade)
                return True, out_human
            else:
                err_msg = str(vresult.get("err","")) if vresult else "no status"
                log("Swap TX failed: "+tx_sig[:20]+" err="+err_msg, "WARN"); log_trade_to_file({"event":"SWAP_FAIL","time":time.strftime("%H:%M:%S"),"router":"Raydium","pair":from_token+"/"+to_token,"side":side,"tx":tx_sig[:20],"error":err_msg})
                return False, 0.0
        else:
            log("Raydium send failed: "+str(result.get("error",""))[:100], "WARN")
            return False, 0.0
    except ImportError as ie:
        log("Missing package: "+str(ie), "WARN"); return False, 0.0
    except Exception as ex:
        log("Raydium swap error: "+str(ex)[:100], "WARN"); return False, 0.0

def jupiter_swap(from_token, to_token, amount_input, price, dex=None):
    """
    Execute a Solana DEX swap via Jupiter aggregator (v6 API).
    Jupiter routes through all DEXes (Raydium, Orca, Meteora, etc.) for best price.
    """
    from_mint = SOL_TOKENS.get(from_token, SOL_TOKENS["USDC"])
    to_mint   = SOL_TOKENS.get(to_token,   SOL_TOKENS["SOL"])
    from_dec  = TOKEN_DECIMALS.get(from_token, 6)
    to_dec    = TOKEN_DECIMALS.get(to_token,   9)
    side      = "BUY" if from_token in ("USDC","USDT") else "SELL"
    via       = (" via "+dex) if dex else ""

    lamports = int(amount_input * (10 ** from_dec))
    log("Swap "+side+via+": "+str(amount_input)+" "+from_token+" → "+to_token)

    # Try Raydium first (primary), Jupiter as fallback
    slippage_bps = "300"
    rq = raydium_get_quote(from_mint, to_mint, lamports, slippage_bps)
    if rq:
        out_lamports = int(rq.get("data",{}).get("outputAmount", 0))
        out_human = out_lamports / (10 ** to_dec) if out_lamports > 0 else 0.0
        if out_human > 0:
            log("Raydium quote: "+str(amount_input)+" "+from_token+" → "+str(round(out_human,6))+" "+to_token)
            ok, result_amt = _raydium_execute_swap(from_token, to_token, from_mint, to_mint,
                amount_input, out_human, price, side, via, lamports, rq, to_dec)
            if ok:
                return True, result_amt
            log("Raydium execution failed, falling back to Jupiter...", "WARN")

    # Fallback: Jupiter
    log("Trying Jupiter swap...", "INFO")
    try:
        r = requests.get("https://api.jup.ag/swap/v1/quote", params={
            "inputMint": from_mint,
            "outputMint": to_mint,
            "amount": str(lamports),
            "slippageBps": "100",
        }, timeout=10)
        qdata = r.json()
        if qdata and not qdata.get("error"):
            out_amount = int(qdata.get("outAmount", 0))
            out_human = out_amount / (10 ** to_dec) if out_amount > 0 else 0.0
            if out_human > 0:
                log("Jupiter quote: "+str(amount_input)+" "+from_token+" → "+str(round(out_human,6))+" "+to_token)
                quote = qdata
            else:
                log("Jupiter quote failed", "WARN")
                return False, 0.0
        else:
            log("Jupiter quote failed: "+str(qdata.get("error","no data")), "WARN")
            return False, 0.0
    except Exception as e:
        log("Jupiter unavailable: "+str(e)[:80], "WARN")
        return False, 0.0

    if state["paper_trading"]:
        trade = {"time":time.strftime("%H:%M:%S"),"side":"[PAPER] "+side+via,
                 "price":price,"amount":out_human,"router":"Jupiter","chain":"solana"}
        state["trades"].append(trade)
        return True, out_human

    # ── Live execution via Jupiter ────────────────────────────────────────────
    try:
        from solders.keypair import Keypair
        from solders.transaction import VersionedTransaction
        from solders import message as solders_message
        import base64 as b64

        private_key = _secret("SOL_PRIVATE_KEY")
        wallet      = cfg.get("sol_wallet","")
        if not private_key or not wallet:
            log("SOL_PRIVATE_KEY or SOL_WALLET_ADDRESS not set", "WARN")
            return False, 0.0
        # Check USDC balance before attempting swap
        usdc_bal = state.get("sol_usdc", 0)
        if from_token in ("USDC","USDT") and amount_input > usdc_bal:
            log(f"Insufficient USDC: need ${amount_input:.2f}, have ${usdc_bal:.2f}", "WARN")
            return False, 0.0

        try:
            keypair = Keypair.from_base58_string(private_key)
        except Exception as ke:
            log("Key decode failed: "+str(ke)[:60], "WARN")
            return False, 0.0

        # Get swap transaction from Jupiter (handles ATA creation automatically)
        swap_payload = {
            "quoteResponse": quote,
            "userPublicKey": wallet,
            "wrapAndUnwrapSol": True,
            "dynamicComputeUnitLimit": True,
            "prioritizationFeeLamports": 10000,
        }
        r = requests.post("https://api.jup.ag/swap/v1/swap",
            json=swap_payload, timeout=15)
        swap_data = r.json()
        if not swap_data.get("swapTransaction"):
            log("Jupiter swap tx failed: "+str(swap_data.get("error",""))[:100], "WARN")
            return False, 0.0

        swap_tx_b64 = swap_data["swapTransaction"]
        raw_tx = b64.b64decode(swap_tx_b64)
        tx_obj = VersionedTransaction.from_bytes(raw_tx)
        sig = keypair.sign_message(solders_message.to_bytes_versioned(tx_obj.message))
        signed_tx = VersionedTransaction.populate(tx_obj.message, [sig])

        # Send transaction
        send_payload = {
            "jsonrpc":"2.0","id":1,"method":"sendTransaction",
            "params":[
                b64.b64encode(bytes(signed_tx)).decode(),
                {"encoding":"base64","skipPreflight":False,
                 "preflightCommitment":"confirmed","maxRetries":3}
            ]
        }
        send_rpc = SOLANA_RPC if SOLANA_RPC else (("https://solana-mainnet.g.alchemy.com/v2/"+ALCHEMY_KEY) if ALCHEMY_KEY else "https://api.mainnet-beta.solana.com")
        r2 = requests.post(send_rpc, json=send_payload, timeout=15)
        result = r2.json()
        if result.get("error",{}).get("code") == 429:
            log("Rate limited — retrying", "WARN")
            time.sleep(3)
            r2 = requests.post("https://api.mainnet-beta.solana.com", json=send_payload, timeout=15)
            result = r2.json()

        tx_sig = result.get("result","")
        if tx_sig:
            # Retry confirmation up to 15s
            tx_ok = False
            for attempt in range(5):
                time.sleep(3)
                try:
                    verify_payload = {"jsonrpc":"2.0","id":1,"method":"getSignatureStatuses","params":[[tx_sig]]}
                    vr = requests.post(send_rpc, json=verify_payload, timeout=8)
                    vdata = vr.json()
                    status = vdata.get("result",{}).get("value",[None])[0]
                    if status and status.get("confirmationStatus") in ("confirmed","finalized") and status.get("err") is None:
                        tx_ok = True
                        break
                except Exception:
                    pass
            if tx_ok:
                log("SWAP CONFIRMED: "+tx_sig[:20]+"... "+from_token+"→"+to_token+via); log_trade_to_file({"event":"SWAP_OK","time":time.strftime("%H:%M:%S"),"router":"Jupiter","pair":from_token+"/"+to_token,"side":side,"tx":tx_sig[:20]})
                trade = {"time":time.strftime("%H:%M:%S"),"side":"LIVE-"+side+via,
                         "price":price,"amount":out_human,"router":"Jupiter",
                         "chain":"solana","tx":tx_sig[:20]}
                state["trades"].append(trade)
                return True, out_human
            else:
                log("Swap submitted but not confirmed: "+tx_sig[:20], "WARN")
                return False, 0.0
        else:
            log("Send failed: "+str(result.get("error",""))[:100], "WARN")
            return False, 0.0

    except ImportError as ie:
        log("Missing package: "+str(ie), "WARN"); return False, 0.0
    except Exception as ex:
        log("Jupiter swap error: "+str(ex)[:100], "WARN"); return False, 0.0


def get_evm_dex_price(chain, pair):
    """Get on-chain DEX price via 0x API for EVM chains"""
    try:
        tokens_map = TOKENS.get(chain, {})
        token = pair.split("/")[0]
        sell_token = tokens_map.get("USDT","")
        buy_token  = tokens_map.get("W"+token, tokens_map.get(token,""))
        if not sell_token or not buy_token: return 0.0
        r = requests.get("https://api.0x.org/swap/v1/price", params={
            "sellToken":  sell_token,
            "buyToken":   buy_token,
            "sellAmount": "1000000",
        }, headers={"0x-api-key": os.environ.get("ZEROX_API_KEY","")}, timeout=8)
        data = r.json()
        price = float(data.get("price", 0))
        return 1.0/price if price > 0 else 0.0
    except Exception as e:
        log("EVM DEX price error: "+str(e), "WARN")
        return 0.0

def scan_arbitrage():
    opps = []
    chain = state.get("chain", "ethereum")

    if chain == "solana":
        sol_pairs = ["SOL/USDC", "JUP/USDC", "ETH/USDC"]

        TOKEN_MINTS = {
            "SOL":  "So11111111111111111111111111111111111111112",
            "ETH":  "7vfCXTUXx5WJV5JADk17DUJ4ksgau7utNKj4b963voxs",
            "JUP":  "JUPyiwrYJFskUPiHa7hkeR8VUtAeFoSYbKedZNsDvCN",
        }

        def get_dexpaprika_prices(token):
            """
            Get per-DEX prices via DexPaprika — confirmed accessible from Render.
            Returns {dex_name: price_usd} for Raydium, Orca, Meteora.
            """
            try:
                mint = TOKEN_MINTS.get(token, "")
                if not mint: return {}
                r = requests.get(
                    "https://api.dexpaprika.com/networks/solana/tokens/"+mint+"/pools",
                    params={"page": 0, "limit": 50, "sort": "desc", "order_by": "volume_usd"},
                    timeout=10
                )
                if r.status_code == 429:
                    log("DexPaprika 429 for "+token+" — skipping", "WARN")
                    return {}
                if r.status_code != 200:
                    log("DexPaprika status "+str(r.status_code)+" for "+token, "WARN")
                    return {}
                pools = r.json().get("pools", [])
                dex_prices = {}
                for pool in pools:
                    dex_id = pool.get("dex_id", "").lower()
                    price  = float(pool.get("price_usd", 0) or 0)
                    tokens_in_pool = [t.get("symbol","") for t in pool.get("tokens",[])]
                    if "USDC" not in tokens_in_pool or price <= 0:
                        continue
                    if dex_id in ("raydium","raydium_clmm") and "Raydium" not in dex_prices:
                        dex_prices["Raydium"] = price
                    elif dex_id == "orca" and "Orca" not in dex_prices:
                        dex_prices["Orca"] = price
                    elif dex_id == "meteora" and "Meteora" not in dex_prices:
                        dex_prices["Meteora"] = price
                    if len(dex_prices) >= 3:
                        break
                return dex_prices
            except Exception as ex:
                log("DexPaprika error for "+token+": "+str(ex)[:60], "WARN")
                return {}

        try:
            usdc_bal = state.get("sol_usdc", 0)
            size     = min(usdc_bal * cfg["risk_pct"] / 100, cfg["max_pos"])

            for pair in sol_pairs:
                token  = pair.split("/")[0]
                prices = get_dexpaprika_prices(token)

                if prices:
                    log("SOL ARB scan "+pair+": "+str({k:round(v,6) for k,v in prices.items()}))
                else:
                    log("SOL ARB scan "+pair+": no prices","WARN")

                if len(prices) >= 2:
                    est_gas = 0.004  # two Solana transactions
                    vals = list(prices.items())
                    for i in range(len(vals)):
                        for j in range(i+1, len(vals)):
                            n1,p1 = vals[i]
                            n2,p2 = vals[j]
                            if p1<=0 or p2<=0: continue
                            spread = abs(p1-p2)/min(p1,p2)*100
                            if spread < 1.5: continue
                            buy_from   = n1 if p1 < p2 else n2
                            sell_on    = n2 if p1 < p2 else n1
                            buy_price  = min(p1,p2)
                            sell_price = max(p1,p2)
                            # Deduct 0.75% per leg for DEX fees + slippage
                            net_spread = spread - 1.5
                            gross      = (net_spread/100) * size if size > 0 else 0
                            est_profit = round(gross - est_gas, 6)
                            opps.append({
                                "pair":           pair,
                                "buy_from":       buy_from,
                                "sell_on":        sell_on,
                                "buy_price":      round(buy_price,6),
                                "sell_price":     round(sell_price,6),
                                "spread_pct":     round(spread,4),
                                "est_gas_usd":    est_gas,
                                "est_profit_usd": est_profit,
                                "chain":          "solana",
                                "executable":     (
                                    spread >= cfg["min_arb_spread"]
                                    and est_profit > 0
                                    and size >= 0.10
                                    and usdc_bal >= 0.10
                                ),
                            })

                time.sleep(3)  # 3s between tokens — DexPaprika allows 60 req/min

        except Exception as ex:
            log("SOL ARB scan error: "+str(ex), "WARN")

    else:
        evm_pairs = ["BTC/USDT","ETH/USDT","BNB/USDT","SOL/USDT"]
        for pair in evm_pairs:
            prices = {}
            p_kraken = get_price_kraken(pair)
            p_cg     = get_price_coingecko(pair)
            p_dex    = get_evm_dex_price(chain, pair)
            if p_kraken > 0: prices["Kraken"]  = p_kraken
            if p_cg     > 0: prices["CoinGecko"] = p_cg
            if p_dex    > 0: prices["DEX"]      = p_dex
            if len(prices) >= 2:
                vals = list(prices.items())
                for i in range(len(vals)):
                    for j in range(i+1, len(vals)):
                        n1,p1 = vals[i]
                        n2,p2 = vals[j]
                        if p1<=0 or p2<=0: continue
                        spread = abs(p1-p2)/min(p1,p2)*100
                        if spread > 0.1:
                            buy_from   = n1 if p1 < p2 else n2
                            sell_on    = n2 if p1 < p2 else n1
                            buy_price  = min(p1,p2)
                            sell_price = max(p1,p2)
                            est_gas    = 0.50 if chain in("base","arbitrum","polygon") else 5.0
                            bal        = state.get("balance",0)
                            size       = min(bal*cfg["risk_pct"]/100, cfg["max_pos"])
                            est_profit = round((sell_price-buy_price)*(size/buy_price if buy_price>0 else 0)-est_gas, 2)
                            opps.append({
                                "pair":           pair,
                                "buy_from":       buy_from,
                                "sell_on":        sell_on,
                                "buy_price":      round(buy_price,4),
                                "sell_price":     round(sell_price,4),
                                "spread_pct":     round(spread,3),
                                "est_gas_usd":    est_gas,
                                "est_profit_usd": est_profit,
                                "chain":          chain,
                                "executable":     spread >= cfg["min_arb_spread"] and est_profit > 0,
                            })

    state["arb_opps"] = sorted(opps, key=lambda x: x["spread_pct"], reverse=True)[:10]
    return state["arb_opps"]

def execute_arbitrage(opp):
    spread     = opp["spread_pct"]
    est_profit = opp["est_profit_usd"]
    chain      = opp.get("chain", state.get("chain","ethereum"))
    pair       = opp["pair"]
    price      = opp["buy_price"]
    buy_from   = opp["buy_from"]
    sell_on    = opp["sell_on"]

    # Pre-flight safety checks
    if spread < cfg["min_arb_spread"]:
        log("ARB skipped — spread "+str(spread)+"% < min "+str(cfg["min_arb_spread"])+"%","WARN")
        return False
    if est_profit <= 0:
        log("ARB skipped — estimated profit negative after fees","WARN")
        return False
    if state["daily_loss"] >= cfg["max_loss"]:
        log("ARB skipped — daily loss limit hit","WARN")
        return False

    usdc_bal = state.get("sol_usdc", 0) if chain == "solana" else state["balance"]
    size     = min(usdc_bal * cfg["risk_pct"] / 100, cfg["max_pos"])
    if size < 0.10:
        log("ARB skipped — USDC balance $"+str(round(usdc_bal,2))+" too low","WARN")
        return False

    token = pair.split("/")[0]
    amt   = round(size / price, 6) if price > 0 else 0

    if state["paper_trading"]:
        log("[PAPER] ARB: "+token+" buy on "+buy_from+" @ $"+str(price)+
            " → sell on "+sell_on+" @ $"+str(opp["sell_price"])+
            " spread "+str(spread)+"% est $"+str(est_profit))
        record_trade("[PAPER] ARB", price, amt, round(est_profit,2), pair=pair)
        state["pnl"] += est_profit * 0.7
        return True

    if chain != "solana":
        # EVM arb — basic implementation
        result = place_order(pair, "buy", amt)
        if result:
            record_trade("ARB "+buy_from+"→"+sell_on, price, amt, round(est_profit,2), pair=pair)
        return bool(result)

    # ── Solana live two-leg arbitrage ─────────────────────────────────────────
    state["trading_lock"]   = True
    state["last_trade_time"] = time.time()

    try:
        log("ARB LEG 1: BUY "+token+" with $"+str(round(size,4))+" USDC on "+buy_from)
        buy_ok, token_received = jupiter_swap("USDC", token, size, price, dex=buy_from)
        if not buy_ok or token_received <= 0:
            log("ARB buy leg failed — aborting", "WARN")
            state["trading_lock"] = False
            return False

        log("ARB LEG 1 complete: received "+str(round(token_received,6))+" "+token)

        # Wait for on-chain confirmation before selling
        time.sleep(5)

        # Sell EXACTLY what we received from the buy quote
        sell_price = opp["sell_price"]
        log("ARB LEG 2: SELL "+str(round(token_received,6))+" "+token+" on "+sell_on)
        sell_ok, usdc_received = jupiter_swap(token, "USDC", token_received, sell_price, dex=sell_on)

        if sell_ok and usdc_received > 0:
            # Actual profit = USDC returned minus USDC spent minus gas
            actual_profit = round(usdc_received - size - opp["est_gas_usd"], 6)
            state["pnl"] += actual_profit
            if actual_profit < 0:
                state["daily_loss"] += abs(actual_profit)
            record_trade(
                "ARB "+buy_from+"→"+sell_on,
                price, token_received,
                round(actual_profit, 4), pair=pair
            )
            log("ARB complete — spent $"+str(round(size,4))+
                " received $"+str(round(usdc_received,4))+
                " profit $"+str(actual_profit))
            state["trading_lock"] = False
            return True
        else:
            log("ARB sell leg failed — holding "+str(round(token_received,6))+" "+token, "WARN")
            record_trade("ARB-BUY-ONLY (sell failed)", price, token_received, None, pair=pair)
            state["trading_lock"] = False
            return False

    except Exception as ex:
        log("execute_arbitrage error: "+str(ex)[:80], "WARN")
        state["trading_lock"] = False
        return False

ARB_PAIRS = ["BTC/USDT","ETH/USDT","BNB/USDT","SOL/USDT"]

# ── Strategies ────────────────────────────────────────────────────────────────
def get_balance():
    if state["mode"] == "dex":
        chain = state.get("chain", "ethereum")
        if chain == "solana" and cfg["sol_wallet"]:
            sol_get_balance()
            return state.get("sol_balance", 0.0)
        else:
            dex_get_balance()
            return state.get("balance", 0.0)
    else:
        return cex_get_balance()

_last_order_key = None
_last_order_time = 0

def place_order(pair, side, amount, grid_idx=None):
    global _last_order_key, _last_order_time
    order_key = f"{pair}:{side}:{amount}"
    if grid_idx is not None:
        order_key = f"{pair}:{side}:{amount}:grid{grid_idx}"
    now = time.time()
    if order_key == _last_order_key and now - _last_order_time < 5:
        log(f"DUPLICATE ORDER BLOCKED: {order_key}", "WARN")
        return False
    _last_order_key = order_key
    _last_order_time = now
    
    # Visible ORDER_ATTEMPT logging
    log(f"[ORDER_ATTEMPT] {side.upper()} {pair} amount={amount}", "WARN")
    
    success = False
    if state["mode"] == "dex":
        chain = state["chain"]
        price = get_price(pair)
        token = pair.split("/")[0]
        stablecoin = pair.split("/")[1]

        if chain == "solana":
            if token not in ("USDC", "USDT"):  # quote currencies aren't gated as trade targets
                ok, reason = authorize_trade(token)
                if not ok:
                    log(f"ORDER BLOCKED by token registry: {reason}", "WARN")
                    return False
            # Use Jupiter/Raydium for Solana trades
            if side in ("buy","buy_market"):
                # amount is token quantity, jupiter_swap needs USDC cost
                cost = amount * price
                log(f"place_order BUY: amt={amount} price={price} cost={cost} pair={pair}", "DEBUG"); log_trade_to_file({"event":"ORDER_ATTEMPT","time":time.strftime("%H:%M:%S"),"side":"BUY","pair":pair,"amount":amount,"price":price,"cost":cost})
                swap_dex = "Raydium" if token in ("SOL","BTC","ETH","USDC","USDT","JUP","BONK","WIF") else None
                result = jupiter_swap(stablecoin, token, cost, price, dex=swap_dex)
            else:
                log(f"place_order SELL: amt={amount} price={price} pair={pair}", "DEBUG"); log_trade_to_file({"event":"ORDER_ATTEMPT","time":time.strftime("%H:%M:%S"),"side":"SELL","pair":pair,"amount":amount,"price":price})
                swap_dex = "Raydium" if token in ("SOL","BTC","ETH","USDC","USDT","JUP","BONK","WIF") else None
                result = jupiter_swap(token, stablecoin, amount, price, dex=swap_dex)
            # jupiter_swap returns (success_bool, amount) tuple — unpack it
            if isinstance(result, tuple):
                success = result[0]
            else:
                success = bool(result)
        else:
            # EVM chains: use 1inch/Uniswap
            tokens = TOKENS.get(chain, {})
            from_t = tokens.get("USDT","")
            to_t   = tokens.get("W"+token, tokens.get(token,""))
            if side in ("buy","buy_market"):
                success = dex_swap(chain, from_t, to_t, amount * price, price)
            else:
                success = dex_swap(chain, to_t, from_t, amount * price, price)
    else:
        success = bool(cex_place_order(pair, side, amount))
        
    if not success:
        log(f"ORDER FAILED: swap execution returned failure for {side.upper()} {pair} {amount}", "WARN")
        
    return success

def log_trade_to_file(entry):
    """Write a trade event to persistent log file."""
    try:
        with open(TRADE_LOG, "a") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception:
        pass

def record_trade(side, price, amount, pnl=None, pair=None):
    _pair = pair if pair else state.get("pair","")
    trade = {"time":time.strftime("%H:%M:%S"),"side":side,"price":price,"amount":amount,"pnl":pnl,"pair":_pair}
    thread_name = threading.current_thread().name
    if "strategies" in state and thread_name in state["strategies"]:
        strat = state["strategies"][thread_name]
        trade["strategy"] = strat["type"]
        trade["sid"] = thread_name
    else:
        trade["strategy"] = state.get("strategy", "") or ""
        trade["sid"] = ""
    with _state_lock:
        state["trades"].append(trade)
        if len(state["trades"]) > 500:
            state["trades"] = state["trades"][-500:]
        state["last_trade"] = {"action": side, "pair": _pair, "price": price, "time": time.time()}
        state["trades_list"] = [{"time":t["time"],"action":t["side"],"price":t["price"],"amount":t["amount"],"pnl":t.get("pnl"),"via":t.get("router",""),"pair":t.get("pair",""), "strategy": t.get("strategy", "")} for t in state["trades"][-50:]]
        # Rebuild pair stats from completed (PnL-bearing) trades.  Buy entries
        # intentionally do not count as trades until their matching sell has a
        # realized PnL; this keeps BTC/ETH metrics consistent with trade history.
        pair_stats = {}
        for t in state["trades"]:
            pnl = t.get("pnl")
            pair = t.get("pair") or state.get("pair", "")
            if pnl is None or not pair: continue
            stats = pair_stats.setdefault(pair, {"trades": 0, "wins": 0, "pnl": 0.0})
            stats["trades"] += 1
            stats["wins"] += 1 if pnl > 0 else 0
            stats["pnl"] += float(pnl)
        for stats in pair_stats.values():
            stats["avg_profit"] = stats["pnl"] / stats["trades"] if stats["trades"] else 0.0
            stats["win_rate"] = stats["wins"] / stats["trades"] * 100 if stats["trades"] else 0.0
        state["pair_stats"] = pair_stats
    # Persist to file
    log_trade_to_file({"event":"TRADE","time":trade["time"],"side":side,"pair":trade["pair"],"price":price,"amount":amount,"pnl":pnl, "strategy": trade.get("strategy", "")})


# ── Technical Indicators (stdlib only) ────────────────────────────────────
def calc_ema(prices, period):
    """Exponential Moving Average. Returns list of EMA values, same length as input."""
    if len(prices) < period:
        return [None] * len(prices)
    ema = [None] * (period - 1)
    sma = sum(prices[:period]) / period
    ema.append(sma)
    multiplier = 2 / (period + 1)
    for i in range(period, len(prices)):
        ema.append((prices[i] - ema[-1]) * multiplier + ema[-1])
    return ema

def calc_rsi(prices, period=14):
    """Relative Strength Index using Wilder's smoothing. Returns list of RSI values."""
    if len(prices) < period + 1:
        return [None] * len(prices)
    deltas = [prices[i] - prices[i-1] for i in range(1, len(prices))]
    gains = [d if d > 0 else 0 for d in deltas]
    losses = [-d if d < 0 else 0 for d in deltas]
    rsi = [None] * (period + 1)  # first period+1 are None (need period deltas + 1 extra)
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for i in range(period, len(deltas)):
        if avg_loss == 0:
            rsi.append(100.0)
        else:
            rs = avg_gain / avg_loss
            rsi.append(100.0 - (100.0 / (1.0 + rs)))
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
    return rsi

def calc_bbands(prices, period=20, stddev=2.0):
    """Bollinger Bands. Returns (middle, upper, lower) — each a same-length list."""
    if len(prices) < period:
        return [None]*len(prices), [None]*len(prices), [None]*len(prices)
    middle, upper, lower = [], [], []
    for i in range(len(prices)):
        if i < period - 1:
            middle.append(None); upper.append(None); lower.append(None)
        else:
            window = prices[i-period+1:i+1]
            m = sum(window) / period
            variance = sum((x - m)**2 for x in window) / period
            sd = variance ** 0.5
            middle.append(m)
            upper.append(m + stddev * sd)
            lower.append(m - stddev * sd)
    return middle, upper, lower

def get_price_history(pair, lookback=100):
    """Fetch recent candle closes for the pair. Returns list of prices (most recent last).
    Uses the live price ticker already maintained by the background price loop."""
    buf = state.get("_price_buf_" + pair, [])
    try:
        price = get_price(pair)
        if price and price > 0:
            if not buf or abs(price - buf[-1]) > 0.0001:
                buf.append(price)
    except Exception as e:
        log("get_price_history warn: " + str(e), "WARN")
    if len(buf) > lookback:
        buf = buf[-lookback:]
    state["_price_buf_" + pair] = buf
    return buf

def seed_history(pair):
    """Seed pair history with four hours of one-minute Kraken closes."""
    try:
        if not requests: return
        mapping = {"BTC/USDC":"XXBTZUSD", "BTC/USDT":"XXBTZUSD", "ETH/USDC":"XETHZUSD", "SOL/USDC":"SOLUSD"}
        r = requests.get("https://api.kraken.com/0/public/OHLC", params={"pair":mapping.get(pair,pair.replace("/","")), "interval":1, "since":int(time.time())-259200}, timeout=10)
        payload = r.json()
        candles = next((v for k,v in payload.get("result",{}).items() if k != "last"), [])
        history = [{"time":int(c[0]),"value":float(c[4])} for c in candles][-4320:]
        if history:
            state["price_history_pairs"][pair] = history
            if pair == state.get("pair"): state["price_history"] = history[:]
            log(f"Seeded {len(history)} candles from Kraken for {pair}")
    except Exception as e: log(f"Seed history failed for {pair}: {e}", "WARN")
def chart_history_for(pair):
    """Return candle history for ANY requested pair, seeding it when missing.

    Arbitrary dropdown pairs have no history until a strategy runs on them, so
    the dashboard asks this endpoint when the user picks a pair. Preference
    order: existing per-pair buffer (running/seeded pairs) -> Kraken OHLC seed
    (reuses seed_history, mapped for BTC/ETH/SOL, generic fallback otherwise)
    -> two points synthesized from the current live price via get_price(pair).
    The result is stored into state["price_history_pairs"][pair] so the 3s
    /state refresh then serves it directly. Returns [] when nothing is
    available so the client can show a placeholder instead of stale candles.
    """
    pair = (pair or "").strip()
    if not pair or "/" not in pair or len(pair) > 64:
        return []
    history = state["price_history_pairs"].get(pair, [])
    if len(history) >= 2:
        return history[-4320:]
    seed_history(pair)
    history = state["price_history_pairs"].get(pair, [])
    if len(history) >= 2:
        return history[-4320:]
    # Live fallback for pairs Kraken does not cover (custom Solana tokens).
    # A single sample synthesized into two points keeps the request fast and
    # shows the selected pair's own current price, never another pair's data.
    try:
        price = get_price(pair)
    except Exception:
        price = 0
    if price and price > 0:
        now = int(time.time())
        samples = [{"time": now - 1, "value": float(price)}, {"time": now, "value": float(price)}]
        state["price_history_pairs"][pair] = samples
        return samples
    return []
def run_dca():
    log("DCA started on "+state["pair"]+" ("+state["mode"].upper()+")")
    buy_prices = []
    while state["running"] and state["strategy"]=="dca":
        while state["paused"]: time.sleep(1)
        price = get_price(state["pair"])
        if price <= 0: time.sleep(60); continue
        bal = get_balance()
        if not buy_prices:
            size = min(bal*cfg["risk_pct"]/100, cfg["max_pos"])
            if size > 1:
                amt = round(size/price, 6)
                if place_order(pair,"buy",amt, grid_idx=i):
                    buy_prices.append(price)
                    state["positions"].append({"price":price,"amount":amt,"strategy":"DCA"})
                    record_trade("DCA-BUY",price,amt, pair=pair)
                    log("DCA BUY "+str(amt)+" @ $"+str(price))
        else:
            avg = sum(buy_prices)/len(buy_prices)
            gain = (price-avg)/avg*100
            loss = (avg-price)/avg*100
            total = sum(p["amount"] for p in state["positions"])
            if gain >= cfg["take_profit"]:
                if place_order(pair,"sell",total):
                    pnl = (price-avg)*total
                    state["pnl"] += pnl
                    record_trade("SELL",price,total,round(pnl,2), pair=pair)
                    log("DCA SELL @ $"+str(price)+" PnL: $"+str(round(pnl,2)))
                    buy_prices.clear(); state["positions"].clear()
            elif loss >= cfg["stop_loss"]:
                if place_order(pair,"sell",total):
                    pnl = (price-avg)*total
                    state["pnl"] += pnl
                    state["daily_loss"] += abs(pnl)
                    record_trade("STOP",price,total,round(pnl,2), pair=pair)
                    log("STOP LOSS @ $"+str(price), "WARN")
                    buy_prices.clear(); state["positions"].clear()
            elif loss >= 2 and state["daily_loss"] < cfg["max_loss"]:
                size = min(bal*cfg["risk_pct"]/100, cfg["max_pos"])
                if size > 1:
                    amt = round(size/price,6)
                    if place_order(pair,"buy",amt, grid_idx=i):
                        buy_prices.append(price)
                        state["positions"].append({"price":price,"amount":amt,"strategy":"DCA"})
                        record_trade("DCA-BUY",price,amt, pair=pair)
                        log("DCA averaging down @ $"+str(price))
        if state["daily_loss"] >= cfg["max_loss"]:
            log("Daily loss limit reached — pausing 1hr", "WARN"); time.sleep(3600)
        time.sleep(60)

def _make_grids(price, spread, levels):
    mid_idx = (levels + 1) // 2
    s = price * spread * 2 / levels
    grids = []
    for i in range(levels + 1):
        if i <= mid_idx:
            grids.append(round(price * (1 - spread) + i * s, 4))
        else:
            grids.append(round(price * (1 - spread) + mid_idx * s + (i - mid_idx) * 2 * s, 4))
    return grids

def _init_grid_pair(pair):
    """Initialize grid state for a pair, return dict with all local vars."""
    price = get_price(pair)
    if price <= 0: return None
    levels=int(cfg.get("grid_level_count", 5)); spread=cfg.get("base_spread", 0.05)
    levels = max(2, min(levels, 100))  # safety clamp
    # Dynamic spread: widen in volatile markets
    if cfg.get("dynamic_spread", True):
        try:
            ph = state.get("price_history", [])
            if len(ph) >= 20:
                prices = [p["value"] for p in ph[-20:] if p.get("value")]
                if prices:
                    avg = sum(prices)/len(prices)
                    var = sum((p-avg)**2 for p in prices)/(len(prices)-1 or 1)
                    vol = (var**0.5)/avg if avg>0 else 0
                    spread = min(spread * (1 + vol * 10), spread * 3)  # max 3x
        except Exception: pass
    grids = _make_grids(price, spread, levels)
    mid_idx = len(grids) // 2
    return {
        "grids": grids, "mid_idx": mid_idx, "filled": {},
        "trailing_pct": 0.5, "trailing_high": 0.0, "trailing_sell_active": False,
        "trailing_low": 0.0, "trailing_buy_active": False, "dip_occurred": False,
        "price": price, "previous_price": None, "levels": levels, "spread": spread,
        "last_price": None, "drop_through_active": False,
        "drop_through_low": price, "drop_through_levels": [],
    }

def _grid_crossed_buy_indices(grids, mid_idx, filled, previous_price, price):
    """Return unfilled buy levels crossed on an upward tick of the final grid."""
    if price <= 0 or (previous_price is not None and price < previous_price):
        return set()
    floor = previous_price if previous_price is not None else price
    return {idx for idx, level in enumerate(grids[:mid_idx+1])
            if idx not in filled and floor <= level <= price}

def _grid_sync_state(pair, gs, grids, mid_idx, filled, trailing_sell_active, trailing_high):
    """Sync per-pair grid state to state dict for dashboard display."""
    gp = state["grid_pairs"].get(pair, {})
    gp.update({
        "grid_levels": grids[:], "grid_buy_zone": grids[mid_idx+1], "grid_mid_idx": mid_idx,
        "grid_filled": {k: v for k, v in filled.items()},
        "grid_trailing_active": trailing_sell_active, "grid_trailing_high": trailing_high,
        "grids": grids[:], "filled": filled, "mid_idx": mid_idx,
        "trailing_sell_active": trailing_sell_active, "trailing_high": trailing_high,
    })
    state["grid_pairs"][pair] = gp
    # Also set top-level state for backward compat (shows active pair's data)
    if state.get("pair") == pair:
        state["grid_levels"] = grids[:]
        state["grid_buy_zone"] = grids[mid_idx+1]
        state["grid_mid_idx"] = mid_idx
        state["grid_filled"] = filled
        state["grid_trailing_active"] = trailing_sell_active
        state["grid_trailing_high"] = trailing_high

def _grid_sell_indices(filled, grid_idx, levels):
    """Select one open tranche for an actual sell cell.

    With the boundary shifted up by one, the first sell cell is mid_idx + 1.
    """
    mid_idx = levels // 2 + (levels % 2)
    first_sell_idx = mid_idx + 1
    if grid_idx < first_sell_idx or grid_idx >= levels or not filled:
        return []
    ordered = sorted(filled)
    if grid_idx == first_sell_idx:
        return [ordered[-1]]
    return [ordered[0]]
def _execute_base_buy_if_needed(pair, gs, price):
    """Execute base buy on start / re-center if not already seeded."""
    if not gs.get("seeded"):
        if price <= 0:
            return
        if state.get("mode") == "dex" and state.get("chain") == "solana":
            if cfg.get("sol_wallet"):
                try: sol_get_balance()
                except Exception: pass
            bal = state.get("sol_usdc", 0.0) or 0.0
        else:
            bal = get_balance() or 0.0
        comp_prof = state.get("compound_profit", 0.0) or 0.0
        effective_bal = bal + (comp_prof if cfg.get("auto_compound", True) else 0.0)
        min_order = max(5.0, float(cfg.get("min_order_usdc", 5) or 5.0))
        levels = int(gs.get("levels", 5) or 5)
        risk_pct = float(cfg.get("risk_pct", 2.0) or 2.0)
        max_pos = float(cfg.get("max_pos", 500.0) or 500.0)
        size = max(min_order, min(effective_bal * risk_pct / 100, max_pos) / levels)

        if size > 1:
            grids = gs["grids"]
            cell = None
            for i, g in enumerate(grids[:-1]):
                ng = grids[i+1]
                if g <= price < ng:
                    cell = i
                    break
            if cell is None:
                cell = gs["mid_idx"]

            base_amt = round(size / price, 6)
            if place_order(pair, "buy", base_amt, grid_idx=cell):
                gs["filled"][cell] = {"price": price, "amount": base_amt}
                state["positions"].append({"price": price, "amount": base_amt, "grid": cell, "strategy": "Grid"})
                record_trade("GRID-BUY", price, base_amt, pair=pair)
                log(f"[{pair}] BASE BUY {base_amt} @ ${price} (grid start)")
                send_telegram("🟢 <b>BUY</b> "+pair+"\nLevel: "+str(cell)+"\nPrice: $"+str(round(price,2))+"\nAmount: "+str(round(base_amt,6))+"\nMode: "+("LIVE" if not state["paper_trading"] else "PAPER"))
                _grid_sync_state(pair, gs, gs["grids"], gs["mid_idx"], gs["filled"], gs["trailing_sell_active"], gs["trailing_high"])
                gs["seeded"] = True

def run_grid():
    pair = state.get("pair","SOL/USDC")
    if pair not in state["active_pairs"]:
        state["active_pairs"].append(pair)
    # Initialize per-pair state for any new pair
    for p in state["active_pairs"]:
        if p not in state["grid_pairs"]:
            gs = _init_grid_pair(p)
            if gs:
                state["grid_pairs"][p] = gs
                seed_history(p)
                log("Grid initialized for "+p+": "+str(gs["grids"]), "INFO")

                # --- NEW BASE BUY ON START (Option B) ---
                _execute_base_buy_if_needed(p, gs, gs["price"])
    if not state["active_pairs"]:
        log("No active pairs to grid", "WARN"); return
    log("Grid started on "+str(state["active_pairs"])+" ("+state["mode"].upper()+")")

    while state["running"] and state["strategy"]=="grid":
        # Check for new pairs added mid-run
        for p in list(state["active_pairs"]):
            if p not in state["grid_pairs"]:
                gs = _init_grid_pair(p)
                if gs:
                    state["grid_pairs"][p] = gs
                    log("Grid initialized for "+p+": "+str(gs["grids"]), "INFO")
        for pair in list(state["active_pairs"]):
            gs = state["grid_pairs"].get(pair)
            if not gs: continue
            grids = gs["grids"]; mid_idx = gs["mid_idx"]; filled = gs["filled"]
            trailing_pct = state["config"].get("trailing_pct", 0.5); trailing_high = gs["trailing_high"]
            trailing_sell_active = gs["trailing_sell_active"]
            trailing_low = gs["trailing_low"]; trailing_buy_active = gs["trailing_buy_active"]
            dip_occurred = gs["dip_occurred"]; levels = gs["levels"]; spread = gs["spread"]

            price = get_price(pair)
            previous_price = gs.get("previous_price")
            if price > 0:
                if pair not in state["price_history_pairs"]:
                    state["price_history_pairs"][pair] = []
                state["price_history_pairs"][pair].append({"time": int(time.time()), "value": price})
                if len(state["price_history_pairs"][pair]) > 4320:
                    state["price_history_pairs"][pair] = state["price_history_pairs"][pair][-4320:]
            if price <= 0:
                _grid_sync_state(pair, gs, grids, mid_idx, filled, trailing_sell_active, trailing_high)
                time.sleep(5); continue

            # ── Pause check: wait while paused ──
            while state["paused"]:
                time.sleep(1)
                price = get_price(pair)
                if price <= 0: break

            # ── Grid re-centering ──
            if (price < grids[0] * 0.98 or price > grids[-1] * 1.02) or not filled:
                has_positions = bool(filled)
                if not filled:
                    log("["+pair+"] Grid re-centering: no positions at $"+str(price))
                else:
                    log("["+pair+"] Grid re-centering: price $"+str(price)+" outside ["+str(round(grids[0],2))+","+str(round(grids[-1],2))+"])")
                if has_positions and price < grids[0]:
                    new_grids = _make_grids(price, spread, levels)
                    for i in range(mid_idx + 2):
                        grids[i] = new_grids[i]
                    trailing_buy_active = False; trailing_low = 0.0; dip_occurred = False
                    log("["+pair+"] Grid buy zone lowered: "+str(grids[:mid_idx+2])+" sell zone kept: "+str(grids[mid_idx+1:]))
                else:
                    grids = _make_grids(price, spread, levels)
                    mid_idx = len(grids) // 2
                    gs["grids"] = grids
                    gs["mid_idx"] = mid_idx
                    trailing_sell_active = False; trailing_high = 0.0
                    trailing_buy_active = False; trailing_low = 0.0; dip_occurred = False
                    state["partial_positions"] = {}
                    gs["seeded"] = False  # Reset base buy seed guard on recenter/start
                    log("["+pair+"] Grid re-centered: "+str(grids)+" buy_zone=<="+str(grids[mid_idx+1]))
                    _execute_base_buy_if_needed(pair, gs, price)
                    # Refresh local loop variables
                    grids = gs["grids"]
                    mid_idx = gs["mid_idx"]
                    filled = gs["filled"]
            # Compute crossings against the final grid, after any recentering.
            # Downward movement defers buys until a later upward tick.
            moving_up = previous_price is None or price >= previous_price
            crossed_buy_indices = _grid_crossed_buy_indices(
                grids, mid_idx, filled, previous_price, price)
            gs["previous_price"] = price
            bal = get_balance()
            effective_bal = bal + (state.get("compound_profit", 0) if cfg.get("auto_compound", True) else 0)
            min_order = max(5.0, float(cfg.get("min_order_usdc", 5)))  # $5 minimum per grid level
            size = max(min_order, min(effective_bal*cfg["risk_pct"]/100, cfg["max_pos"])/levels)
            for i,g in enumerate(grids[:-1]):
                ng = grids[i+1]
                if (g <= price < ng) or (i in crossed_buy_indices):
                    is_buy_zone = i <= mid_idx
                    # ── BUY ZONE: trailing buy (buy on bounce) ──
                    if is_buy_zone:
                        # Track the low
                        if not trailing_buy_active and i not in filled:
                            trailing_buy_active = True
                            trailing_low = price
                            dip_occurred = False
                        elif trailing_buy_active:
                            if price < trailing_low:
                                trailing_low = price
                                dip_occurred = True
                        dip_mult = 1.5 if state.get("dip_active") else 1.0
                        # Buy: immediately if no dip, or on 0.5% bounce if dipped
                        if trailing_buy_active and i not in filled and size > 1:
                            # Every reached level executes immediately on upward movement.
                            should_buy = moving_up
                            if should_buy:
                                amt = round(size*dip_mult/price,6)
                                if place_order(pair,"buy",amt, grid_idx=i):
                                    filled[i]={"price":price,"amount":amt}
                                    state["positions"].append({"price":price,"amount":amt,"grid":i,"strategy":"Grid"})
                                    record_trade("GRID-BUY",price,amt, pair=pair)
                                    log("["+pair+"] BUY level "+str(i)+" @ $"+str(round(price,2))+(" (low $"+str(round(trailing_low,2))+" +"+str(trailing_pct)+"% bounce)" if dip_occurred else " (no dip)"))
                                    send_telegram("🟢 <b>BUY</b> "+state["pair"]+"\nLevel: "+str(i)+"\nPrice: $"+str(round(price,2))+"\nAmount: "+str(round(amt,6))+"\nMode: "+("LIVE" if not state["paper_trading"] else "PAPER"))
                                    trailing_buy_active = False
                                    trailing_low = 0.0
                                    # Reset sell trailing too, new position opened
                                    trailing_sell_active = False
                                    trailing_high = 0.0
                                    state["grid_trailing_active"] = trailing_sell_active
                                    state["grid_trailing_high"] = trailing_high
                    else:
                        # Reset buy trailing when leaving buy zone
                        if trailing_buy_active:
                            trailing_buy_active = False
                            trailing_low = 0.0
                            dip_occurred = False
                            state["grid_trailing_active"] = trailing_sell_active
                            state["grid_trailing_high"] = trailing_high

                    # ── Stop-loss check: immediate sell if position drops too far ──
                    stop_pct = cfg.get("grid_stop_loss_pct", 8)
                    for sl_buy_idx in sorted(list(filled.keys())):
                        sl_bp = filled[sl_buy_idx]["price"]
                        sl_loss = (price - sl_bp) / sl_bp * 100
                        if sl_loss < -stop_pct:
                            sl_amt = filled[sl_buy_idx]["amount"]
                            if place_order(pair,"sell",sl_amt):
                                sl_pnl = (price - sl_bp) * sl_amt
                                state["pnl"] += sl_pnl
                                record_trade("STOP-LOSS",price,sl_amt,round(sl_pnl,2), pair=pair)
                                log("["+pair+"] STOP-LOSS @ $"+str(round(price,2))+" (bought $"+str(round(sl_bp,2))+" loss "+str(round(abs(sl_loss),1))+"%)")
                                del filled[sl_buy_idx]
                                state["positions"]=[p for p in state["positions"] if p.get("grid")!=sl_buy_idx]
                    # ── Trailing take profit (fires on pullback regardless of grid zone) ──
                    # Arm / raise the trailing high while price is in the sell zone.
                    if not is_buy_zone:
                        if not trailing_sell_active and filled:
                            trailing_sell_active = True
                            trailing_high = price
                            state["grid_trailing_active"] = trailing_sell_active
                            state["grid_trailing_high"] = trailing_high
                            log("["+pair+"] Trailing sell active at $"+str(price))
                        elif trailing_sell_active:
                            if price > trailing_high:
                                trailing_high = price
                                state["grid_trailing_high"] = trailing_high
                                log("["+pair+"] Trailing high updated to $"+str(price))
                    # Sell when price drops trailing_pct% below the peak.
                    # Evaluated in BOTH zones so a pullback into the buy zone (at/below mid_idx)
                    # still exits (previously reset to 0 without selling - the no-sell bug).
                    if trailing_sell_active and price <= trailing_high * (1 - trailing_pct / 100):
                            _sell_cell = i if (not is_buy_zone) else (mid_idx + 1)
                            for buy_idx in sorted(_grid_sell_indices(filled, _sell_cell, levels)):
                                amt = filled[buy_idx]["amount"]
                                buy_price = filled[buy_idx]["price"]
                                if price <= buy_price:
                                    log(f"[{pair}] Take-profit/trailing-sell pullback to ${price:.2f} <= entry cost ${buy_price:.2f} for level {buy_idx}. HOLDING.", "WARN")
                                    # Reset trailing sell to prevent loop/log spam, hold position
                                    trailing_sell_active = False
                                    trailing_high = 0.0
                                    state["grid_trailing_active"] = False
                                    state["grid_trailing_high"] = 0.0
                                    break
                                partial_pct = cfg.get("partial_sell_pct", 50)
                                # Check if this position still has a partial remainder
                                partial_key = str(buy_idx)
                                is_partial_sell = cfg.get("partial_sell_pct", 50) < 100
                                sell_amt = amt
                                # ── Partial sell logic ──
                                if is_partial_sell and partial_key not in state.get("partial_positions", {}):
                                    # First sell: only sell partial_pct%
                                    sell_amt = amt * partial_pct / 100
                                    keep_amt = amt - sell_amt
                                    state["partial_positions"][partial_key] = {
                                        "amount": keep_amt, "buy_price": buy_price,
                                        "orig_amount": amt, "price": price
                                    }
                                    # Update filled entry to reflect kept amount
                                    filled[buy_idx]["amount"] = keep_amt
                                    log("PARTIAL SELL: sold "+str(round(sell_amt,6))+" ("
                                        +str(int(partial_pct))+"%) @ $"+str(round(price,2))
                                        +", keeping "+str(round(keep_amt,6))+" for wider trailing")
                                elif partial_key in state.get("partial_positions", {}):
                                    # Second sell: sell the remainder
                                    sell_amt = amt  # sell everything left
                                    if partial_key in state["partial_positions"]:
                                        del state["partial_positions"][partial_key]
                                if place_order(pair,"sell",sell_amt):
                                    pnl=(price-buy_price)*sell_amt
                                    state["pnl"]+=pnl
                                    state["daily_pnl"] = state.get("daily_pnl",0)+pnl
                                    if cfg.get("auto_compound", True) and pnl > 0:
                                        state["compound_profit"] += pnl
                                    tag = "GRID-PARTIAL" if is_partial_sell else "GRID-SELL"
                                    record_trade(tag,price,sell_amt,round(pnl,2), pair=pair)
                                    log("["+pair+"] SELL "+str(round(sell_amt,6))+" @ $"+str(round(price,2))+" (bought $"+str(round(buy_price,2))+" PnL $"+str(round(pnl,2))+")")
                                    log("["+pair+"] TRADE SUMMARY: "+str(round(sell_amt,6))+" bought @ $"+str(round(buy_price,2))+" sold @ $"+str(round(price,2))+" | PnL $"+str(round(pnl,2)))
                                    send_telegram("🔴 <b>SELL</b> "+state["pair"]+"\nBought: $"+str(round(buy_price,2))+"\nSold: $"+str(round(price,2))+"\nPnL: $"+str(round(pnl,2))+"\nTag: "+tag+"\nMode: "+("LIVE" if not state["paper_trading"] else "PAPER"))
                                    if is_partial_sell and partial_key in state.get("partial_positions",{}):
                                        # Don't delete the position yet — still holding remainder
                                        trailing_sell_active = False
                                        trailing_high = 0.0
                                        state["grid_trailing_active"] = False
                                        state["grid_trailing_high"] = 0.0
                                        state["grid_filled"] = {k: v for k, v in filled.items()}
                                        break
                                    else:
                                        del filled[buy_idx]
                                        state["positions"]=[p for p in state["positions"] if p.get("grid")!=buy_idx]
                                        trailing_sell_active = False
                                        trailing_high = 0.0
                                        state["grid_trailing_active"] = False
                                        state["grid_trailing_high"] = 0.0
                                        state["grid_filled"] = {k: v for k, v in filled.items()}
                                        break

            # ── Gap-fill, Buy-On-The-Way-Up, and Drop-Through Recovery Fill ──
            last_price = gs.get("last_price")
            drop_through_active = gs.get("drop_through_active", False)
            drop_through_low = gs.get("drop_through_low", price)
            drop_through_levels = gs.get("drop_through_levels", [])

            if last_price is not None:
                # 1. Check if price is dropping
                if price < last_price:
                    # Count how many unfilled buy levels were crossed/dropped through
                    crossed_levels = []
                    for gap_i in range(mid_idx + 1):
                        if gap_i in filled:
                            continue
                        # Level crossed on the way down: price <= grids[gap_i] < last_price
                        if price <= grids[gap_i] < last_price:
                            crossed_levels.append(gap_i)
                    
                    if len(crossed_levels) > 1:
                        # Drop-through detected!
                        if not drop_through_active:
                            drop_through_active = True
                            drop_through_low = price
                            drop_through_levels = crossed_levels
                            log(f"[{pair}] Drop-through detected: price fell from ${last_price:.2f} to ${price:.2f}, crossing unfilled buy levels {crossed_levels}. Waiting for bottom to buy.", "WARN")
                        else:
                            # Already in drop-through, price fell further
                            drop_through_low = price
                            # Merge new crossed levels
                            for lvl in crossed_levels:
                                if lvl not in drop_through_levels:
                                    drop_through_levels.append(lvl)
                            log(f"[{pair}] Drop-through continues: price fell to ${price:.2f}, lowest is now ${drop_through_low:.2f}.", "INFO")
                    elif drop_through_active:
                        # Already in drop-through, price fell further but crossed <= 1 level in this specific tick
                        if price < drop_through_low:
                            drop_through_low = price
                        for lvl in crossed_levels:
                            if lvl not in drop_through_levels:
                                drop_through_levels.append(lvl)
                
                # 2. Check for confirmed upward tick after the low to recover and buy
                elif price > last_price and drop_through_active:
                    log(f"[{pair}] Confirmed upward tick: price rose from last price ${last_price:.2f} (low was ${drop_through_low:.2f}) to ${price:.2f}. Triggering 'all buy at the bottom' recovery!", "WARN")
                    
                    # Fill ALL tracked drop_through_levels at/near the bottom (current price)
                    drop_through_levels.sort()
                    for gap_i in list(drop_through_levels):
                        if gap_i in filled:
                            continue
                        # Check balance safety rail
                        current_bal = get_balance()
                        if current_bal < size:
                            log(f"[{pair}] Safety rail: Insufficient balance to place order for level {gap_i} during recovery (balance: ${current_bal:.2f}, level size: ${size:.2f}). Skipping.", "WARN")
                            continue
                        
                        gap_amt = round(size / price, 6)
                        if place_order(pair, "buy", gap_amt, grid_idx=gap_i):
                            filled[gap_i] = {"price": price, "amount": gap_amt}
                            state["positions"].append({"price": price, "amount": gap_amt, "grid": gap_i, "strategy": "Grid"})
                            record_trade("GRID-BUY-GAP", price, gap_amt, pair=pair)
                            log(f"[{pair}] GAP-FILL-RECOVERY BUY level {gap_i} @ ${price:.2f}", "WARN")
                    
                    # Reset drop-through state
                    drop_through_active = False
                    drop_through_levels = []
                    drop_through_low = price

            # General gap-fill (C1 downward + upward buy-on-the-way-up) when NOT in a consecutive drop
            if not drop_through_active:
                for gap_i in range(mid_idx + 1):
                    if gap_i in filled:
                        continue
                    
                    # Downward gap-fill: price fell below level
                    _DISABLED_downward_gap = (last_price is not None) and (price <= grids[gap_i] < last_price)
                    
                    # Upward gap-fill: price rose above/through level
                    is_upward_gap = (last_price is not None) and (last_price < grids[gap_i] <= price)
                    
                    if is_upward_gap:
                        # Check balance safety rail
                        current_bal = get_balance()
                        if current_bal < size:
                            log(f"[{pair}] Safety rail: Insufficient balance to place order for level {gap_i} (balance: ${current_bal:.2f}, level size: ${size:.2f}). Skipping.", "WARN")
                            continue
                        
                        gap_amt = round(size / price, 6)
                        if place_order(pair, "buy", gap_amt, grid_idx=gap_i):
                            filled[gap_i] = {"price": price, "amount": gap_amt}
                            state["positions"].append({"price": price, "amount": gap_amt, "grid": gap_i, "strategy": "Grid"})
                            record_trade("GRID-BUY-GAP", price, gap_amt, pair=pair)
                            log(f"[{pair}] 'UPWARD' GAP-FILL BUY level {gap_i} @ ${price:.2f}", "WARN")

            # Save state variables in gs dict
            gs["last_price"] = price
            gs["drop_through_active"] = drop_through_active
            gs["drop_through_low"] = drop_through_low
            gs["drop_through_levels"] = drop_through_levels

            # ── Daily loss limit check ──
            now = int(time.time())
            today_midnight = now - (now % 86400)
            if state.get("last_midnight",0) < today_midnight:
                state["daily_pnl"] = 0.0
                state["last_midnight"] = today_midnight
            # Track peak balance
            usdc_bal = get_balance()
            total_val = usdc_bal
            for gp_name, gp_data in state.get("grid_pairs", {}).items():
                for idx, pos in gp_data.get("filled", {}).items():
                    total_val += pos.get("amount", 0) * pos.get("price", 0)
            if total_val > state.get("peak_balance", 0):
                state["peak_balance"] = total_val
            # Drawdown check
            dd_pct = cfg.get("max_drawdown_pct", 20)
            pk = state.get("peak_balance", 0)
            if pk > 0 and total_val < pk * (1 - dd_pct/100):
                log("DRAWDOWN STOP: portfolio $"+str(round(total_val,2))+" < "+str(round(pk*(1-dd_pct/100),2))+" ("+str(int(dd_pct))+"% drawdown)", "WARN")
                state["running"] = False
                state["strategy"] = None
                state["emergency_stop"] = True
                return
            dl = cfg.get("daily_loss_limit", 200)
            if state["daily_pnl"] < -dl:
                log("DAILY LOSS LIMIT: $"+"{:.2f}".format(-state["daily_pnl"])+" exceeds $"+str(dl), "WARN")
                state["running"] = False
                state["strategy"] = None
                state["emergency_stop"] = True
                return
            # Save per-pair state back
            gs.update({
                "grids": grids, "mid_idx": mid_idx, "filled": filled,
                "trailing_high": trailing_high, "trailing_sell_active": trailing_sell_active,
                "trailing_low": trailing_low, "trailing_buy_active": trailing_buy_active,
                "dip_occurred": dip_occurred,
            })
            _grid_sync_state(pair, gs, grids, mid_idx, filled, trailing_sell_active, trailing_high)
        time.sleep(30)

def run_scalp():
    log("Scalping started on "+state["pair"]+" ("+state["mode"].upper()+")")
    prices=[]; position=None
    while state["running"] and state["strategy"]=="scalp":
        while state["paused"]: time.sleep(1)
        price=get_price(state["pair"])
        if price<=0: time.sleep(10); continue
        prices.append(price)
        if len(prices)>20: prices.pop(0)
        if len(prices)<10: time.sleep(10); continue
        sma=sum(prices)/len(prices)
        bal=get_balance()
        size=min(bal*cfg["risk_pct"]/100,cfg["max_pos"])
        if position is None and price<sma*0.999 and size>1:
            amt=round(size/price,6)
            if place_order(pair,"buy",amt, grid_idx=i):
                position={"price":price,"amount":amt}
                state["positions"]=[{"price":price,"amount":amt,"strategy":"Scalp"}]
                record_trade("SCALP-BUY",price,amt, pair=pair)
                log("Scalp BUY @ $"+str(price))
        elif position:
            gain=(price-position["price"])/position["price"]*100
            loss=(position["price"]-price)/position["price"]*100
            if gain>=cfg["take_profit"]/3 or loss>=cfg["stop_loss"]/2:
                if place_order(pair,"sell",position["amount"]):
                    pnl=(price-position["price"])*position["amount"]
                    state["pnl"]+=pnl
                    if pnl<0: state["daily_loss"]+=abs(pnl)
                    record_trade("SCALP-SELL",price,position["amount"],round(pnl,2), pair=pair)
                    log("Scalp SELL @ $"+str(price)+" PnL: $"+str(round(pnl,2)))
                    position=None; state["positions"]=[]
        time.sleep(10)

def run_copy():
    source=cfg["source_wallet"]
    log("Copy Trading watching: "+source)
    while state["running"] and state["strategy"]=="copy":
        while state["paused"]: time.sleep(1)
        log("Monitoring "+source+" for trades...")
        time.sleep(60)

def run_arbitrage():
    mode = "PAPER" if state["paper_trading"] else "LIVE"
    chain = state.get("chain","ethereum")
    log("Arbitrage started ["+mode+" MODE] on "+chain+" — min spread: "+str(cfg["min_arb_spread"])+"%")
    while state["running"] and state["strategy"]=="arb":
        while state["paused"]: time.sleep(1)
        # Don't scan if a trade is in progress
        if state["trading_lock"]:
            time.sleep(5)
            continue

        # Cooldown between trades — wait 15s after last trade
        time_since_last = time.time() - state["last_trade_time"]
        if time_since_last < 15:
            time.sleep(15 - time_since_last)
            continue

        opps = scan_arbitrage()
        # Only execute the BEST opportunity per cycle, not all of them
        for opp in opps:
            if not state["running"]: break
            if opp["executable"]:
                log("ARB opportunity: "+opp["pair"]+" spread "+str(opp["spread_pct"])+"% est profit $"+str(opp["est_profit_usd"]))
                execute_arbitrage(opp)
                break  # Stop after first executable — wait for next scan cycle
        time.sleep(30)


def run_rsi_ema():
    """RSI + EMA crossover spot strategy. Buys on oversold+crossover, sells on overbought or reverse crossover."""
    pair = state["pair"]
    mode = "PAPER" if state["paper_trading"] else "LIVE"
    log("[RSI-EMA] Started on " + pair + " (" + mode + ")")
    rsi_period = cfg.get("rsi_period", 14)
    ema_fast = cfg.get("ema_fast", 9)
    ema_slow = cfg.get("ema_slow", 21)
    rsi_oversold = cfg.get("rsi_oversold", 30)
    rsi_overbought = cfg.get("rsi_overbought", 70)
    order_size = cfg.get("order_size_usdc", 50)
    has_position = False
    entry_price = 0
    while state["running"] and state["strategy"] == "rsi_ema":
        while state["paused"]: time.sleep(1)
        try:
            price = get_price(pair)
            if price > 0:
                if pair not in state["price_history_pairs"]:
                    state["price_history_pairs"][pair] = []
                state["price_history_pairs"][pair].append({"time": int(time.time()), "value": price})
                if len(state["price_history_pairs"][pair]) > 4320:
                    state["price_history_pairs"][pair] = state["price_history_pairs"][pair][-4320:]
            if price <= 0: time.sleep(30); continue
            # Build price buffer
            buf = get_price_history(pair, 100)
            if len(buf) < max(rsi_period, ema_slow) + 5:
                time.sleep(30); continue
            rsi_vals = calc_rsi(buf, rsi_period)
            ema_f = calc_ema(buf, ema_fast)
            ema_s = calc_ema(buf, ema_slow)
            rsi_now = rsi_vals[-1]
            f_now, s_now = ema_f[-1], ema_s[-1]
            f_prev, s_prev = ema_f[-2] if len(ema_f) > 1 else None, ema_s[-2] if len(ema_s) > 1 else None
            crossover_up = f_prev is not None and s_prev is not None and f_prev <= s_prev and f_now > s_now
            crossover_down = f_prev is not None and s_prev is not None and f_prev >= s_prev and f_now < s_now
            # Buy signal
            if not has_position and rsi_now and rsi_now < rsi_oversold and crossover_up:
                amt = round(order_size / price, 6)
                if place_order(pair, "buy", amt):
                    state["positions"].append({"price": price, "amount": amt, "strategy": "RSI-EMA"})
                    record_trade("RSI-BUY", price, amt, pair=pair)
                    has_position = True
                    entry_price = price
                    log("[RSI-EMA] BUY " + pair + " @ $" + str(round(price, 2)) + " | RSI=" + str(round(rsi_now, 1)) + " crossover")
            # Sell signals
            elif has_position:
                sell_signal = False
                reason = ""
                # Hard stop-loss
                stop_loss_pct = cfg.get("stop_loss_pct", 5.0)
                loss_pct = (price - entry_price) / entry_price * 100
                if loss_pct <= -stop_loss_pct:
                    sell_signal = True
                    reason = "Stop-loss " + str(round(stop_loss_pct,1)) + "%"
                elif rsi_now and rsi_now > rsi_overbought:
                    sell_signal = True
                    reason = "RSI overbought " + str(round(rsi_now, 1))
                elif crossover_down:
                    sell_signal = True
                    reason = "EMA crossover down"
                else:
                    # Trailing sell
                    pnl_pct = (price - entry_price) / entry_price * 100
                    trail_pct = cfg.get("trailing_pct", 1.5)
                    if pnl_pct >= 1.0:
                        peak = state.get("_rsi_peak_" + pair, entry_price)
                        if price > peak:
                            state["_rsi_peak_" + pair] = price
                            peak = price
                        if (peak - price) / peak * 100 >= trail_pct:
                            sell_signal = True
                            reason = "Trailing stop " + str(round(trail_pct, 1)) + "%"
                if sell_signal:
                    amt = state["positions"][-1]["amount"] if state["positions"] else round(order_size / entry_price, 6)
                    if place_order(pair, "sell", amt):
                        pnl = (price - entry_price) * amt
                        record_trade("RSI-SELL", price, amt, pnl, pair=pair)
                        log("[RSI-EMA] SELL " + pair + " @ $" + str(round(price, 2)) + " | PnL $" + str(round(pnl, 2)) + " | " + reason)
                        has_position = False
                        entry_price = 0
        except Exception as e:
            log("[RSI-EMA] Error: " + str(e), "ERROR")
        time.sleep(30)

def run_bbands():
    """Bollinger Bands spot strategy. Buys at lower band, sells at upper band."""
    pair = state["pair"]
    mode = "PAPER" if state["paper_trading"] else "LIVE"
    log("[BBANDS] Started on " + pair + " (" + mode + ")")
    period = cfg.get("bbands_period", 20)
    stddev = cfg.get("bbands_stddev", 2.0)
    order_size = cfg.get("order_size_usdc", 50)
    has_position = False
    entry_price = 0
    while state["running"] and state["strategy"] == "bbands":
        while state["paused"]: time.sleep(1)
        try:
            price = get_price(pair)
            if price > 0:
                if pair not in state["price_history_pairs"]:
                    state["price_history_pairs"][pair] = []
                state["price_history_pairs"][pair].append({"time": int(time.time()), "value": price})
                if len(state["price_history_pairs"][pair]) > 4320:
                    state["price_history_pairs"][pair] = state["price_history_pairs"][pair][-4320:]
            if price <= 0: time.sleep(30); continue
            buf = get_price_history(pair, 100)
            if len(buf) < period + 5:
                time.sleep(30); continue
            middle, upper, lower = calc_bbands(buf, period, stddev)
            lower_now = lower[-1]
            upper_now = upper[-1]
            if lower_now is None or upper_now is None:
                time.sleep(30); continue
            # Buy at lower band
            if not has_position and price <= lower_now:
                amt = round(order_size / price, 6)
                if place_order(pair, "buy", amt):
                    state["positions"].append({"price": price, "amount": amt, "strategy": "Bollinger"})
                    record_trade("BB-BUY", price, amt, pair=pair)
                    has_position = True
                    entry_price = price
                    log("[BBANDS] BUY " + pair + " @ $" + str(round(price, 2)) + " | lower band $" + str(round(lower_now, 2)))
            # Sell at upper band or trailing
            elif has_position:
                pnl_pct = (price - entry_price) / entry_price * 100
                trail_pct = cfg.get("trailing_pct", 1.5)
                sell_signal = False
                reason = ""
                # Hard stop-loss
                stop_loss_pct = cfg.get("stop_loss_pct", 5.0)
                if pnl_pct <= -stop_loss_pct:
                    sell_signal = True
                    reason = "Stop-loss " + str(round(stop_loss_pct,1)) + "%"
                elif price >= upper_now:
                    sell_signal = True
                    reason = "Upper band $" + str(round(upper_now, 2))
                elif pnl_pct >= 1.0:
                    peak = state.get("_bb_peak_" + pair, entry_price)
                    if price > peak:
                        state["_bb_peak_" + pair] = price
                        peak = price
                    if (peak - price) / peak * 100 >= trail_pct:
                        sell_signal = True
                        reason = "Trailing stop " + str(round(trail_pct, 1)) + "%"
                if sell_signal:
                    amt = state["positions"][-1]["amount"] if state["positions"] else round(order_size / entry_price, 6)
                    if place_order(pair, "sell", amt):
                        pnl = (price - entry_price) * amt
                        record_trade("BB-SELL", price, amt, pnl, pair=pair)
                        log("[BBANDS] SELL " + pair + " @ $" + str(round(price, 2)) + " | PnL $" + str(round(pnl, 2)) + " | " + reason)
                        has_position = False
                        entry_price = 0
        except Exception as e:
            log("[BBANDS] Error: " + str(e), "ERROR")
        time.sleep(30)

def run_webhook():
    """TradingView webhook receiver. Waits for external buy/sell signals via /webhook endpoint."""
    pair = state["pair"]
    mode = "PAPER" if state["paper_trading"] else "LIVE"
    log("[WEBHOOK] Active on " + pair + " (" + mode + ") — waiting for TradingView alerts")
    while state["running"] and state["strategy"] == "webhook":
        while state["paused"]: time.sleep(1)
        time.sleep(5)  # Just keep alive; trades come in via webhook handler

def resolve_order_mode(params):
    """Resolve explicit order mode; never infer from ambient environment."""
    raw = params.get("trade_mode", ["live"])[0].strip().lower()
    if raw not in ("live", "paper"):
        return None, "trade_mode must be live or paper"
    if raw == "paper" and params.get("paper_confirm", ["false"])[0].lower() != "true":
        return None, "paper mode requires explicit paper confirmation"
    return raw, None

def validate_limit_order(amount_usdc, side, order_type, limit_price, max_position=None):
    """Validate limit-order inputs without performing any network/trading action."""
    try:
        amount_usdc = float(amount_usdc); limit_price = float(limit_price)
    except (TypeError, ValueError):
        return False, "amount and price must be numeric"
    if not math.isfinite(amount_usdc) or amount_usdc <= 0:
        return False, "amount must be positive"
    if side not in ("buy", "sell"):
        return False, "invalid side"
    if order_type not in ("market", "limit"):
        return False, "invalid order type"
    if order_type == "limit" and (not math.isfinite(limit_price) or limit_price <= 0):
        return False, "limit price must be positive"
    if max_position is not None and amount_usdc > float(max_position):
        return False, "amount exceeds max position"
    return True, "ok"

def run_limit_order():
    """Monitor one validated limit order; market orders execute once immediately."""
    side = state.get("limit_side", "buy"); amount_usdc = float(state.get("limit_amount_usdc", 0)); limit_price = float(state.get("limit_price", 0)); order_type = state.get("limit_order_type", "limit")
    effective_mode = state.get("effective_mode", "live")
    # Limit execution is bound to the API-resolved mode, not ambient config.
    state["paper_trading"] = (effective_mode == "paper")
    pair = state["pair"]
    valid, reason = validate_limit_order(amount_usdc, side, order_type, limit_price, cfg.get("max_pos"))
    if not valid:
        state["last_trade"] = {"action": side, "pair": pair, "amount": amount_usdc, "order_type": order_type, "limit_price": limit_price, "status": "rejected", "error": reason, "time": int(time.time())}
        log("Limit order rejected: "+reason, "WARN"); state["running"] = False; state["strategy"] = None; return
    log("Limit order armed: "+side+" "+pair+" amount=$"+str(amount_usdc)+" type="+order_type+" limit=$"+str(limit_price))
    while state["running"] and state["strategy"] in ("limit_buy", "limit_sell"):
        price = get_price(pair)
        ready = order_type == "market" or (side == "buy" and price <= limit_price) or (side == "sell" and price >= limit_price)
        if price > 0 and ready:
            amount = round(amount_usdc / price, 6)
            if place_order(pair, side, amount):
                positions = state.setdefault("limit_positions", {})
                if side == "buy":
                    # Keep a per-pair position so the matching limit-sell can
                    # realize PnL (mirrors GRID-SELL's buy-price tracking).
                    positions[pair] = {"price": price, "amount": amount, "time": int(time.time())}
                    record_trade("LIMIT-BUY", price, amount, pair=pair)
                elif side == "sell":
                    pos = positions.pop(pair, None)
                    if pos:
                        pnl = round((price - pos["price"]) * amount, 2)
                        state["pnl"] += pnl
                        state["daily_pnl"] = state.get("daily_pnl", 0) + pnl
                        record_trade("LIMIT-SELL", price, amount, pnl, pair=pair)
                        log("["+pair+"] LIMIT-SELL "+str(round(amount,6))+" @ $"+str(round(price,2))+" (bought $"+str(round(pos["price"],2))+" PnL $"+str(round(pnl,2))+")")
                    else:
                        # No matching buy position (e.g. server restarted between
                        # the buy and sell): keep today's behavior (pnl None) and
                        # warn — never invent a PnL.
                        record_trade("LIMIT-SELL", price, amount, pair=pair)
                        log("no matching limit-buy position to compute PnL for "+pair, "WARN")
                else:
                    record_trade("LIMIT-"+side.upper(), price, amount, pair=pair)
                state["last_trade"] = {"action":side,"pair":pair,"price":price,"amount":amount,"order_type":order_type,"limit_price":limit_price,"status":"confirmed","effective_mode":effective_mode,"time":int(time.time())}
                state["running"] = False; state["strategy"] = None
                log("Limit order filled: "+side+" "+pair+" @ $"+str(price))
                return
            state["last_trade"] = {"action":side,"pair":pair,"price":price,"amount":amount,"order_type":order_type,"limit_price":limit_price,"status":"rejected","error":"place_order failed","effective_mode":effective_mode,"time":int(time.time())}
            state["running"] = False; state["strategy"] = None
            log("Limit order rejected after execution failure: "+side+" "+pair, "WARN")
            return
        time.sleep(5)

def run_ai_trading():
    """
    Continuous Multi-Symbol, Multi-Position AI Trading Strategy Loop.
    Integrates the AITradingEngine into the bot's background strategy runtime.
    """
    from ai_trading import AITradingEngine
    
    # 1. Initialize risk configuration dynamically from ambient state
    usdc_bal = state.get("sol_usdc", 0.0) or 0.0
    sol_bal = state.get("sol_bal", 0.0) or 0.0
    sol_price = get_price("SOL/USDC") or 100.0
    comp_prof = state.get("compound_profit", 0.0) if state.get("config", {}).get("auto_compound", True) else 0.0
    equity = max(100.0, usdc_bal + (sol_bal * sol_price) + comp_prof)
    
    risk_config = {
        "account_equity": equity,
        "risk_per_trade_pct": float(state["config"].get("risk_pct", 1.0) or 1.0),
        "max_leverage": float(state["config"].get("max_leverage", 3.0) or 3.0),
        "max_total_exposure": float(state["config"].get("max_total_exposure", 5000.0) or 5000.0),
        "max_per_asset_exposure": float(state["config"].get("max_per_asset_exposure", 2000.0) or 2000.0),
        "max_simultaneous_positions": int(state["config"].get("max_simultaneous_positions", 3) or 3),
        "daily_loss_limit": float(state["config"].get("daily_loss_limit", 100.0) or 100.0),
        "max_drawdown_limit_pct": float(state["config"].get("max_drawdown_limit_pct", 10.0) or 10.0),
        "circuit_breaker_active": False,
        "current_drawdown_pct": 0.0,
        "daily_loss_accrued": 0.0,
        "auto_compound": state["config"].get("auto_compound", True)
    }
    
    whitelist = state.get("ai_whitelisted_symbols", [])
    if not whitelist:
        whitelist = [state.get("pair", "SOL/USDC")]
        
    log("AI Trading Engine Starting Whitelist: " + str(whitelist))
    
    class LiveMarketDataProvider:
        def get_candles(self, symbol: str) -> dict:
            hist = state.get("price_history", [])
            if not hist:
                p = get_price(symbol) or 100.0
                hist = [p] * 60
            elif len(hist) < 60:
                hist = [hist[0]] * (60 - len(hist)) + hist
                
            return {
                "closes": hist,
                "highs": [x * 1.01 for x in hist],
                "lows": [x * 0.99 for x in hist],
                "volumes": [1000.0] * len(hist)
            }
            
        def get_current_price(self, symbol: str) -> float:
            return get_price(symbol) or 100.0
            
    class LiveExecutionAdapter:
        def execute_swap(self, symbol: str, direction: str, size: float, price: float) -> bool:
            side = "buy" if direction == "LONG" else "sell"
            success = place_order(symbol, side, size)
            if success:
                record_trade("AI-" + direction, price, size, pair=symbol)
            return success
            
        def get_venue_positions(self) -> dict:
            return {}

        def record_trade_closed(self, symbol: str, pnl: float):
            if state.get("config", {}).get("auto_compound", True) and pnl > 0:
                state["compound_profit"] = state.get("compound_profit", 0.0) + pnl
                log(f"AI Trading compounded profit: +${pnl:.2f}. Total compounded profit: ${state['compound_profit']:.2f}")

    engine = AITradingEngine(risk_config, whitelist)
    state["ai_engine"] = engine
    
    engine.start(LiveMarketDataProvider(), LiveExecutionAdapter(), interval_sec=10.0)
    sid = threading.current_thread().name
    log("AI Trading engine started - scanning market every 10s.")
    
    try:
        while state["running"] and state["strategy"] == "ai_trading":
            if "strategies" in state and sid in state["strategies"]:
                state["strategies"][sid]["status"] = "RUNNING"
            if engine:
                cur_usdc = state.get("sol_usdc", 0.0) or 0.0
                cur_sol = state.get("sol_bal", 0.0) or 0.0
                cur_price = get_price("SOL/USDC") or 100.0
                comp_added = state.get("compound_profit", 0.0) if state.get("config", {}).get("auto_compound", True) else 0.0
                cur_equity = max(100.0, cur_usdc + (cur_sol * cur_price) + comp_added)
                
                engine.risk_engine.config["account_equity"] = cur_equity
                engine.risk_engine.config["risk_per_trade_pct"] = float(state["config"].get("risk_pct", 1.0) or 1.0)
                engine.risk_engine.config["max_leverage"] = float(state["config"].get("max_leverage", 3.0) or 3.0)
                engine.risk_engine.config["max_total_exposure"] = float(state["config"].get("max_total_exposure", 5000.0) or 5000.0)
                engine.risk_engine.config["max_simultaneous_positions"] = int(state["config"].get("max_simultaneous_positions", 3) or 3)
                engine.risk_engine.config["auto_compound"] = state.get("config", {}).get("auto_compound", True)

            state["ai_status"] = engine.status
            state["ai_explain"] = engine.explain_msg
            state["ai_positions"] = list(engine.positions.keys())
            
            if engine.positions:
                p_symbol = list(engine.positions.keys())[0]
                pos = engine.positions[p_symbol]
                state["ai_regime"] = pos.get("regime", "TRENDING_BULL")
                state["ai_selected_strategy"] = pos.get("strategy", "None")
                state["ai_score"] = pos.get("score", 85.0) or 85.0
                state["ai_confidence"] = "HIGH"
                state["ai_exposure"] = sum(p["exposure_usd"] for p in engine.positions.values())
            else:
                state["ai_regime"] = "TRENDING_BULL"
                state["ai_selected_strategy"] = "None"
                state["ai_score"] = 0.0
                state["ai_confidence"] = "—"
                state["ai_exposure"] = 0.0
                
            time.sleep(2)
    finally:
        engine.stop()
        state["ai_engine"] = None
        log("AI Trading Strategy stopped.")


STRATEGIES = {"dca":run_dca,"grid":run_grid,"scalp":run_scalp,"copy":run_copy,"arb":run_arbitrage,"rsi_ema":run_rsi_ema,"bbands":run_bbands,"webhook":run_webhook,"limit_buy":run_limit_order,"limit_sell":run_limit_order,"ai_trading":run_ai_trading}

def _mark_strategy_stopped(sid, status="STOPPED"):
    """Mark a strategy no-longer-running and reconcile the page-level running flag.

    Called when a strategy thread ends (normally or after a fatal error) so the
    per-strategy card and the global "running" indicator never diverge.
    """
    if sid and "strategies" in state and sid in state["strategies"]:
        strat = state["strategies"][sid]
        strat["running"] = False
        if strat.get("status") not in ("FILLED", "REJECTED"):
            strat["status"] = status
    remaining = [st for st in state.get("strategies", {}).values() if st.get("running")]
    if not remaining:
        dict.__setitem__(state, "running", False)
        dict.__setitem__(state, "strategy", None)
        state["active_pairs"] = []

def _safe_strategy_runner(target_func):
    def wrapper(*args, **kwargs):
        sid = kwargs.get("sid") or (args[0] if args else None)
        try:
            try:
                target_func(*args, **kwargs)
            except TypeError as te:
                if "takes" in str(te) or "argument" in str(te):
                    target_func()
                else:
                    raise te
        except Exception as e:
            import traceback
            err_msg = f"Fatal error in strategy thread: {e}"
            log(err_msg, "ERROR")
            log(traceback.format_exc(), "DEBUG")
            if sid and "strategies" in state and sid in state["strategies"]:
                state["strategies"][sid]["running"] = False
                state["strategies"][sid]["status"] = "REJECTED"
                state["strategies"][sid]["error"] = err_msg
            state["error"] = err_msg
            _mark_strategy_stopped(sid)
        else:
            _mark_strategy_stopped(sid)
    return wrapper

def stop_strategy(sid):
    if "strategies" in state and sid in state["strategies"]:
        strat = state["strategies"][sid]
        strat["running"] = False
        strat["status"] = "STOPPED"
        running_any = any(s.get("running") for s in state.get("strategies", {}).values() if s.get("sid") != sid)
        if not running_any:
            state["running"] = False
            state["strategy"] = None
            state["active_pairs"] = []
        log(f"Strategy {sid} stopped")

def stop_all():
    if "strategies" in state:
        for sid, strat in state["strategies"].items():
            strat["running"] = False
            strat["status"] = "STOPPED"
    state["running"] = False
    state["strategy"] = None
    state["active_pairs"] = []
    for k in list(state.keys()):
        if k.startswith("_rsi_peak_") or k.startswith("_bb_peak_"):
            del state[k]
    log("All strategies stopped")

def start_bot(strategy, pair, mode, exchange=None, chain=None, order=None):
    if "strategies" not in state:
        state["strategies"] = {}
        
    sid = f"{strategy}_{pair}"
    state["strategy"] = strategy
    state["pair"] = pair
    state["mode"] = mode
    state["running"] = True
    state["error"] = None
    if exchange: state["exchange"] = exchange
    if chain: state["chain"] = chain
    else: state["chain"] = "solana"
    
    strategy_config = order if order else {}
    if "paper_trading" not in strategy_config:
        strategy_config["paper_trading"] = state.get("paper_trading", True)
        
    state["strategies"][sid] = {
        "sid": sid,
        "type": strategy,
        "pair": pair,
        "running": True,
        "paused": False,
        "config": strategy_config,
        "status": "RUNNING",
        "last_trade": None,
        "log_tail": [],
        "started_at": int(time.time()),
    }
    
    seed_history(pair)
    
    target_fn = STRATEGIES.get(strategy, run_dca)
    safe_fn = _safe_strategy_runner(target_fn)
    t = threading.Thread(target=safe_fn, args=(sid,), name=sid, daemon=True)
    state["strategies"][sid]["thread"] = t
    t.start()
    
    chain_str = f" / {chain.upper()}" if (mode == "dex" and chain) else ""
    log(f"Started {strategy.upper()} on {pair} via {mode.upper()}{chain_str} (sid: {sid})")

def stop_bot():
    state["running"]=False
    state["strategy"]=None
    state["active_pairs"]=[]
    for k in list(state.keys()):
        if k.startswith("_rsi_peak_") or k.startswith("_bb_peak_"):
            del state[k]
    if "strategies" in state:
        for sid, strat in state["strategies"].items():
            strat["running"] = False
            strat["status"] = "STOPPED"
    log("Bot stopped")

# ── Dashboard ─────────────────────────────────────────────────────────────────
try:
    from limit_orders_addon import LimitOrdersAddon
    limit_orders_addon = LimitOrdersAddon()
except Exception:
    limit_orders_addon = None

SCANNER_HTML = '''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>Token Safety Scanner</title>
<style>
:root{--bg:#080808;--card:#111;--border:#1a1a1a;--text:#eee;--text2:#888;--dim:#444;--accent:#00ff9d;--red:#ff6b6b;--yellow:#ffd43b;--blue:#4dabf7}
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;background:var(--bg);color:var(--text);padding:16px}
h1{font-size:18px;font-weight:900;margin-bottom:4px}
.sub{font-size:12px;color:var(--dim);margin-bottom:16px}
.input-row{display:flex;gap:8px;margin-bottom:8px}
input{flex:1;padding:10px 12px;border:1.5px solid var(--border);border-radius:8px;font-size:13px;background:var(--card);color:var(--text);font-family:monospace}
input:focus{outline:none;border-color:var(--accent)}
.scan-btn{background:var(--accent);color:#000;border:none;padding:10px 20px;border-radius:8px;font-weight:800;font-size:13px;cursor:pointer;white-space:nowrap}
.scan-btn:disabled{background:var(--card);color:var(--dim);cursor:not-allowed}
.symbol-input{margin-bottom:14px}
.symbol-input input{width:100%}
#status{font-size:12px;color:var(--dim);margin-bottom:14px;min-height:16px}
.rating-banner{padding:14px;border-radius:10px;text-align:center;font-weight:900;font-size:16px;margin-bottom:16px;letter-spacing:1px}
.r-low{background:var(--accent)18;color:var(--accent);border:1.5px solid var(--accent)}
.r-lowmed{background:var(--blue)18;color:var(--blue);border:1.5px solid var(--blue)}
.r-med{background:var(--yellow)18;color:var(--yellow);border:1.5px solid var(--yellow)}
.r-high,.r-crit{background:var(--red)18;color:var(--red);border:1.5px solid var(--red)}
.section{background:var(--card);border:1px solid var(--border);border-radius:10px;padding:14px;margin-bottom:12px}
.section-title{font-size:10px;font-weight:700;letter-spacing:1.5px;color:var(--dim);text-transform:uppercase;margin-bottom:10px}
.row{display:flex;justify-content:space-between;padding:5px 0;border-bottom:1px solid var(--border);font-size:12px}
.row:last-child{border-bottom:none}
.row .label{color:var(--text2)}
.row .val{font-family:monospace;font-weight:600}
.ok{color:var(--accent)}.warn{color:var(--yellow)}.bad{color:var(--red)}.unknown{color:var(--dim)}
.flags{list-style:none}
.flags li{padding:8px 10px;background:var(--yellow)0d;border-left:3px solid var(--yellow);border-radius:4px;font-size:12px;margin-bottom:6px;color:var(--text)}
.flags li.crit{background:var(--red)0d;border-left-color:var(--red)}
.flags.empty{color:var(--dim);font-size:12px;padding:8px 0}
.mint-display{font-family:monospace;font-size:10px;color:var(--dim);word-break:break-all;margin-top:2px}
</style>
</head>
<body>
<h1>&#128269; Token Safety Scanner</h1>
<div class="sub">Read-only diagnostic — scanning a token here does NOT add it to GridRunner or make it tradeable.</div>

<div class="input-row">
  <input type="text" id="mint-input" placeholder="Paste Solana mint address (base58)..." autocomplete="off"/>
</div>
<div class="input-row symbol-input">
  <input type="text" id="symbol-input" placeholder="Expected symbol (optional, e.g. BONK) — helps catch impersonators" autocomplete="off"/>
</div>
<div class="input-row">
  <button class="scan-btn" id="scan-btn" onclick="runScan()" style="flex:1">Scan Token</button>
</div>
<div id="status"></div>
<div id="results"></div>

<script>
function apiFetch(url) {
  return fetch(url, { credentials: "same-origin" });
}
function ratingClass(r) {
  if (r === "LOW RISK") return "r-low";
  if (r === "LOW-MEDIUM RISK") return "r-lowmed";
  if (r === "MEDIUM RISK") return "r-med";
  if (r === "HIGH RISK") return "r-high";
  return "r-crit";
}
function fmtBool(v, goodWhenFalse) {
  if (v === null || v === undefined) return '<span class="unknown">unknown</span>';
  var isBad = goodWhenFalse ? !!v : !v;
  return '<span class="' + (isBad ? "bad" : "ok") + '">' + (v ? "yes" : "no") + '</span>';
}
function fmtPct(v, warnAt, badAt) {
  if (v === null || v === undefined) return '<span class="unknown">unknown</span>';
  var cls = "ok";
  if (badAt !== undefined && v >= badAt) cls = "bad";
  else if (warnAt !== undefined && v >= warnAt) cls = "warn";
  return '<span class="' + cls + '">' + v + '%</span>';
}
function esc(s) {
  var d = document.createElement("div"); d.textContent = s == null ? "" : s; return d.innerHTML;
}

function runScan() {
  var mint = document.getElementById("mint-input").value.trim();
  var symbol = document.getElementById("symbol-input").value.trim();
  var statusEl = document.getElementById("status");
  var resultsEl = document.getElementById("results");
  var btn = document.getElementById("scan-btn");
  if (!mint) { statusEl.innerHTML = '<span class="bad">Enter a mint address first</span>'; return; }
  btn.disabled = true; btn.textContent = "Scanning...";
  statusEl.textContent = "Querying on-chain data, DexScreener, and RugCheck — this can take a few seconds...";
  resultsEl.innerHTML = "";

  var url = "/scan_token?mint=" + encodeURIComponent(mint) + (symbol ? "&symbol=" + encodeURIComponent(symbol) : "");
  apiFetch(url).then(function(r) { return r.json(); }).then(function(d) {
    btn.disabled = false; btn.textContent = "Scan Token";
    if (d.error) { statusEl.innerHTML = '<span class="bad">' + esc(d.error) + '</span>'; return; }
    statusEl.textContent = "Scanned " + new Date(d.scanned_at * 1000).toLocaleTimeString();
    render(d);
  }).catch(function(e) {
    btn.disabled = false; btn.textContent = "Scan Token";
    statusEl.innerHTML = '<span class="bad">Scan failed: ' + esc(String(e)) + '</span>';
  });
}

function render(d) {
  var v = d.verification || {};
  var h = d.holders || {};
  var rc = d.rugcheck || {};
  var html = "";

  html += '<div class="rating-banner ' + ratingClass(d.rating) + '">' + esc(d.rating) + ' &mdash; Registry: ' + esc(d.registry_status) + '</div>';

  html += '<div class="section"><div class="section-title">On-Chain Mint</div>';
  html += '<div class="mint-display">' + esc(d.mint) + '</div><br/>';
  html += '<div class="row"><span class="label">Exists on-chain</span><span class="val">' + fmtBool(v.exists, false) + '</span></div>';
  html += '<div class="row"><span class="label">Decimals</span><span class="val">' + (v.decimals ?? '<span class="unknown">unknown</span>') + '</span></div>';
  html += '<div class="row"><span class="label">Mint authority revoked</span><span class="val">' + fmtBool(!v.mint_authority, false) + '</span></div>';
  html += '<div class="row"><span class="label">Freeze authority active</span><span class="val">' + fmtBool(!!v.freeze_authority, true) + '</span></div>';
  html += '</div>';

  html += '<div class="section"><div class="section-title">Market Data</div>';
  html += '<div class="row"><span class="label">Liquidity (USD)</span><span class="val">' + (v.liquidity_usd != null ? "$" + Math.round(v.liquidity_usd).toLocaleString() : '<span class="unknown">unknown</span>') + '</span></div>';
  html += '<div class="row"><span class="label">Pool age</span><span class="val">' + (v.pool_age_hours != null ? v.pool_age_hours.toFixed(1) + "h" : '<span class="unknown">unknown</span>') + '</span></div>';
  html += '<div class="row"><span class="label">Market symbol / name</span><span class="val">' + esc(v.dex_symbol || "?") + ' / ' + esc(v.dex_name || "?") + '</span></div>';
  html += '</div>';

  html += '<div class="section"><div class="section-title">Holder Concentration</div>';
  html += '<div class="row"><span class="label">Top holder</span><span class="val">' + fmtPct(h.top1_holder_pct, 20, 40) + '</span></div>';
  html += '<div class="row"><span class="label">Top 10 holders</span><span class="val">' + fmtPct(h.top10_holder_pct, 50, 75) + '</span></div>';
  html += '<div class="row"><span class="label">Accounts sampled</span><span class="val">' + (h.accounts_sampled || 0) + '</span></div>';
  html += '</div>';

  html += '<div class="section"><div class="section-title">RugCheck.xyz</div>';
  if (rc.available) {
    html += '<div class="row"><span class="label">Risk score</span><span class="val">' + (rc.score ?? '<span class="unknown">n/a</span>') + '</span></div>';
    html += '<div class="row"><span class="label">LP locked/burned</span><span class="val">' + fmtPct(rc.lp_locked_pct, undefined, undefined) + '</span></div>';
  } else {
    html += '<div class="row"><span class="label">Status</span><span class="val unknown">unavailable (' + esc(rc.reason || "unknown") + ')</span></div>';
  }
  html += '</div>';

  html += '<div class="section"><div class="section-title">Flags (' + (d.flags ? d.flags.length : 0) + ')</div>';
  if (d.flags && d.flags.length) {
    html += '<ul class="flags">';
    d.flags.forEach(function(f) {
      var isCrit = /unlimited|does not exist/.test(f);
      html += '<li' + (isCrit ? ' class="crit"' : '') + '>' + esc(f) + '</li>';
    });
    html += '</ul>';
  } else {
    html += '<div class="flags empty">No flags raised.</div>';
  }
  html += '</div>';

  document.getElementById("results").innerHTML = html;
}

document.getElementById("mint-input").addEventListener("keydown", function(e) {
  if (e.key === "Enter") runScan();
});
</script>
</body>
</html>'''

DASHBOARD = '''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>GridRunner — Trading Dashboard</title>
<link rel="manifest" href="/manifest.json"/>
<meta name="theme-color" content="#0a0a1a"/>
<meta name="apple-mobile-web-app-capable" content="yes"/>
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent"/>
<meta name="apple-mobile-web-app-title" content="GridRunner"/>
<link rel="apple-touch-icon" href="/logo.jpeg"/>
<script src="https://unpkg.com/lightweight-charts/dist/lightweight-charts.standalone.production.js"></script>

<style>
:root{--bg:#080808;--card:#111;--border:#1a1a1a;--text:#eee;--text2:#888;--dim:#444;--accent:#00ff9d;--red:#ff6b6b;--blue:#4dabf7;--purple:#cc99ff;--yellow:#ffd43b}
.light{--bg:#f0f2f5;--card:#fff;--border:#d0d5dd;--text:#1a1a1a;--text2:#555;--dim:#999;--accent:#00b875;--red:#e03131;--blue:#1971c2;--purple:#7c3aed;--yellow:#e67700}
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;background:var(--bg);color:var(--text);padding:20px;transition:background .3s,color .3s}
.wrap{max-width:960px;margin:0 auto}
.head-row{display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:10px;margin-bottom:4px}
h1{font-size:22px;font-weight:900;color:var(--text)}
.sub{font-size:13px;color:var(--dim);margin-bottom:24px;display:flex;align-items:center;gap:8px;flex-wrap:wrap}
.dot{width:8px;height:8px;border-radius:50%;background:#333;display:inline-block;transition:all .3s}
.dot.on{background:var(--accent);box-shadow:0 0 8px var(--accent)}
.theme-btn{background:var(--card);border:1px solid var(--border);border-radius:8px;padding:8px 12px;cursor:pointer;font-size:13px;color:var(--text);transition:all .15s}
.theme-btn:hover{border-color:var(--accent)}
#chart-container{height:350px;flex:1;min-width:0;border-radius:10px;background:var(--card);border:1px solid var(--border);overflow:hidden;position:relative}
#chart-container iframe{border-radius:10px}
.stats{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin-bottom:20px}
.stat{background:var(--card);border:1px solid var(--border);border-radius:10px;padding:16px}
.sl{font-size:10px;font-weight:700;letter-spacing:2px;color:var(--dim);text-transform:uppercase;margin-bottom:6px}
.sv{font-size:22px;font-weight:900;color:var(--text)}
.sv.g{color:var(--accent)}.sv.r{color:var(--red)}
.card{background:var(--card);border:1px solid var(--border);border-radius:10px;padding:20px;margin-bottom:16px}
.ct{font-size:10px;font-weight:700;letter-spacing:2px;color:var(--accent);text-transform:uppercase;margin-bottom:14px}
.btn-row{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:14px}
.btn{padding:9px 16px;border:1.5px solid var(--border);border-radius:8px;font-weight:700;font-size:12px;cursor:pointer;background:var(--card);color:var(--text2);transition:all .15s}
.btn:hover{border-color:var(--accent);color:var(--text)}
.btn.active-strat{background:var(--accent)18;color:var(--accent);border-color:var(--accent)}
.btn.active-pair{background:var(--blue)18;color:var(--blue);border-color:var(--blue)}
.btn.active-chain{background:var(--purple)18;color:var(--purple);border-color:var(--purple)}
.btn.active-exch{background:var(--yellow)18;color:var(--yellow);border-color:var(--yellow)}
.btn-start{background:var(--accent);color:var(--bg);border:none;padding:13px 32px;font-size:14px;border-radius:8px;font-weight:800;cursor:pointer;transition:all .15s}
.btn-start:disabled{background:var(--card);color:var(--dim);cursor:not-allowed}
.btn-stop{background:var(--red)18;color:var(--red);border:1.5px solid var(--red)33;padding:13px 24px;font-size:13px;border-radius:8px;font-weight:700;cursor:pointer}
.btn-pause{background:var(--yellow)18;color:var(--yellow);border:1.5px solid var(--yellow)33;padding:13px 24px;font-size:13px;border-radius:8px;font-weight:700;cursor:pointer}
.section-label{font-size:11px;color:var(--dim);font-weight:700;margin-bottom:8px;text-transform:uppercase;letter-spacing:1px}
select.dd{width:100%;padding:10px 12px;border:1.5px solid var(--border);border-radius:8px;font-size:13px;font-weight:600;background:var(--card);color:var(--text);cursor:pointer;margin-bottom:12px;transition:all .15s;appearance:auto}
select.dd:focus{outline:none;border-color:var(--accent)}
.config-grid{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-bottom:14px}
.config-field{display:flex;flex-direction:column;gap:4px}
.config-field label{font-size:10px;color:var(--dim);font-weight:700;text-transform:uppercase;letter-spacing:1px}
.config-field input,.config-field select{padding:8px 10px;border:1.5px solid var(--border);border-radius:6px;font-size:12px;background:var(--card);color:var(--text);transition:all .15s}
.config-field input:focus,.config-field select:focus{outline:none;border-color:var(--accent)}
.preset-row{display:flex;gap:6px;flex-wrap:wrap;margin-bottom:14px}
.preset-btn{padding:6px 14px;border:1.5px solid var(--border);border-radius:6px;font-size:11px;font-weight:600;cursor:pointer;background:var(--card);color:var(--text2);transition:all .15s}
.preset-btn:hover{border-color:var(--accent);color:var(--accent)}
.preset-btn.active{background:var(--accent)18;color:var(--accent);border-color:var(--accent)}
table{width:100%;border-collapse:collapse;font-size:12px}
th{color:var(--dim);font-weight:700;text-align:left;padding:8px 0;border-bottom:1px solid var(--border);font-size:10px;letter-spacing:1px;text-transform:uppercase}
td{padding:8px 0;border-bottom:1px solid var(--border);color:var(--text2)}
.badge{display:inline-block;padding:2px 8px;border-radius:4px;font-size:11px;font-weight:700}
.badge-p{background:var(--accent)18;color:var(--accent)}
.badge-l{background:var(--red)18;color:var(--red)}
.badge-s{background:var(--yellow)18;color:var(--yellow)}
.buy{color:var(--accent);font-weight:700}.sell{color:var(--red);font-weight:700}.stop{color:var(--yellow);font-weight:700}
.log-box{background:var(--bg);border:1px solid var(--border);border-radius:8px;padding:14px;height:180px;overflow-y:auto;font-family:monospace;font-size:11px;line-height:1.8}
.li{color:var(--text2)}.lw{color:var(--yellow)}.le{color:var(--red)}
.arb-row{display:flex;justify-content:space-between;align-items:center;padding:10px 0;border-bottom:1px solid var(--border);font-size:12px}
.arb-spread{color:var(--accent);font-weight:800;font-size:14px}
.dex-info{background:var(--purple)11;border:1px solid var(--purple)22;border-radius:8px;padding:12px;margin-bottom:14px;font-size:12px;color:var(--purple);line-height:1.6}
.summary-cards{display:grid;grid-template-columns:repeat(4,1fr);gap:8px;margin-bottom:16px}
.summary-card{background:var(--card);border:1px solid var(--border);border-radius:8px;padding:12px;text-align:center}
.summary-card .label{font-size:9px;color:var(--dim);font-weight:700;text-transform:uppercase;letter-spacing:1px}
.summary-card .value{font-size:16px;font-weight:900;margin-top:4px;color:var(--text)}
.summary-card .value.g{color:var(--accent)}.summary-card .value.r{color:var(--red)}
.toast-container{position:fixed;top:12px;right:12px;z-index:9999;display:flex;flex-direction:column;gap:6px;pointer-events:none}
.toast{background:var(--card);border:1px solid var(--border);border-radius:8px;padding:10px 16px;font-size:12px;color:var(--text);box-shadow:0 4px 20px rgba(0,0,0,.4);animation:slideIn .3s ease-out;max-width:320px;pointer-events:auto}
.toast.trade{border-left:3px solid var(--accent)}.toast.error{border-left:3px solid var(--red)}.toast.info{border-left:3px solid var(--blue)}
@keyframes slideIn{from{transform:translateX(100%);opacity:0}to{transform:translateX(0);opacity:1}}
@keyframes fadeOut{from{opacity:1}to{opacity:0}}
.action-bar{display:flex;gap:10px;margin-top:8px;flex-wrap:wrap;align-items:center}
.twocol{display:grid;grid-template-columns:1fr 1fr;gap:16px}
@media(max-width:700px){.stats{grid-template-columns:1fr 1fr}.summary-cards{grid-template-columns:1fr 1fr}.twocol{grid-template-columns:1fr}.config-grid{grid-template-columns:1fr}}
</style>
</head>
<body>

<div id="toast-container" class="toast-container"></div>
<div class="wrap">
  <div class="head-row">
    <div><h1><span style="color:#fff">Grid</span><span style="color:#00ff9d">Runner</span></h1><div class="sub"><span class="dot" id="dot"></span><span id="status-text">Stopped</span></div></div>
    <div style="display:flex;gap:6px">
      <button class="theme-btn" id="theme-btn" onclick="toggleTheme()">🌙 Dark</button>
      <button class="btn" onclick="exportCSV()" style="font-size:11px">&#11015; CSV</button>
      <button class="btn" onclick="killSwitch()" title="Emergency close all positions" style="font-size:11px;color:var(--red);border-color:var(--red)44">&#128721; Kill</button>
      <button class="btn" onclick="runBacktest()" style="font-size:11px">📊 Backtest</button>
      <button class="btn" onclick="openScanner()" title="Scan a token's mint for safety before trading it" style="font-size:11px;color:var(--accent);border-color:var(--accent)44">&#128269; Scanner</button>
    </div>
  </div>

  <div class="stats">
    <div class="stat"><div class="sl">Price (Raydium)</div><div class="sv" id="s-price">—</div></div>
    <div class="stat"><div class="sl">EVM Balance</div><div class="sv" id="s-balance">—</div></div>
    <div class="stat"><div class="sl">Solana</div><div class="sv" id="s-sol-balance" style="font-size:16px">—</div></div>
    <div class="stat"><div class="sl">Total P&amp;L</div><div class="sv" id="s-pnl">$0.00</div></div>
    <div class="stat"><div class="sl">Open Positions</div><div class="sv" id="s-pos">0</div></div>
    <div class="stat"><div class="sl">Mode</div><div class="sv" id="s-mode" style="font-size:14px">—</div></div>
  </div>

  <div id="charts-container" style="display:none;grid-template-columns:repeat(auto-fit,minmax(320px,1fr));justify-content:end;align-items:start;gap:16px;width:100%;margin-bottom:16px;box-sizing:border-box"></div>
  <div style="display:flex;gap:16px;align-items:stretch" id="single-chart-row">
    <div id="chart-container" style="flex:1;min-width:0">
      <div id="chart-placeholder" style="display:none;position:absolute;inset:0;align-items:center;justify-content:center;color:var(--dim);font-size:13px;pointer-events:none;z-index:5"></div>
    </div>
    <div class="card" id="grid-details-card" style="width:420px;flex-shrink:0;height:400px;overflow-y:auto">
      <div class="ct" style="display:flex;align-items:center;gap:8px">
        Grid Details
        <span id="gdt-status" style="font-size:11px;font-weight:400;color:var(--dim)"></span>
      </div>
      <div id="grid-details-body"></div>
    
    <div class="card" id="ai-trading-status-card" style="display:none;width:420px;flex-shrink:0;height:400px;overflow-y:auto">
      <div class="ct">AI Trading Live Status</div>
      <div style="display:flex;flex-direction:column;gap:10px;margin-top:12px;font-size:13px">
        <div style="display:flex;justify-content:space-between"><strong>Engine State:</strong> <span id="ai-engine-status" style="font-weight:700">analyzing</span></div>
        <div style="display:flex;justify-content:space-between"><strong>Market Regime:</strong> <span id="ai-regime-status">—</span></div>
        <div style="display:flex;justify-content:space-between"><strong>Signal Score:</strong> <span id="ai-score-status">—</span></div>
        <div style="display:flex;justify-content:space-between"><strong>Confidence:</strong> <span id="ai-confidence-status">—</span></div>
        <div style="display:flex;justify-content:space-between"><strong>Selected Strategy:</strong> <span id="ai-selected-strategy">—</span></div>
        <div style="display:flex;justify-content:space-between"><strong>Portfolio Exposure:</strong> <span id="ai-exposure-status">$0.00</span></div>
        <div style="display:flex;justify-content:space-between"><strong>Risk Status:</strong> <span id="ai-risk-status" style="color:var(--accent)">PASS</span></div>
        <div style="border-top:1px solid var(--border);padding-top:8px">
          <strong>Decision Logic:</strong>
          <div id="ai-decision-explain" style="font-size:11px;color:var(--text2);margin-top:4px;white-space:pre-wrap">Analyzing markets...</div>
        </div>
      </div>
    </div>

    </div>
  </div>

  <div class="summary-cards" id="summary-cards">
    <div class="summary-card"><div class="label">Win Rate</div><div class="value" id="sm-winrate">0%</div></div>
    <div class="summary-card"><div class="label">Avg Profit</div><div class="value g" id="sm-avgprofit">$0.00</div></div>
    <div class="summary-card"><div class="label">Total Trades</div><div class="value" id="sm-trades">0</div></div>
    <div class="summary-card"><div class="label">Best Trade</div><div class="value g" id="sm-best">—</div></div>
  </div>

  <div class="card">
    <div class="ct">Trading</div>
    <div class="dex-info">Spot grid trading on Solana DEXs (Raydium + Jupiter). Paste your Solana wallet key to start. No API keys needed.</div>
    <div style="background:var(--accent)11;border:1px solid var(--accent)22;border-radius:8px;padding:10px 14px;margin-bottom:4px;font-size:12px;color:var(--accent)">
      &#9889; <strong>Solana</strong> — gas &lt;$0.01 per trade, routed via Jupiter aggregator for best prices
    </div>

    <div class="section-label" style="margin-top:16px">Strategy</div>
    <select class="dd" id="strat-select" onchange="selectStrat(this.value)">
      <option value="">— Select Strategy —</option>
      <option value="grid">Grid Trading</option>
      <option value="limit_buy">Limit Buy</option>
      <option value="limit_sell">Limit Sell</option>
      <option value="ai_trading">AI Trading</option>
    </select>

    <div class="section-label">Trading Pair</div>
    <div style="display:flex;gap:8px;margin-bottom:12px">
      <select class="dd" id="pair-select" onchange="selectPair(this.value)" style="flex:1;margin-bottom:0">
        <option value="">— Select Pair —</option>
        <optgroup label="USDT Pairs" id="usdt-optgroup" style="display:none">
          <option value="BTC/USDT">BTC/USDT</option>
          <option value="ETH/USDT">ETH/USDT</option>
          <option value="BNB/USDT">BNB/USDT</option>
          <option value="SOL/USDT">SOL/USDT</option>
          <option value="MATIC/USDT">MATIC/USDT</option>
        </optgroup>
        <optgroup label="USDC Pairs" id="usdc-optgroup">
          <option value="SOL/USDC">SOL/USDC</option>
          <option value="BTC/USDC">BTC/USDC</option>
          <option value="ETH/USDC">ETH/USDC</option>
          <option value="JUP/USDC">JUP/USDC</option>
          <option value="WIF/USDC">WIF/USDC</option>
        </optgroup>
      </select>
      <button class="btn" onclick="switchPair()" title="One-click pair switch" style="padding:9px 12px">&#128260;</button>
    </div>
    <div class="card" id="limit-order-card" style="display:none;margin:10px 0;padding:12px;background:var(--bg)">
      <div class="section-label">Limit Order Configuration</div>
      <div class="config-grid">
        <div class="config-field"><label>Amount (USDC)</label><input type="number" id="limit-amount" min="0.01" step="0.01" value="10"/></div>
        <div class="config-field"><label>Side</label><select id="limit-side"><option value="buy">Buy</option><option value="sell">Sell</option></select></div>
        <div class="config-field"><label>Order Type</label><select id="limit-type"><option value="limit">Limit</option><option value="market">Market</option></select></div>
        <div class="config-field"><label>Quote Token</label><select id="limit-quote"><option value="USDC">USDC</option><option value="USDT">USDT</option></select></div>
        <div class="config-field"><label>Limit Price</label><input type="number" id="limit-price" min="0.000001" step="0.000001" placeholder="Required for limit"/></div>
      </div>
      <div id="limit-order-summary" style="font-size:11px;color:var(--dim)">Orders default to LIVE mode; PAPER is used only when explicitly enabled. Risk limits apply.</div>
      <div id="limit-order-status" style="font-size:12px;margin-top:8px;line-height:1.6"></div>
      <label style="display:block;margin-top:8px;color:var(--yellow);font-size:11px"><input type="checkbox" id="limit-confirm"/> I confirm this order, pair/mint, amount, price, and visible trading mode.</label>
    </div>

    <div class="card" id="ai-trading-card" style="display:none;margin:10px 0;padding:12px;background:var(--bg)">
      <div class="section-label">AI Trading Configuration</div>
      <div class="config-grid">
        <div class="config-field"><label>Risk Per Trade (%)</label><input type="number" id="ai-risk-pct" min="0.1" max="10" step="0.1" value="1.0"/></div>
        <div class="config-field"><label>Max Leverage</label><input type="number" id="ai-max-leverage" min="1.0" max="5.0" step="0.1" value="3.0"/></div>
        <div class="config-field"><label>Max Total Exposure ($)</label><input type="number" id="ai-max-exposure" min="10" step="10" value="1000"/></div>
        <div class="config-field"><label>Max Simultaneous Positions</label><input type="number" id="ai-max-positions" min="1" max="5" step="1" value="3"/></div>
        <div class="config-field"><label>Trading Mode</label><select id="ai-trade-mode"><option value="paper" selected>📋 PAPER</option><option value="live">🔴 LIVE</option></select></div>
      </div>
      <div style="margin-top:10px">
        <label style="font-size:11px;font-weight:700;text-transform:uppercase;color:var(--dim)">Whitelisted Tokens</label>
        <div style="display:grid;grid-template-columns:1fr 1fr;gap:6px;margin-top:4px;font-size:12px" id="ai-whitelist-checkboxes">
          <label><input type="checkbox" value="SOL/USDC" checked/> SOL/USDC</label>
          <label><input type="checkbox" value="BTC/USDC" checked/> BTC/USDC</label>
          <label><input type="checkbox" value="ETH/USDC" checked/> ETH/USDC</label>
          <label><input type="checkbox" value="JUP/USDC" checked/> JUP/USDC</label>
          <label><input type="checkbox" value="WIF/USDC"/> WIF/USDC</label>
        </div>
      </div>
    </div>
    <div style="display:flex;gap:8px;margin-bottom:12px;align-items:flex-end">
      <div style="flex:1">
        <input type="text" id="custom-mint" placeholder="Validated Solana mint (base58)..." style="width:100%;padding:8px 10px;border:1.5px solid var(--border);border-radius:6px;font-size:12px;background:var(--card);color:var(--text)"/>
      </div>
      <div style="width:70px">
        <input type="text" id="custom-symbol" placeholder="Symbol" maxlength="10" style="width:100%;padding:8px 10px;border:1.5px solid var(--border);border-radius:6px;font-size:12px;background:var(--card);color:var(--text);text-transform:uppercase"/>
      </div>
      <button class="btn" onclick="addCustomToken()" style="white-space:nowrap;font-size:11px">+ Add Token</button>
    </div>

    <div class="action-bar">
      <button class="btn-start" id="start-btn" onclick="startBot()" disabled>Select options above</button>
      <button class="btn-stop" onclick="stopBot()">&#9209; Stop</button>
      <button class="btn-pause" id="pause-btn" onclick="pauseBot()" style="display:none">⏸ Pause</button>
      <button class="btn" id="paper-btn" onclick="togglePaper()" style="background:var(--yellow)18;color:var(--yellow);border-color:var(--yellow)44;padding:13px 20px">📋 Paper: —</button>
    </div>
  </div>

  <div class="card" id="config-card" style="display:none">
    <div class="ct">Configuration</div>
    <div class="config-grid">
      <div class="config-field"><label>Risk Per Trade (%)</label><input type="number" id="cfg-risk" value="" min="0.1" max="100" step="0.1" placeholder="Loaded from state..."/></div>
      <div class="config-field"><label>Daily Loss Limit ($)</label><input type="number" id="cfg-maxloss" value="200" min="0" step="1"/></div>
      <div class="config-field"><label>Take Profit (%)</label><input type="number" id="cfg-takeprofit" value="15" min="0" step="0.5"/></div>
      <div class="config-field"><label>Arbitrage Min Spread (%)</label><input type="number" id="cfg-arbspread" value="1.5" min="0" step="0.1"/></div>
      <div class="config-field"><label>Max Position ($)</label><input type="number" id="cfg-maxpos" value="500" min="0"/></div>
      <div class="config-field"><label>Stop Loss (%)</label><input type="number" id="cfg-stoploss" value="8" min="1" max="50" step="0.5"/></div>
      <div class="config-field"><label>Trailing Sell (%)</label><input type="number" id="cfg-trailing" value="0.5" min="0.1" max="10" step="0.1"/></div>
      <div class="config-field"><label>Partial Sell (%)</label><input type="number" id="cfg-partial" value="50" min="0" max="100" step="5"/></div>
      <div class="config-field"><label>Grid Spread (%)</label><input type="number" id="cfg-spread" value="5" min="1" max="30" step="0.5"/></div>
      <div class="config-field"><label>Auto-Compound</label><select id="cfg-compound"><option value="true">On</option><option value="false">Off</option></select></div>
    </div>
    <div class="section-label">Quick Presets</div>
    <div class="preset-row">
      <button class="preset-btn" onclick="applyPreset('conservative')">&#128737; Conservative</button>
      <button class="preset-btn" onclick="applyPreset('moderate')">&#9878; Moderate</button>
      <button class="preset-btn" onclick="applyPreset('aggressive')">&#128640; Aggressive</button>
    </div>
    <button class="btn" onclick="saveConfig()" style="margin-top:8px;background:var(--accent)18;color:var(--accent);border-color:var(--accent)">&#128190; Save Config</button>
  </div>

  <div class="card" id="arb-card" style="display:none">
    <div class="ct">Arbitrage Opportunities</div>
    <div id="arb-list"><div style="color:var(--dim);font-size:13px">Scanning for opportunities...</div></div>
  </div>

  <div class="card" id="manual-trade-card">
    <div class="ct">Manual Trade</div>
    <div style="display:flex;gap:10px;align-items:flex-end;flex-wrap:wrap">
      <div style="flex:1;min-width:120px">
        <label style="font-size:10px;color:var(--dim);font-weight:700;text-transform:uppercase">Pair</label>
        <select id="mt-pair" class="dd" style="margin-bottom:0">
          <option value="SOL/USDC">SOL/USDC</option>
          <option value="BTC/USDC">BTC/USDC</option>
          <option value="ETH/USDC">ETH/USDC</option>
          <option value="JUP/USDC">JUP/USDC</option>
          <option value="BONK/USDC">BONK/USDC</option>
          <option value="WIF/USDC">WIF/USDC</option>
          <option value="SPCX/USDC">SPCX/USDC</option>
        </select>
      </div>
      <div style="flex:1;min-width:100px">
        <label style="font-size:10px;color:var(--dim);font-weight:700;text-transform:uppercase">USDC Amount</label>
        <input type="number" id="mt-amount" value="10" min="0.1" step="1" style="width:100%;padding:10px 12px;border:1.5px solid var(--border);border-radius:8px;font-size:13px;font-weight:600;background:var(--card);color:var(--text)"/>
      </div>
      <button class="btn-start" onclick="manualBuy()" style="padding:10px 20px;font-size:13px;margin-right:4px">Buy</button>
      <button class="btn-stop" onclick="manualSell()" style="padding:10px 20px;font-size:13px">Sell</button>
    </div>
    <div id="mt-result" style="margin-top:8px;font-size:12px;color:var(--dim)"></div>
  </div>

  <div class="card" id="strategies-card">
    <div class="ct" style="display:flex;justify-content:space-between;align-items:center">
      <span>Active Strategies</span>
      <button class="btn" onclick="stopAll()" style="font-size:11px;color:var(--red);border-color:var(--red)44">🛑 Stop All</button>
    </div>
    <div id="strategies-list" style="margin-top:10px;display:flex;flex-direction:column;gap:10px">
      <div style="color:var(--dim);text-align:center;padding:12px;font-size:12px">No active strategies running</div>
    </div>
  </div>

  <div class="card">
    <div class="ct" style="display:flex;justify-content:space-between;align-items:center">
      <span>Trade History</span>
      <button class="btn" onclick="exportCSV()" style="font-size:10px;padding:4px 10px">&#11015; CSV</button>
    </div>
    <div style="overflow-x:auto">
      <table>
        <thead><tr><th>Pair</th><th>Strategy</th><th>Action</th><th>Price</th><th>Amount</th><th>P&amp;L</th><th>Via</th></tr></thead>
        <tbody id="trades-body"><tr><td colspan="7" style="color:var(--dim);text-align:center;padding:20px">No trades yet</td></tr></tbody>
      </table>
    </div>
  </div>



  <div class="card">
    <div class="ct">Live Log</div>
    <div class="log-box" id="log-box"></div>
  </div>
</div>

<script>
var sel = {mode:"dex", strat:null, pair:null, exch:null, chain:"solana"};
var isDark = true;
var tradeLog = [];
var _lastTradeTime = null;
var toastId = 0;
var notifRequested = false;

function toggleTheme() {
  isDark = !isDark;
  document.body.classList.toggle("light", !isDark);
  document.getElementById("theme-btn").textContent = isDark ? "🌙 Dark" : "☀ Light";
  if (chart) setTimeout(function() { updateChartTheme(isDark); }, 100);
}


var chart = null;
var candleSeries = null;
var gridLines = [];

function aggregateCandles(data, intervalSec) {
  var candles = [], current = null;
  data = (data || []).filter(function(d) { return d && Number.isFinite(Number(d.time)) && Number.isFinite(Number(d.value)) && Number(d.value) > 0; })
    .slice().sort(function(a, b) { return Number(a.time) - Number(b.time); });
  data.forEach(function(d) {
    d.time = Number(d.time); d.value = Number(d.value);
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
      height: 350,
      layout: {
        background: {type: "solid", color: "transparent"},
        textColor: "#888",
      },
      grid: {
        vertLines: {color: "#1a1a1a"},
        horzLines: {color: "#1a1a1a"},
      },
      crosshair: {
        vertLine: {color: "#444", labelBackgroundColor: "#111"},
        horzLine: {color: "#444", labelBackgroundColor: "#111"},
      },
      timeScale: {
        borderColor: "#1a1a1a",
        timeVisible: true,
        secondsVisible: false,
        barSpacing: 3,
        minBarSpacing: 3,
        rightOffset: 20,
      },
      rightPriceScale: {
        borderColor: "#1a1a1a",
      },
    });
    candleSeries = chart.addSeries(LightweightCharts.CandlestickSeries, {
      upColor: "#00ff9d",
      downColor: "#ff6b6b",
      borderUpColor: "#00ff9d",
      borderDownColor: "#ff6b6b",
      wickUpColor: "#00ff9d",
      wickDownColor: "#ff6b6b",
      priceFormat: {type: "price", precision: 4, minMove: 0.0001},
    });
  } catch(e) { console.log("Chart init error:", e); }
}

function updateChartTheme(isDarkMode) {
  if (!chart) return;
  chart.applyOptions({
    layout: {
      textColor: isDarkMode ? "#888" : "#666",
    },
    grid: {
      vertLines: {color: isDarkMode ? "#1a1a1a" : "#e0e0e0"},
      horzLines: {color: isDarkMode ? "#1a1a1a" : "#e0e0e0"},
    },
    timeScale: {
      borderColor: isDarkMode ? "#1a1a1a" : "#d0d5dd",
    },
    rightPriceScale: {
      borderColor: isDarkMode ? "#1a1a1a" : "#d0d5dd",
    },
  });
}

function updateChart(data, gridLevels, gridBuyZone, pair) {
  if (!chart || !candleSeries) return;
  // Remove old grid lines (do this first, regardless of data)
  try {
    gridLines.forEach(function(l) { chart.removeSeries(l); });
  } catch(e) { console.log("Grid remove error:", e); }
  gridLines = [];
  if (!data || data.length < 2) {
    // Never silently keep showing the previous pair's candles: clear the
    // series so the placeholder is visible instead of stale wrong-pair data.
    try { if (candleSeries) candleSeries.setData([]); } catch(e) {}
    showChartPlaceholder(pair || "", true);
    return;
  }
  showChartPlaceholder(pair || "", false);

  // Update candles
  var candles = aggregateCandles(data, 60);
  candleSeries.setData(candles);
  var dataStart = candles[0].time;
  var dataEnd = candles[candles.length - 1].time;

  // Keep 3px candles and pin the newest candle at the right edge on each refresh.
  // A positive logical end leaves the live candle drifting left as data arrives.
  chart.timeScale().applyOptions({ barSpacing: 3, minBarSpacing: 3, rightOffset: 0 });
  var visibleBars = Math.max(1, Math.ceil((document.getElementById("chart-container").clientWidth || 600) / 3));
  chart.timeScale().setVisibleLogicalRange({from: Math.max(0, candles.length - visibleBars), to: candles.length});

  // Grid overlay
  if (!gridLevels || gridLevels.length < 2) return;

  var midIdx = Math.floor(gridLevels.length / 2);
  var midPrice = gridLevels[midIdx];
  var buyZone = gridLevels.filter(function(g, idx) { return idx <= midIdx; });
  var sellZone = gridLevels.filter(function(g, idx) { return idx > midIdx; });

  try {
    // Buy zone lines (green)
    buyZone.forEach(function(g) {
      var s = chart.addSeries(LightweightCharts.LineSeries, {
        color: "#00ff9d44",
        lineWidth: 1,
        lineStyle: 2,
        priceLineVisible: false,
        lastValueVisible: false,
      });
      s.setData([{time: dataStart, value: g}, {time: dataEnd, value: g}]);
      gridLines.push(s);
    });

    // Sell zone lines (red)
    sellZone.forEach(function(g) {
      var s = chart.addSeries(LightweightCharts.LineSeries, {
        color: "#ff6b6b44",
        lineWidth: 1,
        lineStyle: 2,
        priceLineVisible: false,
        lastValueVisible: false,
      });
      s.setData([{time: dataStart, value: g}, {time: dataEnd, value: g}]);
      gridLines.push(s);
    });

    // Midpoint line (yellow, thicker)
    var midLine = chart.addSeries(LightweightCharts.LineSeries, {
      color: "#ffd43b88",
      lineWidth: 2,
      lineStyle: 2,
      priceLineVisible: false,
      lastValueVisible: false,
    });
    midLine.setData([{time: dataStart, value: midPrice}, {time: dataEnd, value: midPrice}]);
    gridLines.push(midLine);
  } catch(e) { console.log("Grid overlay error:", e); }
}


function showChartPlaceholder(pair, show) {
  var el = document.getElementById("chart-placeholder");
  if (!el) return;
  if (show) {
    el.textContent = "No price data for " + pair + " yet — fetching…";
    el.style.display = "flex";
  } else {
    el.style.display = "none";
  }
}
function fetchPairChartHistory(pair, levels, buyZone) {
  // Fetches candle history for a pair the server has not seeded yet (arbitrary
  // dropdown pair) and renders it immediately. The server stores the history
  // into state.price_history_pairs, so the 3s /state refresh then serves it
  // directly. Cooldown prevents hammering the endpoint (which seeds server-
  // side) when a pair keeps returning no data.
  if (!window._pairChartFetches) window._pairChartFetches = {};
  var nowTs = Date.now();
  var last = window._pairChartFetches[pair] || 0;
  if (nowTs - last < 15000) return;
  window._pairChartFetches[pair] = nowTs;
  apiFetch("/chart_history?pair=" + encodeURIComponent(pair)).then(function(r) { return r.json(); }).then(function(d) {
    if (d && d.history && d.history.length >= 2) {
      updateChart(d.history, levels, buyZone, pair);
    } else {
      updateChart([], levels, buyZone, pair);
    }
  }).catch(function() {
    updateChart([], levels, buyZone, pair);
  });
}
function showToast(msg, type) {
  var c = document.getElementById("toast-container");
  var t = document.createElement("div");
  t.className = "toast " + (type || "info");
  t.textContent = msg;
  t.id = "t" + (++toastId);
  c.appendChild(t);
  setTimeout(function(){ var el = document.getElementById(t.id); if(el) el.style.animation = "fadeOut .3s ease-out"; setTimeout(function(){ if(el) el.remove(); }, 300); }, 4000);
}

function playBeep() {
  try {
    var ctx = new (window.AudioContext || window.webkitAudioContext)();
    var osc = ctx.createOscillator();
    var gain = ctx.createGain();
    osc.connect(gain); gain.connect(ctx.destination);
    osc.frequency.value = 880;
    gain.gain.value = 0.08;
    osc.start(); gain.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + 0.12);
    osc.stop(ctx.currentTime + 0.12);
  } catch(e) {}
}

function sendNotif(title, body) {
  if ("Notification" in window && Notification.permission === "granted") {
    new Notification(title, {body: body});
  }
}

function requestNotif() {
  if (!notifRequested && "Notification" in window) {
    Notification.requestPermission();
    notifRequested = true;
  }
}

function exportCSV() {
  if (tradeLog.length === 0) { showToast("No trades to export", "error"); return; }
  var headers = "Time,Pair,Action,Price,Amount,P&L,Via";
  var rows = tradeLog.map(function(t) {
    return [t.time, t.pair, t.action, t.price, t.amount, t.pnl, t.via].join(",");
  });
  var csv = [headers].concat(rows).join("\\n");
  var blob = new Blob([csv], {type: "text/csv"});
  var url = URL.createObjectURL(blob);
  var a = document.createElement("a");
  a.href = url; a.download = "trades_" + new Date().toISOString().slice(0,10) + ".csv";
  a.click(); URL.revokeObjectURL(url);
  showToast("Exported " + tradeLog.length + " trades", "info");
}

function addCustomToken() {
  var mint = document.getElementById("custom-mint").value.trim();
  var symbol = document.getElementById("custom-symbol").value.trim().toUpperCase();
  if (!mint || !symbol) { showToast("Enter mint address and symbol", "error"); return; }
  if (mint.length < 32 || mint.length > 44) { showToast("Invalid mint address", "error"); return; }
  // Add to dropdown
  var pair = symbol + "/USDC";
  var optgroup = document.getElementById("usdc-optgroup");
  var existing = document.querySelector("#usdc-optgroup option[value='" + pair + "']");
  if (existing) { showToast(pair + " already exists", "error"); return; }
  var opt = document.createElement("option");
  opt.value = pair; opt.textContent = pair;
  optgroup.appendChild(opt);
  // Notify server to add mint
  apiFetch("/add_token", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({symbol: symbol, mint: mint, pair: pair})
  }).then(function(r) { return r.json(); }).then(function(d) {
    if (d.ok) {
      document.getElementById("pair-select").value = pair;
      selectPair(pair);
      document.getElementById("custom-mint").value = "";
      document.getElementById("custom-symbol").value = "";
      showToast("Added " + pair, "info");
    } else {
      showToast(d.error || "Failed to add token", "error");
    }
  }).catch(function(e) { showToast("Error adding token", "error"); });
}

function switchPair() {
  var common = ["SOL/USDC","BTC/USDC","ETH/USDC","JUP/USDC","BONK/USDC","WIF/USDC"];
  var current = sel.pair;
  if (!current) { showToast("Select a pair first", "error"); return; }
  var idx = common.indexOf(current);
  var next = common[(idx + 1) % common.length];
  var ps = document.getElementById("pair-select");
  ps.value = next;
  selectPair(next);
  showToast("Switched to " + next, "info");
}

function applyPreset(name) {
  var presets = {
    conservative: {risk: 1, maxpos: 250, stoploss: 6, trailing: 0.3, partial: 25, spread: 3},
    moderate: {risk: 2, maxpos: 500, stoploss: 8, trailing: 0.5, partial: 50, spread: 5},
    aggressive: {risk: 5, maxpos: 1000, stoploss: 12, trailing: 1.0, partial: 75, spread: 8}
  };
  var p = presets[name];
  document.getElementById("cfg-risk").value = p.risk;
  document.getElementById("cfg-maxpos").value = p.maxpos;
  document.getElementById("cfg-stoploss").value = p.stoploss;
  document.getElementById("cfg-trailing").value = p.trailing;
  document.getElementById("cfg-partial").value = p.partial;
  document.getElementById("cfg-spread").value = p.spread;
  document.querySelectorAll(".preset-btn").forEach(function(b) { b.classList.remove("active"); });
  event.target.classList.add("active");
  showToast("Preset '" + name + "' applied", "info");
}

var configDirty = false;
function markConfigDirty() { configDirty = true; }
function configNumber(id, fallback) {
  var value = parseFloat(document.getElementById(id).value);
  return Number.isFinite(value) ? value : fallback;
}
function saveConfig() {
  var cfg = {
    risk_pct: configNumber("cfg-risk", 2),
    max_pos: configNumber("cfg-maxpos", 500),
    max_loss: configNumber("cfg-maxloss", 200),
    take_profit: configNumber("cfg-takeprofit", 15),
    min_arb_spread: configNumber("cfg-arbspread", 1.5),
    grid_stop_loss_pct: configNumber("cfg-stoploss", 8),
    trailing_pct: configNumber("cfg-trailing", 0.5),
    partial_sell_pct: configNumber("cfg-partial", 50),
    base_spread: configNumber("cfg-spread", 5) / 100,
    auto_compound: document.getElementById("cfg-compound").value === "true"
  };
  apiFetch("/config", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify(cfg)
  }).then(function(r) { return r.json(); }).then(function(d) {
    showToast("Config saved", "info");
  }).catch(function() { showToast("Failed to save config", "error"); });
}

function selectStrat(s) {
  if (!s) return;
  sel.strat = s;
  document.getElementById("arb-card").style.display = s=="arb"?"block":"none";
  document.getElementById("limit-order-card").style.display = (s=="limit_buy" || s=="limit_sell") ? "block" : "none";
  document.getElementById("ai-trading-card").style.display = s=="ai_trading"?"block":"none";
  if (s=="limit_buy" || s=="limit_sell") document.getElementById("limit-side").value = s=="limit_buy" ? "buy" : "sell";
  document.getElementById("config-card").style.display = "block";
  document.getElementById("grid-details-card").style.display = s=="ai_trading"?"none":"block";
  document.getElementById("ai-trading-status-card").style.display = s=="ai_trading"?"block":"none";
  updateBtn();
}

function selectPair(p) {
  if (!p) return;
  sel.pair = p;
  updateBtn();
  // Immediately refresh grid details for selected pair
  refresh();
}

function updateBtn() {
  var btn = document.getElementById("start-btn");
  // Fall back to reading dropdowns directly if sel not set
  var st = sel.strat || document.getElementById("strat-select").value;
  var pr = sel.pair || document.getElementById("pair-select").value;
  var ready = st && pr;
  if (ready) {
    sel.strat = st; sel.pair = pr;
    btn.disabled = false;
    btn.textContent = "Start " + st.toUpperCase() + " on " + pr;
  } else {
    btn.disabled = true;
    btn.textContent = "Select strategy and pair above";
  }
}

// Also enable button on ANY dropdown change
document.getElementById("strat-select").addEventListener("change", updateBtn);
document.getElementById("pair-select").addEventListener("change", updateBtn);

function startBot() {
  var params = "strategy=" + sel.strat + "&pair=" + encodeURIComponent(sel.pair) + "&mode=dex&chain=solana";
  if (sel.strat == "ai_trading") {
    var risk = document.getElementById("ai-risk-pct").value;
    var leverage = document.getElementById("ai-max-leverage").value;
    var exposure = document.getElementById("ai-max-exposure").value;
    var positions = document.getElementById("ai-max-positions").value;
    var ai_mode = document.getElementById("ai-trade-mode").value;
    var checked_symbols = [];
    var checkboxes = document.querySelectorAll("#ai-whitelist-checkboxes input[type='checkbox']:checked");
    checkboxes.forEach(function(cb) {
      checked_symbols.push(cb.value);
    });
    if (checked_symbols.length === 0) {
      checked_symbols.push(sel.pair);
    }
    params += "&risk_pct=" + risk + "&max_leverage=" + leverage + "&max_total_exposure=" + exposure + "&max_simultaneous_positions=" + positions + "&ai_whitelist=" + encodeURIComponent(checked_symbols.join(",")) + "&ai_mode=" + ai_mode;
  }
  if ((sel.strat=="limit_buy" || sel.strat=="limit_sell") && document.getElementById("custom-mint").value.trim()) {
    var mint=document.getElementById("custom-mint").value.trim(), sym=document.getElementById("custom-symbol").value.trim().toUpperCase(), quote=document.getElementById("limit-quote").value;
    if (!sym || !/^[1-9A-HJ-NP-Za-km-z]{32,44}$/.test(mint)) { showToast("Enter a symbol and valid base58 Solana mint", "error"); return; }
    sel.pair = sym+"/"+quote; params = "strategy="+sel.strat+"&pair="+encodeURIComponent(sel.pair)+"&mode=dex&chain=solana&custom_mint="+encodeURIComponent(mint)+"&custom_symbol="+encodeURIComponent(sym);
  }
  if (sel.strat=="limit_buy" || sel.strat=="limit_sell") {
    var amount=parseFloat(document.getElementById("limit-amount").value), price=parseFloat(document.getElementById("limit-price").value), typ=document.getElementById("limit-type").value, side=document.getElementById("limit-side").value;
    if (!(amount>0) || (typ=="limit" && !(price>0))) { showToast("Enter a positive amount and limit price", "error"); return; }
    if (!document.getElementById("limit-confirm").checked) { showToast("Confirm order details and trading mode", "error"); return; }
    params += "&amount_usdc="+encodeURIComponent(amount)+"&limit_price="+encodeURIComponent(price||0)+"&order_type="+typ+"&side="+side+"&trade_mode=live&confirm=true";
  }
  apiFetch("/start?" + params).then(function(r) {
    return r.json().then(function(d) { return {ok: r.ok, body: d}; });
  }).then(function(res) {
    var d = res.body || {};
    // Never claim success when the server returned a non-OK or an error body
    // (e.g. 400 {"error":"amount exceeds max position"}).
    if (!res.ok || d.error) {
      showToast(d.error || "Failed to start", "error");
      return;
    }
    if (!d.ok) {
      // 200 without ok:true means the strategy did not actually start; the
      // 3s refresh surfaces the log line in the detail-card warning.
      showToast("Bot did not start — check status", "error");
      return;
    }
    showToast("Bot started: " + sel.strat.toUpperCase(), "info");
    document.getElementById("pause-btn").style.display = "inline-block";
    document.getElementById("pause-btn").textContent = "⏸ Pause";
  }).catch(function() {
    showToast("Failed to start: server unreachable", "error");
  });
}

function stopBot() {
  apiFetch("/stop").then(function(r) { return r.json(); }).then(function(d) {
    showToast("Bot stopped", "info");
    document.getElementById("pause-btn").style.display = "none";
  });
}

function pauseBot() {
  apiFetch("/pause").then(function(r) { return r.json(); }).then(function(d) {
    var paused = d.paused || d.status === "paused";
    document.getElementById("pause-btn").textContent = paused ? "▶ Resume" : "⏸ Pause";
    showToast(paused ? "Bot paused" : "Bot resumed", "info");
  }).catch(function() {
    var btn = document.getElementById("pause-btn");
    var paused = btn.textContent.indexOf("Pause") !== -1;
    btn.textContent = paused ? "▶ Resume" : "⏸ Pause";
    showToast(paused ? "Bot paused" : "Bot resumed", "info");
  });
}

function pnlHtml(v) {
  if (v == null || v === undefined) return "—";
  var cls = v >= 0 ? "badge badge-p" : "badge badge-l";
  return "<span class='" + cls + "'>" + (v >= 0 ? "+" : "") + "$" + Math.abs(v).toFixed(2) + "</span>";
}

function killSwitch() {
  if (!confirm("🛑 KILL SWITCH: Close ALL positions on ALL pairs? This cannot be undone.")) return;
  apiFetch("/kill",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({confirm:true})}).then(function(r){return r.json()}).then(function(d){
    showToast("KILL: "+d.closed+" positions closed, $"+d.total_value.toFixed(2),"error");
  }).catch(function(){showToast("Kill failed","error")});
}

function openScanner() {
  window.open("/scanner", "TokenScanner", "width=480,height=760,resizable=yes,scrollbars=yes");
}

function runBacktest() {
  var pair = document.getElementById("pair-select").value;
  var strategy = document.getElementById("strat-select").value;
  showToast("Running backtest on " + pair + "...", "info");
  apiFetch("/backtest", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({pair: pair, strategy: strategy})
  })
    .then(function(r) { return r.json(); })
    .then(function(d) {
      if (d.error) { showToast("Backtest error: " + d.error, "error"); return; }
      var msg = "Backtest: " + d.total_trades + " trades | Win: " + d.win_rate + "% | PnL: $" + (d.total_pnl||0).toFixed(2) + " | Drawdown: " + (d.max_drawdown||0).toFixed(1) + "%";
      showToast(msg, "info");
      if (d.trades && d.trades.length) {
        var lines = d.trades.slice(0, 5).map(function(t) { return t.action + " @ $" + t.price.toFixed(2) + " PnL: $" + (t.pnl||0).toFixed(2); });
        addLog("Backtest: " + lines.join(" | "));
      } else {
        showToast("Backtest done: 0 simulated trades in range", "info");
      }
    })
    .catch(function(e) { showToast("Backtest failed: " + e, "error"); });
}

function manualBuy() {
  var pair = document.getElementById("mt-pair").value;
  var amt = parseFloat(document.getElementById("mt-amount").value) || 10;
  if (amt <= 0) { showToast("Enter amount > 0", "error"); return; }
  document.getElementById("mt-result").innerHTML = '<span style="color:var(--yellow)">Buying ' + amt + ' USDC of ' + pair + '...</span>';
  apiFetch("/manual_trade", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({pair: pair, side: "buy", amount_usdc: amt})
  }).then(function(r) { return r.json(); }).then(function(d) {
    if (d.ok) {
      document.getElementById("mt-result").innerHTML = '<span style="color:var(--accent)">✓ Bought ' + d.amount + ' ' + pair.split('/')[0] + ' @ $' + d.price.toFixed(2) + '</span>';
      showToast("Buy executed: " + d.amount + " " + pair.split('/')[0], "trade");
    } else {
      document.getElementById("mt-result").innerHTML = '<span style="color:var(--red)">✗ ' + (d.error || "Buy failed") + '</span>';
      showToast("Buy failed: " + (d.error || "unknown"), "error");
    }
  }).catch(function(e) {
    document.getElementById("mt-result").innerHTML = '<span style="color:var(--red)">✗ Error: ' + e + '</span>';
    showToast("Buy error", "error");
  });
}

function manualSell() {
  var pair = document.getElementById("mt-pair").value;
  var amt = parseFloat(document.getElementById("mt-amount").value) || 10;
  if (amt <= 0) { showToast("Enter amount > 0", "error"); return; }
  document.getElementById("mt-result").innerHTML = '<span style="color:var(--yellow)">Selling ' + amt + ' USDC worth of ' + pair + '...</span>';
  apiFetch("/manual_trade", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({pair: pair, side: "sell", amount_usdc: amt})
  }).then(function(r) { return r.json(); }).then(function(d) {
    if (d.ok) {
      document.getElementById("mt-result").innerHTML = '<span style="color:var(--accent)">✓ Sold ' + d.amount + ' ' + pair.split('/')[0] + ' @ $' + d.price.toFixed(2) + ' | Received: $' + d.received.toFixed(2) + '</span>';
      showToast("Sell executed: $" + d.received.toFixed(2), "trade");
    } else {
      document.getElementById("mt-result").innerHTML = '<span style="color:var(--red)">✗ ' + (d.error || "Sell failed") + '</span>';
      showToast("Sell failed: " + (d.error || "unknown"), "error");
    }
  }).catch(function(e) {
    document.getElementById("mt-result").innerHTML = '<span style="color:var(--red)">✗ Error: ' + e + '</span>';
    showToast("Sell error", "error");
  });
}

// Replay pair history after asynchronous chart creation. History is supplied
// by the owning card so refreshes cannot overwrite another pair's candles.
function setMultiPairChartData(card, ph, chartEl) {
  if (!card || !card._series || !ph || !ph.length) return;
  var chartData = ph.map(function(p, j) {
    var o = j > 0 ? ph[j - 1].value : p.value;
    return {time:p.time, open:o, high:Math.max(o,p.value), low:Math.min(o,p.value), close:p.value};
  });
  if (chartData.length === 1) chartData.unshift({time:ph[0].time-1, open:ph[0].value, high:ph[0].value, low:ph[0].value, close:ph[0].value});
  card._series.setData(chartData);
  if (card._chart) {
    card._chart.timeScale().applyOptions({barSpacing:3, minBarSpacing:3, rightOffset:0});
    var width = chartEl && chartEl.clientWidth || 380;
    var visibleBars = Math.max(1, Math.ceil(width / 3));
    card._chart.timeScale().setVisibleLogicalRange({from:Math.max(0, chartData.length-visibleBars), to:chartData.length});
  }
}
function togglePaper() {
  apiFetch("/toggle_paper").then(function(r) { return r.json(); }).then(function(d) {
    var btn = document.getElementById("paper-btn");
    var on = d.paper_trading;
    btn.textContent = "📋 Paper: " + (on ? "ON" : "OFF");
    btn.style.color = on ? "var(--yellow)" : "var(--red)";
    btn.style.borderColor = on ? "var(--yellow)44" : "var(--red)44";
    btn.style.background = on ? "var(--yellow)18" : "var(--red)18";
    showToast("Paper trading: " + (on ? "ON" : "OFF"), "info");
  });
}

function updateLimitOrderStatus(d) {
  var card = document.getElementById("limit-order-card");
  var statusEl = document.getElementById("limit-order-status");
  if (!card || !statusEl) return;
  var isLimit = sel.strat === "limit_buy" || sel.strat === "limit_sell";
  if (!isLimit) { statusEl.innerHTML = ""; return; }
  var viewPair = sel.pair || d.pair || "";
  var marketPrice = (d.price && d.price > 0) ? d.price : 0;
  if (!marketPrice && d.price_history_pairs && d.price_history_pairs[viewPair] && d.price_history_pairs[viewPair].length) {
    var ph = d.price_history_pairs[viewPair];
    marketPrice = ph[ph.length - 1].value;
  }
  var lt = d.last_trade || {};
  var side = d.limit_side || "buy";
  var amount = d.limit_amount_usdc || 0;
  var lprice = d.limit_price || 0;
  var otype = d.limit_order_type || "limit";
  var pair = d.pair || viewPair;
  var mode = d.paper_trading ? "PAPER" : "LIVE";
  var html = "";
  // ARMED — the limit order is live and watching the price
  if (d.running && (d.strategy === "limit_buy" || d.strategy === "limit_sell")) {
    var cmp = side === "buy" ? "\u2264" : "\u2265";
    html += '<div style="color:#ffd43b">\u26A1 ARMED \u2014 ' + side.toUpperCase() + ' $' + amount + ' of ' + pair + ' at limit $' + lprice + ' (' + mode + ') \u00B7 ' + otype + ' \u00B7 waiting for price ' + cmp + ' $' + lprice + '</div>';
  }
  // Terminal result from the last attempted limit order
  if (lt.status === "confirmed") {
    html += '<div style="color:#00ff9d">\u2713 FILLED @ $' + (lt.price != null ? lt.price : "\u2014") + '</div>';
  } else if (lt.status === "rejected") {
    html += '<div style="color:#ff6b6b">\u2717 REJECTED: ' + (lt.error || "order failed") + '</div>';
  }
  if (marketPrice > 0) {
    html += '<div style="color:var(--dim);margin-top:2px">Current ' + pair + ': $' + marketPrice + '</div>';
  }
  // Already-running warning from the server log tail (see checkAlreadyRunning)
  if (d.log && d.log.length) {
    for (var i = 0; i < Math.min(d.log.length, 10); i++) {
      if (d.log[i].indexOf("Already running") !== -1) {
        html += '<div style="color:#ff6b6b">\u26A0 Already running \u2014 stop first</div>';
        break;
      }
    }
  }
  statusEl.innerHTML = html;
}
function checkAlreadyRunning(d) {
  if (!d || !d.log || !d.log.length) return;
  var found = false;
  for (var i = 0; i < Math.min(d.log.length, 10); i++) {
    if (d.log[i].indexOf("Already running") !== -1) { found = true; break; }
  }
  // Warn once per occurrence so the 3s refresh does not spam the toast.
  if (found && !window._alreadyRunningWarned) {
    window._alreadyRunningWarned = true;
    showToast("Already running \u2014 stop first", "error");
  } else if (!found) {
    window._alreadyRunningWarned = false;
  }
}
function stopStrategy(sid) {
  apiFetch("/stop?sid=" + encodeURIComponent(sid)).then(function(r) { return r.json(); }).then(function(d) {
    if (d.ok || d.success) {
      showToast("Strategy stopped", "info");
      refresh();
    } else {
      showToast(d.error || "Failed to stop", "error");
    }
  });
}
function stopAll() {
  apiFetch("/stop_all").then(function(r) { return r.json(); }).then(function(d) {
    if (d.ok || d.success) {
      showToast("All strategies stopped", "info");
      refresh();
    } else {
      showToast(d.error || "Failed to stop all", "error");
    }
  });
}
function updateStrategiesList(d) {
  var container = document.getElementById("strategies-list");
  if (!container) return;
  var strats = d.strategies || {};
  var keys = Object.keys(strats);
  var runningKeys = keys.filter(function(k) { return strats[k].running; });
  if (runningKeys.length === 0) {
    container.innerHTML = '<div style="color:var(--dim);text-align:center;padding:12px;font-size:12px">No active strategies running</div>';
    return;
  }
  var html = '';
  runningKeys.forEach(function(sid) {
    var s = strats[sid];
    var isPaper = s.config && s.config.paper_trading;
    var modeLabel = isPaper ? '<span style="color:var(--yellow);font-size:10px;font-weight:700;background:var(--yellow)11;padding:2px 6px;border-radius:4px;border:1px solid var(--yellow)22">📋 PAPER</span>' : '<span style="color:var(--red);font-size:10px;font-weight:700;background:var(--red)11;padding:2px 6px;border-radius:4px;border:1px solid var(--red)22">🔴 LIVE</span>';
    var statusColor = "var(--dim)";
    if (s.status === "RUNNING") statusColor = "var(--accent)";
    else if (s.status === "ARMED") statusColor = "var(--blue)";
    else if (s.status === "FILLED") statusColor = "var(--accent)";
    else if (s.status === "REJECTED") statusColor = "var(--red)";
    else if (s.status === "STOPPED") statusColor = "var(--dim)";
    
    var statusBadge = '<span style="color:' + statusColor + ';font-size:11px;font-weight:700;background:' + statusColor + '11;padding:2px 6px;border-radius:4px;border:1px solid ' + statusColor + '22">' + (s.status || "RUNNING") + '</span>';
    
    var paramsText = '';
    if (s.type === "grid") {
      paramsText = "Risk: " + (s.config.risk_pct || 2) + "%, Max Pos: $" + (s.config.max_pos || 500);
    } else if (s.type === "limit_buy" || s.type === "limit_sell") {
      paramsText = "Amount: $" + (s.config.limit_amount_usdc || 0) + ", Price: $" + (s.config.limit_price || 0) + ", Type: " + (s.config.limit_order_type || "limit");
    } else if (s.type === "ai_trading") {
      paramsText = "Risk: " + (s.config.risk_pct || 1) + "%, Max Leverage: " + (s.config.max_leverage || 3) + ", Max Exposure: $" + (s.config.max_total_exposure || 5000);
    } else {
      paramsText = "Risk: " + (s.config.risk_pct || 2) + "%";
    }

    html += '<div style="background:var(--bg);border:1px solid var(--border);border-radius:8px;padding:12px;display:flex;justify-content:space-between;align-items:center;font-size:12px">';
    html += '  <div>';
    html += '    <div style="font-weight:700;font-size:13px;display:flex;align-items:center;gap:8px">' + s.type.toUpperCase() + ' <span style="font-weight:400;color:var(--text2)">' + s.pair + '</span> ' + modeLabel + ' ' + statusBadge + '</div>';
    var logLines = (s.log_tail || []).slice(0, 4);
    var logHtml = '';
    if (logLines.length) {
      logHtml = '<div style="margin-top:6px;font-family:monospace;font-size:10px;color:var(--text2);line-height:1.4;max-height:46px;overflow:hidden">' +
        logLines.map(function(l){ return '<div>' + String(l).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;') + '</div>'; }).join('') +
        '</div>';
    }
    html += '    <div style="color:var(--dim);font-size:11px;margin-top:4px">' + paramsText + '</div>' + logHtml;
    html += '  </div>';
    html += '  <div>';
    html += `    <button class="btn" onclick="stopStrategy('${sid}')" style="color:var(--red);border-color:var(--red)44;font-size:11px;padding:6px 12px">&#9209; Stop</button>`;
    html += '  </div>';
    html += '</div>';
  });
  container.innerHTML = html;
}

function refresh() {
  apiFetch("/state").then(function(r) { return r.json(); }).then(function(d) {
    try {
    var on = d.running;
    if (!on) window._gridPairs = [];
    // Keep the last known pair set while the bot is running. A transient state
    // response must not tear down multi-grid cards (or their chart state).
    if (on && d.strategy === "grid" && d.active_pairs && d.active_pairs.length) {
      window._gridPairs = d.active_pairs.slice();
    }
    var activePairs = (on && d.strategy === "grid" && window._gridPairs) ? window._gridPairs : (d.active_pairs || []);
        // Update AI Trading live metrics if active
    if (d.strategy === "ai_trading") {
      document.getElementById("ai-engine-status").textContent = d.ai_status || "analyzing";
      document.getElementById("ai-regime-status").textContent = d.ai_regime || "TRENDING_BULL";
      document.getElementById("ai-score-status").textContent = d.ai_score ? d.ai_score.toFixed(1) : "—";
      document.getElementById("ai-confidence-status").textContent = d.ai_confidence || "—";
      document.getElementById("ai-selected-strategy").textContent = d.ai_selected_strategy || "None";
      document.getElementById("ai-exposure-status").textContent = d.ai_exposure ? "$" + d.ai_exposure.toFixed(2) : "$0.00";
      document.getElementById("ai-decision-explain").textContent = d.ai_explain || "Analyzing markets...";
    }
    document.getElementById("dot").className = "dot" + (on ? " on" : "");
    document.getElementById("status-text").textContent = on ? "Running — " + (d.strategy || "").toUpperCase() + " on " + (activePairs.length ? activePairs.join(", ") : d.pair) + " (" + (d.mode || "").toUpperCase() + ")" : "Stopped";
    document.getElementById("s-price").textContent = d.price > 0 ? "$" + (d.price||0).toFixed(4) : "—";
    // Live limit-order status card + already-running warning (dashboard only)
    updateLimitOrderStatus(d);
    checkAlreadyRunning(d);
    updateStrategiesList(d);
    // Multi-pair mode: show per-pair charts when 2+ active pairs
    var multiPair = activePairs.length >= 2;
    var singleRow = document.getElementById("single-chart-row");
    var chartsWrap = document.getElementById("charts-container");
    if (multiPair) {
      if (singleRow) singleRow.style.display = "none";
      if (chartsWrap) chartsWrap.style.display = "grid";
      window._wasMulti = true;
    } else {
      window._wasMulti = false;
      if (singleRow) singleRow.style.display = "flex";
      if (chartsWrap) chartsWrap.style.display = "none";
      if (!on && chartsWrap) {
        chartsWrap.querySelectorAll('[id^="mpcard-"]').forEach(function(card) { card.remove(); });
      }
    }
    // Do not reconcile/remove cards against a transient payload. A running
    // grid owns its cards until the explicit stop path below clears them.
    // Render multi-pair cards whenever we have 2+ pairs
    if (activePairs.length >= 2) {
      var multiPairs = activePairs;
      multiPairs.forEach(function(pair) {
        var cardId = "mpcard-" + pair.replace(/[^a-zA-Z0-9]/g, "_");
        var ph = d.price_history_pairs && d.price_history_pairs[pair] ? d.price_history_pairs[pair] : [];
        var card = document.getElementById(cardId);
        if (!card) {
          card = document.createElement("div");
          card.id = cardId;
          // A card owns its complete chart/history/info panel. Grid sizing keeps
          // every card wide enough while anchoring the collection to the right.
          card.style.cssText = "width:100%;min-width:0;background:var(--card);border:1px solid var(--border);border-radius:8px;padding:12px;box-sizing:border-box;";
          card.innerHTML = '<div style="font-weight:600;color:#14b8a6;margin-bottom:8px">' + pair + '</div>' +
            '<div id="' + cardId + '-price" style="font-size:18px;font-weight:700;margin-bottom:8px">--</div>' +
            '<div id="' + cardId + '-chart" style="height:200px"></div>' +
            '<div id="' + cardId + '-info" style="font-size:11px;color:var(--dim);margin-top:8px"></div>';
          chartsWrap.appendChild(card);
          card._history = ph.slice();
          // Create chart after DOM layout (ensures proper width). Use an IIFE
          // to isolate card, pair, and history from subsequent refresh iterations.
          (function(ownerCard, ownerPair, ownerHistory) {
          setTimeout(function() {
            try {
              var chartEl = document.getElementById(ownerCard.id + "-chart");
              if (!chartEl) return;
              var w = chartEl.clientWidth || 380;
              var ch = LightweightCharts.createChart(chartEl, {
                width: w, height: 200,
              layout: { background: {type: "solid", color: "transparent"}, textColor: "#888" },
              grid: { vertLines: {color: "#1a1a1a"}, horzLines: {color: "#1a1a1a"} },
              timeScale: { borderColor: "#1a1a1a", timeVisible: true, barSpacing: 3 },
              rightPriceScale: { borderColor: "#1a1a1a" }
            });
            ownerCard._chart = ch;
            ownerCard._series = ch.addSeries(LightweightCharts.CandlestickSeries, {
              upColor: "#00ff9d", downColor: "#ff6b6b", borderUpColor: "#00ff9d", borderDownColor: "#ff6b6b",
              wickUpColor: "#00ff9d", wickDownColor: "#ff6b6b", priceFormat: {type: "price", precision: 6, minMove: 0.000001}
            });
            // The initial refresh may have run before this deferred callback.
            // Replay the captured history now that the series is initialized.
            setMultiPairChartData(ownerCard, ownerCard._history || ownerHistory, chartEl);
            } catch(e) { console.log("Chart error for " + ownerPair, e); }
          }, 50);
          })(card, pair, ph.slice());
        }
        card._history = ph.slice();
        // Update chart data using the chart instance owned by this card. The
        // deferred chart creation callback's `ch` is out of scope here; using it
        // raised ReferenceError and stopped the pair loop, leaving ETH blank.
        var chartEl = document.getElementById(cardId + "-chart");
        var chart = card._chart;
        if (card._series && chart && chartEl) {
          var chartData = [];
          if (ph.length >= 1) {
            for (var j = 0; j < ph.length; j++) {
              var p = ph[j];
              var o = j > 0 ? ph[j-1].value : p.value;
              chartData.push({time: p.time, open: o, high: Math.max(o, p.value), low: Math.min(o, p.value), close: p.value});
            }
            // If only 1 point, duplicate it so candles render
            if (ph.length === 1) {
              chartData.push({time: ph[0].time - 1, open: ph[0].value, high: ph[0].value, low: ph[0].value, close: ph[0].value});
            }
          }
          if (chartData.length) {
            card._series.setData(chartData);
            // Keep the latest pair candle anchored while retaining the 3px density.
            chart.timeScale().applyOptions({ barSpacing: 3, minBarSpacing: 3, rightOffset: 0 });
            var pairVisibleBars = Math.max(1, Math.ceil((chartEl.clientWidth || 380) / 3));
            chart.timeScale().setVisibleLogicalRange({from: Math.max(0, chartData.length - pairVisibleBars), to: chartData.length});
          }
        }
        // Update price
        var lastPrice = ph.length ? ph[ph.length-1].value : (d.price || 0);
        var priceEl = document.getElementById(cardId + "-price");
        if (priceEl && lastPrice !== undefined && lastPrice !== null) priceEl.textContent = "$" + (typeof lastPrice === "number" ? lastPrice.toFixed(6) : lastPrice);
        // Update info with full grid details
        var gp = d.grid_pairs && d.grid_pairs[pair];
        var infoEl = document.getElementById(cardId + "-info");
        if (infoEl && gp && gp.grids) {
          var gl = gp.grids;
          var midIdx = gp.mid_idx != null ? gp.mid_idx : Math.floor(gl.length / 2);
          var fc = gp.filled ? Object.keys(gp.filled).length : 0;
          var curP = lastPrice || 0;
          var trailActive = gp.trailing_sell_active || false;
          var html = '<div style="font-size:11px;margin-top:8px">';
          var buySpacing = gl.length >= 2 ? (gl[1] - gl[0]) : 0;
          html += '<span style="color:var(--dim)">Levels: ' + gl.length + ' | Filled: ' + fc + ' | Buy ≤$' + gl[midIdx].toFixed(2) + ' | Sell >$' + gl[midIdx].toFixed(2) + ' (2× sell gap: $' + (buySpacing * 2).toFixed(2) + ')</span>';
          if (trailActive) html += ' <span style="color:#ff6b6b">⚠ Trailing</span>';
          html += '<div style="margin-top:6px;display:grid;grid-template-columns:repeat(auto-fill,minmax(100px,1fr));gap:2px;font-size:10px">';
          for (var i = 0; i < gl.length; i++) {
            var isFilled = gp.filled && gp.filled[i] != null;
            var isMid = i === midIdx;
            var isMidMinusOne = i === (midIdx - 1);
            var isBuyZone = i <= midIdx;
            var color = isMid ? "#ffd43b" : isMidMinusOne ? "#ffbc42" : isBuyZone ? "#00ff9d" : "#ff6b6b";
            var marker = isFilled ? "●" : isMid ? "◆" : isMidMinusOne ? "◇" : isBuyZone ? "▲" : "▼";
            html += '<span style="color:' + color + '">' + marker + '$' + gl[i].toFixed(2) + '</span>';
          }
          html += "</div></div>";
          infoEl.innerHTML = html;
        }
      });
    }
    if (!multiPair) {
      // Show grid for currently selected pair. Arbitrary dropdown pairs have
      // no history in /state until seeded, so fetch it on demand and render
      // the selected pair's own candles — never stale candles from another.
      var viewPair = sel.pair || d.pair || "SOL/USDC";
      var pairHistory = d.price_history_pairs && d.price_history_pairs[viewPair];
      var chartHistory = (pairHistory && pairHistory.length >= 2) ? pairHistory : ((viewPair === d.pair) ? d.price_history : []);
      var gp = d.grid_pairs && d.grid_pairs[viewPair];
      var levels = gp ? gp.grids : d.grid_levels;
      var buyZone = gp ? gp.grids[gp.mid_idx + 1] : d.grid_buy_zone;
      if (chartHistory && chartHistory.length >= 2) {
        updateChart(chartHistory, levels, buyZone, viewPair);
      } else {
        // No history for the selected pair yet: clear stale candles and fetch.
        updateChart([], levels, buyZone, viewPair);
        fetchPairChartHistory(viewPair, levels, buyZone);
      }
      // Override grid details for selected pair
      if (gp) {
        d.grid_levels = gp.grids;
        d.grid_buy_zone = gp.grids[gp.mid_idx + 1];
        d.grid_filled = gp.filled;
        d.grid_mid_idx = gp.mid_idx;
        d.grid_trailing_active = gp.trailing_sell_active;
        d.grid_trailing_high = gp.trailing_high;
      }
    }
    document.getElementById("s-balance").textContent = d.balance > 0 ? "$" + (d.balance||0).toFixed(2) : "—";
    document.getElementById("s-sol-balance").textContent = d.sol_balance > 0 ? "$" + d.sol_balance.toFixed(2) + " (USDC: $" + (d.sol_usdc||0).toFixed(2) + " USDT: $" + (d.sol_usdt||0).toFixed(2) + ")" : "—";
    document.getElementById("s-mode").textContent = d.paper_trading ? "📋 PAPER" : "🔴 LIVE";
    document.getElementById("s-mode").style.color = d.paper_trading ? "var(--yellow)" : "var(--red)";
    var pb = document.getElementById("paper-btn");
    if (pb) {
      pb.textContent = "📋 Paper: " + (d.paper_trading ? "ON" : "OFF");
      pb.style.color = d.paper_trading ? "var(--yellow)" : "var(--red)";
      pb.style.borderColor = d.paper_trading ? "var(--yellow)44" : "var(--red)44";
      pb.style.background = d.paper_trading ? "var(--yellow)18" : "var(--red)18";
    }
    document.getElementById("s-pnl").innerHTML = d.pnl != null ? pnlHtml(d.pnl) : "$0.00";
    document.getElementById("s-pos").textContent = d.positions != null && d.positions.length != null ? d.positions.length : 0;

    // Update pause button state
    if (on) {
      document.getElementById("pause-btn").style.display = "inline-block";
      document.getElementById("pause-btn").textContent = d.paused ? "▶ Resume" : "⏸ Pause";
    } else {
      document.getElementById("pause-btn").style.display = "none";
    }

    // Update summary cards from realized P&L for the selected pair. The legacy
    // top-level fields are not pair-aware and remain zero during multi-grid runs.
    var metricPair = sel.pair || d.pair || "";
    var pairMetric = d.pair_stats && d.pair_stats[metricPair];
    var pairTrades = d.trades_list ? d.trades_list.filter(function(t) {
      return (t.pair || d.pair || "") === metricPair && t.pnl != null;
    }) : [];
    var metricCount = pairMetric ? pairMetric.trades : pairTrades.length;
    var metricAvg = pairMetric ? pairMetric.avg_profit : (metricCount ? pairTrades.reduce(function(sum, t) { return sum + Number(t.pnl || 0); }, 0) / metricCount : 0);
    var metricWins = pairMetric ? pairMetric.wins : pairTrades.filter(function(t) { return Number(t.pnl) > 0; }).length;
    var metricWinRate = metricCount ? (pairMetric ? pairMetric.win_rate : metricWins / metricCount * 100) : 0;
    var metricBest = pairTrades.length ? Math.max.apply(null, pairTrades.map(function(t) { return Number(t.pnl || 0); })) : null;
    document.getElementById("sm-winrate").textContent = metricWinRate.toFixed(1).replace(/\.0$/, "") + "%";
    document.getElementById("sm-avgprofit").textContent = "$" + Number(metricAvg || 0).toFixed(2);
    document.getElementById("sm-trades").textContent = metricCount;
    document.getElementById("sm-best").textContent = metricBest != null ? "$" + metricBest.toFixed(2) : "—";

    // Update trade table
    if (d.trades_list && d.trades_list.length) {
      var html = "";
      tradeLog = [];
      d.trades_list.forEach(function(t) {
        var actionClass = t.action === "buy" ? "buy" : t.action === "sell" ? "sell" : "stop";
        var pnlBadge = t.pnl != null ? pnlHtml(t.pnl) : "—";
        html += "<tr><td>" + (t.pair || "—") + "</td><td>" + (t.strategy || "—") + "</td><td class='" + actionClass + "'>" + t.action.toUpperCase() + "</td><td>$" + t.price + "</td><td>" + t.amount + "</td><td>" + pnlBadge + "</td><td>" + (t.via || "—") + "</td></tr>";
        // Log to trade log for CSV export
        tradeLog.push({time: t.time, action: t.action, price: t.price, amount: t.amount, pnl: t.pnl, via: t.via || "", strategy: d.strategy, pair: t.pair || d.pair});
      });
      document.getElementById("trades-body").innerHTML = html;
    }

    // Update positions with badges
    if (d.positions_list && d.positions_list.length) {
      var pHtml = "";
      d.positions_list.forEach(function(p) {
        var badge = p.pnl != null ? pnlHtml(p.pnl) : "—";
        pHtml += "<div class='arb-row'><span>" + p.token + " @ $" + p.entry + "</span><span>" + badge + "</span></div>";
      });
      // Could add a positions card here
    }
    // ── Grid Details ──
    var gdCard = document.getElementById("grid-details-card");
    if (d.strategy === "grid" && d.grid_levels && d.grid_levels.length >= 2) {
      gdCard.style.display = "block";
      var gl = d.grid_levels;
      var midIdx = d.grid_mid_idx != null ? d.grid_mid_idx : Math.floor(gl.length / 2);
      var midPrice = gl[midIdx];
      var curPrice = d.price || 0;
      var filled = d.grid_filled || {};
      var trailActive = d.grid_trailing_active || false;
      var trailHigh = d.grid_trailing_high || 0;
      var trailingPct = 0.5;
      document.getElementById("gdt-status").textContent = trailActive ? "🔴 TRAILING SELL ACTIVE" : "⏸ Waiting for sell zone";
      var html = '<div style="margin-top:12px">';
      var minP = gl[0], maxP = gl[gl.length-1], range = maxP - minP;
      var curPct = range > 0 ? ((curPrice - minP) / range * 100) : 50;
      html += '<div style="position:relative;height:6px;background:linear-gradient(90deg,#00ff9d44,#ffd43b44,#ff6b6b44);border-radius:3px;margin-bottom:16px">';
      html += '<div style="position:absolute;left:' + curPct.toFixed(0) + '%;top:-4px;width:3px;height:14px;background:#3399ff;border-radius:1px"></div>';
      html += '<div style="display:flex;justify-content:space-between;font-size:10px;color:var(--dim);margin-top:8px">';
      html += '<span style="color:#00ff9d">$' + minP.toFixed(0) + '</span>';
      html += '<span style="color:#ffd43b">Mid $' + midPrice.toFixed(0) + '</span>';
      html += '<span style="color:#ff6b6b">$' + maxP.toFixed(0) + '</span></div></div>';
      html += '<div style="display:grid;grid-template-columns:80px 80px 80px 1fr 80px;gap:4px;font-size:11px;color:var(--dim);padding:4px 8px;text-transform:uppercase;letter-spacing:0.5px">';
      html += '<span>Zone</span><span>Price</span><span>Gap</span><span>Status</span><span style="text-align:right">Dist</span></div>';
      for (var i = gl.length - 1; i >= 0; i--) {
        var isMid = i === midIdx;
        var isMidMinusOne = i === (midIdx - 1);
        var isBuyZone = i <= midIdx;
        var isFilled = filled[i] != null;
        var isCur = (i < gl.length - 1 && curPrice >= gl[i] && curPrice < gl[i+1]) || (i === gl.length - 1 && curPrice >= gl[i]);
        
        var zone = "SELL";
        var zoneColor = "#ff6b6b";
        var bgColor = "#ff6b6b08";
        var borderColor = "#ff6b6b44";
        
        if (isBuyZone) {
          if (isMid) {
            zone = "MID (BUY)";
            zoneColor = "#ffd43b";
            bgColor = "#ffd43b08";
            borderColor = "#ffd43b44";
          } else if (isMidMinusOne) {
            zone = "MID-1 (BUY)";
            zoneColor = "#ffbc42";
            bgColor = "#ffbc4208";
            borderColor = "#ffbc4244";
          } else {
            zone = "BUY";
            zoneColor = "#00ff9d";
            bgColor = "#00ff9d08";
            borderColor = "#00ff9d44";
          }
        }
        if (isCur) { zone = "●"; zoneColor = "#3399ff"; bgColor = "#3399ff10"; borderColor = "#3399ff"; }
        if (isFilled) { zoneColor = "#00ff9d"; bgColor = "#00ff9d15"; borderColor = "#00ff9d"; }
        
        var gapVal = i > 0 ? (gl[i] - gl[i-1]) : 0;
        var gapStr = "—";
        var gapColor = "var(--dim)";
        if (i > 0) {
          var isSellGap = i > midIdx;
          var multiplier = isSellGap ? " (2x)" : " (1x)";
          gapColor = isSellGap ? "#ff6b6b" : "#00ff9d";
          gapStr = "$" + gapVal.toFixed(2) + multiplier;
        }
        
        var dist = curPrice > 0 ? (gl[i] - curPrice) : 0;
        var distStr = dist > 0 ? "+$" + dist.toFixed(0) : dist < 0 ? "-$" + Math.abs(dist).toFixed(0) : "—";
        var status = isFilled ? "✅ $" + filled[i].price.toFixed(0) : isCur ? "← Current" : isBuyZone ? "Buy Zone" : "Waiting";
        if (isFilled && trailActive && isBuyZone) status = "✅ Trailing...";
        if (isFilled && !trailActive && isBuyZone) status = "✅ Filled";
        html += '<div style="display:grid;grid-template-columns:80px 80px 80px 1fr 80px;gap:4px;align-items:center;padding:5px 8px;border-radius:4px;margin-bottom:2px;font-size:12px;background:' + bgColor + ';border-left:2px solid ' + borderColor + '">';
        html += '<span style="font-weight:600;color:' + zoneColor + ';font-size:9px;text-transform:uppercase">' + zone + '</span>';
        html += '<span style="font-family:monospace;font-weight:600">$' + gl[i].toFixed(2) + '</span>';
        html += '<span style="font-family:monospace;font-weight:600;color:' + gapColor + ';font-size:11px">' + gapStr + '</span>';
        html += '<span style="color:' + (isFilled ? "#00ff9d" : isCur ? "#3399ff" : "var(--text)") + '">' + status + '</span>';
        html += '<span style="text-align:right;font-family:monospace;font-size:11px;color:' + (dist > 0 ? "#ff6b6b" : dist < 0 ? "#00ff9d" : "var(--dim)") + '">' + distStr + '</span></div>';
      }
      document.getElementById("grid-details-body").innerHTML = html;
    } else {
      gdCard.style.display = "block";
      document.getElementById("gdt-status").textContent = "\u23F8 Idle \u2014 start a Grid strategy to see levels";
      document.getElementById("grid-details-body").innerHTML = '<div style="padding:20px;text-align:center;color:var(--dim);font-size:13px;margin-top:40px">Start a Grid strategy to see buy/sell levels, filled positions, and trailing sell status here.</div>';
    }
    // Update log
    if (d.log && d.log.length) {
      var logHtml = d.log.slice(0, 30).map(function(l) {
        var cls = "li";
        if (l.includes("BUY") || l.includes("buy")) cls = "lw";
        if (l.includes("SELL") || l.includes("sell")) cls = "buy";
        if (l.includes("ERROR") || l.includes("error")) cls = "le";
        return "<div class='" + cls + "'>" + l + "</div>";
      }).join("");
      document.getElementById("log-box").innerHTML = logHtml;
    }

    // Toast on new trade (only once per trade)
    if (d.last_trade && d.last_trade.action && d.last_trade.time != _lastTradeTime) {
      _lastTradeTime = d.last_trade.time;
      showToast(d.last_trade.action.toUpperCase() + " " + d.last_trade.pair + " @ $" + d.last_trade.price, "trade");
      playBeep();
      requestNotif();
      sendNotif("GridRunner", d.last_trade.action.toUpperCase() + " " + d.last_trade.pair + " @ $" + d.last_trade.price);
    }

    // Update config display
    if (d.config && !configDirty && !document.querySelector("#config-card input:focus, #config-card select:focus")) {
      document.getElementById("cfg-risk").value = d.config.risk_pct || "";
      document.getElementById("cfg-maxpos").value = d.config.max_pos ?? 500;
      document.getElementById("cfg-maxloss").value = d.config.max_loss ?? 200;
      document.getElementById("cfg-takeprofit").value = d.config.take_profit ?? 15;
      document.getElementById("cfg-arbspread").value = d.config.min_arb_spread ?? 1.5;
      document.getElementById("cfg-stoploss").value = d.config.grid_stop_loss_pct || 8;
      document.getElementById("cfg-trailing").value = d.config.trailing_pct || 0.5;
      document.getElementById("cfg-partial").value = d.config.partial_sell_pct || 50;
      document.getElementById("cfg-spread").value = ((d.config.base_spread || 0.05) * 100).toFixed(1);
      document.getElementById("cfg-compound").value = d.config.auto_compound ? "true" : "false";
    }
  } catch(e) { console.error("refresh error:", e); } }).catch(console.error);
}

window.addEventListener("resize", function() {
  if (chart) {
    var w = document.getElementById("chart-container").clientWidth || 600;
    chart.applyOptions({width: w});
  }
});

setInterval(refresh, 3000);
refresh();
initChart();
  function apiFetch(url, opts) {
    opts = opts || {};
    opts.credentials = "same-origin";
    return fetch(url, opts);
  }
  document.querySelectorAll("#config-card input, #config-card select").forEach(function(el) {
    el.addEventListener("input", markConfigDirty);
    el.addEventListener("change", markConfigDirty);
  });


</script>

</body>
</html>'''

# ── HTTP Server ───────────────────────────────────────────────────────────────
class Handler(BaseHTTPRequestHandler):

    def _is_authenticated(self):
        """Check credentials without writing a response — callers decide what to send on failure."""
        secret = os.environ.get("API_SECRET", "")
        if not secret:
            return False
        # Programmatic access (webhooks, scripts): X-API-Secret header
        sent = self.headers.get("X-API-Secret", "")
        if sent and hmac.compare_digest(sent, secret):
            return True
        # Browser access: HTTP Basic Auth (browser prompts natively, caches per-origin)
        auth_header = self.headers.get("Authorization", "")
        expected = "Basic " + base64.b64encode(("dashboard:" + secret).encode()).decode()
        if auth_header and hmac.compare_digest(auth_header, expected):
            return True
        return False

    def _auth_or_401(self):
        if self._is_authenticated():
            return True
        self.respond(401, "text/plain", b"Unauthorized",
                      extra_headers=[("WWW-Authenticate", 'Basic realm="GridRunner"')])
        return False

    def do_OPTIONS(self):
        self.respond(204, "text/plain", b"")
    def do_GET(self):
        parsed=urlparse(self.path)
        path=parsed.path
        params=parse_qs(parsed.query)

        if path=="/":
            if not self._auth_or_401(): return
            self.respond(200,"text/html",DASHBOARD.encode())
        elif path=="/logo.jpeg":
            try:
                with open("logo.jpeg","rb") as f: logo_data=f.read()
                self.respond(200,"image/jpeg",logo_data)
            except: self.respond(404,"text/plain",b"logo not found")
        elif path=="/manifest.json":
            manifest = json.dumps({
                "name":"GridRunner","short_name":"GridRunner","start_url":"/","display":"standalone",
                "background_color":"#0a0a1a","theme_color":"#0a0a1a",
                "icons":[{"src":"/logo.jpeg","sizes":"512x512","type":"image/jpeg","purpose":"any maskable"}]
            })
            self.respond(200,"application/json",manifest.encode())
        elif path=="/sw.js":
            self.respond(200,"application/javascript",
                b"self.addEventListener('install',function(e){self.skipWaiting()});"
                b"self.addEventListener('activate',function(e){e.waitUntil(clients.claim())});"
                b"self.addEventListener('fetch',function(e){e.respondWith(fetch(e.request).catch(function(){return caches.match(e.request)}))})")
        elif path=="/state":
            state["trades_list"] = [{"time":t["time"],"action":t["side"],"price":t["price"],"amount":t["amount"],"pnl":t.get("pnl"),"via":t.get("router",""),"pair":t.get("pair","")} for t in state["trades"][-50:]]
            pair_stats = {}
            for t in state["trades"]:
                pnl = t.get("pnl"); pair = t.get("pair") or state.get("pair", "")
                if pnl is None or not pair: continue
                stats = pair_stats.setdefault(pair, {"trades": 0, "wins": 0, "pnl": 0.0})
                stats["trades"] += 1; stats["wins"] += 1 if pnl > 0 else 0; stats["pnl"] += float(pnl)
            for stats in pair_stats.values():
                stats["avg_profit"] = stats["pnl"] / stats["trades"] if stats["trades"] else 0.0
                stats["win_rate"] = stats["wins"] / stats["trades"] * 100 if stats["trades"] else 0.0
            state["pair_stats"] = pair_stats
            state["positions_count"] = len(state.get("positions", []))
            if not self._is_authenticated():
                self.respond(200,"application/json",json.dumps({"price":state.get("price",0),"running":state.get("running",False),"strategy":state.get("strategy",""),"pair":state.get("pair",""),"mode":state.get("mode",""),"paper_trading":state.get("paper_trading",True)}).encode())
                return
            self.respond(200,"application/json",json.dumps(state).encode())
        elif path=="/chart_history":
            if not self._auth_or_401(): return
            pair = params.get("pair", [""])[0]
            history = chart_history_for(pair)
            body = {"ok": True, "pair": pair, "history": history}
            if not history:
                body["error"] = "No price data for " + pair + " yet"
            self.respond(200,"application/json",json.dumps(body).encode())
        elif path=="/limit_orders/status":
            if not self._auth_or_401(): return
            self.respond(200,"application/json",json.dumps(limit_orders_addon.status() if limit_orders_addon else {"valid":False}).encode())
        elif path=="/license_status":
            info = {
                "valid": state.get("license_valid", True),
                "type": state.get("license_type", "unknown"),
                "expires": state.get("license_expires"),
                "days_remaining": state.get("license_days_left"),
            }
            self.respond(200, "application/json", json.dumps(info).encode())
        elif path=="/scanner":
            if not self._auth_or_401(): return
            self.respond(200, "text/html", SCANNER_HTML.encode())
        elif path=="/scan_token":
            if not self._auth_or_401(): return
            mint = params.get("mint", [""])[0].strip()
            symbol = params.get("symbol", [""])[0].strip()
            if not validate_solana_mint(mint):
                self.respond(400, "application/json", json.dumps({"error": "Invalid Solana mint address"}).encode())
                return
            log("Scan requested for " + mint[:12] + "...", "INFO")
            report = scan_token_full(mint, symbol)
            self.respond(200, "application/json", json.dumps(report).encode())
        elif path=="/start":
            if not self._auth_or_401(): return
            if params.get("custom_mint", [""])[0]:
                custom_mint = params["custom_mint"][0]; custom_symbol = params.get("custom_symbol", [""])[0].upper()
                if not validate_solana_mint(custom_mint) or not custom_symbol or not custom_symbol.isalnum():
                    self.respond(400,"application/json",json.dumps({"error":"Invalid custom token mint or symbol"}).encode()); return
                existing = get_registry_entry(custom_symbol)
                if existing and existing.get("mint") not in (None, custom_mint):
                    self.respond(409,"application/json",json.dumps({"error":"Symbol already mapped to a different mint"}).encode()); return
                if not existing or existing.get("status") not in ("APPROVED",):
                    log("Verifying new token " + custom_symbol + " (" + custom_mint[:12] + "...) before starting strategy", "INFO")
                    verification = verify_token_authenticity(custom_mint, custom_symbol)
                    status, warnings, message = register_verified_token(custom_symbol, custom_mint, verification)
                    if status != "APPROVED":
                        self.respond(403,"application/json",json.dumps({
                            "error": "Token not approved for trading",
                            "status": status, "warnings": warnings, "message": message,
                        }).encode()); return
                SOL_TOKENS[custom_symbol] = custom_mint; TOKEN_DECIMALS.setdefault(custom_symbol, 6)
            start_strategy = params.get("strategy",["dca"])[0]
            pair_param = params.get("pair",[cfg["pair"]])[0]
            
            order_cfg = {}
            if start_strategy == "ai_trading":
                state["config"]["risk_pct"] = float(params.get("risk_pct", [1.0])[0])
                state["config"]["max_leverage"] = float(params.get("max_leverage", [3.0])[0])
                state["config"]["max_total_exposure"] = float(params.get("max_total_exposure", [1000.0])[0])
                state["config"]["max_simultaneous_positions"] = int(params.get("max_simultaneous_positions", [3])[0])
                state["config"]["auto_compound"] = params.get("auto_compound", ["true"])[0].lower() != "false"
                ai_whitelist_raw = params.get("ai_whitelist", [""])[0]
                if ai_whitelist_raw:
                    state["ai_whitelisted_symbols"] = [s.strip() for s in ai_whitelist_raw.split(",")]
                else:
                    state["ai_whitelisted_symbols"] = [pair_param]
                    
                ai_mode = params.get("ai_mode", ["paper"])[0]
                ai_paper = (ai_mode != "live")
                
                if not ai_paper:
                    has_keys = False
                    chain_choice = params.get("chain", ["solana"])[0]
                    if chain_choice == "solana":
                        if cfg.get("sol_wallet") and _secret("SOL_PRIVATE_KEY"):
                            has_keys = True
                    else:
                        if cfg.get("wallet") and _secret("PRIVATE_KEY"):
                            has_keys = True
                    if not has_keys:
                        self.respond(400, "application/json", json.dumps({"error": "Cannot start in LIVE mode: Wallet/API keys are not configured."}).encode())
                        return
                        
                order_cfg = {
                    "paper_trading": ai_paper,
                    "risk_pct": float(state["config"].get("risk_pct", 1.0)),
                    "max_leverage": float(state["config"].get("max_leverage", 3.0)),
                    "max_total_exposure": float(state["config"].get("max_total_exposure", 1000.0)),
                    "max_simultaneous_positions": int(state["config"].get("max_simultaneous_positions", 3)),
                    "auto_compound": state["config"].get("auto_compound", True),
                    "ai_whitelist": state["ai_whitelisted_symbols"]
                }
                state["paper_trading"] = ai_paper
            elif start_strategy in ("limit_buy", "limit_sell"):
                requested_side = params.get("side", ["buy"])[0]
                expected_side = "buy" if start_strategy == "limit_buy" else "sell"
                if requested_side != expected_side:
                    self.respond(400, "application/json", json.dumps({"error":"strategy side mismatch"}).encode()); return
                if params.get("confirm", ["false"])[0].lower() != "true":
                    self.respond(400, "application/json", json.dumps({"error":"explicit order confirmation required"}).encode()); return
                effective_mode, mode_error = resolve_order_mode(params)
                if mode_error:
                    self.respond(400, "application/json", json.dumps({"error":mode_error}).encode()); return
                state["paper_trading"] = (effective_mode == "paper")
                ok, reason = validate_limit_order(params.get("amount_usdc",[0])[0], params.get("side",["buy"])[0], params.get("order_type",["limit"])[0], params.get("limit_price",[0])[0], cfg.get("max_pos"))
                if not ok:
                    self.respond(400, "application/json", json.dumps({"error": reason}).encode()); return
                order_cfg = {"limit_amount_usdc": float(params.get("amount_usdc",[0])[0] or 0), "limit_price": float(params.get("limit_price",[0])[0] or 0), "limit_order_type": params.get("order_type",["limit"])[0], "limit_side": params.get("side",["buy"])[0], "custom_mint": params.get("custom_mint", [""])[0], "custom_symbol": params.get("custom_symbol", [""])[0].upper(), "quote_token": params.get("quote_token", ["USDC"])[0], "effective_mode": effective_mode, "paper_trading": (effective_mode == "paper")}
            else:
                # Grid and all other non-AI strategies default to LIVE. Paper
                # applies automatically ONLY to AI Trading. A globally-forced
                # paper-only license lock (license_valid False) still wins.
                grid_paper = default_strategy_paper(start_strategy)
                order_cfg = {
                    "paper_trading": grid_paper,
                    "risk_pct": float(state["config"].get("risk_pct", 2.0)),
                    "max_pos": float(state["config"].get("max_pos", 500.0)),
                }
                state["paper_trading"] = grid_paper

            sid = f"{start_strategy}_{pair_param}"
            if "strategies" in state and sid in state["strategies"] and state["strategies"][sid].get("running"):
                self.respond(400, "application/json", json.dumps({"error": "Strategy already running on this pair"}).encode())
                return
                
            mode_param = params.get("mode", ["dex"])[0]
            chain_param = params.get("chain", ["solana"])[0]
            exchange_param = params.get("exchange", [cfg["exchange"]])[0]
            # Reflect the requested venue before the capital-reservation check so
            # live reservations are measured against the correct chain's balance.
            state["mode"] = mode_param
            state["chain"] = chain_param
            state["exchange"] = exchange_param
            ok, cap_error = check_capital_reservation(start_strategy, order_cfg)
            if not ok:
                self.respond(400, "application/json", json.dumps({"error": cap_error}).encode())
                return
            start_bot(
                start_strategy,
                pair_param,
                mode_param,
                exchange_param,
                chain_param,
                order_cfg
            )
            self.respond(200,"application/json",b'{"ok":true}')
        elif path=="/stop":
            if not self._auth_or_401(): return
            sid = params.get("sid", [""])[0]
            if sid:
                stop_strategy(sid)
            else:
                stop_bot()
            self.respond(200,"application/json",b'{"ok":true}')
        elif path=="/stop_all":
            if not self._auth_or_401(): return
            stop_all()
            self.respond(200,"application/json",b'{"ok":true}')
        elif path=="/backtest":
            if not self._auth_or_401(): return
            try: data = json.loads(self.rfile.read(int(self.headers.get("Content-Length",0))))
            except Exception: data = {}
            pair = data.get("pair", state.get("pair", "SOL/USDC"))
            strategy = data.get("strategy", "grid")
            # Load price data
            prices = []
            if state.get("price_history") and len(state["price_history"]) > 5:
                prices = state["price_history"]
            else:
                # Fallback: fetch from Kraken
                try:
                    r = requests.get("https://api.kraken.com/0/public/OHLC", params={
                        "pair": pair.replace("/",""), "interval": 5
                    }, timeout=10)
                    ohlc = r.json().get("result", {})
                    for k in ohlc:
                        if k != "last":
                            prices = [{"time": int(p[0]), "value": float(p[4])} for p in ohlc[k][-200:]]
                except Exception: pass
            if not prices or len(prices) < 5:
                self.respond(200,"application/json",json.dumps({"error":"Not enough price data"}).encode()); return
            # Simple grid backtest
            trades = []; pnl_total = 0; wins = 0; peak_equity = 0; max_dd = 0; equity = 100
            levels=5; spread_val=cfg.get("base_spread",0.05)
            base_price = prices[0]["value"]
            grids = [round(base_price*(1-spread_val)+i*(base_price*spread_val*2/levels),4) for i in range(levels+1)]
            mid_idx = len(grids)//2; filled = {}
            for pt in prices[1:]:
                pr = pt["value"]
                if pr <= 0: continue
                # Re-center check
                if pr < grids[0]*0.98 or pr > grids[-1]*1.02:
                    base_price = pr
                    grids = [round(pr*(1-spread_val)+i*(pr*spread_val*2/levels),4) for i in range(levels+1)]
                    mid_idx = len(grids)//2
                for i,g in enumerate(grids[:-1]):
                    ng = grids[i+1]
                    if g <= pr < ng:
                        is_buy = i <= mid_idx
                        if is_buy and i not in filled:
                            filled[i] = {"price":pr,"amount":1}
                        elif not is_buy:
                            for bi in sorted(filled.keys()):
                                if bi < i:
                                    bp = filled[bi]["price"]
                                    pnl = pr - bp
                                    pnl_total += pnl; equity += pnl
                                    if equity > peak_equity: peak_equity = equity
                                    dd = peak_equity - equity
                                    if dd > max_dd: max_dd = dd
                                    if pnl > 0: wins += 1
                                    trades.append({"action":"sell","price":pr,"buy_price":bp,"pnl":round(pnl,2),"time":pt["time"]})
                                    del filled[bi]; break
            result = {
                "total_trades": len(trades),
                "win_rate": round(wins/max(len(trades),1)*100,1),
                "total_pnl": round(pnl_total,2),
                "max_drawdown": round(max_dd,2),
                "trades": trades[-20:]
            }
            self.respond(200,"application/json",json.dumps(result).encode())
        elif path=="/webhook":
            if not self._auth_or_401(): return
            try:
                data = json.loads(self.rfile.read(int(self.headers.get("Content-Length",0))))
            except Exception:
                self.respond(400,"application/json",json.dumps({"error":"Invalid JSON"}).encode()); return
            signal = data.get("signal","")
            wpair = data.get("pair",state.get("pair","SOL/USDC"))
            wprice = data.get("price", 0.0)
            if signal == "buy" and wprice > 0:
                # Force buy at signaled level
                gs = state["grid_pairs"].get(wpair, {})
                grids = gs.get("grids", [])
                filled = gs.get("filled", {})
                mid_idx = gs.get("mid_idx", len(grids)//2) if grids else 2
                if not grids:
                    levels=5; spread_val=cfg.get("base_spread",0.05)
                    grids = [round(wprice*(1-spread_val)+i*(wprice*spread_val*2/levels),4) for i in range(levels+1)]
                    mid_idx = len(grids)//2
                    state["grid_pairs"][wpair] = {"grids":grids,"mid_idx":mid_idx,"filled":{}}
                    if wpair not in state.get("active_pairs",[]): state["active_pairs"].append(wpair)
                bal = get_balance()
                sz = min(bal*cfg["risk_pct"]/100, cfg["max_pos"])/5
                amt = round(sz/wprice,6)
                if place_order(wpair,"buy",amt):
                    for i,g in enumerate(grids[:-1]):
                        if g <= wprice < grids[i+1] and i <= mid_idx and i not in filled:
                            filled[i] = {"price":wprice,"amount":amt}
                            state["grid_pairs"][wpair]["filled"] = filled
                            record_trade("WEBHOOK-BUY",wprice,amt, pair=wpair)
                            log("[WEBHOOK] Forced buy "+wpair+" @ $"+str(round(wprice,2)))
                            break
                self.respond(200,"application/json",json.dumps({"ok":True,"pair":wpair}).encode())
            elif signal == "sell":
                gs = state["grid_pairs"].get(wpair, {})
                filled = gs.get("filled", {})
                sold = 0
                for bi in sorted(filled.keys()):
                    amt = filled[bi]["amount"]
                    bp = filled[bi]["price"]
                    sp = wprice if wprice > 0 else get_price(wpair)
                    if place_order(wpair,"sell",amt):
                        pnl = (sp - bp) * amt
                        state["pnl"] += pnl
                        record_trade("WEBHOOK-SELL",sp,amt,round(pnl,2), pair=wpair)
                        log("[WEBHOOK] Forced sell "+wpair+" @ $"+str(round(sp,2)))
                        sold += 1
                state["grid_pairs"][wpair]["filled"] = {}
                self.respond(200,"application/json",json.dumps({"ok":True,"pair":wpair,"closed":sold}).encode())
            else:
                self.respond(400,"application/json",json.dumps({"error":"signal must be buy or sell"}).encode())
        elif path=="/debug_orca":
            if not self._auth_or_401(): return
            try:
                import base64 as b64
                pool = "Czfq3xZZDmsdGdUyrNLtRhGc47cXcZtLG4crryfu44zE"
                payload = {"jsonrpc":"2.0","id":1,"method":"getAccountInfo","params":[pool,{"encoding":"base64"}]}
                r = requests.post(SOL_RPC, json=payload, timeout=10)
                raw_b64 = r.json().get("result",{}).get("value",{}).get("data",[None])[0]
                if raw_b64:
                    raw = b64.b64decode(raw_b64)
                    sqrt_price = int.from_bytes(raw[65:81], "little")
                    price = (sqrt_price / (2**64))**2 * (10**(6-9))
                    result = {"length":len(raw),"sqrt_price":sqrt_price,"price":round(price,4),"offset_65_hex":raw[65:81].hex()}
                else:
                    result = {"error":"no data"}
                self.respond(200,"application/json",json.dumps(result).encode())
            except Exception as ex:
                self.respond(200,"application/json",json.dumps({"error":str(ex)}).encode())
        elif path=="/toggle_paper":
            if not self._auth_or_401(): return
            if not state.get("license_valid", True) and state["paper_trading"]:
                self.respond(403,"application/json",json.dumps({"error":"Cannot enable live trading — invalid license"}).encode())
                return
            state["paper_trading"] = not state["paper_trading"]
            _save_paper_mode(state["paper_trading"])
            mode = "PAPER" if state["paper_trading"] else "LIVE"
            log("Switched to "+mode+" trading mode")
            self.respond(200,"application/json",json.dumps({"paper_trading":state["paper_trading"]}).encode())
            return
        elif path=="/add_token":
            if not self._auth_or_401(): return
            try:
                body_len = int(self.headers.get("Content-Length", 0))
                raw = self.rfile.read(body_len) if body_len > 0 else b"{}"
                token_data = json.loads(raw)
            except Exception:
                self.respond(400,"application/json",json.dumps({"error":"Invalid JSON"}).encode()); return
            symbol = token_data.get("symbol", "").upper()
            mint = token_data.get("mint", "")
            pair = token_data.get("pair", "")
            if not symbol or not mint:
                self.respond(400,"application/json",json.dumps({"error":"Symbol and mint required"}).encode()); return
            if not validate_solana_mint(mint):
                self.respond(400,"application/json",json.dumps({"error":"Invalid Solana mint: expected base58 32-byte public key"}).encode()); return
            existing = get_registry_entry(symbol)
            if existing and existing.get("mint") not in (None, mint):
                self.respond(409,"application/json",json.dumps({"error":"Symbol already mapped to a different mint"}).encode()); return
            log("Verifying new token " + symbol + " (" + mint[:12] + "...) — running authenticity check", "INFO")
            verification = verify_token_authenticity(mint, symbol)
            status, warnings, message = register_verified_token(symbol, mint, verification)
            if status is None:
                self.respond(409,"application/json",json.dumps({"error":message}).encode()); return
            if status != "REJECTED":
                SOL_TOKENS[symbol] = mint
                TOKEN_DECIMALS[symbol] = verification.get("decimals") or 6
            log("Token verification result for " + symbol + ": " + status + (" — " + "; ".join(warnings) if warnings else ""), "WARN" if status != "APPROVED" else "INFO")
            self.respond(200,"application/json",json.dumps({
                "ok": status != "REJECTED", "symbol": symbol, "pair": pair,
                "status": status, "warnings": warnings, "message": message,
            }).encode())
            return
        elif path=="/pause":
            if not self._auth_or_401(): return
            state["paused"] = not state["paused"]
            log("Bot "+("paused" if state["paused"] else "resumed"))
            self.respond(200,"application/json",json.dumps({"paused":state["paused"]}).encode())
            return
        elif path=="/webhook":
            if not self._auth_or_401(): return
            signal = data.get("signal","")
            wpair = data.get("pair",state.get("pair","SOL/USDC"))
            wprice = data.get("price", 0.0)
            if signal == "buy" and wprice > 0:
                gs = state["grid_pairs"].get(wpair, {})
                grids = gs.get("grids", [])
                filled = gs.get("filled", {})
                mid_idx = gs.get("mid_idx", len(grids)//2) if grids else 2
                if not grids:
                    levels=5; spread_val=cfg.get("base_spread",0.05)
                    grids = [round(wprice*(1-spread_val)+i*(wprice*spread_val*2/levels),4) for i in range(levels+1)]
                    mid_idx = len(grids)//2
                    state["grid_pairs"][wpair] = {"grids":grids,"mid_idx":mid_idx,"filled":{}}
                    if wpair not in state.get("active_pairs",[]): state["active_pairs"].append(wpair)
                bal = get_balance()
                sz = min(bal*cfg["risk_pct"]/100, cfg["max_pos"])/5
                amt = round(sz/wprice,6)
                if place_order(wpair,"buy",amt):
                    for i,g in enumerate(grids[:-1]):
                        if g <= wprice < grids[i+1] and i <= mid_idx and i not in filled:
                            filled[i] = {"price":wprice,"amount":amt}
                            state["grid_pairs"][wpair]["filled"] = filled
                            record_trade("WEBHOOK-BUY",wprice,amt, pair=wpair)
                            log("[WEBHOOK] Forced buy "+wpair+" @ $"+str(round(wprice,2)))
                            break
                self.respond(200,"application/json",json.dumps({"ok":True,"pair":wpair}).encode())
            elif signal == "sell":
                gs = state["grid_pairs"].get(wpair, {})
                filled = gs.get("filled", {})
                sold = 0
                for bi in sorted(filled.keys()):
                    amt = filled[bi]["amount"]
                    bp = filled[bi]["price"]
                    sp = wprice if wprice > 0 else get_price(wpair)
                    if place_order(wpair,"sell",amt):
                        pnl = (sp - bp) * amt
                        state["pnl"] += pnl
                        record_trade("WEBHOOK-SELL",sp,amt,round(pnl,2), pair=wpair)
                        log("[WEBHOOK] Forced sell "+wpair+" @ $"+str(round(sp,2)))
                        sold += 1
                state["grid_pairs"][wpair]["filled"] = {}
                self.respond(200,"application/json",json.dumps({"ok":True,"pair":wpair,"closed":sold}).encode())
            else:
                self.respond(400,"application/json",json.dumps({"error":"signal must be buy or sell"}).encode())
        elif path=="/backtest":
            if not self._auth_or_401(): return
            pair = data.get("pair", state.get("pair", "SOL/USDC"))
            strategy = data.get("strategy", "grid")
            prices = []
            if state.get("price_history") and len(state["price_history"]) > 5:
                prices = state["price_history"]
            else:
                try:
                    r = requests.get("https://api.kraken.com/0/public/OHLC", params={
                        "pair": pair.replace("/",""), "interval": 5
                    }, timeout=10)
                    ohlc = r.json().get("result", {})
                    for k in ohlc:
                        if k != "last":
                            prices = [{"time": int(p[0]), "value": float(p[4])} for p in ohlc[k][-200:]]
                except Exception: pass
            if not prices or len(prices) < 5:
                self.respond(200,"application/json",json.dumps({"error":"Not enough price data"}).encode()); return
            trades = []; pnl_total = 0; wins = 0; peak_equity = 0; max_dd = 0; equity = 100
            levels=5; spread_val=cfg.get("base_spread",0.05)
            base_price = prices[0]["value"]
            grids = [round(base_price*(1-spread_val)+i*(base_price*spread_val*2/levels),4) for i in range(levels+1)]
            mid_idx = len(grids)//2; filled = {}
            for pt in prices[1:]:
                pr = pt["value"]
                if pr <= 0: continue
                if pr < grids[0]*0.98 or pr > grids[-1]*1.02:
                    base_price = pr
                    grids = [round(pr*(1-spread_val)+i*(pr*spread_val*2/levels),4) for i in range(levels+1)]
                    mid_idx = len(grids)//2
                for i,g in enumerate(grids[:-1]):
                    ng = grids[i+1]
                    if g <= pr < ng:
                        is_buy = i <= mid_idx
                        if is_buy and i not in filled:
                            filled[i] = {"price":pr,"amount":1}
                        elif not is_buy:
                            for bi in sorted(filled.keys()):
                                if bi < i:
                                    bp = filled[bi]["price"]
                                    pnl = pr - bp
                                    pnl_total += pnl; equity += pnl
                                    if equity > peak_equity: peak_equity = equity
                                    dd = peak_equity - equity
                                    if dd > max_dd: max_dd = dd
                                    if pnl > 0: wins += 1
                                    trades.append({"action":"sell","price":pr,"buy_price":bp,"pnl":round(pnl,2),"time":pt["time"]})
                                    del filled[bi]; break
            result = {
                "total_trades": len(trades),
                "win_rate": round(wins/max(len(trades),1)*100,1),
                "total_pnl": round(pnl_total,2),
                "max_drawdown": round(max_dd,2),
                "trades": trades[-20:]
            }
            self.respond(200,"application/json",json.dumps(result).encode())
        else:
            self.respond(404,"text/plain",b"Not found")

    def do_POST(self):
        try:
            content_len = int(self.headers.get("Content-Length", 0))
        except ValueError:
            self.respond(400, "text/plain", b"Invalid Content-Length"); return
        if content_len > 1024 * 1024:
            self.respond(413, "text/plain", b"Payload Too Large"); return
        if content_len > 0:
            body = self.rfile.read(content_len)
            try: data = json.loads(body)
            except Exception as e:
                log("JSON parse error: "+str(e), "WARN")
                data = {}
        else: data = {}
        path = urlparse(self.path).path
        if path == "/stripe/webhook":
            import license_issuance
            signature = self.headers.get("Stripe-Signature", "")
            try:
                key = license_issuance.handle_stripe_webhook(body, signature)
                self.respond(200, "application/json", json.dumps({"ok": True, "issued": bool(key)}).encode())
            except ValueError:
                self.respond(400, "application/json", b'{"error":"invalid webhook"}')
            except Exception:
                self.respond(500, "application/json", b'{"error":"webhook processing failed"}')
            return
        if path == "/kill":
            if not self._auth_or_401(): return
            if data.get("confirm") is not True:
                self.respond(400, "application/json", b'{"error":"confirmation required"}'); return
            closed = 0; total_val = 0.0
            for pair in list(state.get("active_pairs", [])):
                filled = state["grid_pairs"].get(pair, {}).get("filled", {})
                for idx, pos in list(filled.items()):
                    if place_order(pair, "sell", pos["amount"]):
                        total_val += pos["amount"] * pos.get("price", 0); closed += 1; del filled[idx]
            state["running"] = False; state["active_pairs"] = []
            self.respond(200, "application/json", json.dumps({"closed": closed, "total_value": round(total_val, 2)}).encode()); return
        if path == "/config":
            if not self._auth_or_401(): return
            config_keys = ["risk_pct", "max_pos", "max_loss", "take_profit", "min_arb_spread", "stop_loss", "grid_stop_loss_pct", "trailing_pct", "partial_sell_pct", "base_spread", "auto_compound"]
            bool_keys = {"auto_compound"}
            float_keys = {"risk_pct", "max_pos", "max_loss", "take_profit", "min_arb_spread", "stop_loss", "grid_stop_loss_pct", "trailing_pct", "partial_sell_pct", "base_spread"}
            for key in config_keys:
                if key in data and data[key] is not None:
                    if key in bool_keys:
                        cfg[key] = str(data[key]).lower() in ("true","1","yes")
                    elif key in float_keys:
                        try:
                            val = float(data[key])
                            bounds = {"risk_pct": (0.01, 100), "max_pos": (0.01, 1_000_000), "max_loss": (0, 1_000_000), "take_profit": (0, 1_000), "min_arb_spread": (0, 100), "stop_loss": (0, 100), "grid_stop_loss_pct": (0, 100), "trailing_pct": (0, 100), "partial_sell_pct": (1, 99), "base_spread": (0, 1)}
                            lo, hi = bounds[key]
                            if not math.isfinite(val) or not lo <= val <= hi:
                                raise ValueError("out of bounds")
                            cfg[key] = val
                        except (ValueError, TypeError) as e:
                            log("Config "+key+" parse error: "+str(e), "WARN")
                    else:
                        cfg[key] = data[key]
            # Keep the shared stop-loss control aligned for both grid and non-grid loops.
            if "grid_stop_loss_pct" in data:
                cfg["stop_loss"] = cfg["grid_stop_loss_pct"]
            # Persist every dashboard control into the live cfg used by trade loops.
            # The former handler omitted Render's max_loss/take_profit/min_arb_spread,
            # so those controls appeared to save but never affected a trade.
            state["config"] = {k: cfg.get(k) for k in ["risk_pct", "max_pos", "max_loss", "take_profit", "min_arb_spread", "stop_loss", "grid_stop_loss_pct", "trailing_pct", "partial_sell_pct", "base_spread", "auto_compound", "dynamic_spread"] if cfg.get(k) is not None}
            log("Config updated: "+json.dumps(data))
            self.respond(200,"application/json",json.dumps({"status":"ok","config":state["config"]}).encode())
        elif path == "/trade_log":
            if not self._auth_or_401(): return
            try:
                with open(TRADE_LOG) as f:
                    lines = f.readlines()[-200:]
                self.respond(200,"application/json",json.dumps({"log":[json.loads(l) for l in lines]}).encode())
            except Exception as e:
                self.respond(200,"application/json",json.dumps({"log":[],"error":str(e)}).encode())
        elif path == "/manual_trade":
            if not self._auth_or_401(): return
            pair = data.get("pair", state.get("pair", "SOL/USDC"))
            side = data.get("side", "buy")
            usdc_amt = float(data.get("amount_usdc", 10))
            price = get_price(pair)
            if price > 0:
                if pair not in state["price_history_pairs"]:
                    state["price_history_pairs"][pair] = []
                state["price_history_pairs"][pair].append({"time": int(time.time()), "value": price})
                if len(state["price_history_pairs"][pair]) > 4320:
                    state["price_history_pairs"][pair] = state["price_history_pairs"][pair][-4320:]
            if price <= 0:
                self.respond(400,"application/json",json.dumps({"error":"Cannot get price for "+pair}).encode()); return
            if side == "buy":
                token_amt = round(usdc_amt / price, 6)
                ok = place_order(pair, "buy", token_amt)
                if ok:
                    record_trade("MANUAL-BUY", price, token_amt, pair=pair)
                    log("[MANUAL] BUY "+pair+" "+str(token_amt)+" @ $"+str(round(price,2)))
                    self.respond(200,"application/json",json.dumps({"ok":True,"price":price,"amount":token_amt,"pair":pair}).encode())
                else:
                    self.respond(500,"application/json",json.dumps({"error":"Buy order failed"}).encode())
            else:
                token_amt = round(usdc_amt / price, 6)
                ok = place_order(pair, "sell", token_amt)
                if ok:
                    received = token_amt * price
                    record_trade("MANUAL-SELL", price, token_amt, round(received - usdc_amt, 2), pair=pair)
                    log("[MANUAL] SELL "+pair+" "+str(token_amt)+" @ $"+str(round(price,2)))
                    self.respond(200,"application/json",json.dumps({"ok":True,"price":price,"amount":token_amt,"pair":pair,"received":round(received,2)}).encode())
                else:
                    self.respond(500,"application/json",json.dumps({"error":"Sell order failed"}).encode())
        elif path=="/webhook":
            if not self._auth_or_401(): return
            signal = data.get("signal","")
            wpair = data.get("pair",state.get("pair","SOL/USDC"))
            wprice = data.get("price", 0.0)
            if signal == "buy" and wprice > 0:
                gs = state["grid_pairs"].get(wpair, {})
                grids = gs.get("grids", [])
                filled = gs.get("filled", {})
                mid_idx = gs.get("mid_idx", len(grids)//2) if grids else 2
                if not grids:
                    levels=5; spread_val=cfg.get("base_spread",0.05)
                    grids = [round(wprice*(1-spread_val)+i*(wprice*spread_val*2/levels),4) for i in range(levels+1)]
                    mid_idx = len(grids)//2
                    state["grid_pairs"][wpair] = {"grids":grids,"mid_idx":mid_idx,"filled":{}}
                    if wpair not in state.get("active_pairs",[]): state["active_pairs"].append(wpair)
                bal = get_balance()
                sz = min(bal*cfg["risk_pct"]/100, cfg["max_pos"])/5
                amt = round(sz/wprice,6)
                if place_order(wpair,"buy",amt):
                    for i,g in enumerate(grids[:-1]):
                        if g <= wprice < grids[i+1] and i <= mid_idx and i not in filled:
                            filled[i] = {"price":wprice,"amount":amt}
                            state["grid_pairs"][wpair]["filled"] = filled
                            record_trade("WEBHOOK-BUY",wprice,amt, pair=wpair)
                            log("[WEBHOOK] Forced buy "+wpair+" @ $"+str(round(wprice,2)))
                            break
                self.respond(200,"application/json",json.dumps({"ok":True,"pair":wpair}).encode())
            elif signal == "sell":
                gs = state["grid_pairs"].get(wpair, {})
                filled = gs.get("filled", {})
                sold = 0
                for bi in sorted(filled.keys()):
                    amt = filled[bi]["amount"]
                    bp = filled[bi]["price"]
                    sp = wprice if wprice > 0 else get_price(wpair)
                    if place_order(wpair,"sell",amt):
                        pnl = (sp - bp) * amt
                        state["pnl"] += pnl
                        record_trade("WEBHOOK-SELL",sp,amt,round(pnl,2), pair=wpair)
                        log("[WEBHOOK] Forced sell "+wpair+" @ $"+str(round(sp,2)))
                        sold += 1
                state["grid_pairs"][wpair]["filled"] = {}
                self.respond(200,"application/json",json.dumps({"ok":True,"pair":wpair,"closed":sold}).encode())
            else:
                self.respond(400,"application/json",json.dumps({"error":"signal must be buy or sell"}).encode())
        elif path=="/backtest":
            if not self._auth_or_401(): return
            pair = data.get("pair", state.get("pair", "SOL/USDC"))
            strategy = data.get("strategy", "grid")
            prices = []
            if state.get("price_history") and len(state["price_history"]) > 5:
                prices = state["price_history"]
            else:
                try:
                    r = requests.get("https://api.kraken.com/0/public/OHLC", params={
                        "pair": pair.replace("/",""), "interval": 5
                    }, timeout=10)
                    ohlc = r.json().get("result", {})
                    for k in ohlc:
                        if k != "last":
                            prices = [{"time": int(p[0]), "value": float(p[4])} for p in ohlc[k][-200:]]
                except Exception: pass
            if not prices or len(prices) < 5:
                self.respond(200,"application/json",json.dumps({"error":"Not enough price data"}).encode()); return
            trades = []; pnl_total = 0; wins = 0; peak_equity = 0; max_dd = 0; equity = 100
            levels=5; spread_val=cfg.get("base_spread",0.05)
            base_price = prices[0]["value"]
            grids = [round(base_price*(1-spread_val)+i*(base_price*spread_val*2/levels),4) for i in range(levels+1)]
            mid_idx = len(grids)//2; filled = {}
            for pt in prices[1:]:
                pr = pt["value"]
                if pr <= 0: continue
                if pr < grids[0]*0.98 or pr > grids[-1]*1.02:
                    base_price = pr
                    grids = [round(pr*(1-spread_val)+i*(pr*spread_val*2/levels),4) for i in range(levels+1)]
                    mid_idx = len(grids)//2
                for i,g in enumerate(grids[:-1]):
                    ng = grids[i+1]
                    if g <= pr < ng:
                        is_buy = i <= mid_idx
                        if is_buy and i not in filled:
                            filled[i] = {"price":pr,"amount":1}
                        elif not is_buy:
                            for bi in sorted(filled.keys()):
                                if bi < i:
                                    bp = filled[bi]["price"]
                                    pnl = pr - bp
                                    pnl_total += pnl; equity += pnl
                                    if equity > peak_equity: peak_equity = equity
                                    dd = peak_equity - equity
                                    if dd > max_dd: max_dd = dd
                                    if pnl > 0: wins += 1
                                    trades.append({"action":"sell","price":pr,"buy_price":bp,"pnl":round(pnl,2),"time":pt["time"]})
                                    del filled[bi]; break
            result = {
                "total_trades": len(trades),
                "win_rate": round(wins/max(len(trades),1)*100,1),
                "total_pnl": round(pnl_total,2),
                "max_drawdown": round(max_dd,2),
                "trades": trades[-20:]
            }
            self.respond(200,"application/json",json.dumps(result).encode())
        else:
            self.respond(404,"text/plain",b"Not found")
    
    def respond(self,code,ctype,body,extra_headers=None):
        self.send_response(code)
        origin = self.headers.get("Origin")
        allowed = os.environ.get("DASHBOARD_ORIGIN", "")
        if origin and allowed and origin == allowed:
            self.send_header("Access-Control-Allow-Origin", allowed)
            self.send_header("Access-Control-Allow-Headers", "Content-Type, X-API-Secret")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Vary", "Origin")
        if extra_headers:
            for key, value in extra_headers: self.send_header(key, value)
        self.send_header("Content-Type",ctype)
        self.send_header("Content-Length",str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self,format,*args): pass

if __name__=="__main__":
    port=int(os.environ.get("PORT",10000))
    if not os.environ.get("API_SECRET", ""):
        raise RuntimeError("API_SECRET must be set")
    log("Bot dashboard starting on port "+str(port))
    valid, linfo = validate_license()
    state["license_valid"] = valid
    state["license_type"] = linfo.get("type", "unknown")
    state["license_expires"] = linfo.get("expires")
    state["license_days_left"] = linfo.get("days_remaining")
    if not valid:
        error_msg = linfo.get("error", "License validation failed")
        log(f"LICENSE INVALID — {error_msg}. Live trading disabled. Paper mode only.", "WARN")
        state["paper_trading"] = True  # force paper-only
    # Seed the selected/default pair before serving the dashboard. Strategy
    # startup also refreshes this through start_bot for pair changes.
    seed_history(state.get("pair", "SOL/USDC"))
    start_background_loops()
    server=HTTPServer(("0.0.0.0",port),Handler)
    log("Ready — open your URL to control the bot")
    server.serve_forever()
