# drift-exec

Stateless Node/TypeScript CLI for **Drift Protocol perp execution**, built for the
GridRunner Ultimate leverage-grid extension. The Python grid bot invokes this CLI
per operation over subprocess and parses the single JSON object on stdout.

> **DEVNET ONLY.** This build refuses `mainnet-beta` (see the safety gate below).
> It never touches the existing spot/AI execution path, license, webhook, or DB code.

## Requirements

- Node `>= 22` (the SDK declares `^24`; it runs fine on Node 22 — a `bigint` native
  binding falls back to pure JS with a one-time warning)
- A Solana keypair — a throwaway devnet keypair you generate yourself. **Never** use
  the bot's live wallet or any production secret.

## Install & build

```bash
cd drift-exec
npm install
npm run build        # tsc -> dist/
node dist/index.js help
```

## Environment

| Var              | Meaning                                             | Default                      |
| ---------------- | --------------------------------------------------- | ---------------------------- |
| `RPC_URL`        | Solana RPC endpoint                                 | `https://api.devnet.solana.com` |
| `SOLANA_KEYPAIR` | Path to keypair file (JSON array) or base58 secret  | *(required)*                 |
| `DRIFT_ENV`      | `devnet` \| `mainnet-beta`                          | `devnet`                     |

Keypair examples:

```bash
export SOLANA_KEYPAIR=/path/to/id.json     # standard [1,2,...,64] JSON array
export SOLANA_KEYPAIR="<base58 secret key>"
```

Generate a throwaway devnet keypair:

```bash
node -e "const {Keypair}=require('@solana/web3.js'); const k=Keypair.generate(); require('fs').writeFileSync('devnet-key.json', JSON.stringify(Array.from(k.secretKey))); console.log(k.publicKey.toBase58())"
```

## Commands

Every command prints **exactly one JSON object to stdout** and exits `0` on success;
on failure it prints `{"error":"..."}` and exits `1`. (The SDK may print a `bigint`
binding notice to stderr on first run — ignore stderr.)

### `markets`

List perp markets (index derived from `getPerpMarketAccounts()`, never hardcoded).

```bash
node dist/index.js markets
# {"markets":[{"index":0,"symbol":"SOL-PERP"},{"index":1,"symbol":"BTC-PERP"},...]}
```

### `create-account`

Initialize the Drift user account (PDA) for the keypair.

```bash
node dist/index.js create-account
# {"txSig":"...","userAccount":"<pubkey>"}
```

### `place`

Place a **postOnly** limit order. `--size` is base-asset amount (e.g. `0.1` = 0.1 SOL),
`--price` is USD. All orders are `postOnly: true` (hard requirement) — the
`--postonly` flag is accepted for compatibility but is always on.

```bash
node dist/index.js place --market 0 --long --size 0.1 --price 150 --postonly
# {"orderId":12,"userOrderId":1750000000,"txSig":"..."}
```

### `cancel`

Cancel an order by Drift order id (the `orderId` returned by `place`).

```bash
node dist/index.js cancel --market 0 --order-id 12
# {"txSig":"..."}
```

### `position`

Read back a perp position and account margin state for a market.

```bash
node dist/index.js position --market 0
# {"size":0.1,"entry":150.0,"unrealizedPnl":1.23,"liqPrice":12.34,
#  "margin":500.0,"freeCollateral":480.0,"leverage":2.0,"marketIndex":0}
```

- `size` — signed base-asset amount (negative = short)
- `entry` — average entry price (USD)
- `unrealizedPnl` — USD, includes funding
- `liqPrice` — USD liquidation price (0 when flat)
- `margin` / `freeCollateral` — USD
- `leverage` — notional ÷ margin

### `deposit`

**Builds** (does not send) a USDC collateral deposit transaction for the owner to
sign and submit. Returns a base64-serialized transaction. This is owner-triggered and
is not executed in devnet paper.

```bash
node dist/index.js deposit --amount 500
# {"transaction":"<base64>","marketIndex":0,"amountUsdc":500,"userAccount":"<pubkey>"}
```

## Safety gate

`loadConfig` hard-refuses `DRIFT_ENV=mainnet-beta` unless **both** the env is set to
`mainnet-beta` **and** `DRIFT_ALLOW_MAINNET=1` is present. Live trading additionally
requires owner funding + sign-off per the spec — do not set the escape hatch during
devnet/paper work.

## Contract

- stateless — each invocation is independent; no daemon, no long-lived state
- `postOnly: true` on every order (maker-side)
- `marketIndex` is always derived from `getPerpMarketAccounts()` — never hardcoded
- additive only — does not modify `main.py` or any existing Python logic
