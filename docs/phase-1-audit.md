# Phase 1 — Repository audit and V1 baseline

Status: **complete, awaiting approval before Phase 2.**
Date: 2026-10-21. Upstream: `0xPunisher/crawlscan` @ `ddc31e2` (the October 9, 2026 public V1 snapshot),
imported with its original git history and authorship. MIT licence and copyright notice (`LICENSE`,
"Copyright (c) 2026 CRAWLSCAN") preserved unchanged.

This document records what the original code does, what was verified by running it, what is
broken or risky, how far V1 is from the target specification, and the decisions needed before
Phase 2. Nothing below is inferred from the live site's marketing unless it is explicitly marked
as a *claim*.

---

## 1. Baseline verification (what was actually run)

| Check | Result |
| --- | --- |
| Unit tests, Python 3.12.x (`python3.12 -m unittest discover -s tests`) | **634 / 634 pass**, 45.8 s |
| Unit tests, Python 3.13.x | **634 / 634 pass** |
| Runtime dependencies | **0** (standard library only; tests use a fake adapter, no network) |
| Live scan, Robinhood Chain mainnet, public RPC `rpc.mainnet.chain.robinhood.com` (chain id `0x1237` = 4663) | **Works** — real verdict produced (see below) |
| Outbound reachability from the build sandbox | GeckoTerminal, DexScreener, DexPaprika, Jupiter (lite-api), RugCheck, Solana public RPC, BNB public RPC (`0x38`), Base public RPC (`0x2105`), Robinhood public RPC — all reachable |

### Live baseline scan

Token `0xc04ad5f4b019a90f5bd3a1cc68e612c311a4bc74` ("Censored Hooks Privacy", CHP, Pons V2), picked
from crawlscan.fun's public `/api/recent` feed so a reference value existed:

```
stage launch      0.7 s
stage transfers   1.2 s
stage entries     1.9 s
stage wallets     4.8 s   20 holders dealt to 6 spiders
stage links      24.0 s   18 of 20 wallet histories unread at budget expiry
done             39.2 s   score=60 band=OK
                 "20 wallets → 18 operators, biggest holds 10.6% of float (6.1% of supply),
                  could move price −24% if sold"
                 2 proven `direct` links → 2 two-wallet operators (10.6%, 7.1% of float)
                 RPC requests: 40
```

crawlscan.fun's feed showed the same token at **OK 72** at around the same time. **These results are
not claimed to be equivalent.** The difference is fully explained by this run's coverage:
18 of 20 wallet histories were unread at the default 8 RPS on a free public endpoint, so the virgin
part was scored neutral (12.5 of 25 points) and the reason line says so. The live site runs on paid
Alchemy endpoints *(claim, README)*. This is exactly the behaviour the spec asks for: missing history is
reported as missing and never treated as clean.

**Implication for the remaining phases:** realistic coverage needs a paid or dedicated RPC per chain
(Alchemy / QuickNode for EVM, Helius for Solana). Public endpoints are fine for development and CI
smoke tests only.

---

## 2. Architecture of V1 (as found)

```
             ┌──────────────── server.py (ThreadingHTTPServer, stdlib) ───────────────┐
 browser ──► │ POST /api/scan → job id    GET /api/events?after=N  (polling, 300 ms)  │
 (index.html)│ GET /api/result /chart /early /recent /config /index /replay           │
             │ jobs dict + per-token 10-min cache + dedupe (BY_TOKEN) + semaphore     │
             │ bounded queue (MAX_CONCURRENT + SCAN_QUEUE_MAX) + per-IP rate limit    │
             └───────┬────────────────────────────────────────────────────────────────┘
                     │ thread per job
                     ▼
             engine.scan(token, emit)  — 25 s hard budget, emits every step
                ├─ market.fetch_market (GeckoTerminal → DexScreener fallback, background thread)
                ├─ chain_of(): 0x+40hex → robinhood, base58/32B → solana
                ├─ launchpad detect: flap.detect → bankr.detect → Pons (robinhood.get_launch)
                ├─ adapter.token_facts → transfers, supply, excluded, market, reserve
                ├─ detect.supply_base / top_holders (top-20, dust < 0.1 % float dropped)
                ├─ adapter.classify_entries (buy vs transfer, eth_in, tx, block)
                ├─ 12-thread pool + _RankGate (largest first) → wallet_distinct_tokens (history)
                ├─ detect.wallet_signals → find_packs → find_links → operators (union-find)
                ├─ detect.score (5 weighted parts + hard/soft gates) → rug_projection
                └─ result dict  ──► opmem.record (queue) · recent_store · alerts snapshot
```

