# LeverBot — Automated Grid Trading Bot for Solana DEXs

Run automated spot grid trading on Solana DEXs (Raydium, Jupiter). Multi-pair, smart trailing buys/sells, partial profit taking, auto-compounding.

## One-Click Deploy to Render

[![Deploy to Render](https://render.com/images/deploy-to-render-button.svg)](https://render.com/deploy?repo=https://github.com/bobwhite6973/my-trading-bot&branch=release)

1. Click the button above
2. Set your environment variables (LICENSE_KEY, SOLANA_PRIVATE_KEY, etc.)
3. Render spins up your bot in ~2 minutes
4. Open your Render URL to access the dashboard

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `LICENSE_KEY` | Yes for paid/live use | Your GridRunner license key (LB-XXXX-XXXX-XXXX) |
| `DATABASE_URL` | Yes for paid/live use | Private Neon/PostgreSQL license registry connection string |
| `SOLANA_PRIVATE_KEY` | Yes (live) | Solana wallet private key |
| `PAPER_TRADING` | No | Set to `true` for demo mode; an invalid license always forces paper-only mode |
| `API_SECRET` | No | Dashboard API authentication |
| `TG_BOT_TOKEN` | No | Telegram bot token for alerts |
| `TG_CHAT_ID` | No | Telegram chat ID for alerts |
| `PORT` | No | Server port (default: 10000) |

Full config in `.env.example`.

## License

GridRunner validates paid licenses against the private Neon/PostgreSQL registry configured through `DATABASE_URL`. Do not publish or commit license keys, registry exports, or the database URL.

- Trial: 7 days, full functionality
- Full: One-time purchase, no recurring fees

## Manual Deploy

```bash
pip install -r requirements.txt
export LICENSE_KEY="LB-YOUR-KEY-HERE"
# Set this through your secret manager; do not paste a real value into docs or source control.
export DATABASE_URL="<private-postgres-connection-string>"
export PAPER_TRADING="true"
python main.py
```

## Support

- Website: https://aitrader.ctonew.app
- Repository: https://github.com/bobwhite6973/my-trading-bot
