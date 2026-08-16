from limit_orders_addon import LimitOrdersAddon
def test_locked_until_separate_key_activation(tmp_path):
 a=LimitOrdersAddon(tmp_path/'s',valid_keys={'K'}); assert not a.status()['valid']; assert a.activate('X')['valid'] is False; assert a.activate('K')['valid']
def test_registry_isolated_lifecycle_and_persistence(tmp_path):
 p=tmp_path/'s'; a=LimitOrdersAddon(p,valid_keys={'K'}); a.activate('K'); o=a.create_order('SOL/USDC','buy',1,100); assert a.cancel(o['id'])['status']=='cancelled'; assert LimitOrdersAddon(p,valid_keys={'K'}).list_orders()[0]['status']=='cancelled'
