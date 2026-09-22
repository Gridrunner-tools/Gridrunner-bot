import {
  DriftClient,
  Wallet,
  PRICE_PRECISION,
  BASE_PRECISION,
  QUOTE_PRECISION,
  PostOnlyParams,
  PositionDirection,
  OrderType,
  MarketType,
  calculateEntryPrice,
  BN,
  type PerpMarketAccount,
  type OptionalOrderParams,
} from '@drift-labs/sdk';
import { Connection, Keypair, PublicKey } from '@solana/web3.js';
import type { Config } from './config.js';

const BN_BASE_PRECISION = new BN(BASE_PRECISION.toString());
const BN_PRICE_PRECISION = new BN(PRICE_PRECISION.toString());

export interface BuiltClient {
  client: DriftClient;
  connection: Connection;
  keypair: Keypair;
}

export async function buildClient(config: Config): Promise<BuiltClient> {
  const connection = new Connection(config.rpcUrl, 'confirmed');
  const wallet = new Wallet(config.keypair);

  const client = new DriftClient({
    connection,
    wallet,
    env: config.env,
  });

  await client.subscribe();

  return { client, connection, keypair: config.keypair };
}

export async function shutdownClient(built: BuiltClient): Promise<void> {
  try {
    await built.client.unsubscribe();
  } catch {
    // best-effort teardown
  }
}

/** Decode a Drift perp market `name` (a borsh byte array) into a UTF-8 symbol. */
export function decodeMarketName(name: number[]): string {
  // Market names are stored left-aligned in a fixed-width byte array, padded
  // with spaces (0x20) — strip trailing null bytes and spaces.
  return Buffer.from(name as unknown as Uint8Array)
    .toString('utf8')
    .replace(/[\u0000 ]+$/g, '');
}

export function listMarkets(client: DriftClient): Array<{ index: number; symbol: string }> {
  return client.getPerpMarketAccounts().map((m: PerpMarketAccount) => ({
    index: m.marketIndex,
    symbol: decodeMarketName(m.name),
  }));
}

export function resolveMarket(client: DriftClient, index: number): PerpMarketAccount {
  const market = client.getPerpMarketAccount(index);
  if (!market) {
    throw new Error(`No perp market at index ${index}. Run 'markets' to list valid indexes.`);
  }
  return market;
}

export function baseAmountToBn(baseAmt: number): BN {
  return new BN(Math.round(baseAmt * BN_BASE_PRECISION.toNumber()));
}

export function priceToBn(price: number): BN {
  return new BN(Math.round(price * BN_PRICE_PRECISION.toNumber()));
}

function bnToNumber(bn: BN, precision: number): number {
  return Number(bn.toString()) / precision;
}

export interface PlaceResult {
  orderId: number | undefined;
  userOrderId: number;
  txSig: string;
}

export async function placeOrder(
  client: DriftClient,
  marketIndex: number,
  direction: 'long' | 'short',
  sizeBaseAmt: number,
  price: number,
): Promise<PlaceResult> {
  // Unique client-assigned id; used to read the Drift-assigned orderId back.
  const userOrderId = Math.floor(Date.now() / 1000) % 0x7fffffff;

  const orderParams: OptionalOrderParams = {
    orderType: OrderType.LIMIT,
    marketType: MarketType.PERP,
    marketIndex,
    direction: direction === 'long' ? PositionDirection.LONG : PositionDirection.SHORT,
    baseAssetAmount: baseAmountToBn(sizeBaseAmt),
    price: priceToBn(price),
    postOnly: PostOnlyParams.MUST_POST_ONLY,
    reduceOnly: false,
    userOrderId,
  };

  const txSig = await client.placePerpOrder(orderParams);

  // Read back the Drift-assigned order id.
  const user = client.getUser();
  await user.fetchAccounts();
  const placed = user.getOpenOrders().find((o) => o.userOrderId === userOrderId);

  return { orderId: placed?.orderId, userOrderId, txSig };
}

export async function cancelOrder(client: DriftClient, orderId: number): Promise<string> {
  return client.cancelOrder(orderId);
}

export interface PositionResult {
  size: number;
  entry: number;
  unrealizedPnl: number;
  liqPrice: number;
  margin: number;
  freeCollateral: number;
  leverage: number;
  marketIndex: number;
}

export async function getPosition(client: DriftClient, marketIndex: number): Promise<PositionResult> {
  const user = client.getUser();
  await user.subscribe();

  const pos = user.getPerpPosition(marketIndex);

  const margin = bnToNumber(user.getTotalCollateral(), 1e6);
  const freeCollateral = bnToNumber(user.getFreeCollateral(), 1e6);
  const leverage = bnToNumber(user.getLeverage(), 1e4);

  if (!pos || pos.baseAssetAmount.eq(new BN(0))) {
    return {
      size: 0,
      entry: 0,
      unrealizedPnl: 0,
      liqPrice: 0,
      margin,
      freeCollateral,
      leverage,
      marketIndex,
    };
  }

  const entryPrice = calculateEntryPrice(pos); // PRICE_PRECISION
  let liqPrice = new BN(0);
  try {
    liqPrice = user.liquidationPrice(marketIndex);
  } catch {
    liqPrice = new BN(0);
  }

  return {
    size: bnToNumber(pos.baseAssetAmount, 1e9),
    entry: bnToNumber(entryPrice, 1e6),
    unrealizedPnl: bnToNumber(user.getUnrealizedPNL(true, marketIndex), 1e6),
    liqPrice: bnToNumber(liqPrice, 1e6),
    margin,
    freeCollateral,
    leverage,
    marketIndex,
  };
}

/** Spot market index for the USDC quote asset (0 on devnet and mainnet-beta). */
export const USDC_SPOT_MARKET_INDEX = 0;

export interface DepositTxResult {
  transaction: string; // base64 serialized transaction
  marketIndex: number;
  amountUsdc: number;
  userAccount: string;
}

export async function buildDepositTx(
  client: DriftClient,
  amountUsdc: number,
  walletPublicKey: PublicKey,
): Promise<DepositTxResult> {
  const amount = new BN(Math.round(amountUsdc * 1e6)); // QUOTE_PRECISION

  const spotMarket = client.getSpotMarketAccount(USDC_SPOT_MARKET_INDEX);
  if (!spotMarket) {
    throw new Error(`No spot market at index ${USDC_SPOT_MARKET_INDEX}`);
  }

  // Wallet's USDC associated token account (source of funds).
  const ata = getAssociatedTokenAddressSync(walletPublicKey, spotMarket.mint);

  const tx = await client.createDepositTxn(amount, USDC_SPOT_MARKET_INDEX, ata);
  const serialized = tx.serialize();

  const userAccount = await client.getUserAccountPublicKey();

  return {
    transaction: Buffer.from(serialized).toString('base64'),
    marketIndex: USDC_SPOT_MARKET_INDEX,
    amountUsdc,
    userAccount: userAccount.toBase58(),
  };
}

// ── Associated Token Account derivation (no extra dependency) ───────────────
const ATA_PROGRAM_ID = new PublicKey('ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL');
const TOKEN_PROGRAM_ID = new PublicKey('TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA');

function getAssociatedTokenAddressSync(owner: PublicKey, mint: PublicKey): PublicKey {
  const [ata] = PublicKey.findProgramAddressSync(
    [owner.toBuffer(), TOKEN_PROGRAM_ID.toBuffer(), mint.toBuffer()],
    ATA_PROGRAM_ID,
  );
  return ata;
}
