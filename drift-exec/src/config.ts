import fs from 'node:fs';
import { Keypair } from '@solana/web3.js';
import bs58 from 'bs58';

/**
 * DriftEnv — Phase 1 is DEVNET ONLY. `mainnet-beta` is hard-refused by
 * `loadConfig` unless an explicit escape hatch is set; the owner will flip
 * this when the live flag + funding + sign-off are in place (see spec).
 */
export type DriftEnv = 'devnet' | 'mainnet-beta';

export interface Config {
  rpcUrl: string;
  keypair: Keypair;
  env: DriftEnv;
  walletPublicKey: string;
}

const DEVNET_RPC = 'https://api.devnet.solana.com';

function parseEnv(raw: string | undefined): DriftEnv {
  const env = (raw ?? 'devnet').trim().toLowerCase();
  if (env === 'devnet') return 'devnet';
  if (env === 'mainnet-beta' || env === 'mainnet') return 'mainnet-beta';
  throw new Error(`DRIFT_ENV must be 'devnet' or 'mainnet-beta' (got '${raw}')`);
}

/**
 * Load a Solana keypair from SOLANA_KEYPAIR, which may be:
 *   - a path to a keypair file (standard Solana JSON array, e.g. [1,2,...,64])
 *   - a base58 secret key string
 *   - an inline JSON array of 64 bytes
 */
function loadKeypair(spec: string | undefined): Keypair {
  if (!spec || spec.trim() === '') {
    throw new Error('SOLANA_KEYPAIR is required (path to keypair file, or base58 secret key)');
  }
  const trimmed = spec.trim();
  if (fs.existsSync(trimmed)) {
    return parseKeypair(fs.readFileSync(trimmed, 'utf8'));
  }
  return parseKeypair(trimmed);
}

function parseKeypair(raw: string): Keypair {
  const trimmed = raw.trim();
  if (trimmed.startsWith('[')) {
    let arr: unknown;
    try {
      arr = JSON.parse(trimmed);
    } catch {
      throw new Error('SOLANA_KEYPAIR: invalid JSON array');
    }
    if (Array.isArray(arr) && arr.length === 64 && arr.every((n) => Number.isInteger(n))) {
      return Keypair.fromSecretKey(Uint8Array.from(arr as number[]));
    }
    throw new Error('SOLANA_KEYPAIR: expected a 64-byte integer array');
  }
  try {
    const bytes = bs58.decode(trimmed);
    if (bytes.length === 64) {
      return Keypair.fromSecretKey(bytes);
    }
    throw new Error(`SOLANA_KEYPAIR: base58 secret key must decode to 64 bytes (got ${bytes.length})`);
  } catch (e) {
    if (e instanceof Error && e.message.startsWith('SOLANA_KEYPAIR:')) throw e;
    throw new Error('SOLANA_KEYPAIR: not a valid base58 secret key');
  }
}

export function loadConfig(): Config {
  const env = parseEnv(process.env.DRIFT_ENV);

  // ── SAFETY GATE ──────────────────────────────────────────────────────────
  // DEVNET ONLY for Phase 1. Never touch real funds. This is deliberately
  // aggressive: mainnet requires BOTH DRIFT_ENV=mainnet-beta AND an explicit
  // DRIFT_ALLOW_MAINNET=1 escape hatch. Do not set that in devnet paper work.
  // ──────────────────────────────────────────────────────────────────────────
  if (env === 'mainnet-beta' && process.env.DRIFT_ALLOW_MAINNET !== '1') {
    throw new Error(
      'REFUSING to run against mainnet-beta. drift-exec is DEVNET ONLY in this build. ' +
        'Live trading requires owner funding + sign-off (see DRIFT_LEVERAGE_GRID_SPEC.md).',
    );
  }

  const rpcUrl = process.env.RPC_URL?.trim() || DEVNET_RPC;
  const keypair = loadKeypair(process.env.SOLANA_KEYPAIR);

  return {
    rpcUrl,
    keypair,
    env,
    walletPublicKey: keypair.publicKey.toBase58(),
  };
}
