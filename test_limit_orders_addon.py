from pathlib import Path
from limit_orders_addon import LimitOrdersAddon

def test_locked_until_separate_key_activation(tmp_path):
    a=LimitOrdersAddon(tmp_path/'state.json', valid_keys={'LO-VALID'})
    assert not a.status()['valid']
    try: a.create_order('SOL/USDC','buy',10)
    except PermissionError: pass
    else: raise AssertionError('locked add-on created order')
    assert a.activate('GRID-KEY')['valid'] is False
    assert a.activate('LO-VALID')['valid'] is True

def test_registry_isolated_lifecycle_and_persistence(tmp_path):
    p=tmp_path/'state.json'; a=LimitOrdersAddon(p, valid_keys={'K'}); a.activate('K')
    order=a.create_order('SOL/USDC','buy',10,price=100)
    assert order['status']=='open' and order['mode']=='paper'
    assert a.cancel(order['id'])['status']=='cancelled'
    b=LimitOrdersAddon(p, valid_keys={'K'}); assert b.list_orders()[0]['status']=='cancelled'
