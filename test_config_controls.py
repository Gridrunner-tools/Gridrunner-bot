"""Regression checks for dashboard config controls and live runtime wiring."""
from pathlib import Path

SOURCE = Path(__file__).with_name("main.py").read_text()


def test_render_controls_are_exposed_and_submitted():
    for dom_id in ("cfg-risk", "cfg-maxpos", "cfg-maxloss", "cfg-takeprofit", "cfg-arbspread", "cfg-stoploss", "cfg-trailing", "cfg-partial", "cfg-spread", "cfg-compound"):
        assert f'id="{dom_id}"' in SOURCE
    for key in ("risk_pct", "max_pos", "max_loss", "take_profit", "min_arb_spread", "grid_stop_loss_pct", "trailing_pct", "partial_sell_pct", "base_spread", "auto_compound"):
        assert key in SOURCE


def test_config_handler_updates_runtime_keys():
    handler = SOURCE[SOURCE.index('if path == "/config":'):SOURCE.index('elif path == "/trade_log":')]
    for key in ("risk_pct", "max_pos", "max_loss", "take_profit", "min_arb_spread", "grid_stop_loss_pct", "trailing_pct", "partial_sell_pct", "base_spread", "auto_compound"):
        assert f'"{key}"' in handler
    # Every newly mutable control is consumed by an active trading loop.
    for key in ("max_loss", "take_profit", "min_arb_spread"):
        assert f'cfg["{key}"]' in SOURCE


if __name__ == "__main__":
    test_render_controls_are_exposed_and_submitted()
    test_config_handler_updates_runtime_keys()
    print("All config-control regression tests passed.")

def test_security_regressions_present():
    assert "import os, json, time, hmac, hashlib, threading, requests, logging, base64, random, string, math" in SOURCE
    # Credentials are read at point of use, not retained in module-level cfg.
    cfg_block = SOURCE[SOURCE.index("cfg = {"):SOURCE.index("import threading", SOURCE.index("cfg = {"))]
    for name in ("api_key", "api_secret", "private_key", "sol_key", "license_key", "tg_bot_token", "tg_chat_id"):
        assert f'"{name}"' not in cfg_block
    assert 'requests.post("https://api.telegram.org/bot" + token + "/sendMessage"' in SOURCE
    assert 'params={"bot": token}' not in SOURCE
    assert 'if not math.isfinite(val)' in SOURCE

def test_config_bounds_reject_non_finite_values():
    # Ensure the runtime path uses finite and explicit bounds checks.
    handler = SOURCE[SOURCE.index('if path == "/config":'):SOURCE.index('elif path == "/trade_log":')]
    assert 'math.isfinite(val)' in handler
    assert '"partial_sell_pct": (1, 99)' in handler
