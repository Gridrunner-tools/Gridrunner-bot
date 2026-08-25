"""
Token Registry — single source of truth for which tokens GridRunner is allowed to trade.

Every mint here has been checked against at least one independent block explorer
before being marked APPROVED. Nothing gets traded on the strength of a symbol
match alone — the registry entry gates it.

Status values:
    APPROVED            — verified mint, safe to trade subject to the limits below
    PENDING_VERIFICATION — mint known but not yet independently confirmed; never tradeable
    REJECTED            — deliberately blocked (e.g. a known-impersonator token)

How this gets used (see main.py integration):
    - place_order() / jupiter_swap() / _raydium_execute_swap() must call
      authorize_trade() before sending anything to the chain.
    - /add_token inserts new custom tokens as PENDING_VERIFICATION, never
      APPROVED — a human has to flip the status in this file deliberately.
    - Liquidity and price-impact checks use live data pulled at trade time
      (see get_token_liquidity_usd / estimate_price_impact_pct in main.py);
      the numbers below are the thresholds those live checks are compared against.
"""

TOKEN_REGISTRY = {
    "USDC": {
        "mint": "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
        "decimals": 6,
        "status": "APPROVED",
        "min_liquidity_usd": 0,          # quote currency, not liquidity-gated
        "max_price_impact_pct": 0.5,
        "allowed_routes": ["Raydium", "Jupiter"],
    },
    "USDT": {
        "mint": "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB",
        "decimals": 6,
        "status": "APPROVED",
        "min_liquidity_usd": 0,
        "max_price_impact_pct": 0.5,
        "allowed_routes": ["Raydium", "Jupiter"],
    },
    "SOL": {
        "mint": "So11111111111111111111111111111111111111112",
        "decimals": 9,
        "status": "APPROVED",
        "min_liquidity_usd": 50_000,
        "max_price_impact_pct": 1.0,
        "allowed_routes": ["Raydium", "Jupiter"],
    },
    "BTC": {
        # Wrapped BTC (Wormhole/Portal). Verified against Solscan, Solana
        # Explorer, and GeckoTerminal — all three agree on this mint.
        "mint": "3NZ9JMVBmGAqocybic2c7LQCJScmgsAZ6vQqTDzcqmJh",
        "decimals": 8,
        "status": "APPROVED",
        "min_liquidity_usd": 25_000,
        "max_price_impact_pct": 1.0,
        "allowed_routes": ["Raydium", "Jupiter"],
    },
    "ETH": {
        # Wrapped Ether (Wormhole/Portal). Verified against Solscan, Solana
        # Explorer, OKX, and GeckoTerminal.
        "mint": "7vfCXTUXx5WJV5JADk17DUJ4ksgau7utNKj4b963voxs",
        "decimals": 8,
        "status": "APPROVED",
        "min_liquidity_usd": 25_000,
        "max_price_impact_pct": 1.0,
        "allowed_routes": ["Raydium", "Jupiter"],
    },
    "SPCX": {
        # SpaceX — Backpack Securities tokenized equity. Owner-confirmed mint,
        # matches the address already live in SOL_TOKENS.
        "mint": "SPCXxcqXj6e5dJDVNovHN8744zkbhM2bYudU45BimGb",
        "decimals": 6,
        "status": "APPROVED",
        "min_liquidity_usd": 10_000,
        "max_price_impact_pct": 2.0,
        "allowed_routes": ["Raydium"],
    },
    "BNB": {
        "mint": "9gP2kCy3wA1ctvYWQk75guqXuzoJGLIDs5oPHkHGs89",
        "decimals": 8,
        "status": "PENDING_VERIFICATION",  # was in SOL_TOKENS but not independently
        "min_liquidity_usd": 25_000,        # re-verified in this pass — confirm before flipping to APPROVED
        "max_price_impact_pct": 1.5,
        "allowed_routes": ["Raydium", "Jupiter"],
    },
    "JUP": {
        "mint": "JUPyiwrYJFskUPiHa7hkeR8VUtAeFoSYbKedZNsDvCN",
        "decimals": 6,
        "status": "APPROVED",
        "min_liquidity_usd": 15_000,
        "max_price_impact_pct": 2.0,
        "allowed_routes": ["Raydium", "Jupiter"],
    },
    "BONK": {
        "mint": "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263",
        "decimals": 5,
        "status": "APPROVED",
        "min_liquidity_usd": 15_000,
        "max_price_impact_pct": 3.0,       # meme-coin volatility — wider band, still capped
        "allowed_routes": ["Raydium", "Jupiter"],
    },
    "WIF": {
        "mint": "EKpQGSJtjMFqKZ9KQanSqYXRcF8fBopzLHYxdM65zcjm",
        "decimals": 6,
        "status": "PENDING_VERIFICATION",
        "min_liquidity_usd": 15_000,
        "max_price_impact_pct": 3.0,
        "allowed_routes": ["Raydium", "Jupiter"],
    },
    "MATIC": {
        "mint": "Gz7VkD4MacbEB6yC5XD3HcumEiYx2EtDYYrfikGsvopG",
        "decimals": 8,
        "status": "PENDING_VERIFICATION",
        "min_liquidity_usd": 20_000,
        "max_price_impact_pct": 1.5,
        "allowed_routes": ["Raydium", "Jupiter"],
    },
    "XRP": {
        # Wrapped XRP (wXRP) via Hex Trust custody + LayerZero OFT, launched
        # on Solana April 2026 — real, but the exact mint was NOT confirmed
        # from a searchable source. Fill this in yourself from tokens.xyz/xrp
        # and cross-check on Solscan before flipping status to APPROVED.
        "mint": None,
        "decimals": None,
        "status": "PENDING_VERIFICATION",
        "min_liquidity_usd": 20_000,
        "max_price_impact_pct": 1.5,
        "allowed_routes": ["Jupiter"],
    },
    "STARLINK": {
        # No legitimate standalone "Starlink" token was found. Do not fill
        # this in from a symbol/name match alone — SpaceX-adjacent branding
        # is a known impersonation target on Solana (see e.g. the unrelated
        # "Starship SpaceX Coin" token, which has no connection to SpaceX).
        # If a real Starlink-branded asset exists, verify the issuer directly
        # before adding a mint here.
        "mint": None,
        "decimals": None,
        "status": "REJECTED",
        "min_liquidity_usd": None,
        "max_price_impact_pct": None,
        "allowed_routes": [],
    },
}


