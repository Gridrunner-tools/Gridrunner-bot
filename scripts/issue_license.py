#!/usr/bin/env python3
"""Issue one GridRunner license into the private Neon/PostgreSQL registry.

Run this only from a trusted operator environment where DATABASE_URL is set.
No license data is written to source control or a local SQLite database.
"""

import argparse
import os
import secrets
import string
import sys
from datetime import datetime, timedelta, timezone

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(SCRIPT_DIR)
sys.path.insert(0, ROOT_DIR)

from license_registry import LicenseRegistryUnavailable, add_license

ALPHABET = string.ascii_uppercase + string.digits


def generate_key() -> str:
    return "LB-" + "-".join(
        "".join(secrets.choice(ALPHABET) for _ in range(4)) for _ in range(3)
    )


def expiry_for(days: int) -> datetime | None:
    return None if days == 0 else datetime.now(timezone.utc) + timedelta(days=days)


def main() -> None:
    parser = argparse.ArgumentParser(description="Issue a GridRunner license in the private registry")
    parser.add_argument("--email", required=True, help="Customer email address")
    parser.add_argument("--phone", default=None, help="Customer phone number (optional)")
    parser.add_argument("--type", default="full", choices=["full", "trial"], help="License type (default: full)")
    parser.add_argument("--days", type=int, default=365, help="Validity in days; use 0 for no expiry (default: 365)")
    args = parser.parse_args()
    if args.days < 0:
        parser.error("--days must be zero or positive")

    key = generate_key()
    expires = expiry_for(args.days)
    try:
        add_license(key, args.type, expires, customer_email=args.email, phone=args.phone)
    except LicenseRegistryUnavailable as exc:
        parser.error(str(exc))

    print(f"License issued: {key}")
    print(f"Type: {args.type} | Expires: {expires.date().isoformat() if expires else 'never'}")
    print(f"Customer: {args.email}")
    print("Deliver the key through the approved customer channel; do not commit or publish it.")


if __name__ == "__main__":
    main()
