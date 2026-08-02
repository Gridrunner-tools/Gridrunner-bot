# GridRunner — Solana automated trading bot

GridRunner runs automated spot-grid strategies on Solana DEXs, with paper/live modes, risk controls, alerts, and a dashboard.

> **Release and safety:** Each installation is a separate licensed deployment. Issue a new, unique `LICENSE_KEY` for every installation. Never reuse a key between customers, environments, or Render services. Never commit, paste into an issue, or publish a key, wallet private key, Telegram token, API secret, or database connection string.

## Deploy the current release to Render

1. Open the repository's **Deploy to Render** flow and select the current `release` branch. Do not deploy an old feature branch.
2. Create one Render web service per installation. The service uses `pip install -r requirements.txt` and starts with `python main.py` (port `10000`).
3. Before configuring the service, issue a fresh key for this specific installation (see [License issuance](#license-issuance)).
4. In Render **Environment → Environment Variables**, add the variables below. Use Render's secret input for every sensitive value; do not put values in `render.yaml`, this README, or source control.
5. Save and deploy. Confirm the service log reports successful license validation before enabling live trading.

### Required Render variables

| Variable | Value | Notes |
| --- | --- | --- |
| `LICENSE_KEY` | Newly issued key for this installation | One unique key per installation; do not reuse. |
| `DATABASE_URL` | Private Neon PostgreSQL connection string | The license registry URL. Keep it secret; it is not a public API URL. |
| `SOLANA_PRIVATE_KEY` | Wallet private key | Required for live trading; store only as a Render secret. |
| `PAPER_TRADING` | `true` or `false` | Start with `true` while verifying configuration. Invalid/missing licensing forces paper-only behavior. |

Recommended optional variables are `API_SECRET`, `TG_BOT_TOKEN`, and `TG_CHAT_ID`. Trading/risk defaults can be left as supplied by the current release or explicitly configured (`MAX_POSITION_USD`, `MAX_DAILY_LOSS_USD`, `RISK_PCT`, `COOLDOWN`, `SLIPPAGE`, `AUTO_COMPOUND`, `PARTIAL_SELL_PCT`, `DYNAMIC_SPREAD`, and `BASE_SPREAD`). `PORT` defaults to `10000`.

`DATABASE_URL` must point to the private Neon/PostgreSQL registry used by this release. Do not substitute a local SQLite file, a public key list, or a customer-facing URL. The application validates against the registry with parameterized SQL. If Neon is temporarily unavailable, a previously successful matching validation may be used for up to 48 hours; without a fresh result or valid cache, validation fails closed and the bot remains paper-only.

## License issuance (operator-only)

Run issuance from a trusted operator machine or secure Render shell where `DATABASE_URL` is already set. Initialize the private registry once:

```bash
psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f db/license_registry.sql
```

Issue a new full key for each installation or purchase:

```bash
python scripts/issue_license.py --email customer@example.com --type full --days 365
```

For an installation tied to a recorded purchase, use the purchase helper instead:

```bash
python scripts/process_purchase.py \
  --email customer@example.com \
  --stripe-id <session-id> \
  --days 365
```

Use `--sol-tx <signature>` for a Solana payment when appropriate. Use `--trial` for a seven-day trial. Use `--days 0` only when the approved license should not expire. Each command generates and inserts a unique key directly into Neon; it does not create a `keys.json` export. Deliver the generated key only through the approved customer channel, then enter it only in that installation's secret environment. Keep an internal installation-to-key record outside this repository. Never regenerate a key by copying an existing value, and never include a real key in support screenshots or logs.

## Manual/local startup

For a trusted local or server deployment:

```bash
python -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
export LICENSE_KEY="<fresh key issued for this installation>"
export DATABASE_URL="<private Neon connection string>"
export SOLANA_PRIVATE_KEY="<secret wallet key>"
export PAPER_TRADING=true
python main.py
```

Use a secret manager or shell environment for all placeholders. Do not replace the private Neon URL with a value committed to `.env`, and do not run live mode until wallet, risk limits, and license validation have been checked.

## Dashboard chart behavior in the current release

- At process startup, the selected/default pair is seeded with available Kraken one-minute close candles covering approximately the previous four hours (up to 1,440 points).
- Starting a bot or adding another grid pair seeds that pair through the shared startup path, so history is available to the dashboard before strategy loops begin.
- The dashboard maintains history separately per pair. Switching pairs therefore uses that pair's history instead of displaying the previously selected pair's data.
- New prices append/update the current pair history and retain at most 1,440 points (roughly 24 hours at one-minute resolution). If the upstream candle request fails, the dashboard can still populate as live price samples arrive; the failure is logged as a warning.

Chart history is market data, not a guarantee of execution or future performance. Verify the displayed pair and timestamp before relying on it for decisions.

## Environment reference

See [`.env.example`](.env.example) for the complete list and safe blank placeholders. See [`docs/LICENSE_REGISTRY.md`](docs/LICENSE_REGISTRY.md) for registry operations and safety behavior.

## Security checklist

- [ ] A newly issued, unique key is assigned to this installation.
- [ ] `LICENSE_KEY`, `DATABASE_URL`, `SOLANA_PRIVATE_KEY`, and alert/API secrets are Render secrets.
- [ ] No secrets or registry exports are committed or shared in screenshots/logs.
- [ ] `PAPER_TRADING=true` was used for initial verification.
- [ ] Risk limits and emergency-stop procedures were reviewed before live mode.