### Module map

| Module | Lines | Responsibility | Notes |
| --- | ---: | --- | --- |
| `server.py` | 1054 | HTTP API, job lifecycle, caches, rate limit, schedulers | Event delivery is client polling, not SSE/WebSocket |
| `engine.py` | 516 | Scan orchestration, budget, crawler events | Chain-agnostic apart from the launchpad dispatch at the top |
| `detect.py` | 564 | **Pure** detectors: holders, signals, packs, links, union-find operators, score, rug projection, early-buyer report | No I/O. The heart of the product, and well tested |
| `chains/robinhood.py` | 1116 | Pons V2: JSON-RPC, adaptive `eth_getLogs` windows, transfer cache, receipts, history, early buyers | Defines the de-facto adapter contract |
| `chains/solana.py` | 851 | pump.fun / PumpSwap / Raydium: ATA→owner, pre/post token balances, inner programs, PDA detection (own ed25519 on-curve check) | Holder list from `getTokenLargestAccounts` (lower bound on holder count) |
| `chains/flap.py` | 596 | Flap (Robinhood): Portal state, tax tokens, windowed history for large tokens, Multicall3 balances | Behind `FLAP_ENABLED` |
| `chains/bankr.py` | 983 | Bankr / Doppler V4 multicurve: V4Quoter impact curve, vesting, background holder index | Behind `BANKR_ENABLED`; README calls it "in progress" |
| `chains/priority.py` | 168 | Live / background / critical RPS throttles | See bug B2 |
| `market.py` | 352 | GeckoTerminal header and candles, DexScreener fallback, "too established" cache | |
| `early.py` | 158 | First-20-buyers report | |
| `opmem.py` | 313 | Operator memory: SQLite, async writer, `scans` / `operators` / `wallets` | **Write-only: never read back into a verdict** |
| `replay.py` | 322 | Rug Replay: DANGER scans → DexScreener mcap tracking → ≥ 90 % drop within 7 days | Also computes the clean-group drop rate |
| `alerts*.py` | 606 | Watchlist store, snapshot diff, Telegram notify, scheduled recheck | |
| `bot/` | 1122 | Telegram long-poll client of the HTTP API (`/scan`, `/watch`, `/watchlist`, `/unwatch`, `/rewards`) | Talks only to the API, never to a chain |
| `draw*.py`, `rewards*.py` | 1220 | $CrawlScan holder draw, burns, payouts | Financial feature: disabled by default (spec: isolate) |
| `memguard.py` | 175 | RSS soft and hard limits, cache relief, `malloc_trim` | |
| `index.html` | 554 KB | **Generated** from `design.html` (a Claude Design self-extracting bundle) by `scripts/build_frontend.py` string patches | See §5 |

### The chain adapter contract (input to Phase 2)

Every adapter is a module that exposes the following (taken from `engine.scan`, verified across the
four existing adapters):

```
CHAIN, REQUESTS:[int], RPS, PACK_WINDOW, BUNDLE_WINDOW, RUG_SNIPERS, [HISTORY_PARALLEL]
get_launch(token)                     -> {"block","curve","deployer","tx"} | None
token_facts(token, launch[, deadline])-> {"supply","transfers","excluded","market","reserve",
                                          "reserve_ok","base"|None, [q_factor, impact_curve, locked, dev, flap|bankr]}
classify_entries(token, transfers, wallets) -> {w: {"kind","tx","block","via","eth_in"}}
block_timestamps(blocks)              -> {block: unix}
wallet_distinct_tokens(w, before, skip_token, window, cap) -> int | None (None = unread)
is_contract(addrs)                    -> {addr: bool}
token_meta(token)                     -> {"name","symbol",...}
early_buyers(...), early_status(...)  (early.py)
```

This contract is a sound basis for Phase 2. It is implicit (duck-typed), though: there is no
interface definition, and the launchpad dispatch is hard-coded in `engine.scan`. Phase 2 makes it
explicit and adds a registry.

---

## 3. Defects and risks found in V1

