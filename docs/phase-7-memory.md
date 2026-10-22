# Memory brain (Supabase) and Phase 7: repeat operators and cabals

Status: implemented. 750 offline tests pass, plus a real-SQL integration test against Postgres. The schema is applied to the
Supabase project `crawlmeme` (us-east-1). **Memory switches on in production as soon as `SUPABASE_SECRET_KEY` is set
in Vercel** (see "What you need to do").

Phase 11's memory brain was built first, at your request, so that Phase 7 has earlier scans to compare with.

## Architecture

```
scan ──► engine ──► memory_lookup(chain, token, top holders, their funders, deployer)   ← one RPC, ~60 ms
                     │   (other tokens only)
                     ▼
                cabals.analyse → result.memory  (crews, repeat funders, seen wallets, deployer history)
                     ▼
server ──► memory_record(the finished scan, normalised)                                ← one RPC, one transaction
```

- **Supabase Postgres, reached through its REST API (PostgREST RPC).** The engine stays standard-library-only, so
  there is no database driver to install on Vercel and no connection pool to manage in serverless functions.
- **Two calls per scan,** both server-side Postgres functions in `supabase/migrations/20261022120000_memory.sql`:
  - `memory_lookup` reads;
  - `memory_record` writes the whole scan in one transaction.
- **Each scan is recorded before it is marked done.** On Vercel nothing runs after the response, so a write queued
  for later could be lost.

## Schema

All tables are keyed by `chain` plus address.

| table | one row per | notes |
|---|---|---|
| `tokens` | token | ticker, launchpad, deployer, last verdict, scan count |
| `scans` | finished scan | band, score, coverage, holders, largest merged operator, model version, rug projection |
| `wallets` | wallet ever among the analysed holders | first and last seen |
| `holdings` | (scan, wallet) | rank, share, entry kind, flags, merged operator, funder |
| `funding` | wallet | its first native deposit before entry: a historical fact, written once |
| `funder_stats` | funder | outgoing count and hub verdict, refreshed when re-measured |
| `edges` | (scan, a, b, kind) | the Phase 6 evidence graph |

## Security

- **Locked to the server.** Row-level security is on for every table with no policies. The `anon` and
  `authenticated` roles have no privileges on the tables or the two functions, and both functions are executable by
  `service_role` only.
- **Verified from outside.** Using the project's publishable key against the live project:
  - both functions return `permission denied for function`;
  - every table returns `permission denied`.
- **The key never leaves the server.** The server uses a secret key (`sb_secret_…`), held only in the server
  environment:
  - it is sent in the `apikey` header only (a legacy `service_role` JWT is also sent as a Bearer token, as Supabase
    requires);
  - it is never put in a URL and never logged; errors print the HTTP status or the exception type only;
  - it is never sent to the browser;
  - the client refuses any non-HTTPS endpoint, except localhost for tests.
- **Fails safe.** Memory is evidence, never a dependency. If it is unreachable or slow (3 s lookup cap), the scan
  goes on and `result.memory` is null.
- **Supabase advisors.**
  - Security: only "RLS enabled, no policy". That is intended, because the server-only access is by design.
  - Performance: "unused index", which is expected on a new, empty database.

## Phase 7: what a scan now reports (`result.memory`, "seen before" panel)

**Leakage-safe:** the SQL excludes the scanned token's own earlier scans, so a token is judged only by what its
holders, funders and deployer did elsewhere.

| finding | definition |
|---|---|
| **crew** | top holders that were top holders of **2 or more other tokens together** (`CREW_MIN_TOKENS`). Sharing one other token is common for active traders; sharing several is a crew. Linked pairs are merged into groups |
| **serial deployer** | the deployer has 2 or more other tokens in memory, with how many were rated DANGER |
| **repeat funder** | a funder of this scan's holders that also funded top holders of other tokens. Hubs (exchange-like, from this scan's hub check or memory's stored one) are listed apart and never treated as evidence |
| **seen wallets** | each top holder's appearances among other tokens' top holders, with those tokens' verdicts |
| **danger regulars** | 2 or more top holders that each sat in the top of 2 or more DANGER tokens |

**Score unchanged.** Memory starts empty and needs real history before cross-token findings can be weighted without
look-ahead bias (Phase 1 audit). The findings are shown as evidence; weighting them is part of the versioned scoring
work (Phase 8). The scan already records `model` with every row so that later model versions can be compared.

## Verification

- **Offline tests (14 new, all mocked):** the client (headers, HTTPS only, key never logged, failure degrades to
  null, payload built from a real engine result) and the cabals logic.
- **Real SQL:** an integration test (`CRAWLER_PG=…`) runs the migration's functions on a local Postgres 16 behind a
  stand-in for Supabase's REST API.
- **End to end:** 3 live mainnet scans were recorded through it, with lookups taking about 60 ms. Seeding two earlier
  tokens that shared holders and the deployer with a live Robinhood token produced the expected crew, serial
  deployer and seen-wallet findings on the page.
- **On Supabase itself:** `memory_record` and `memory_lookup` were run as `service_role`. The test rows were deleted;
  the database is empty.

## What you need to do

1. Open Supabase → project **crawlmeme** → **Settings → API Keys** → **Secret keys**.
2. Create or copy a secret key.
3. Add it in Vercel → crawlmeme → **Settings → Environment Variables** as `SUPABASE_SECRET_KEY`. Mark it
   **Sensitive**, and select **Production** (and **Preview** if you want previews to remember too).
4. Redeploy. `SUPABASE_URL` is already set.

Do not paste the key into chat or commit it.

## Limits

- **Memory starts empty.** Findings appear as scans accumulate. The first scans of each chain see nothing.
- **Analysed holders only.** Holders are recorded for the analysed top (`SCAN_DEPTH`), not the full holder list,
  which keeps a row count of about 20 per scan.
- **Free-tier storage.** The Supabase free tier allows a 500 MB database, which is roughly millions of holdings
  rows. Retention or archiving can be added in Phase 18 (backups).
