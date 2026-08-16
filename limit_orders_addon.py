"""Limit Orders add-on entitlement backed by the private Neon registry."""
import os
from license_registry import _database_url, _driver, LicenseRegistryUnavailable
class LimitOrdersAddon:
 def __init__(self): pass
 def status(self, key=None): return {'product':'limit_orders','valid': self._valid(key)}
 def _valid(self,key):
  if not key: return False
  try:
   with _driver().connect(_database_url(),connect_timeout=10) as c:
    with c.cursor() as cur:
     cur.execute("SELECT 1 FROM licenses WHERE license_key=%s AND product='limit_orders' AND is_active=TRUE AND (expires_at IS NULL OR expires_at>NOW())",(key,)); return cur.fetchone() is not None
  except Exception as exc: raise LicenseRegistryUnavailable('License registry unavailable') from exc
 def activate(self,key): return self.status(key)
