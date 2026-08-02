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

## Lost-key recovery (operator-only)

A lost key is recovered by an operator; it is not reissued automatically by a
public endpoint. This workflow preserves the one-key-per-installation rule:

1. **Open a private support case.** Record the customer's verified account or
   purchase details and the exact installation identifier (for example, the
   Render service ID or an internal install ID). Do not ask the customer to
   post a key, database URL, wallet secret, or other credential in a public
   issue, chat, or form.
2. **Verify identity and ownership before lookup.** Use the approved private
   support channel and independently verify the customer's identity and
   ownership of that purchase/install using records available to the operator
   (such as the matching purchase record plus a previously verified account
   detail). An install identifier alone is not proof of ownership. If
   verification fails or records conflict, do not disclose a key; escalate
   through the private operator process.

3. **Look up the existing record privately.** From a trusted operator machine,
   connect to the private Neon registry through `DATABASE_URL` and query the
   existing license record using the verified customer/install identifier. Do
   not query through a customer-facing route, create a public lookup endpoint,
   export the registry, or paste the query/result into logs, GitHub, tickets,
   or chat. Redact key values from command history and terminal capture.
4. **Confirm uniqueness and status.** Confirm that the record belongs to the
   verified installation and is active/eligible. Return the already-issued key
   only; never generate a replacement by copying a key or reuse that key for a
   different customer, Render service, environment, or installation. If the
   installation changed, issue a new unique key through the normal issuance
   process and mark/retire the old association according to operator records.

5. **Disclose once, privately.** Send the key through the approved private,
   authenticated customer-delivery channel (not a public endpoint, logs,
   GitHub, or chat). Ask the customer to enter it only in that installation's
   secret environment and to remove any accidental local exposure. Do not
   include the key in support screenshots or transcripts.

6. **Record a redacted audit trail.** Record the verification performed, the
   operator, install identifier, and outcome without recording the key,
   `DATABASE_URL`, or other secrets. If exposure is suspected, treat the key
   as compromised, revoke/retire it, issue a fresh unique key, and repeat
   verification and private delivery.

Never disclose a key when ownership cannot be verified. The registry and its
operator credentials remain private; application runtime and public clients
must not gain a key-recovery capability.

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
