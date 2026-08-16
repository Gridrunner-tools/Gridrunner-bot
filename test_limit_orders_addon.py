"""Limit Orders add-on: per-install license gating, order lifecycle, persistence.

The gate must use the install's OWN LICENSE_KEY (mirroring the main product
gate) so one customer's license never unlocks another install. Tests exercise
both the lookup call and the real SQL predicate via a fake psycopg driver.
"""
import os
from unittest.mock import patch

import license_registry
import limit_orders_addon
from license_registry import LicenseRegistryUnavailable
from limit_orders_addon import LimitOrdersAddon


def test_order_lifecycle_and_persistence(tmp_path):
    p = tmp_path / "s.json"
    with patch.dict(os.environ, {"LICENSE_KEY": "LB-AAAA-BBBB-CCCC"}), patch.object(
        limit_orders_addon, "lookup_license", return_value={"type": "full"}
    ):
        a = LimitOrdersAddon(p)
        order = a.create_order("SOL/USDC", "buy", 1, 100)
        assert order["status"] == "open" and order["mode"] == "paper"
        assert a.cancel(order["id"])["status"] == "cancelled"
        b = LimitOrdersAddon(p)
        assert b.list_orders()[0]["status"] == "cancelled"


def test_gate_uses_installs_own_license_key(tmp_path):
    with patch.dict(os.environ, {"LICENSE_KEY": "LB-INSTALL-OWN-KEY"}), patch.object(limit_orders_addon, "lookup_license", return_value={"type": "full"}) as lookup:
        a = LimitOrdersAddon(tmp_path / "s.json")
        assert a.status()["valid"] is True
        lookup.assert_called_once_with("LB-INSTALL-OWN-KEY", product="gridrunner")


def test_gated_on_invalid_install_license(tmp_path):
    with patch.dict(os.environ, {"LICENSE_KEY": "LB-INVALID-KEY"}), patch.object(limit_orders_addon, "lookup_license", return_value=None) as lookup:
        a = LimitOrdersAddon(tmp_path / "s.json")
        assert a.status()["valid"] is False
        try:
            a.create_order("SOL/USDC", "buy", 1)
            raise AssertionError("unlicensed install created an order")
        except PermissionError:
            pass
        lookup.assert_called_with("LB-INVALID-KEY", product="gridrunner")


def test_no_license_key_never_queries(tmp_path):
    with patch.dict(os.environ, {}, clear=True), patch.object(limit_orders_addon, "lookup_license", return_value={"type": "full"}) as lookup:
        a = LimitOrdersAddon(tmp_path / "s.json")
        assert a.status()["valid"] is False
        lookup.assert_not_called()


def test_fails_closed_when_registry_unavailable(tmp_path):
    def boom(*args, **kwargs):
        raise LicenseRegistryUnavailable("registry down")

    with patch.dict(os.environ, {"LICENSE_KEY": "LB-AAAA-BBBB-CCCC"}), patch.object(limit_orders_addon, "lookup_license", side_effect=boom):
        a = LimitOrdersAddon(tmp_path / "s.json")
        try:
            a.status()
            raise AssertionError("registry failure did not fail closed")
        except LicenseRegistryUnavailable:
            pass


class _FakeCursor:
    def __init__(self, row):
        self.row = row
        self.query = None
        self.params = None

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def execute(self, query, params):
        self.query = query
        self.params = params

    def fetchone(self):
        return self.row


class _FakeConn:
    def __init__(self, cursor):
        self._cursor = cursor

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def cursor(self):
        return self._cursor


class _FakePsycopg:
    def __init__(self, cursor):
        self.cursor = cursor

    def connect(self, url, connect_timeout=10):
        self.url = url
        return _FakeConn(self.cursor)


def test_sql_predicate_scopes_to_install_key_and_gridrunner_product(tmp_path):
    fake = _FakePsycopg(_FakeCursor(("full", None)))
    with patch.dict(os.environ, {"LICENSE_KEY": "LB-ABCD-EFGH-IJKL", "DATABASE_URL": "postgresql://x"}), patch.object(
        license_registry, "_driver", return_value=fake
    ):
        a = LimitOrdersAddon(tmp_path / "s.json")
        assert a._valid() is True
        q = fake.cursor.query
        assert "license_key = %s" in q and "product = %s" in q and "is_active = TRUE" in q
        assert fake.cursor.params == ("LB-ABCD-EFGH-IJKL", "gridrunner")
        assert "LIMIT 1" in q
