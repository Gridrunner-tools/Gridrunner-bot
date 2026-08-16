"""Limit Orders order registry, gated on the main GridRunner license.

Limit Orders ships bundled with the GridRunner license (owner-approved): there
is no separate key or activation. The registry holds an independent paper order
store for limit orders; order creation is permitted only while a valid
GridRunner license exists in the private registry. Registry unavailability
fails closed (PermissionError) so a network blip can never open the gate.
"""
import json
import os
import secrets
import time
from pathlib import Path

from license_registry import _database_url, _driver, LicenseRegistryUnavailable


class LimitOrdersAddon:
    def __init__(self, state_file=".limit_orders_addon.json", valid_keys=None):
        self.state_file = Path(state_file)
        self.orders = self._load().get("orders", {})

    def _load(self):
        try:
            return json.loads(self.state_file.read_text())
        except (OSError, ValueError):
            return {}

    def _save(self):
        tmp = self.state_file.with_suffix(".tmp")
        tmp.write_text(json.dumps({"orders": self.orders}))
        os.replace(tmp, self.state_file)

    def _valid(self):
        """True only when an active GridRunner license exists in the registry."""
        try:
            with _driver().connect(_database_url(), connect_timeout=10) as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT 1 FROM licenses WHERE product='gridrunner' AND is_active=TRUE "
                        "AND (expires_at IS NULL OR expires_at > NOW()) LIMIT 1"
                    )
                    return cur.fetchone() is not None
        except Exception as exc:
            raise LicenseRegistryUnavailable("License registry unavailable") from exc

    def status(self):
        return {"product": "limit_orders", "valid": self._valid()}

    def create_order(self, pair, side, amount, price=None, mode="paper"):
        if not self._valid():
            raise PermissionError("Limit Orders require a valid GridRunner license")
        oid = secrets.token_hex(8)
        self.orders[oid] = {
            "id": oid,
            "pair": pair,
            "side": side,
            "amount": float(amount),
            "price": price,
            "mode": mode,
            "status": "open",
            "pnl": None,
            "created_at": int(time.time()),
        }
        self._save()
        return self.orders[oid].copy()

    def cancel(self, oid):
        order = self.orders.get(oid)
        if not order:
            return None
        order["status"] = "cancelled"
        order["updated_at"] = int(time.time())
        self._save()
        return order.copy()

    def list_orders(self):
        return [o.copy() for o in self.orders.values()]
