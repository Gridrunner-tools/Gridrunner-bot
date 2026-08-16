"""Private PostgreSQL/Neon license registry access.

This module deliberately reads only ``DATABASE_URL`` from the environment and
never logs connection strings or license keys. It is shared by the runtime
validator and the operator-only license issuance scripts.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any

DATABASE_URL_ENV = "DATABASE_URL"


class LicenseRegistryUnavailable(RuntimeError):
    """The private registry cannot be consulted right now."""


def _database_url(database_url: str | None = None) -> str:
    value = database_url if database_url is not None else os.environ.get(DATABASE_URL_ENV, "")
    value = value.strip()
    if not value:
        raise LicenseRegistryUnavailable("DATABASE_URL is not configured")
    return value


def _driver():
    try:
        import psycopg
    except ImportError as exc:
        raise LicenseRegistryUnavailable("PostgreSQL driver is unavailable") from exc
    return psycopg


def lookup_license(license_key: str, database_url: str | None = None, product: str | None = None) -> dict[str, Any] | None:
    """Return the active license record for *license_key*, or ``None``.

    *product* optionally scopes the lookup to a product value (e.g.
    'gridrunner'); None means any product. Registry connectivity/configuration
    problems raise :class:`LicenseRegistryUnavailable`; callers must fail
    closed unless a previously verified grace cache is still valid.
    """
    url = _database_url(database_url)
    psycopg = _driver()
    try:
        with psycopg.connect(url, connect_timeout=10) as conn:
            with conn.cursor() as cursor:
                if product is None:
                    cursor.execute(
                        """
                        SELECT license_type, expires_at
                        FROM licenses
                        WHERE license_key = %s AND is_active = TRUE
                        LIMIT 1
                        """,
                        (license_key,),
                    )
                else:
                    cursor.execute(
                        """
                        SELECT license_type, expires_at
                        FROM licenses
                        WHERE license_key = %s AND product = %s AND is_active = TRUE
                        LIMIT 1
                        """,
                        (license_key, product),
                    )
                row = cursor.fetchone()
    except Exception as exc:
        # Do not include exception details: database drivers often echo the URL.
        raise LicenseRegistryUnavailable("License registry is unavailable") from exc

    if row is None:
        return None
    license_type, expires_at = row
    if expires_at is not None and expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    return {
        "type": license_type or "full",
        "expires": expires_at.isoformat() if expires_at else None,
    }


def add_license(
    license_key: str,
    license_type: str,
    expires_at: datetime | None,
    *,
    customer_email: str | None = None,
    phone: str | None = None,
    stripe_session_id: str | None = None,
    sol_tx_signature: str | None = None,
    database_url: str | None = None,
) -> None:
    """Insert a newly issued license into the private registry.

    This is intended for trusted operator scripts, never the public runtime.
    """
    url = _database_url(database_url)
    psycopg = _driver()
    try:
        with psycopg.connect(url, connect_timeout=10) as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO licenses (
                        license_key, license_type, expires_at, customer_email,
                        phone, stripe_session_id, sol_tx_signature
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        license_key,
                        license_type,
                        expires_at,
                        customer_email,
                        phone,
                        stripe_session_id,
                        sol_tx_signature,
                    ),
                )
            conn.commit()
    except Exception as exc:
        # Do not include exception details: they can contain DATABASE_URL.
        raise LicenseRegistryUnavailable("License registry write failed") from exc
