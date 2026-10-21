# Phase 2 — Four-chain adapter system

Status: **complete for EVM; Solana launchpads beyond pump.fun blocked on a Helius key.** Awaiting approval before Phase 3.
Date: 2026-10-21. Everything marked *verified* was observed on mainnet in this phase; nothing is inferred from the
live product's claims.

## 1. Support matrix

A launchpad is **supported** only when its mainnet integration case passes (`CRAWLER_LIVE=1 python -m unittest
tests/test_live_launchpads.py`) and its recorded-mainnet replay passes offline in CI (`tests/test_evm_launchpads.py`).

| Chain | Launchpad | Status | Identification | Sell impact | Evidence |
| --- | --- | --- | --- | --- | --- |
| Robinhood | Pons V2 | ✅ V1 (unchanged) | factory launch event | curve / V4 pool reserve | upstream tests |
| Robinhood | Flap | ✅ V1 (unchanged) | suffix + Portal state | virtual curve / V2 reserves | upstream tests |
| Robinhood | Bankr | 🟡 V1 (unchanged, upstream "in progress") | suffix + Airlock | V4Quoter curve | upstream tests |
| Robinhood | **Clanker v4** | ✅ **new** | `tokenDeploymentInfo` | V4Quoter curve | live + replay; headline equal to crawlscan.fun's on one token (§4) |
| BNB Chain | **four.meme** (TokenManager V2) | ✅ **new** | Helper3 `getTokenInfo` | Helper3 `trySell` curve; PancakeSwap V2 reserves after graduation | live + replay |
| BNB Chain | **Flap** | ✅ **new** | suffix + Portal `getTokenV8Safe` | virtual curve / V2 reserves, sell tax as q-factor | live + 2 replays (BNB- and stock-quoted) |
| BNB Chain | **Clanker v4** | 🟡 implemented, live case skipped | `tokenDeploymentInfo` (factory verified on 2 tokens) | V4Quoter curve | no launch inside the public window; needs `ALCHEMY_API_KEY` to test older tokens |
| Base | **Clanker v4** | ✅ **new** | `tokenDeploymentInfo` | V4Quoter curve | live + replay |
| Solana | pump.fun / PumpSwap / Raydium | ✅ V1 (unchanged) | bonding-curve PDA | curve / pool reserves | upstream tests |
| Robinhood | Pools | ❌ not started | — | — | no contract identified yet |
| Solana | Bonk.fun, StonkFun, Meteora DLMM, custom stock pairs | ❌ blocked | — | — | public Solana RPC rate-limits `getTokenLargestAccounts` (HTTP 429): needs `HELIUS_API_KEY` |
| Base | Flap | ❌ not listed | Portal `0x00214f8C…` exists | — | no launch observed in ~1.7 h of blocks |
| BNB / Base | "other standard pools" | ❌ not started | — | — | planned on the generic adapter once a reliable identification rule exists |
| Base / BNB | Clanker v0–v3.1 (older factories) | ❌ | — | — | `tokenDeploymentInfo` does not exist there |
| BNB | four.meme V1 tokens (2024) | ❌ | — | — | different events |

Defaults: every new chain and launchpad is behind a switch (`BSC_ENABLED`, `BASE_ENABLED`, `CLANKER_ENABLED`,
`FOURMEME_ENABLED`, `FLAP_ENABLED`); with none set, behaviour and request counts are exactly V1's.

## 2. What was built

```
crawler/chains/evm/
  keccak.py      Keccak-256 (stdlib): every selector/topic is computed and tested from its Solidity signature
  abi.py         decoding helpers (words, addresses, int256, strings, address[]) and call encoding
  client.py      multi-provider JSON-RPC client per chain (routing by capability, failover, learning, metrics)
  networks.py    Robinhood / BNB / Base configs; providers from env (own URLs, Alchemy, public endpoints)
  token.py       generic EVM token adapter (the engine's adapter contract) + plugin interface
  v4.py          Uniswap V4 quote encoding, Multicall3 tryAggregate, impact curve (shared by Clanker, four.meme)
  launchpads/    clanker.py, fourmeme.py, flap.py (BNB)
crawler/chains/registry.py   chain/launchpad registry, parallel chain location of 0x addresses
```

