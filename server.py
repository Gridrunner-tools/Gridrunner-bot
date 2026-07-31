#!/usr/bin/env python3
"""GridRunner — FastAPI server"""
import os, json, time, asyncio
from fastapi import FastAPI, Request, HTTPException, Depends, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.middleware.cors import CORSMiddleware
from starlette.websockets import WebSocketState

# Import trading logic from main.py (no changes to trading code)
import main as bot

app = FastAPI(title="GridRunner")
templates = Jinja2Templates(directory="templates")

# Auth
API_SECRET = os.environ.get("API_SECRET", "")

def check_auth(request: Request):
    if API_SECRET and request.headers.get("X-API-Secret", "") != API_SECRET:
        raise HTTPException(status_code=401, detail="Unauthorized")

# ── Pages ────────────────────────────────────────────────────────────────
@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    return templates.TemplateResponse("dashboard.html", {
        "request": request,
        "api_secret": API_SECRET
    })

@app.get("/setup.html", response_class=HTMLResponse)
async def setup():
    try:
        return FileResponse("setup.html")
    except:
        raise HTTPException(status_code=404)

# ── Static files ─────────────────────────────────────────────────────────
@app.get("/logo.jpeg")
async def logo():
    try:
        return FileResponse("logo.jpeg", media_type="image/jpeg")
    except:
        raise HTTPException(status_code=404)

@app.get("/manifest.json")
async def manifest():
    return {
        "name": "GridRunner", "short_name": "GridRunner",
        "start_url": "/", "display": "standalone",
        "background_color": "#0a0a1a", "theme_color": "#0a0a1a",
        "icons": [{"src": "/logo.jpeg", "sizes": "512x512", "type": "image/jpeg", "purpose": "any maskable"}]
    }

@app.get("/sw.js")
async def service_worker():
    return Response(
        content="self.addEventListener('install',function(e){self.skipWaiting()});self.addEventListener('activate',function(e){e.waitUntil(clients.claim())});self.addEventListener('fetch',function(e){e.respondWith(fetch(e.request).catch(function(){return caches.match(e.request)}))})",
        media_type="application/javascript"
    )

# ── State endpoints ──────────────────────────────────────────────────────
@app.get("/debug")
async def debug():
    ph = bot.state.get("price_history", [])
    gp = bot.state.get("grid_pairs", {}).get(bot.state.get("pair", ""), {})
    filled = gp.get("filled", {})
    return {
        "pair": bot.state.get("pair", ""),
        "price": bot.state.get("price", 0),
        "running": bot.state.get("running", False),
        "strategy": bot.state.get("strategy", ""),
        "price_history_len": len(ph),
        "price_history_first_5": ph[:5] if ph else [],
        "price_history_last_5": ph[-5:] if ph else [],
        "price_history_all": ph,
        "grid_levels": gp.get("grid_levels") or gp.get("grids", []),
        "grid_mid_idx": gp.get("grid_mid_idx", 0),
        "grid_trailing_active": gp.get("grid_trailing_active", False),
        "grid_trailing_high": gp.get("grid_trailing_high", 0),
        "filled_positions": {str(k): {"price": v.get("price"), "trailing_active": v.get("trailing_active", False), "trailing_high": v.get("trailing_high", 0)} for k, v in filled.items()},
        "active_pairs": bot.state.get("active_pairs", []),
    }

@app.get("/state")
async def get_state(request: Request):
    bot.state["trades_list"] = [{"time":t["time"],"action":t["side"],"price":t["price"],"amount":t["amount"],"pnl":t.get("pnl"),"via":t.get("router",""),"pair":t.get("pair","")} for t in bot.state["trades"][-50:]]
    bot.state["positions_count"] = len(bot.state.get("positions", []))
    
    sent = request.headers.get("X-API-Secret", "")
    if API_SECRET and sent != API_SECRET:
        return {
            "price": bot.state.get("price", 0),
            "running": bot.state.get("running", False),
            "strategy": bot.state.get("strategy", ""),
            "pair": bot.state.get("pair", ""),
            "mode": bot.state.get("mode", ""),
            "paper_trading": bot.state.get("paper_trading", True)
        }
    return bot.state