| ID | Severity | Finding | Evidence | Planned fix (phase) |
| --- | --- | --- | --- | --- |
| **B1** | High | **Scan workers outlive the scan.** `ex.shutdown(wait=False, cancel_futures=True)` cancels only queued futures. History reads already running carry on after the verdict, and the pool threads are non-daemon, so `scripts/run_scan.py` never exits (killed by `timeout` at 100 s after a 39 s scan). In the server, those leftover reads go on consuming the shared RPC budget of the next scans. | Reproduced: the process was still alive after `done` at 39.2 s; a faulthandler dump shows the threads in `wallet_distinct_tokens → _scan_back → rate_limit` | Cooperative cancellation token checked between RPC calls (Phases 3 and 17) |
| **B2** | High | **The hard time budget is not hard.** `priority.Throttle` hands out *future* slots (`self.next += gap*n`). History batches already in flight have reserved slots several seconds ahead, so the `links` stage's `is_contract` call waits behind them: 15 s spent in `links`, scan done at 39 s against `BUDGET = 25`. | Event timestamps: `links` at 24.0 s → `done` at 39.2 s with only 1–2 RPC calls in between | Cancel and refund reserved slots on budget expiry; priority lane for post-crawl stages (Phase 17) |
| S1 | Medium | Request bodies are read with `rfile.read(Content-Length)` and no upper bound (memory exhaustion). | `server.py:762, 851, 873` | Body cap of 64 KB, plus `413` (Phase 5 or 16) |
| S2 | Medium | `CF-Connecting-IP` is trusted from any client. Outside Cloudflare, the per-IP rate limit can be bypassed by spoofing it. | `scan_client()` | Trusted-proxy allowlist (`TRUSTED_PROXIES`) (Phase 16) |
| S3 | Info | No authentication at all (public by design). For a private deployment this is the main gap. | — | Phase 16 |
| R1 | Medium | One process holds all state (jobs, caches, Bankr index) in memory. A restart loses running scans and the Bankr index, and only one replica can run. | `JOBS`, `_TCACHE`, `_INDEX` globals | Redis-backed queue and job state, separate worker process (Phases 5 and 17) |
| R2 | Medium | SQLite with one file lock is shared by four stores. Tests log `database is locked` under contention (handled, but the writes are lost). | Test output | PostgreSQL for the memory brain (Phase 11); SQLite kept as the zero-dependency dev fallback |
| R3 | Low | `opmem` records but never reads; `operator_id = sha1(sorted wallets)` only matches *identical* sets across tokens, so a recurring crew with one wallet more or less is invisible. | `opmem.py` | Entity resolution over wallet-level edges, not set hashes (Phase 11) |
| R4 | Low | Funding-graph links are fully disabled (`USE_FUNDING = False`, Solana `NotImplementedError`), so the "common funder" evidence class does not exist in V1. | `engine.py`, `chains/solana.py` | Phase 6, with hub and CEX discounting |
| Q1 | Low | ResourceWarnings: unclosed files in `tests/test_timeline.py` and `tests/test_stability.py`. | Test output | Opportunistic fix |

None of these change V1 verdict semantics. B1 and B2 are fixed before any feature work that adds
RPC load (the Phase 3 holder crawler) and are covered by regression tests.

---

## 4. Gap analysis: V1 versus the 20-phase specification

Legend: ✅ in V1 and tested · 🟡 partial · ❌ absent · 🔒 claimed by the live product, cannot be inspected

