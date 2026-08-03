# GridRunner — Release and setup guide

GridRunner is an automated spot-grid trading dashboard for Solana DEXs. The production deployment is the `release` branch; the staging/PR target is `main`.

## Required secrets

For paid/live use, configure these values in the deployment platform's secret environment settings:

| Variable | Required | Purpose |
|---|---|---|
| `LICENSE_KEY` | Yes for paid/live use | A unique key issued for this specific installation. Every installation/customer must receive a different key. |
| `DATABASE_URL` | Yes for paid/live use | Private Neon/PostgreSQL license-registry connection string. The same registry URL may be shared by authorized deployments; it is not a license key. |
| `SOLANA_PRIVATE_KEY` | Yes for live trading | Wallet key used by this installation. |
| `PAPER_TRADING` | Recommended default | Set `true` for paper mode; use `false` only after licensing and wallet configuration are verified. |
| `API_SECRET` | Recommended | Dashboard API authentication secret. |

Telegram and risk/configuration variables are optional and documented in `.env.example`.

**Never commit or paste real values into this repository, an issue, chat, or deployment logs.** Keep `LICENSE_KEY`, `DATABASE_URL`, wallet keys, API secrets, and Telegram tokens in Render's encrypted environment settings or an approved secret manager.

## Private license registry and per-install issuance

Paid licenses are validated against the private registry configured through `DATABASE_URL`. Before creating a deployment, issue or assign a new registry record and a new `LICENSE_KEY` for that installation. Do not reuse an existing customer's key, copy a key from another Render service, or generate a replacement by editing source files. Keep the registry private and record the installation/customer association in the operator's secure system.

A second Render service uses the same private `DATABASE_URL` when authorized, but it still requires its own newly issued, unique `LICENSE_KEY`. Copying the database connection setting is not the same as copying a license. Copy only non-secret configuration defaults from the existing service; enter each secret separately through Render's secret environment settings.

## Deploying the current release

1. Confirm the audited release commit/branch approved for production. Production deploys from `release`; do not deploy an unreviewed feature branch.
2. Create the new Render service from the repository and select the approved `release` branch/commit.
3. Add a fresh unique `LICENSE_KEY` issued for this installation and the authorized shared `DATABASE_URL` in Render's environment settings.
4. Add this service's wallet and other secrets individually. Do not export or paste them into the repository.
5. Keep `PAPER_TRADING=true` for initial verification. Check the dashboard, license status, pair selection, and chart before considering live mode.
6. Change to live mode only after the installation's unique key validates and wallet/risk settings are confirmed.

If a key is invalid or the registry cannot be reached, the bot must remain paper-only; do not bypass validation or substitute another installation's key.

## Chart behavior in the current release

The current audited chart release loads pair-aware history from the startup window (four hours where the exchange history source supports it), keeps candle spacing at 3px, retains history beyond the viewport for horizontal scrolling, and anchors the newest candle at the right edge. In multi-pair grid mode, each active pair has its own chart and grid details. If a deployment does not show this behavior, verify that it is running the approved current `release` commit before changing configuration.

## Manual local run

```bash
pip install -r requirements.txt
export LICENSE_KEY="LB-YOUR-UNIQUE-KEY-HERE"
export DATABASE_URL="<private-postgres-connection-string>"
export PAPER_TRADING="true"
python main.py
```

Use placeholders only in shell examples. Never replace them with real secrets in committed documentation.

## References

- Website: https://aitrader.ctonew.app
- Repository: https://github.com/Gridrunner-tools/Gridrunner-bot