@app.get("/license_status")
async def license_status():
    return {
        "valid": bot.state.get("license_valid", True),
        "type": bot.state.get("license_type", "unknown"),
        "expires": bot.state.get("license_expires"),
        "days_remaining": bot.state.get("license_days_left"),
    }

# ── Bot control ──────────────────────────────────────────────────────────
@app.get("/start")
async def start(request: Request, strategy: str = "dca", pair: str = "", mode: str = "dex", exchange: str = "", chain: str = "solana"):
    check_auth(request)
    if not pair:
        pair = bot.cfg["pair"]
    bot.start_bot(strategy, pair, mode, exchange or bot.cfg["exchange"], chain)
    return {"ok": True}

@app.get("/stop")
async def stop(request: Request):
    check_auth(request)
    bot.stop_bot()
    return {"ok": True}

@app.get("/kill")
async def kill(request: Request):
    check_auth(request)
    closed = 0; total_val = 0.0
    for pair in list(bot.state.get("active_pairs", [])):
        gs = bot.state["grid_pairs"].get(pair, {})
        filled = gs.get("filled", {})
        for idx, pos in list(filled.items()):
            if bot.place_order(pair, "sell", pos["amount"]):
                total_val += pos["amount"] * pos.get("price", 0)
                closed += 1
                del filled[idx]
    bot.state["running"] = False
    bot.state["active_pairs"] = []
    return {"closed": closed, "total_value": round(total_val, 2)}

@app.get("/pause")
async def pause(request: Request):
    check_auth(request)
    bot.state["paused"] = not bot.state["paused"]
    bot.log("Bot " + ("paused" if bot.state["paused"] else "resumed"))
    return {"paused": bot.state["paused"]}

@app.get("/toggle_paper")
async def toggle_paper(request: Request):
    check_auth(request)
    if not bot.state.get("license_valid", True) and bot.state["paper_trading"]:
        raise HTTPException(status_code=403, detail="Cannot enable live trading — invalid license")
    bot.state["paper_trading"] = not bot.state["paper_trading"]
    mode = "PAPER" if bot.state["paper_trading"] else "LIVE"
    bot.log("Switched to " + mode + " trading mode")
    return {"paper_trading": bot.state["paper_trading"]}

# ── Trading ──────────────────────────────────────────────────────────────
@app.post("/manual_trade")
async def manual_trade(request: Request):
    check_auth(request)
    data = await request.json()
    pair = data.get("pair", bot.state.get("pair", "SOL/USDC"))
    side = data.get("side", "buy")
    amount_usdc = float(data.get("amount_usdc", 10))
    
    price = bot.get_price(pair)
    if price <= 0:
        return {"ok": False, "error": "Cannot fetch price"}
    
    token = pair.split("/")[0]
    if side == "buy":
        amt = amount_usdc / price
        ok = bot.place_order(pair, "buy", amt)
        if ok:
            return {"ok": True, "amount": round(amt, 6), "price": round(price, 2)}
    else:
        amt = amount_usdc / price
        ok = bot.place_order(pair, "sell", amt)
        if ok:
            received = amount_usdc
            return {"ok": True, "amount": round(amt, 6), "price": round(price, 2), "received": round(received, 2)}
    
    return {"ok": False, "error": "Order failed"}

@app.get("/trade_log")
async def trade_log(request: Request):
    check_auth(request)
    try:
        with open(bot.TRADE_LOG, "r") as f:
            lines = f.readlines()[-100:]
        return {"trades": [json.loads(l) for l in lines if l.strip()]}
    except:
        return {"trades": []}

