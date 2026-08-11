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

A customer who has lost their `LICENSE_KEY` must go through a verified recovery
process. The operator never discloses a key without confirming the requester's
identity and ownership of the installation.

### 1. Authenticate the requester
The customer must contact the operator through a **verified, authenticated
channel** — for example, the same email address used at purchase, or a support
ticket opened from that address. Accept no request that arrives through an
unverified channel (public chat, social-media DM, GitHub issue, or forwarded
message).

At a minimum, confirm **two** of the following before proceeding:
- The inbound email address matches `customer_email` on the license record.
- The customer can supply the Stripe session ID or Solana transaction signature
  associated with the purchase (validated against `stripe_session_id` or
  `sol_tx_signature` in the registry).
- The customer can describe the Render service name, approximate deployment
  date, or other installation-specific detail that only the legitimate operator
  would recognise.

If any verification step fails, **stop**. Do not proceed to lookup. Log the
attempt internally (without the key) and notify the customer that verification
could not be completed.

### 2. Look up the key through the private Neon registry
Only after identity is confirmed, from a trusted operator machine with
`DATABASE_URL` set:

```bash
psql "$DATABASE_URL" -c "
  SELECT license_key, license_type, customer_email, expires_at, is_active
  FROM licenses
  WHERE customer_email = 'verified@example.com' AND is_active = TRUE
  ORDER BY created_at DESC
  LIMIT 10;
"
```

Do **not** run this query in a shared terminal, screen-share, or recorded
session. If the customer has multiple active licenses (e.g. paper and live
installations), use the installation-specific detail the customer provided
(service name, deployment date) to identify the correct row.

Record internally which key was disclosed, to whom, and when — keep this log
outside the repository.

### 3. Deliver the key through a secure private channel
Deliver the recovered key **only** through a secure, private, out-of-band
channel:

| ✅ Allowed | ❌ Prohibited |
|---|---|
| Encrypted email (PGP) to the verified address | Plain-text email body |
| Secure customer portal with authenticated session | GitHub issue, pull request, or commit |
| One-time secret link (expiring, access-logged) | Public or team chat (Slack, Discord, Telegram) |
| Voice call to a known, verified phone number | Support ticket body (ticket systems are often readable by multiple staff) |
| | Render log output, environment-variable dump, or any public endpoint |

After delivery, instruct the customer to enter the key only as a Render secret
(or local environment variable) and then delete any copy from their email,
clipboard history, or downloads.

### 4. Post-recovery: consider rotation
A recovered key has now existed in at least two places (the delivery channel
and the customer's environment). If there is any concern that the original key
was compromised rather than genuinely lost, **issue a replacement key** and
deactivate the old one:

```bash
# Deactivate the old key
psql "$DATABASE_URL" -c "
  UPDATE licenses SET is_active = FALSE, updated_at = NOW()
  WHERE license_key = 'LB-XXXX-XXXX-XXXX';
"

# Issue a fresh unique key — never copy or rename the old value
python scripts/issue_license.py --email customer@example.com --type full --days <remaining>
```

### Hard rules
- **Never expose a license key** in a public endpoint, log message, GitHub
  repository (issue, PR, commit, or wiki), chat platform, or support ticket.
- **Never look up or disclose a key** without completing the identity
  verification steps above.
- **Preserve per-install uniqueness.** A recovered key belongs to exactly one
  installation. Never give the same key to a second customer or installation,
  and never suggest that a customer reuse a key across Render services.
- The private Neon registry is the **only** source of truth for license keys.
  Do not maintain a secondary key list in a spreadsheet, shared document, or
  configuration file — those copies inevitably leak.

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
