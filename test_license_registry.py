"""Offline tests for the private registry validator.

These tests fake the psycopg driver and never open a database connection.
"""

import os
import sys
import tempfile
import types
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

# main.py only needs requests for its optional trial-time lookup. Stub it so these
# offline tests do not install or use any integration dependency.
sys.modules.setdefault("requests", types.ModuleType("requests"))

import license_registry
import main


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


class _FakeConnection:
    def __init__(self, cursor):
        self._cursor = cursor

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def cursor(self):
        return self._cursor


class _FakePsycopg:
    def __init__(self, row):
        self.cursor = _FakeCursor(row)
        self.url = None
        self.timeout = None

    def connect(self, url, connect_timeout):
        self.url = url
        self.timeout = connect_timeout
        return _FakeConnection(self.cursor)


class LicenseRegistryTests(unittest.TestCase):
    def test_lookup_uses_parameterized_private_registry_query(self):
        driver = _FakePsycopg(("full", datetime(2030, 1, 1, tzinfo=timezone.utc)))
        with patch.dict(sys.modules, {"psycopg": driver}):
            result = license_registry.lookup_license("LB-ABCD-EFGH-IJKL", "postgresql://example")

        self.assertEqual(result["type"], "full")
        self.assertEqual(result["expires"], "2030-01-01T00:00:00+00:00")
        self.assertIn("license_key = %s", driver.cursor.query)
        self.assertEqual(driver.cursor.params, ("LB-ABCD-EFGH-IJKL",))
        self.assertEqual(driver.timeout, 10)

    def test_registry_error_never_echoes_connection_string(self):
        class BrokenDriver:
            def connect(self, *_args, **_kwargs):
                raise RuntimeError("connection failed for postgresql://private.invalid/license")

        with patch.dict(sys.modules, {"psycopg": BrokenDriver()}):
            with self.assertRaises(license_registry.LicenseRegistryUnavailable) as raised:
                license_registry.lookup_license("LB-ABCD-EFGH-IJKL", "postgresql://private.invalid/license")
        self.assertEqual(str(raised.exception), "License registry is unavailable")
        self.assertNotIn("private.invalid", str(raised.exception))

    def test_missing_database_url_is_not_a_valid_license(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(license_registry.LicenseRegistryUnavailable, "DATABASE_URL is not configured"):
                license_registry.lookup_license("LB-ABCD-EFGH-IJKL")


class ValidatorFallbackTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.original_cache = main.LICENSE_CACHE_FILE
        main.LICENSE_CACHE_FILE = os.path.join(self.tempdir.name, "license-cache.json")

    def tearDown(self):
        main.LICENSE_CACHE_FILE = self.original_cache
        self.tempdir.cleanup()

    def test_registry_outage_uses_only_matching_recent_cache(self):
        key = "LB-ABCD-EFGH-IJKL"
        cached_info = {"valid": True, "type": "full", "expires": None, "days_remaining": None}
        main._cache_write({"key": key, "last_checked": datetime.now(timezone.utc).isoformat(), "info": cached_info})
        with patch.dict(os.environ, {"LICENSE_KEY": key}, clear=False), patch.object(
            main, "lookup_license", side_effect=main.LicenseRegistryUnavailable("unavailable")
        ):
            valid, info = main.validate_license()
        self.assertTrue(valid)
        self.assertEqual(info, cached_info)

    def test_registry_outage_without_cache_fails_closed(self):
        with patch.dict(os.environ, {"LICENSE_KEY": "LB-ABCD-EFGH-IJKL"}, clear=False), patch.object(
            main, "lookup_license", side_effect=main.LicenseRegistryUnavailable("unavailable")
        ):
            valid, info = main.validate_license()
        self.assertFalse(valid)
        self.assertEqual(info["error"], "Cannot reach license registry")


if __name__ == "__main__":
    unittest.main()
