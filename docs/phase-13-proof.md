# Phase 13: historical proof — Rug Replay, Gem Replay, Proof of Work

Status: implemented and applied to Supabase. 848 offline tests pass, and the real-SQL tests pass on Postgres 16.

## Immutable scan-time records

**Append-only.** Database triggers make `scans` and `market_snapshots` append-only: any UPDATE or DELETE fails, for
the database owner too. A measured `outcome` can be written once and never rewritten. Tested on real SQL: an edit of a
verdict, a deletion and a rewritten outcome are all refused.

**Fingerprinted.** Every scan row gets a **SHA-256 fingerprint** of its scan-time record. It covers chain, token, time,
verdict, score, model, every model's verdict, market at scan, rug projection, coverage, confidence, holders and
largest operator share. The database computes it as the row is written.
- **Checking it:** `GET /api/proof/scan?id=` recomputes the fingerprint and returns `fingerprint_ok`, along with the record, every later market snapshot and every measured outcome. This is the "record" link on each row of the page.
- **Production check:** all 10 existing scans were fingerprinted, and all 10 verify.

## Observing outcomes

**The tracker:** `GET /api/cron/track`. The GitHub workflow calls it every 30 minutes, right after radar discovery.
- **What it samples:** every token scanned in the last 7 days whose newest snapshot is over 25 min old, in batches of 30 per DexScreener request.
- **What it stores:** price, market cap and liquidity (summed over pairs), as append-only snapshots.
- **Delisted tokens:** a token DexScreener no longer lists is "no data", never zero.

This gives each scan an **observed** peak and trough. The daily outcome worker from Phase 11 keeps adding its 24 h and
7 d measurements.

## Definitions (`crawler/proof.py`)

- **Eligible scan.** Each token's **first** scan in the period that has a verdict and a market price. A token scanned many times counts once.
- **Reference value.** The market cap at scan, or the price when the market cap is unknown (the same ratio for a fixed supply).
- **Observation window.** (scan time, scan time + window]: 24 h, 72 h, 7 days (default) or 30 days. Snapshots taken by the scan itself are excluded.
- **Rug event.** The market cap fell at least **90%** (configurable: 50–99.9%).
- **Gem event.** The market cap rose to at least **3×** (configurable: 1.5–100×).
- **When an event is measured** (configurable):
  - **at any point** (default): the trough or peak seen in the window;
  - **at the window end**: the last snapshot.
- **Resolution.** An event is resolved as soon as it is seen. "No event" needs the window to pass with a snapshot in its last quarter. Anything else is **unresolved** and counted as neither.
- **Flagged.**
  - **Rug Replay:** the verdict was **DANGER**.
  - **Gem Replay:** the verdict was **CLEAN**.
  - **Model:** the live `crawlmeme-1`, or a shadow model, as stored at scan time.
- **Metrics:**
  - true and false positives and negatives;
  - **precision** = TP / flagged;
  - **recall** = TP / events;
  - each of these with a **95% Wilson interval**;
  - base rate;
  - **coverage** = resolved / eligible;
  - the unresolved count.

The page always shows the full denominator and the misses, not just the hits.

## The page: `/proof`

Three tabs. `/replay` and `/gems` redirect to the right tab; when the self-hosted V1 Rug Replay is enabled,
`/replay` stays the V1 page.
- **Proof of Work:** every eligible scan. Each row shows the scan time, market cap at scan, score and verdict, later market cap and when it was measured, the observed peak and trough with when they happened, the evaluation time, the number of snapshots, and both classifications.
- **Rug Replay:** DANGER-rated tokens whose market cap then fell 90% or more (the hits).
- **Gem Replay:** CLEAN-rated tokens that then rose 3× or more.

**Options:**
- **Settings:** window, period (7 / 30 / 90 / 365 days), drop %, multiple, when to measure, model, chain, minimum market cap at scan.
- **Which class to show:** any class can be listed: false positives, false negatives, true negatives, unresolved.

**Supporting links on each row:**
- the immutable record, with its fingerprint check and every later snapshot;
- the scan page;
- GeckoTerminal;
- the chain explorer.

**Wording.** A standing notice says historical results are not predictive certainty and that small samples swing
widely. That is why every rate carries its 95% range.

## Limits

- **The history starts now.** The proof covers CrawlMeme's own scans from this deployment on. There is no access to CrawlScan's private dataset, and none is assumed. With only 2 eligible scans today, the metrics will be wide and mostly unresolved for the first week; that is shown, not hidden.
- **Samples can miss moves.** Peaks and troughs are what the 30-minute samples saw, so a spike and collapse between two samples can be missed. Market data comes from DexScreener and GeckoTerminal.
- **One case per token.** Each token's first scan is the case. A later re-scan with a different verdict doesn't change it, which avoids picking the scan that matched the outcome after the fact.
- **Scale.** `proof_rows` returns up to 5,000 cases per query and is cached for 60 s. Past that size, precomputing per-scan aggregates would be the next step.
