#!/usr/bin/env python3
"""Record a paid purchase and issue a GridRunner key in the private registry.

Run this only from a trusted operator environment where DATABASE_URL is set.
"""

import argparse
import os
import sys
from datetime import datetime, timedelta, timezone

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(SCRIPT_DIR)
sys.path.insert(0, ROOT_DIR)

from license_registry import LicenseRegistryUnavailable, add_license
from issue_license import generate_key


def main() -> None:
    parser = argparse.ArgumentParser(description="Process a purchase and issue a private-registry license")
    parser.add_argument("--email", required=True, help="Customer email address")
    parser.add_argument("--phone", default=None, help="Customer phone number (optional)")
    parser.add_argument("--stripe-id", default=None, help="Stripe session ID")
    parser.add_argument("--sol-tx", default=None, help="Solana transaction signature")
    parser.add_argument("--days", type=int, default=365, help="License validity in days; use 0 for no expiry")
    parser.add_argument("--trial", action="store_true", help="Issue a seven-day trial key")
    args = parser.parse_args()
    if args.days < 0:
        parser.error("--days must be zero or positive")

    key_type = "trial" if args.trial else "full"
    days = 7 if args.trial else args.days
    expires = None if days == 0 else datetime.now(timezone.utc) + timedelta(days=days)
    key = generate_key()
    try:
        add_license(
            key,
            key_type,
            expires,
            customer_email=args.email,
            phone=args.phone,
            stripe_session_id=args.stripe_id,
            sol_tx_signature=args.sol_tx,
        )
    except LicenseRegistryUnavailable as exc:
        parser.error(str(exc))

    print(f"LICENSE ISSUED: {key}")
    print(f"Type: {key_type} | Expires: {expires.date().isoformat() if expires else 'never'}")
    print(f"Customer: {args.email}")
    print("Deliver the key through the approved customer channel; do not commit or publish it.")


if __name__ == "__main__":
    main()