Engine integration: `engine.chain_of(token, chain=None)` and `engine.scan(token, emit, chain=None)`.
- With only Robinhood enabled, a `0x` address maps to Robinhood with no request (V1).
- With BNB/Base enabled, the address is located by one bounded `eth_getCode` per chain in parallel (4 s, one attempt,
  ≤ 2 providers): found on one chain → scanned there; on several → `AmbiguousChain` (API: **409** with the chain list,
  for the manual chain selector); a chain whose RPC did not answer is reported as unknown, never silently dropped.
- API: `POST /api/scan {"token", "chain"?}`; `/api/config` lists enabled chains and launchpads.

### Design decisions and why

1. **Plugins on one generic adapter, not one adapter per chain.** Holder reconstruction, entry classification, history
   and data-source routing are the same on every EVM chain; only "how did this token launch, where is its liquidity,
   what does a buy look like" differs. That part is ~100–200 lines per launchpad.
2. **Quotes for concentrated liquidity.** Clanker pools are single-sided V4 positions: the PoolManager's token
   balance says nothing about price impact. Impact comes from simulated swaps (V4Quoter, four.meme `trySell`) over a
   share grid in one Multicall3 call. A quote that reverts with `NotEnoughLiquidity` means "this sale cannot be
   absorbed" and counts as a 100 % drop — it is never treated as missing data.
3. **Capability routing instead of one RPC URL.** Free tiers differ more than expected (§3). Requests go only to
   providers that can serve them, log scans to the widest one, and providers' own refusals refine the capabilities.
4. **Explicit unavailability.** A history no provider can serve raises `HistoryUnavailable` (a user-facing scan
   error that names the missing key); a partial history is never scored as complete.
5. **Never mislabel units.** Flap and four.meme tokens are often quoted in ERC-20s (observed: a tokenized Trump Media
   stock, DJTB). Their buys are buys, but the cost is not reported as BNB.

## 3. Provider measurements (2026-10-21)

| Endpoint | getLogs with address | getLogs without address | History (logs/receipts) | Historical state | Notes |
| --- | --- | --- | --- | --- | --- |
| Robinhood public `rpc.mainnet.chain.robinhood.com` | ≤ 10,000,000 blocks | ≤ 30,000 blocks | full | **none** ("historical state … is not available") | rejects Python's default User-Agent (403) |
| Alchemy Free (all chains) | **≤ 10 blocks** | ≤ 10 blocks | full | full | Transfers API (`alchemy_getAssetTransfers`) on the free tier: full token and wallet history |
| BNB publicnode | ✓ (≥ 20k results refused) | refused | ~8,000 blocks (~1.7 h) | ~8,000 blocks | receipts **HTTP 403**; filters with ≳ 5–19 addresses → 403; 403 also when throttled |
| BNB Binance dataseed | refused ("limit exceeded") | refused | full (blocks, receipts) | **~128 blocks** | receipts ✓ |
| Base publicnode | ✓ (≥ 20k results refused) | refused | ~8,000 blocks (~4.4 h) | ~8,000 blocks | receipts 403 |
| Base official `mainnet.base.org` | refused ("request limit reached") | refused | full | **full archive** | receipts ✓ |
| Solana public | — | — | — | — | `getTokenLargestAccounts` HTTP 429: not usable for holder scans |

Consequences with **no keys** (public endpoints only):
- Robinhood: every token is scannable (Pons, Flap, Clanker); wallet histories are mostly unread within the budget
  (address-less queries cap at 30k blocks), so virgin/short-history signals are often "unknown" and the score is
  lower-confidence — reported as such, never as clean.
- BNB / Base: tokens younger than ~1.7 h (BNB) / ~4.4 h (Base) are fully scannable; older ones raise
  `HistoryUnavailable` until `ALCHEMY_API_KEY` is set. Wallet histories need the Transfers API (Alchemy).
