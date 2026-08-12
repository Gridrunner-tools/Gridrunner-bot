"""Isolated Limit Orders add-on: entitlement and mock order registry.
This module deliberately owns its state and never imports the grid bot state.
"""
from __future__ import annotations
import json, os, secrets, time
from pathlib import Path

class LimitOrdersAddon:
    def __init__(self, state_file: str = ".limit_orders_addon.json", valid_keys=None):
        self.state_file = Path(state_file)
        env = os.environ.get("LIMIT_ORDERS_LICENSE_KEYS", "")
        self.valid_keys = set(valid_keys or [k.strip() for k in env.split(",") if k.strip()])
        self.activation = self._load().get("activation")
        self.orders = self._load().get("orders", {})
    def _load(self):
        try: return json.loads(self.state_file.read_text())
        except (OSError, ValueError): return {}
    def _save(self):
        payload = {"activation": self.activation, "orders": self.orders}
        tmp = self.state_file.with_suffix(self.state_file.suffix + ".tmp")
        tmp.write_text(json.dumps(payload)); os.replace(tmp, self.state_file)
    def activate(self, key: str):
        key = (key or "").strip()
        match = key in self.valid_keys
        if not match:
            return {"valid": False, "error": "Invalid Limit Orders add-on license"}
        self.activation = {"product": "limit_orders", "key_id": secrets.token_hex(6), "activated_at": int(time.time()), "valid": True}
        self._save()
        return {"valid": True, **self.activation}
    def status(self):
        return self.activation or {"product": "limit_orders", "valid": False}
    def create_order(self, pair, side, amount, price=None, mode="paper"):
        if not self.status().get("valid"): raise PermissionError("Limit Orders add-on is not activated")
        oid = secrets.token_hex(8)
        self.orders[oid] = {"id": oid, "pair": pair, "side": side, "amount": float(amount), "price": price, "mode": mode, "status": "open", "pnl": None, "created_at": int(time.time())}
        self._save(); return self.orders[oid].copy()
    def cancel(self, order_id):
        order = self.orders.get(order_id)
        if not order: return None
        order["status"] = "cancelled"; order["updated_at"] = int(time.time()); self._save(); return order.copy()
    def list_orders(self): return [o.copy() for o in self.orders.values()]
