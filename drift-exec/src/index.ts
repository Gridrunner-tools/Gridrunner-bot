#!/usr/bin/env node
import { loadConfig } from './config.js';
import {
  buildClient,
  shutdownClient,
  listMarkets,
  placeOrder,
  cancelOrder,
  getPosition,
  buildDepositTx,
} from './client.js';

const USAGE = `drift-exec — Drift perp execution CLI (devnet/paper)

Usage:
  drift-exec markets
  drift-exec create-account
  drift-exec place --market <IDX> (--long|--short) --size <baseAmt> --price <float> [--postonly]
  drift-exec cancel --market <IDX> --order-id <id>
  drift-exec position --market <IDX>
  drift-exec deposit --amount <USDC>
  drift-exec help

Env:
  RPC_URL         Solana RPC endpoint (defaults to devnet)
  SOLANA_KEYPAIR  Path to keypair file (JSON array) or base58 secret key
  DRIFT_ENV       devnet | mainnet-beta (mainnet is gated OFF in this build)

Every command prints exactly one JSON object to stdout and exits 0 on success;
on failure it prints {"error":"..."} to stdout and exits 1.
`;

interface ParsedArgs {
  command: string;
  flags: Map<string, string | boolean>;
}

function parseArgs(argv: string[]): ParsedArgs {
  const [command = 'help', ...rest] = argv;
  const flags = new Map<string, string | boolean>();
  for (let i = 0; i < rest.length; i++) {
    const arg = rest[i];
    if (arg.startsWith('--')) {
      const name = arg.slice(2);
      const next = rest[i + 1];
      if (next !== undefined && !next.startsWith('--')) {
        flags.set(name, next);
        i++;
      } else {
        flags.set(name, true);
      }
    }
  }
  return { command, flags };
}

function num(flags: Map<string, string | boolean>, name: string): number {
  const v = flags.get(name);
  if (v === undefined) throw new Error(`Missing required flag --${name}`);
  const n = Number(v);
  if (!Number.isFinite(n)) throw new Error(`Flag --${name} must be a number (got '${v}')`);
  return n;
}

function int(flags: Map<string, string | boolean>, name: string): number {
  const n = num(flags, name);
  if (!Number.isInteger(n)) throw new Error(`Flag --${name} must be an integer (got '${n}')`);
  return n;
}

async function main(): Promise<void> {
  const { command, flags } = parseArgs(process.argv.slice(2));

  if (command === 'help' || command === '--help' || command === '-h') {
    process.stdout.write(USAGE);
    return;
  }

  const config = loadConfig();
  const built = await buildClient(config);

  try {
    switch (command) {
      case 'markets': {
        const markets = listMarkets(built.client);
        process.stdout.write(JSON.stringify({ markets }) + '\n');
        break;
      }
      case 'create-account': {
        const [txSig, userAccount] = await built.client.initializeUserAccount();
        process.stdout.write(
          JSON.stringify({ txSig, userAccount: userAccount.toBase58() }) + '\n',
        );
        break;
      }
      case 'place': {
        const marketIndex = int(flags, 'market');
        const hasLong = flags.has('long');
        const hasShort = flags.has('short');
        if (hasLong === hasShort) {
          throw new Error('Specify exactly one of --long or --short');
        }
        const direction = hasLong ? 'long' : 'short';
        const size = num(flags, 'size');
        const price = num(flags, 'price');
        if (size <= 0) throw new Error('--size must be > 0');
        if (price <= 0) throw new Error('--price must be > 0');
        const res = await placeOrder(built.client, marketIndex, direction, size, price);
        process.stdout.write(JSON.stringify(res) + '\n');
        break;
      }
      case 'cancel': {
        const marketIndex = int(flags, 'market');
        const orderId = int(flags, 'order-id');
        void marketIndex; // market is validated implicitly; order id is the handle
        const txSig = await cancelOrder(built.client, orderId);
        process.stdout.write(JSON.stringify({ txSig }) + '\n');
        break;
      }
      case 'position': {
        const marketIndex = int(flags, 'market');
        const pos = await getPosition(built.client, marketIndex);
        process.stdout.write(JSON.stringify(pos) + '\n');
        break;
      }
      case 'deposit': {
        const amount = num(flags, 'amount');
        if (amount <= 0) throw new Error('--amount must be > 0');
        const res = await buildDepositTx(built.client, amount, config.keypair.publicKey);
        process.stdout.write(JSON.stringify(res) + '\n');
        break;
      }
      default:
        throw new Error(`Unknown command '${command}'. Run 'drift-exec help'.`);
    }
  } finally {
    await shutdownClient(built);
  }
}

main()
  .then(() => process.exit(0))
  .catch((err: unknown) => {
    const message = err instanceof Error ? err.message : String(err);
    process.stdout.write(JSON.stringify({ error: message }) + '\n');
    process.exit(1);
  });
