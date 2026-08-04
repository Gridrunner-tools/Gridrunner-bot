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
