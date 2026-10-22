# Phase 11: the persistent memory brain

Status: implemented and applied to Supabase. 808 offline tests pass, and the real-SQL tests (Postgres 16) pass too.

Phase 7 brought part of this phase forward (memory of tokens, scans, wallets, holdings, funding, funder checks and
evidence edges). Phase 11 makes it a full memory: more of each scan is kept, what happened to the tokens afterwards is
measured, and the history feeds the shadow models under rules that prevent leakage.

## What is stored (Supabase Postgres)

| entity | table | from |
|---|---|---|
| tokens, deployers | `tokens` | every scan |
| token launches | `launches` | block, time, tx, deployer, launchpad |
| wallets | `wallets` | analysed holders |
| balances and observations per scan | `holdings` | rank, share, entry, flags, operator, funder, **P&L, cost, hold time, bundle role** |
| wallet transaction events | `wallet_events` | buys, sells, transfers of analysed holders (newest 30 each, raw amounts) |
| funding edges | `funding`, `funder_stats` | first funder, hub check |
| relationship edges | `edges` | Phase 6 graph with evidence |
| operator clusters | `operators` | merged groups (2+ wallets) |
| scan results and historical verdicts | `scans` | band, score, **every model's verdict, confidence, data health, market at scan time** |
| market prices at scan time | `market_snapshots` | price, market cap, FDV, liquidity, source |
| later market outcomes | `outcomes` | 24 h and 7 d after each scan |
| snipers and bundles | `bundles`, `bundle_participants` | Phase 10 |
| recurring developer groups | `entity_links` | deployers whose launches share 2+ bundle wallets |
| confirmed and unconfirmed observations | `observations` | each with its evidence |
| model versions and confidence changes | `scans.model`, `scans.scores`, `scans.confidence` | per scan, so changes over time are a query |

**Storage choices:**
- **No TimescaleDB.** It isn't justified: price data is a few rows per scan, kept in a plain table with a time index.
- **No graph store.** Dev groups are found with a recursive SQL query over `entity_links`, limited to 3 hops.
- **Locked to the server.** Row-level security is on with no policies, and only `service_role` can run the functions. Supabase's security advisor reports only the expected "RLS enabled, no policy" notice.

## Writing without delaying the result

- **The result goes out first.** The page receives the result as soon as the scan and the fast local stores finish.
- **The memory write follows.** It is one transaction, `memory_record`, capped at 4 s, and it runs after the result is sent.
- **The request stays open until the write finishes** (at most 8 s). On Vercel nothing runs after a response ends, so a write queued for later could be lost. This holds even if the browser has already disconnected.
- **Tested:** the page receives the result while a deliberately slow write is still running, and the request closes only after it.

## Reading: every scan queries the history

`memory_lookup` takes the scan's top holders, their funders and the deployer, and returns:
- wallets seen before, and crews;
- repeat funders, with hubs listed apart;
- the deployer's other tokens and its **dev group**;
- **recurring operator clusters**: 2+ current holders that were one operator in another token;
- prior **observations** about these wallets;
- the **market outcomes** of every related token.

## Rules against leakage and bias

1. **As-of reads (temporal joins).** Every read takes `p_as_of`, and only rows that existed before it count: scans, outcomes, funding, entity links and observations. A scan reads as of its own start time. A backtest passes its evaluation time, so it never sees a later outcome. Tested on real SQL: a lookup as of a time before an outcome was measured does not see it.
2. **No circular self-labelling.** Outcome labels come only from later market data, never from our verdicts:
   - **rugged:** liquidity ≤ 10% of what it was and price ≤ 20%;
   - **dead:** price ≤ 10%, liquidity still there;
   - **up:** price ≥ 2×;
   - **survived:** anything else;
   - **unpriced:** no market data 72 h past due.
3. **No survivorship bias.** Every scanned token with a price gets outcome rows at 24 h and 7 d, whatever its verdict.
4. **No self-evidence.** A token's own scans and outcomes never count as evidence about it.
5. **Documented evidence only:**
   - **Confirmed** observations are backed by a transaction: a shared transaction, a direct transfer, a funding transfer, or a launch transaction.
   - **Unconfirmed** observations are statistical: packs, co-holding, a common funder.
   - Hub funding is never recorded as an observation.

## Entity resolution

When a scan is recorded, its deployer is linked to any other deployer whose token's launch bundle shares 2 or more of
the same wallets. The link stores those wallets and both tokens as evidence, plus a confidence.

A **dev group** is the set of deployers connected through such links (up to 3 hops, as of the scan's start time).
Outcome history is counted over the whole group.

## Learning from outcomes

**The outcome worker.** `crawler/outcomes.py`, called through `GET /api/cron/outcomes`.
- **Schedule:** Vercel Cron, daily at 04:17 UTC. Daily schedules work on every Vercel plan; on Pro it can run hourly.
- **Each run:** it reads the due outcomes, measures each token once (GeckoTerminal, then DexScreener, then DexPaprika), labels it, and stores the label together with a fresh market snapshot.
- **Access:** `Authorization: Bearer $CRON_SECRET`, compared in constant time. Without the secret the endpoint refuses every request.

**How the history is used.** A new shadow model, **`crawlmeme-3`**, uses only leakage-safe evidence:

| adjustment | evidence |
|---|---|
| rug history −12, band no better than RISKY | the deployer or its dev group has 2+ earlier tokens labelled rugged, each with its measured time |
| rugged regulars −6 | 2+ top holders each sat in 2+ tokens that later rugged |
| recurring cluster −6 | current holders were one proven operator in another token |

- **Why it replaces `crawlmeme-2`'s history adjustments.** `crawlmeme-2`'s "DANGER history" adjustments use our own past verdicts as labels, which is circular. `crawlmeme-3` uses market outcomes instead.
- **Neither is live.** `crawlmeme-1` still gives the verdict. Both shadow models are stored with every scan, so their accuracy can be compared on real outcomes before one is promoted (`SCORE_MODEL`).
- **What grows with the database.** More labelled outcomes, more dev-group links and more clusters, so each new scan gets more context rather than just more rows.

## What you need to do

1. Generate a long random value (for example `openssl rand -hex 32`).
2. In Vercel, go to crawlmeme → **Settings → Environment Variables** and add it as `CRON_SECRET`, marked **Sensitive**, for **Production**.
3. Redeploy. Vercel Cron sends the secret automatically.

Don't paste the value into chat or commit it. Until it is set, the outcome worker is off, but scans still record everything.

## Limits

- **The first outcome labels appear 24 h after the first scans of this deploy**, so `crawlmeme-3` only starts to adjust once those exist.
- **Market-only labels can mislabel a token.** A token that moved its liquidity to a pool the market data sources don't see can read as "rugged".
- **Hub checks aren't time-stamped.** `funder_stats` keeps the latest hub check, not a history, so a funder can be read as a hub that wasn't one at an earlier time. The risk is low: it can only make the hub rule stricter.
- **`opmem.py` (local SQLite) is unchanged.** It remains the self-hosted store behind Rug Replay. On Vercel, the Supabase memory is the persistent brain.
- **Storage.** The Supabase free tier allows 500 MB. Wallet events are capped at 30 per holder per scan to stay well inside it; retention can come with backups (Phase 18).