def get_registry_entry(symbol):
    """Look up a token's registry entry by symbol. Returns None if unknown."""
    return TOKEN_REGISTRY.get(symbol.upper())


def is_approved(symbol):
    """True only if the symbol exists and is explicitly APPROVED."""
    entry = get_registry_entry(symbol)
    return bool(entry and entry.get("status") == "APPROVED" and entry.get("mint"))


def authorize_trade(symbol, liquidity_usd=None, price_impact_pct=None, route=None):
    """
    Single gate every trade must pass. Returns (ok: bool, reason: str).

    liquidity_usd, price_impact_pct, and route are optional so this can be
    called early (status-only check before quoting) or late (full check with
    live numbers right before submitting the order). Callers doing the final
    pre-submit check should always pass all three.
    """
    entry = get_registry_entry(symbol)
    if not entry:
        return False, f"{symbol} is not in the token registry"
    if entry["status"] != "APPROVED":
        return False, f"{symbol} is {entry['status']}, not APPROVED — will not trade"
    if not entry.get("mint"):
        return False, f"{symbol} has no verified mint on file"

    if liquidity_usd is not None and entry.get("min_liquidity_usd") is not None:
        if liquidity_usd < entry["min_liquidity_usd"]:
            return False, (
                f"{symbol} liquidity ${liquidity_usd:,.0f} is below the "
                f"${entry['min_liquidity_usd']:,.0f} floor"
            )

    if price_impact_pct is not None and entry.get("max_price_impact_pct") is not None:
        if price_impact_pct > entry["max_price_impact_pct"]:
            return False, (
                f"{symbol} estimated price impact {price_impact_pct:.2f}% exceeds "
                f"the {entry['max_price_impact_pct']:.2f}% cap"
            )

    if route is not None:
        allowed = entry.get("allowed_routes", [])
        if allowed and route not in allowed:
            return False, f"{symbol} may not route through {route} — allowed: {allowed}"

    return True, "ok"


def add_pending_token(symbol, mint, decimals=6):
    """
    Register a new custom token as PENDING_VERIFICATION only. Never inserts
    as APPROVED — a human must edit this file deliberately to approve it.
    Refuses to overwrite an existing entry silently.
    """
    symbol = symbol.upper()
    if symbol in TOKEN_REGISTRY:
        existing = TOKEN_REGISTRY[symbol]
        if existing.get("mint") and existing["mint"] != mint:
            return False, f"{symbol} already registered with a different mint"
        return True, f"{symbol} already registered ({existing['status']})"
    TOKEN_REGISTRY[symbol] = {
        "mint": mint,
        "decimals": decimals,
        "status": "PENDING_VERIFICATION",
        "min_liquidity_usd": 20_000,
        "max_price_impact_pct": 2.0,
        "allowed_routes": ["Jupiter"],
    }
    return True, f"{symbol} added as PENDING_VERIFICATION — not tradeable until approved"


