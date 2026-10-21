# Phase 3: holder crawler, DEX launches, coverage

Status: implemented, 691 offline tests passing; live mainnet suite passing on Robinhood, Base and BNB Chain.

## What changed and why

### 1. Tokens launched straight into a DEX pool (`crawler/chains/evm/launchpads/dex.py`)
V1 only recognised Pons V2 tokens; Phase 2 added Clanker, Flap, four.meme and Bankr. A token deployed by hand and
paired on Uniswap (common on Robinhood Chain) failed with "not a Pons V2, Flap or Clanker token". The `dex` plugin
is a **fallback tier**: it runs only after every launchpad plugin said no, so a launchpad token is never mislabelled.

| Chain | DEXes | Versions |
|---|---|---|
| Robinhood | Uniswap | V2, V3, V4 |
| Base | Uniswap | V2, V3, V4 |
| BNB Chain | PancakeSwap, Uniswap | V2, V3 |

- Pools are found by `getPair`/`getPool` against the factory for every quote token and fee tier (one batched round),
  plus PoolManager `Initialize` events for V4.
- Launch block = first transfer into a pool; holder history is read from the mint (`history_from`), so pre-pool
  distributions (team wallets, airdrops) are visible.
- Deployer = mint recipient, or the transaction sender when the recipient is a contract.
- Sell impact uses the deepest pool: V2 from reserves, V3 from QuoterV2 (`sqrtPriceX96After` against `slot0`), and V4
  from the V4 quoter.
- Switch: `DEX_ENABLED=true`.

### 2. Resumable history index (`crawler/chains/evm/history_index.py`)
Before this, every scan re-read a token's whole transfer history. A large history either used up the scan budget or
never finished. Now:
- History is stored per (chain, token) in SQLite as zlib blobs, capped by `INDEX_MAX_MB` with LRU eviction.
- A repeat scan reads only the last `REORG_LAG` (64) blocks plus whatever is newer. Measured on mainnet: half the
  requests of the first scan.
- A scan that runs out of time saves a checkpoint and answers "Indexing this token's history: X% read so far. Scan
  again to continue". The next scan resumes from the checkpoint instead of starting over.
- Verification checks:
  - **Conservation:** rebuilt balances sum to `totalSupply`, with no negative balance.
  - **`balanceOf`:** the 25 largest rebuilt balances match the chain at the head block. This catches lost
    holder-to-holder transfers, which a sum-only check misses.
- Switch: `HISTORY_INDEX=off` disables the index.

### 3. Coverage (`crawler/coverage.py`)
Every result now carries `coverage: {level, reasons, holders, wallets, sources}`:
- **full:** balances exact and verified, and every examined wallet read.
- **partial:** a few wallets unread, a Solana holder count that is a lower bound, or balances that could not be
  verified.
- **limited:** more than half of the wallets unread, history read in windows, or verification failed.

A limited scan is **never CLEAN**: its score is capped at 79, with a note naming the cause. V1 scoring is otherwise
unchanged. The cap never raises a verdict.

### 4. Holder details
- `holders[].holder_type` is wallet or contract. Contracts stay holders: they are labelled, not excluded.
- EIP-7702 delegated EOAs count as wallets.
- `SCAN_DEPTH` sets how many top holders are examined (5–100, default 20 as in V1).

### 5. Provider robustness
- An RPC key that is not enabled for a network (Alchemy's "BNB_MAINNET is not enabled for this app") now disables
  that provider for the chain. Requests go to the other providers instead of failing the scan.
- The recent-window mint search no longer raises when no provider can serve it. The scan reports that the history
  is unavailable instead.

## Live results (mainnet, 2026-10-21)
| Chain / pad | Verdict | Time |
|---|---|---|
| robinhood / clanker | DANGER 22 | 23s |
| robinhood / dex (Uniswap) | RISKY 59 | 23s |
| bsc / flap | OK 78 | 22s |
| bsc / fourmeme (fresh launch) | TOO_EARLY | 5s |
| base / clanker (fresh launch) | TOO_EARLY | 24s |

## Security notes
- Read-only RPC only; nothing signs.
- Provider URLs (which contain API keys) are never logged. Provider status shows names only.
- The index is local and per instance. On Vercel it lives in `/tmp` and is ephemeral until Postgres arrives in Phase 11.

## Scalability notes and limits
- **Vercel:** each instance has its own `/tmp` index, so a scan landing on a cold instance re-reads the history. A
  shared store (Supabase Postgres) fixes this in Phase 11.
- **Alchemy free tier:** `eth_getLogs` is limited to 10 blocks. Old BNB/Base tokens rely on the Transfers API.
  publicnode serves only about 8,000 blocks of history.
- **V3/V4 sell impact:** the quoter grid is an estimate at grid points, not a full tick walk.
