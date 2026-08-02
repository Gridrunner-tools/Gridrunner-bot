# Private Neon License Registry

GridRunner validates paid licenses against the private PostgreSQL registry using
`DATABASE_URL`. It does **not** fetch `keys.json` or use a public key list.

## One-time schema setup

From a trusted operator machine or a secure Render shell, with `DATABASE_URL`
already set there, run:

```bash
psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f db/license_registry.sql
```

Do not commit the connection string, generated keys, exports, or database dumps.

## Seed / issue a license

After the schema exists, issue the first license (and every subsequent purchase)
from a trusted environment. Every deployed installation must receive its own newly
issued unique `LICENSE_KEY`; never reuse a key across customers, Render services,
or paper/live environments. The registry's unique constraint rejects duplicate keys,
but operators must still maintain the installation-to-key record outside this repo.

```bash
python scripts/issue_license.py --email customer@example.com --type full --days 365
```

The script generates a key and inserts it directly into `licenses` through
`DATABASE_URL`. It does not write `keys.json`, SQLite files, or customer data to
the repository. For a recorded Stripe/Solana purchase, use:

```bash
python scripts/process_purchase.py --email customer@example.com --stripe-id <session-id>
```

Keep the generated key in the approved customer-delivery channel; it is not a
source-control artifact.

## Render configuration

Set `DATABASE_URL` as a secret environment variable on the Render service. The
runtime needs no new public URL and does not need `LICENSE_URL`. `LICENSE_KEY`
remains the per-installation key.

## Availability and safety behavior

The validator queries an active license with a parameterized SQL statement. If
the database cannot be reached, it may use a matching **previously successful**
local validation cache for up to 48 hours. With no fresh registry result and no
valid cache, validation fails closed: live trading is disabled and the existing
paper-mode safeguard remains active. Missing or invalid `DATABASE_URL` is never
treated as a valid license.
