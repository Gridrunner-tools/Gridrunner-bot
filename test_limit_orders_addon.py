"""Limit Orders add-on: registry-backed gating, order lifecycle, persistence."""
from unittest.mock import patch
import limit_orders_addon
from limit_orders_addon import LimitOrdersAddon
from license_registry import LicenseRegistryUnavailable


def test_order_lifecycle_and_persistence(tmp_path):
    p = tmp_path / "s.json"
    with patch.object(limit_orders_addon.LimitOrdersAddon, "_valid", return_value=True):
        a = LimitOrdersAddon(p)
        order = a.create_order("SOL/USDC", "buy", 1, 100)
        assert order["status"] == "open" and order["mode"] == "paper"
        assert a.cancel(order["id"])["status"] == "cancelled"
        b = LimitOrdersAddon(p)
        assert b.list_orders()[0]["status"] == "cancelled"


def test_gated_on_valid_license(tmp_path):
    with patch.object(limit_orders_addon.LimitOrdersAddon, "_valid", return_value=False):
        a = LimitOrdersAddon(tmp_path / "s.json")
        assert a.status()["valid"] is False
        try:
            a.create_order("SOL/USDC", "buy", 1)
            raise AssertionError("locked add-on created an order")
        except PermissionError:
            pass


def test_fails_closed_when_registry_unavailable(tmp_path):
    def boom(self):
        raise LicenseRegistryUnavailable("registry down")

    with patch.object(limit_orders_addon.LimitOrdersAddon, "_valid", new=boom):
        a = LimitOrdersAddon(tmp_path / "s.json")
        try:
            a.status()
            raise AssertionError("registry failure did not fail closed")
        except LicenseRegistryUnavailable:
            pass
