"""Regression checks for dashboard config controls and live runtime wiring."""
import re
from pathlib import Path

SOURCE = Path(__file__).with_name("main.py").read_text()


def test_render_controls_are_exposed_and_submitted():
    for dom_id in ("cfg-risk", "cfg-maxpos", "cfg-maxloss", "cfg-takeprofit", "cfg-arbspread", "cfg-trailing", "cfg-partial", "cfg-spread", "cfg-compound"):
        assert f'id="{dom_id}"' in SOURCE
    for key in ("risk_pct", "max_pos", "max_loss", "take_profit", "min_arb_spread", "trailing_pct", "partial_sell_pct", "base_spread", "auto_compound"):
        assert key in SOURCE


def test_config_handler_updates_runtime_keys():
    handler = SOURCE[SOURCE.index('if path == "/config":'):SOURCE.index('elif path == "/trade_log":')]
    for key in ("risk_pct", "max_pos", "max_loss", "take_profit", "min_arb_spread", "trailing_pct", "partial_sell_pct", "base_spread", "auto_compound"):
        assert f'"{key}"' in handler
    # Every newly mutable control is consumed by an active trading loop.
    for key in ("max_loss", "take_profit", "min_arb_spread"):
        assert f'cfg["{key}"]' in SOURCE


def test_dead_grid_stop_loss_control_is_gone():
    """The grid never sells at a loss (owner rule), so its Stop Loss (%) control
    was dead weight: the field must be gone AND every JS reference to it removed,
    otherwise a dangling getElementById would throw and blank the whole dashboard.
    The shared cfg["stop_loss"] used by the non-grid loops must stay intact."""
    assert 'id="cfg-stoploss"' not in SOURCE, "dead Stop Loss (%) field must be removed"
    assert "cfg-stoploss" not in SOURCE, "no JS may reference the removed field"
    assert "grid_stop_loss_pct" not in SOURCE, "dead grid stop-loss config plumbing must be gone"
    # Non-grid stop-loss behaviour is untouched.
    handler = SOURCE[SOURCE.index('if path == "/config":'):SOURCE.index('elif path == "/trade_log":')]
    assert '"stop_loss"' in handler and '"stop_loss": (0, 100)' in handler
    assert 'cfg["stop_loss"]' in SOURCE


def test_every_cfg_dom_id_reference_exists():
    """No dangling getElementById("cfg-...") — that throws and kills the script."""
    ids = set(re.findall(r'id="(cfg-[a-z-]+)"', SOURCE))
    refs = set(re.findall(r'"(cfg-[a-z-]+)"', SOURCE))
    assert refs, "expected cfg-* references in the dashboard script"
    missing = refs - ids
    assert not missing, "JS references removed config field(s): %s" % sorted(missing)


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


if __name__ == "__main__":
    test_render_controls_are_exposed_and_submitted()
    test_config_handler_updates_runtime_keys()
    test_dead_grid_stop_loss_control_is_gone()
    test_every_cfg_dom_id_reference_exists()
    test_security_regressions_present()
    test_config_bounds_reject_non_finite_values()
    print("All config-control regression tests passed.")