@app.post("/backtest")
async def backtest(request: Request):
    check_auth(request)
    try:
        data = await request.json()
    except:
        data = {}
    pair = data.get("pair", bot.state.get("pair", "SOL/USDC"))
    
    prices = []
    if bot.state.get("price_history") and len(bot.state["price_history"]) > 5:
        prices = bot.state["price_history"]
    else:
        try:
            r = bot.requests.get("https://api.kraken.com/0/public/OHLC", params={
                "pair": pair.replace("/", ""), "interval": 5
            }, timeout=10)
            ohlc = r.json().get("result", {})
            for k in ohlc:
                if k != "last":
                    prices = [{"time": int(p[0]), "value": float(p[4])} for p in ohlc[k][-200:]]
        except:
            pass
    
    if not prices or len(prices) < 5:
        return {"error": "Not enough price data"}
    
    trades = []; pnl_total = 0; wins = 0; peak_equity = 0; max_dd = 0; equity = 100
    levels = int(bot.cfg.get("grid_levels", 10))
    spread_val = bot.cfg.get("base_spread", 0.05)
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
                is_buy = i < mid_idx
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
    
    return {
        "total_trades": len(trades),
        "win_rate": round(wins/max(len(trades),1)*100,1),
        "total_pnl": round(pnl_total,2),
        "max_drawdown": round(max_dd,2),
        "trades": trades[-20:]
    }

# ── Config ───────────────────────────────────────────────────────────────
@app.post("/config")
async def save_config(request: Request):
    check_auth(request)
    data = await request.json()
    for key in ["risk_pct", "max_pos", "grid_stop_loss_pct", "trailing_pct", 
                "partial_sell_pct", "base_spread", "grid_levels"]:
        if key in data:
            bot.state["config"][key] = data[key]
    if "auto_compound" in data:
        bot.state["config"]["auto_compound"] = data["auto_compound"]
    bot.log("Config updated")
    return {"ok": True}

