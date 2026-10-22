# Phase 12: live launch radar

Status: implemented. 828 offline tests pass, and the real-SQL radar test passes on Postgres 16. Discovery was checked
live on all four chains.

## The page: `/radar`

The radar lists fresh tokens whose **latest real scan** meets your filters.
- **Every row is a stored scan.** It shows when it was scanned ("4m ago", with the exact UTC time on hover).
- **Default view:** the last 60 minutes, CLEAN and OK verdicts, CLEAN first.
- **Wider windows:** 3 h, 6 h, 24 h and 7 days.
- **Updates:** the page refreshes every 15 s (paused while the tab is hidden).
- **Filters:** chain, launchpad, window, verdicts, minimum score, maximum age, market-cap range, minimum liquidity, maximum launch-bundle exposure (% of supply still concentrated in launch wallets), maximum largest-operator share (% of float), minimum price change since the scan, and full coverage only.
- **Shareable views:** filters are kept in the URL, so a view can be bookmarked.
- **Two tabs:** **on radar** (active and downgraded tokens) and **dropped** (removed tokens, with the reasons).
- **Rescan on demand:** the **rescan** button runs a real scan now (`POST /api/scan` with `fresh: true`). It skips the 10-minute result cache unless the cached result is under a minute old, and the result goes through the usual rate limit.
- **Safe rendering:** every value is inserted as text, never as HTML.
- **Header link:** the main page has a "radar" link.

## Downgrade and remove rules (`crawler/radar.py: evaluate`)

| condition | result |
|---|---|
| latest verdict no longer in the allowed bands (e.g. now RISKY) | removed, "verdict risky (was clean)" |
| verdict slipped but still allowed (CLEAN → OK) | downgraded |
| a probably-rug projection on the latest scan | removed |
| largest operator's share fell ≥ 5 points between scans | downgraded (operator selling or moving) |
| launch bundle sold ≥ 5% more of supply between scans | downgraded |
| launch bundle still concentrated ≥ 25% of supply | downgraded |
| price or liquidity ≤ 50% of the scan's, measured after the scan | downgraded (market moved against the scan) |
| price ≤ 20% or liquidity ≤ 10% of the scan's | removed (collapse) |
| a measured outcome "rugged" (Phase 11) | removed |
| scan older than 45 min | downgraded (stale) |
| scan older than 3 h | removed (data too old) |

A market snapshot only counts if it was taken after the scan.

Thresholds can be changed through environment variables (`RADAR_WINDOW_MIN`, `RADAR_STALE_MIN`, `RADAR_EXPIRE_MIN`,
`RADAR_MIN_LIQ_USD`) or in the module.

## Data: the memory brain

- **`radar_feed(since)`** (SQL, server only) returns every token first seen in the window: launched then, or first scanned then if the launch time is unknown. Each row carries:
  - its latest scan and the scan before it;
  - its launch bundle at both scans;
  - the newest market snapshot;
  - any measured outcome.
- **`radar_recent`** tells discovery which tokens were scanned in the last 30 minutes.
- **`market_snapshot_record`** stores prices taken between scans.
- **Caching:** API responses are cached for 10 s per query, so many viewers polling every 15 s cost one database call per query.

## Discovery: GitHub Actions every 30 minutes

`.github/workflows/radar.yml` calls `GET /api/cron/radar?chain=…` for each chain at :07 and :37 past the hour.

Each call:
1. **Finds new pools** on the chain: GeckoTerminal `new_pools`, falling back to DexPaprika's newest pools when GeckoTerminal rate-limits. On DexPaprika the new token is the side that isn't a quote coin (native, wrapped native, a stablecoin, or a token that appears on one side of 3+ of the newest pools).
2. **Picks candidates:** pools created in the last 60 min, with at least $3,000 liquidity, not scanned in the last 30 min, deepest first.
3. **Scans them** with the real engine and records each scan to memory and the "recent" feed. No new scan starts after 22 s, so the call ends inside Vercel's 60 s limit.
4. **Takes a market snapshot** for up to 12 of the chain's radar tokens, so a collapse between scans is seen.
5. **Rescans the stalest qualifying radar tokens** if there is time left.

Access: `Authorization: Bearer $CRON_SECRET`, compared in constant time, and refused when the secret is unset.

Live check on 2026-10-22, without writing to memory:

| chain | new pools | source | candidates | scanned |
|---|---|---|---|---|
| Base | 20 | GeckoTerminal | 15 | 2: DANGER 20, DANGER 70 |
| Solana | 49 | DexPaprika (GeckoTerminal returned 429) | 4 | 2: DANGER 45, DANGER 26 |
| BNB | 20 | GeckoTerminal | 8 | 1: DANGER 65 |
| Robinhood | 50 | DexPaprika (GeckoTerminal returned 429) | 13 | 2: **CLEAN 82**, OK 78 |

Most fresh launches are DANGER. That is exactly why the radar shows only the ones that qualify.

## What you need to do

The workflow is in this PR. **GitHub runs scheduled workflows only from the default branch**, so discovery starts once
this PR is merged into `main`. Until then, the radar shows tokens people scan from the site.

1. **CRON_SECRET for GitHub.** In GitHub, go to linds444/crawlmeme → **Settings → Secrets and variables → Actions → New repository secret**. Add `CRON_SECRET` with the same value as in Vercel.
2. **Bypass secret.** Vercel Authentication protects the deployment, so a GitHub runner needs a bypass:
   - in Vercel, go to crawlmeme → **Settings → Deployment Protection → Protection Bypass for Automation** and create a secret;
   - add it in GitHub as `VERCEL_AUTOMATION_BYPASS_SECRET`.
3. **Optional:** a repository variable `RADAR_URL` if the site moves off https://crawlmeme.vercel.app.
4. **Test it.** After merging, start the workflow once from the **Actions** tab ("radar discovery" → Run workflow).

Don't paste any of these values into chat.

## Cost

- **GitHub Actions:** each run takes about 3–4 minutes of runner time (4 calls of up to a minute each). At 48 runs a day that is roughly 3,000–5,000 minutes a month, which is more than GitHub's free 2,000 minutes for private repositories.
- **Options:**
  - run every hour instead (change the schedule to `7 * * * *`);
  - make the repository public (Actions minutes are free for public repositories);
  - accept the overage.
- **Data APIs:** discovery uses no extra paid service. GeckoTerminal, DexScreener and DexPaprika are free, and the scans use your existing Helius and Alchemy keys within their free tiers.

## Limits

- **Only real scans.** The radar lists only tokens CrawlMeme has scanned: discovery, users and rescans. A launch that discovery skipped (too thin, or a busy run) appears only once someone scans it.
- **Markets between runs.** Market changes are seen only from discovery's snapshots (every 30 min) and rescans; the page itself doesn't fetch prices.
- **Timing.** Discovery picks the deepest pools first, and a 30-minute cycle can miss launches that rise and fall inside it.
