"""Standalone full-suite runner (pytest-equivalent, no pytest dependency)."""
import sys, tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
failed = []
def run(modname, needs_tmp=False, needs_monkey=False):
    m = __import__(modname)
    tests = [n for n in dir(m) if n.startswith('test_') and getattr(getattr(m, n), '__module__', '') == modname]
    for name in tests:
        fn = getattr(m, name)
        try:
            if needs_monkey:
                from types import SimpleNamespace
                monkey = SimpleNamespace(setenv=__import__('os').environ.__setitem__)
                fn(monkey)
            elif needs_tmp:
                with tempfile.TemporaryDirectory() as d:
                    fn(Path(d))
            else:
                fn()
            print(f"PASS  {modname}.{name}")
        except Exception as e:
            failed.append(f"{modname}.{name}: {type(e).__name__}: {e}")
            print(f"FAIL  {modname}.{name}: {type(e).__name__}: {e}")
run('test_license_registry', needs_tmp=True)
run('test_limit_orders_addon', needs_tmp=True)
run('test_license_issuance', needs_monkey=True)
run('test_limit_order_config')
run('test_config_controls')
run('test_strategy')
run('test_buy_on_way_up')
run('test_grid_pullback_sell')
run('test_multi_grid_dashboard')
run('test_pair_chart_history')
run('test_limit_order_detail_card')
run('test_limit_order_pnl')
run('test_grid_base_buy')
run('test_grid_zone_spec')
run('test_asymmetric_grid_geometry')
run('test_dashboard_grid_zones')
run('test_base_buy_safety_fixes')
run('test_grid_stuck_running_fixes')
run('test_ai_trading')
run('test_concurrent_strategies')
run('test_paper_mode_defaults')
run('test_strategy_log_tail')
run('test_state_serialization')
if failed:
    print(f"\n{len(failed)} FAILURE(S)"); sys.exit(1)
print("\nALL SUITES PASS")