# ── Webhook (TradingView) ────────────────────────────────────────────────
@app.post("/webhook")
async def webhook(request: Request):
    check_auth(request)
    data = await request.json()
    signal = data.get("signal", "")
    wpair = data.get("pair", bot.state.get("pair", "SOL/USDC"))
    wprice = float(data.get("price", 0))
    
    if signal == "buy" and wprice > 0:
        gs = bot.state["grid_pairs"].get(wpair, {})
        grids = gs.get("grids", [])
        filled = gs.get("filled", {})
        mid_idx = gs.get("mid_idx", len(grids)//2) if grids else 2
        
        if not grids:
            levels = int(bot.cfg.get("grid_levels", 10))
            spread_val = bot.cfg.get("base_spread", 0.05)
            grids = [round(wprice*(1-spread_val)+i*(wprice*spread_val*2/levels),4) for i in range(levels+1)]
            mid_idx = len(grids)//2
            bot.state["grid_pairs"][wpair] = {"grids":grids,"mid_idx":mid_idx,"filled":{}}
            if wpair not in bot.state.get("active_pairs",[]):
                bot.state["active_pairs"].append(wpair)
        
        bal = bot.get_balance()
        sz = min(bal*bot.cfg["risk_pct"]/100, bot.cfg["max_pos"])/5
        amt = round(sz/wprice,6)
        if bot.place_order(wpair,"buy",amt):
            for i,g in enumerate(grids[:-1]):
                if g <= wprice < grids[i+1] and i < mid_idx and i not in filled:
                    filled[i] = {"price":wprice,"amount":amt}
                    bot.state["grid_pairs"][wpair]["filled"] = filled
                    bot.record_trade("WEBHOOK-BUY",wprice,amt, pair=wpair)
                    bot.log("[WEBHOOK] Forced buy "+wpair+" @ $"+str(round(wprice,2)))
                    break
        return {"ok": True, "pair": wpair}
    
    elif signal == "sell":
        gs = bot.state["grid_pairs"].get(wpair, {})
        filled = gs.get("filled", {})
        sold = 0
        for bi in sorted(filled.keys()):
            amt = filled[bi]["amount"]
            bp = filled[bi]["price"]
            sp = wprice if wprice > 0 else bot.get_price(wpair)
            if bot.place_order(wpair,"sell",amt):
                pnl = (sp - bp) * amt
                bot.state["pnl"] += pnl
                bot.record_trade("WEBHOOK-SELL",sp,amt,round(pnl,2), pair=wpair)
                bot.log("[WEBHOOK] Forced sell "+wpair+" @ $"+str(round(sp,2)))
                sold += 1
        bot.state["grid_pairs"][wpair]["filled"] = {}
        return {"ok": True, "pair": wpair, "closed": sold}
    
    return {"error": "signal must be buy or sell"}

# ── Token management ────────────────────────────────────────────────────
@app.post("/add_token")
async def add_token(request: Request):
    check_auth(request)
    try:
        data = await request.json()
    except Exception:
        return {"error": "Invalid JSON"}
    symbol = data.get("symbol", "").upper()
    mint = data.get("mint", "")
    pair = data.get("pair", "")
    if not symbol or not mint:
        return {"error": "Symbol and mint required"}
    bot.SOL_TOKENS[symbol] = mint
    bot.TOKEN_DECIMALS[symbol] = 6
    bot.log("Added custom token: "+symbol+" ("+mint+")")
    return {"ok": True, "symbol": symbol, "pair": pair}

# ── Debug ────────────────────────────────────────────────────────────────
@app.get("/debug_orca")
async def debug_orca(request: Request):
    check_auth(request)
    try:
        import base64 as b64
        pool = "Czfq3xZZDmsdGdUyrNLtRhGc47cXcZtLG4crryfu44zE"
        payload = {"jsonrpc":"2.0","id":1,"method":"getAccountInfo","params":[pool,{"encoding":"base64"}]}
        r = bot.requests.post(bot.SOL_RPC, json=payload, timeout=10)
        raw_b64 = r.json().get("result",{}).get("value",{}).get("data",[None])[0]
        if raw_b64:
            raw = b64.b64decode(raw_b64)
            sqrt_price = int.from_bytes(raw[65:81], "little")
            price = (sqrt_price / (2**64))**2 * (10**(6-9))
            return {"length":len(raw),"sqrt_price":sqrt_price,"price":round(price,4),"offset_65_hex":raw[65:81].hex()}
        else:
            return {"error":"no data"}
    except Exception as ex:
        return {"error": str(ex)}

# ── WebSocket for real-time updates ──────────────────────────────────────
@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    try:
        while True:
            # Send current state every 2 seconds
            bot.state["trades_list"] = [{"time":t["time"],"action":t["side"],"price":t["price"],"amount":t["amount"],"pnl":t.get("pnl"),"via":t.get("router",""),"pair":t.get("pair","")} for t in bot.state["trades"][-50:]]
            bot.state["positions_count"] = len(bot.state.get("positions", []))
            
            await websocket.send_json({
                "price": bot.state.get("price", 0),
                "running": bot.state.get("running", False),
                "strategy": bot.state.get("strategy", ""),
                "pair": bot.state.get("pair", ""),
                "balance": bot.state.get("balance", 0),
                "sol_balance": bot.state.get("sol_balance", 0),
                "pnl": bot.state.get("pnl", 0),
                "positions": len(bot.state.get("positions", [])),
                "trades_list": bot.state["trades_list"][-10:],
                "log": bot.state.get("log", [])[:10],
                "active_pairs": bot.state.get("active_pairs", []),
                "grid_pairs": {p: {
                    "grids": g.get("grids", []),
                    "mid_idx": g.get("mid_idx", 0),
                    "filled": {str(k): {"price": v.get("price"), "trailing_active": v.get("trailing_active", False)} for k, v in g.get("filled", {}).items()},
                    "trailing_sell_active": g.get("trailing_sell_active", False),
                } for p, g in bot.state.get("grid_pairs", {}).items()},
                "config": bot.state.get("config", {}),
                "paper_trading": bot.state.get("paper_trading", True),
            })
            await asyncio.sleep(2)
    except WebSocketDisconnect:
        pass
    except Exception:
        pass

# ── Startup ──────────────────────────────────────────────────────────────
@app.on_event("startup")
async def startup():
    # Validate license on startup
    valid, info = bot.validate_license()
    bot.state["license_valid"] = valid
    bot.state["license_type"] = info.get("type", "demo")
    bot.state["license_expires"] = info.get("expires")
    bot.state["license_days_left"] = info.get("days_remaining")
    
    # Start background loops
    bot.start_background_loops()
    bot.log("GridRunner FastAPI server started")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 3000)))