| Spec area | V1 state | Gap |
| --- | --- | --- |
| P2 Robinhood: Pons V2 | ✅ | — |
| P2 Robinhood: Flap | ✅ (flag) | — |
| P2 Robinhood: Bankr | 🟡 (flag; README says "in progress") | Integration test on mainnet tokens |
| P2 Robinhood: Pools, Clanker | ❌ 🔒 | New adapters; contract addresses must be discovered on-chain |
| P2 Solana: pump.fun / PumpSwap / Raydium | ✅ (flag) | — |
| P2 Solana: Bonk.fun, StonkFun, custom/tokenized-stock pairs, Meteora DLMM, Token-2022 extensions | ❌ / 🟡 (Token-2022 program id known) | New launchpad detectors and pool readers |
| P2 BNB: four.meme, Flap, Clanker | ❌ 🔒 | New EVM chain and launchpad adapters |
| P2 Base: Clanker, smart wallets (ERC-4337 / EIP-7702) | ❌ 🔒 (V1 already handles the 7702 delegation prefix on Robinhood) | New chain and adapter |
| P2 EVM address → chain disambiguation | ❌ (`0x` means Robinhood, always) | Multi-chain probe plus manual selector |
| P3 Holder crawler, top-N methodology | ✅ (top 20, fixed) | Configurable depth, resumable cursors, explicit completeness level |
| P4 Six-crawler real event stream | ✅ (polling, real events) | SSE delivery; no change to event semantics |
| P5 Wallet forensics (P&L, hold time, sell multiple, funding source) | ❌ (only kind, eth_in, history depth, sniper, sold flag) | Phase 5 |
| P6 Relationship graph with provenance | 🟡 (links have kind, level and via; no timestamp, confidence or chain) | Persisted edge model, funding edges, UI graph |
| P7 Cross-token operators, cabals, smart crews | ❌ 🔒 | Requires P11 memory first |
| P8 Scoring v1 (35/25/20/10/10, bands, hard gates) | ✅ exactly as specified | Model versioning, rerun-under-version, data-health block |
| P9 Sell impact (x·y=k, V4 quoter curve, tax) | ✅ (constant product, Bankr quoter curve, Flap sell tax) | V3 and DLMM concentrated liquidity, aggregate multi-pool depth |
| P10 Launch bundles from launch history | 🟡 (only among current top holders) | Launch-block tracing with conservation checks |
| P11 Persistent memory brain | 🟡 (write-only SQLite) | PostgreSQL schema, read path into verdicts, leakage-safe labels |
| P12 Launch Radar | ❌ 🔒 | Needs a launch-event listener per launchpad plus auto-scan |
| P13 Rug Replay / Gem Replay / Proof of Work | 🟡 (Rug Replay only, 7-day window) | Gem Replay, full-denominator accuracy statistics |
| P14 Terminal UI | 🟡 (generated single file, see §5) | Radar, proof, wallet, graph and i18n pages |
| P15 Telegram bot | 🟡 (scan and watch; public) | `/wallet`, `/radar`, `/status`, allowlist, new alert types |
| P16 Private access | ❌ | Auth, private-network mode; financial modules isolated |
| P17 Reliability | 🟡 (queue, rate limit, cache, memguard) | Redis queue, SSE, metrics, provider failover and budgets, B1/B2 |
| P18 Deployment | 🟡 (Procfile only) | Docker Compose, Postgres, Redis, TLS proxy, backups |
| Extra providers you listed | 🟡 GeckoTerminal and DexScreener are already used | DexPaprika (third market source and cross-check), Jupiter (Solana price and token list), RugCheck (Solana mint and freeze authority, LP lock — shown as *external* evidence and never folded silently into our score), Helius (enhanced transactions, DAS `getTokenAccounts` for full Solana holder lists, which fixes V1's lower-bound holder count) |

### What cannot be reproduced exactly, stated up front

* The private repository after October 9, the live product's model parameters, and its
  "200,000+ scans" memory are not available, and I do not try to infer them. Live-only features
  (Clanker, four.meme, cabals, crews, radar, Gem Replay) will be **independent implementations**
  built from on-chain evidence, documented as such, and tested against real mainnet tokens.
* Agreement with crawlscan.fun scores will be reported as observed agreement with coverage stated,
  never as equivalence (see §1).
* "Smart crew" requires a backtested profitability definition. It will be built only once the local
  memory holds enough of *your* scans to evaluate it without look-ahead bias.

---

## 5. Frontend assessment

`index.html` (554 KB) is **not source code**. It is a Claude Design export, a JSON-encoded
template inside a self-extracting bundler, which `scripts/build_frontend.py` post-processes with
about 40 string-replacement patches. Extending it with eight new pages (radar, proof, wallet,
developer, cluster graph, watchlists, settings, i18n in eight languages) would mean writing more
string patches against an opaque blob. That is the technical debt your rules forbid.

The visual language is simple and well defined (near-black `#07090b`, charcoal panels, neon green
`#00c805`, cyan `#9fd9ff`, monospace logs), and the five screenshots in the repo give a precise
reference. A rebuilt frontend can match it closely.

---

## 6. Proposed target architecture (for approval)

Rationale: your brief says to extend the Python engine, not rewrite it. `detect.py` and the
adapters are well-tested, dependency-free and correct, so they stay as the analysis core. The
changes are at the edges, where V1's single-process design runs out.

```
                ┌────────────────────────────── reverse proxy (Caddy, TLS, IP allowlist) ─┐
 web (Next.js 15│ /            → web                                                       │
 + Tailwind +   │ /api/v1/*    → api                                                       │
 shadcn/ui)  ───┤ /api/* (V1)  → api (V1 routes kept bit-for-bit for the bot and tests)   │
 Telegram bot ──┘                                                                         │
                ▼                                                                          
   api  (Python 3.12, ASGI: Starlette + uvicorn)  — auth, validation, SSE event stream     
     │  enqueue / dedupe (chain, token) / read results                                     
     ▼                                                                                     
   Redis  — scan queue (bounded), job state, event streams (XADD/XREAD), rate limits, caches
     ▼                                                                                     
   scan-workers (N)   engine.scan  ← existing engine, detect.py, chains/* (extended)       
   index-workers      resumable EVM log indexing, Solana account crawls (background)      
   radar-worker       launch listeners per launchpad → auto-scan → radar eligibility      
   outcome-worker     market snapshots → Rug/Gem Replay, Proof of Work labels             
   alert-worker       watchlist rechecks → diff → Telegram (dedupe, cooldown, priority)   
     ▼                                                                                     
   PostgreSQL 16 (+ TimescaleDB for price series)  — memory brain, scans, edges, outcomes 
```

Key choices and why:

1. **Engine stays standard-library Python.** It is the most tested part, and it is chain logic, not
   web glue. New dependencies go only into the service layer (`api/`, `workers/`), so the engine
   can still run on its own (`python -m crawler.scan <ca>`).
2. **ASGI API (Starlette) in front of the engine, not a Next.js API.** Your general preferences list
   Next.js/Node for backends, but your brief for this project says to keep the Python engine.
   Putting a Node API in front of Python workers would add a second language to the hot path for no
   gain. Next.js is used for the **frontend only**.
3. **SSE instead of 300 ms polling.** The same event objects, pushed instead of polled. The V1
   polling endpoints remain for backward compatibility.
4. **PostgreSQL relational graph, not a graph database.** Edges are `(chain, wallet_a, wallet_b,
   kind, tx, block, ts, confidence, provenance, scan_id)` with B-tree indexes. The queries needed
   (1–3 hops, connected components over a few thousand wallets) are fast in Postgres with recursive
   CTEs and in-memory union-find. A Neo4j service would add operational cost without a measured
   need. TimescaleDB is used for the price and market-cap time series that drive replay and proof.
5. **Financial modules (`draw*`, `rewards*`) are moved to `legacy/financial/`** behind flags that stay
   off by default. No signing code is added anywhere.
6. **Scoring is versioned.** `detect.score` is frozen as `score_v1` (golden tests against the current
   suite). Experimental models are separate modules, and stored scan inputs can be re-scored under
   any version for backtests.

### Proposed folder layout after Phase 2

```
crawler/            existing engine (engine.py, detect.py, market.py, early.py …) — moved, not rewritten
  chains/           robinhood/{pons,flap,bankr,pools,clanker} · solana/{pumpfun,bonk,stonk,…} · bsc/ · base/
  scoring/          v1.py (frozen V1 detect.score) · experimental/
api/                Starlette app, routes/v1, schemas (pydantic), auth
workers/            scan, index, radar, outcome, alert
db/                 migrations (Alembic), repositories
web/                Next.js + Tailwind + shadcn/ui frontend
bot/                Telegram bot (extended)
legacy/financial/   draw / rewards (disabled)
deploy/             docker-compose, Caddyfile, backup scripts
tests/              existing 634 tests + new unit, integration, db and e2e tests
docs/
```

---

## 7. Decisions I need from you before Phase 2

1. **Frontend:** (a) rebuild in Next.js + Tailwind + shadcn/ui, faithful to the V1 terminal look
   (recommended, see §5), or (b) keep extending the generated `index.html`?
2. **API layer:** Starlette (Python) as proposed in §6.2, or do you want a TypeScript/Next.js API
   anyway, to match your general stack preferences?
3. **RPC providers:** which do you have or will you get? Recommended: Alchemy (Robinhood, Base, BNB)
   and Helius (Solana). Without them I build and test against public RPCs, with the coverage limits
   shown in §1. Keys go only into `.env`, never into the repo.
4. **Hosting target:** one VPS with Docker Compose (recommended for a private tool), or Kubernetes?
5. **Financial modules:** OK to move `draw*` / `rewards*` to `legacy/financial/` (disabled), or delete
   them outright?

## 8. Phase 2 plan, once approved

1. Fix B1 and B2 (cancellation and budget enforcement) with regression tests. These are prerequisites
   for adding chains.
2. Restructure into the layout above with **no behaviour change** (all 634 tests still pass).
3. Formal adapter interface (Protocol) plus launchpad registry, replacing the hard-coded dispatch.
4. EVM chain abstraction (one JSON-RPC client with provider failover and per-provider budgets),
   shared by Robinhood, BNB and Base.
5. Chain disambiguation for `0x` addresses (parallel `eth_getCode` probe across the configured chains).
6. Add launchpads one at a time, each shipped only with a passing mainnet integration test:
   Clanker (Base, then BNB and Robinhood) → four.meme → Flap on BNB → Bonk.fun → StonkFun →
   Robinhood Pools → Meteora DLMM pools.