- Solana: needs `HELIUS_API_KEY` (or `SOLANA_RPC`).

## 4. Verification

| Check | Result |
| --- | --- |
| Offline suite (Python 3.12) | **661 tests: 656 pass, 5 skipped** (the 5 live mainnet cases, run with `CRAWLER_LIVE=1`); V1's 634 − 63 removed financial tests + 90 new |
| Replays of recorded mainnet traffic (5 tokens, 4 launchpads, 3 chains) | pass: supply conservation exact (balances rebuilt from transfers = minted supply), infrastructure excluded, impact curve monotonic in [0, 1], entries classified, full engine verdict |
| Live mainnet suite (`CRAWLER_LIVE=1`) | 4 pass, 1 skipped (BNB Clanker: no launch in window): Robinhood Clanker DANGER 22 in 23.0 s · Base Clanker OK 60 in 12.1 s · BNB Flap 5.4 s · BNB four.meme OK 68 in 4.2 s |
| Comparison with crawlscan.fun (one public scan, token `0xc527…ab07`, Robinhood Clanker) | **same headline** ("4 wallets → 2 operators, biggest holds 99.7% of float (1.0% of supply), could move price −100% if sold"), same top-holder shares (88.8 %, 9.96 %, 0.92 %, 0.14 %), same circulating (1.0 % of supply), same band DANGER; score differs (theirs 3, ours 22) because all four wallet histories were unread on the public node (virgin part scored neutral). One token: agreement on this case, not equivalence. |
| Contract addresses | every address in `networks.py` and the plugins has code on-chain; factories, managers, portals and quoters were verified functionally (a call that returns the expected token / reverts for a foreign one) |

Bugs found and fixed while doing this (beyond Phase 1's B1/B2):
- `market.py` matched EVM addresses case-sensitively except on Robinhood; DexScreener returns checksummed addresses, so
  every Base/BNB pair was dropped (no price, no market cap).
- `.env` inline comments (`FLAP_ENABLED=true   # …`) made a switch silently *off*; the loader now strips them.
- `chains/robinhood.py` crashed at import without `CRAWLER_RPC`, so a BNB/Base-only deployment could not start.
- Request bodies were unbounded (audit S1): now 413 above 64 KB without reading.
- A slow public node could hold a scan for minutes inside chain location / launch location: probes are bounded and the
  creation mint is found newest-first with a conservation stop (mints found ≥ totalSupply), not by reading from
  genesis.

## 5. Security notes

- Provider URLs carry API keys (Alchemy, Helius): never logged, never in errors, health output, fixtures or the browser;
  tests assert this.
- All RPC use is read-only; no signing code exists anywhere.
- External contract addresses found from third-party sources (clanker.world, an npm SDK) were trusted only after an
  on-chain functional check.
- Downloaded third-party code (the npm SDK) was read as data in an isolated directory and never executed.

## 6. Scalability and known limits

- The V1 HTTP server still holds jobs and caches in process memory (Phase 1 R1); Phase 5 moves the scan path behind
  the Starlette API and a worker queue.
- Wallet-history coverage on free tiers is the main quality limit (§3). Phase 3 adds an explicit per-scan coverage
  report (today an unread wallet is flagged in its signals, but the result's `unread` list only lists wallets the
  budget cut off, not wallets no provider could read).
- Large BNB/Base histories through the Transfers API cost one request per 1,000 transfers; `SCAN_MAX_LOGS` caps a scan.

## 7. What needs you

1. `ALCHEMY_API_KEY` and `HELIUS_API_KEY` as environment secrets (a new session picks them up) — unlocks older
   BNB/Base tokens, wallet histories on BNB/Base, and the Solana launchpads.
2. A `main` branch to open the pull request against (the repository has only the working branch).
3. Hosting for the Python API and workers (Vercel serves the Next.js frontend; Supabase provides Postgres): Fly.io,
   Railway, Render or a small VPS — needed from Phase 5, decision can wait until then.
