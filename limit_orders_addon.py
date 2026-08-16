"""Limit Orders entitlement backed by Neon, with independent paper registry."""
import json,os,secrets,time
from pathlib import Path
from license_registry import _database_url,_driver,LicenseRegistryUnavailable
class LimitOrdersAddon:
 def __init__(self,state_file='.limit_orders_addon.json',valid_keys=None): self.state_file=Path(state_file); self.valid_keys=set(valid_keys or []); self.activation=self._load().get('activation'); self.orders=self._load().get('orders',{})
 def _load(self):
  try:return json.loads(self.state_file.read_text())
  except (OSError,ValueError):return {}
 def _save(self):
  t=self.state_file.with_suffix('.tmp'); t.write_text(json.dumps({'activation':self.activation,'orders':self.orders})); os.replace(t,self.state_file)
 def _valid(self,key):
  if not key:return False
  if key in self.valid_keys:return True
  try:
   with _driver().connect(_database_url(),connect_timeout=10) as c:
    with c.cursor() as cur: cur.execute("SELECT 1 FROM licenses WHERE license_key=%s AND product='limit_orders' AND is_active=TRUE AND (expires_at IS NULL OR expires_at>NOW())",(key,)); return cur.fetchone() is not None
  except Exception as e: raise LicenseRegistryUnavailable('License registry unavailable') from e
 def status(self,key=None): return {'product':'limit_orders','valid':self.activation is not None or self._valid(key)}
 def activate(self,key):
  ok=self._valid(key)
  if ok:self.activation={'product':'limit_orders','key_id':key,'activated_at':int(time.time()),'valid':True}; self._save()
  return self.status(key)
 def create_order(self,pair,side,amount,price=None,mode='paper'):
  if not self.status().get('valid'):raise PermissionError('Limit Orders add-on is not activated')
  oid=secrets.token_hex(8); self.orders[oid]={'id':oid,'pair':pair,'side':side,'amount':float(amount),'price':price,'mode':mode,'status':'open','pnl':None,'created_at':int(time.time())}; self._save(); return self.orders[oid].copy()
 def cancel(self,oid):
  o=self.orders.get(oid)
  if not o:return None
  o['status']='cancelled'; o['updated_at']=int(time.time()); self._save(); return o.copy()
 def list_orders(self):return [o.copy() for o in self.orders.values()]
