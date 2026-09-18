#!/usr/bin/env python3
"""
Regression tests: the grid strategy must NEVER self-terminate on balance checks.

Owner-approved (2026-09-15): run_grid's buggy 20%-drawdown stop and the dead
-$200 daily-loss stop falsely killed the live bot after a trailing sell. Both
are deleted completely. The grid runs continuously and is only stopped by the
authoritative loop guard `while state["running"] and state["strategy"]=="grid"`
or an explicit user /stop. These tests guard against reintroduction.
"""
import sys
import types

# Ensure requests module is mocked for clean imports
sys.modules.setdefault("requests", types.SimpleNamespace())


def _run_grid_body():
    import main
    src = open(main.__file__, encoding="utf-8").read()
    start = src.index("def run_grid(")
    end = src.index("def run_scalp(")
    return src, src[start:end]


def test_authoritative_grid_loop_guard_present():
    """The grid while-loop guard must remain the ONLY loop stopper."""
    src, _ = _run_grid_body()
    assert 'while state["running"] and state["strategy"]=="grid":' in src, \
        "authoritative grid loop guard must stay intact"


def test_grid_drawdown_auto_stop_removed():
    """The 20%-drawdown balance check must be gone entirely."""
    src, _ = _run_grid_body()
    assert "DRAWDOWN STOP" not in src, "grid drawdown auto-stop must be removed"
    assert "max_drawdown_pct" not in src, "drawdown threshold config must be removed"


def test_grid_daily_loss_auto_stop_removed():
    """The -$200 daily-loss balance check must be gone entirely."""
    src, _ = _run_grid_body()
    assert "DAILY LOSS LIMIT:" not in src, "grid daily-loss auto-stop must be removed"


def test_grid_never_sets_running_false_internally():
    """run_grid must not flip state itself — no self-termination on balances."""
    _, body = _run_grid_body()
    assert 'state["running"] = False' not in body, \
        "run_grid must never set running=False internally"
    assert 'state["strategy"] = None' not in body, \
        "run_grid must never clear strategy internally"
    assert 'state["emergency_stop"] = True' not in body, \
        "run_grid must never set the emergency-stop flag"


def test_grid_peak_and_loss_stop_config_gone():
    """The now-dead stop config reads must be gone from run_grid."""
    _, body = _run_grid_body()
    assert "get(\"peak_balance\"" not in body, "peak-balance stop read must be removed"
    assert "daily_loss_limit" not in body, "daily-loss stop config must be removed"