def evaluate_verification(verification, claimed_symbol, min_liquidity_usd=10_000, min_pool_age_hours=24):
    """
    Turn live on-chain + market data into an auto-decided status. Never
    returns APPROVED unless every hard check passes — anything uncertain
    or borderline falls back to PENDING_VERIFICATION for a human to review.

    `verification` is the dict produced by main.py's verify_token_authenticity():
        exists, decimals, mint_authority, freeze_authority,
        liquidity_usd, pool_age_hours, dex_symbol, dex_name
    """
    warnings = []

    if not verification.get("exists"):
        return "REJECTED", ["Mint does not exist on-chain — refusing to register"]

    if verification.get("mint_authority"):
        warnings.append("Mint authority is NOT revoked — issuer can mint unlimited additional supply")

    if verification.get("freeze_authority"):
        # Freeze authority alone is NOT a hard-fail. Regulated/RWA assets
        # (stablecoins, tokenized securities) commonly keep it active for
        # compliance — sanctions enforcement, stolen-fund recovery, KYC/AML
        # orders. It's a real risk on an anonymous-issuer meme token (honeypot
        # pattern: freeze right after purchase) but a normal feature on a
        # known regulated issuer. An automated check can't tell those apart,
        # so this surfaces as a warning for a human to weigh, not a block.
        warnings.append("Freeze authority is active — verify the issuer is legitimate before trusting this (common on regulated assets, risky on anonymous ones)")

    liquidity = verification.get("liquidity_usd")
    if liquidity is None:
        warnings.append("No liquidity pool found for this mint on DexScreener")
    elif liquidity < min_liquidity_usd:
        warnings.append(f"Liquidity ${liquidity:,.0f} is below the ${min_liquidity_usd:,.0f} auto-approval floor")

    pool_age = verification.get("pool_age_hours")
    if pool_age is None:
        warnings.append("Could not determine pool age")
    elif pool_age < min_pool_age_hours:
        warnings.append(f"Liquidity pool is only {pool_age:.1f}h old (< {min_pool_age_hours}h) — classic rug-pull window")

    dex_symbol = (verification.get("dex_symbol") or "").upper()
    if dex_symbol and dex_symbol != claimed_symbol.upper():
        warnings.append(
            f"On-chain/market symbol '{dex_symbol}' does not match the claimed symbol "
            f"'{claimed_symbol.upper()}' — possible impersonation"
        )

    hard_fail = (
        verification.get("mint_authority")
        or liquidity is None
        or liquidity < min_liquidity_usd
        or pool_age is None
        or pool_age < min_pool_age_hours
        or (dex_symbol and dex_symbol != claimed_symbol.upper())
    )

    return ("PENDING_VERIFICATION" if hard_fail else "APPROVED"), warnings


def register_verified_token(symbol, mint, verification, claimed_decimals=6,
                             min_liquidity_usd=10_000, min_pool_age_hours=24):
    """
    Run a fetched verification result through evaluate_verification() and
    write the outcome into the registry. Prefers the on-chain decimals over
    whatever the caller claimed. Refuses to silently remap an existing
    symbol to a different mint. Returns (status, warnings, message).
    """
    symbol = symbol.upper()
    if symbol in TOKEN_REGISTRY and TOKEN_REGISTRY[symbol].get("mint") not in (None, mint):
        return None, [], f"{symbol} is already registered with a different mint — use a unique symbol"

    status, warnings = evaluate_verification(
        verification, symbol, min_liquidity_usd=min_liquidity_usd, min_pool_age_hours=min_pool_age_hours
    )
    decimals = verification.get("decimals")
    if decimals is None:
        decimals = claimed_decimals

    TOKEN_REGISTRY[symbol] = {
        "mint": mint if status != "REJECTED" else None,
        "decimals": decimals,
        "status": status,
        "min_liquidity_usd": min_liquidity_usd,
        "max_price_impact_pct": 2.0,
        "allowed_routes": ["Jupiter"],
    }
    if status == "APPROVED":
        msg = f"{symbol} verified and APPROVED — tradeable immediately"
    elif status == "REJECTED":
        msg = f"{symbol} REJECTED — {warnings[0] if warnings else 'failed verification'}"
    else:
        msg = f"{symbol} added as PENDING_VERIFICATION — not tradeable until a human reviews and approves it"
    return status, warnings, msg
