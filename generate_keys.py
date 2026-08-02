#!/usr/bin/env python3
"""Generate and register licenses in the private Neon/PostgreSQL registry.

This operator-only utility replaces the former keys.json export workflow.
Run it only where DATABASE_URL is set; it never writes a key file.
"""

import argparse
import os
import sys
from datetime import datetime, timedelta, timezone

ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT_DIR)

from license_registry import LicenseRegistryUnavailable, add_license
from scripts.issue_license import generate_key


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate licenses in the private registry")
    parser.add_argument("--count", type=int, default=1, help="Number of keys to generate (default: 1)")
    parser.add_argument("--trial-days", type=int, default=7, help="Trial duration in days (default: 7)")
    parser.add_argument("--full", action="store_true", help="Generate full licenses instead of trial licenses")
    parser.add_argument("--days", type=int, default=365, help="Full-license duration; use 0 for no expiry")
    args = parser.parse_args()
    if args.count < 1 or args.trial_days < 1 or args.days < 0:
        parser.error("--count and --trial-days must be positive; --days must be zero or positive")

    key_type = "full" if args.full else "trial"
    duration = args.days if args.full else args.trial_days
    expires = None if duration == 0 else datetime.now(timezone.utc) + timedelta(days=duration)
    try:
        for _ in range(args.count):
            key = generate_key()
            add_license(key, key_type, expires)
            print(f"{key} ({key_type}, expires {expires.date().isoformat() if expires else 'never'})")
    except LicenseRegistryUnavailable as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    main()
