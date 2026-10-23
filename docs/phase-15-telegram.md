# Phase 15 — private Telegram bot and personal alerts

## Architecture

```
Telegram ──POST /api/telegram──▶ Vercel function ──▶ bot/core.py ──▶ engine scan / Supabase / radar
  (secret header, update id once)                         │
                                                          └─▶ crawler/tg_alerts.on_scan (every recorded scan)
GitHub Actions ──every 15 min──▶ GET /api/cron/alerts ──▶ radar eligibility · rechecks · delivery
Supabase: tg_chats · tg_watches · tg_snapshots · tg_alerts (queue) · tg_updates · tg_state
```

Why this shape:

- **A webhook, not a polling process.** The site runs on Vercel serverless functions: there is no machine to keep
  `getUpdates` running, and in-memory threads (the V1 notifier and rechecker) die with each request. Telegram POSTs each
  update to `/api/telegram`; the function handles it inside its 60 s limit (a scan takes 15–40 s).
- **Supabase for all bot state.** Watchlists, snapshots and the alert queue must outlive a request and be shared by
  concurrent function instances. The V1 SQLite store could do neither on Vercel, so it was removed rather than kept
  as a second, diverging alert system (`alerts_store.py`, `alerts_notify.py`, `alerts_recheck.py`, `bot/api.py` and
  the `/api/alerts/*` endpoints are gone).
- **Reused from the original client:** the Bot API client (`bot/tg.py`, token kept out of errors), the alert rules
  (`crawler/alerts.py`: snapshot + diff, extended), address parsing and verdict formatting (`bot/text.py`, rewritten
  for four chains). `bot/main.py` stays as a polling runner for local development, using the same bot core.

## Commands

| Command | What it does |
|---|---|
| paste an address, or `/scan <token>` | compact card: score and band, the primary warning, the largest operator (wallets linked to the explorer, impact if sold), the probably-rug scenario, top holder and deployer links with the deployer's memory record, market cap / liquidity / holders, **Full report** and **Watch** buttons |
| `/watch <token>` | adds the token to your watchlist (default limit 20, `TG_WATCH_LIMIT`) |
| `/watchlist` | each watched token with its latest band, rug and radar state, and an Unwatch button |
| `/unwatch <token>` | removes it (all chains for a 0x address unless one is named) |
| `/wallet <address>` | memory's record: tokens it top-held (latest scan, rank, share, verdict, market outcome), tokens it deployed, its funder (hub flag) and wallets it funded, linked operator groups, launch bundles, rugged / up counts, explorer links |
| `/radar` | tokens passing the launch radar over 24 h |
| `/status` | chains, memory, alerts, your watch count, alerts sent in 24 h, the last alert run |
| `/start`, `/help` | what the bot does, every command, how to read a verdict |

A 0x address can exist on Robinhood, Base and BNB Chain. A chain word in the message pins it ("base 0x…"). Otherwise,
when the address is on several chains, or some chains could not be checked, the bot asks with one button per chain.

## Alerts

| Kind | Fires when | Priority | Cooldown |
|---|---|---|---|
| rug_appeared / rug_worse | the probably-rug scenario appears, or its drop grows ≥ 15 points | 0 | 2 h |
| operator_sold | the previous largest operator's wallets hold ≥ 30% less | 0 | 1 h |
| verdict_worse | band falls; into DANGER always (priority 0), other steps when the score moved ≥ 8 | 1 | 2 h |
| early_dropped | the first buyers' supply share fell ≥ 30% | 1 | 3 h |
| coordinated | a new same-block pack holds ≥ 5% of float, or the launch bundle sold ≥ 10% more supply between scans | 1 | 3 h |
| deployer_repeat | a token launched < 48 h ago whose deployer has ≥ 1 earlier token labelled rugged by the market, or ≥ 2 rated DANGER | 1 | once per token |
| rug_gone | the scenario disappeared | 2 | 2 h |
| smart_crew | a fresh token's holders include a recurring crew whose shared earlier tokens went up (≥ 2, market outcome labels) | 2 | once per token |
| radar_eligible | a watched token became active in the default radar filters | 2 | 12 h |

Each watched-token alert is a change between two real scans. The deployer and crew alerts use market outcomes measured
before the scan (the Phase 11 memory brain), never CrawlMeme's own verdicts. Verdict improvements are not alerts:
they were noise in V1.

### Queue mechanics

- **Dedupe:** a unique `(chat, dedupe_key)`. The key includes the snapshot time, so the same change between the same two
  scans is never queued twice, even if two scans of a token finish at once.
- **Cooldown:** no second alert of the same kind for the same token while one is queued or was sent within the cooldown.
- **Priority:** delivery claims the most urgent first (0 → 2), then the oldest.
- **Merging:** one message per chat per token, so a verdict drop and a rug warning from the same scan arrive together.
- **Per-chat hourly cap:** default 20 (`TG_ALERT_HOURLY_CAP`). It counts alerts sent in the last hour, alerts in
  flight, and the batch being claimed; the rest wait.
- **Delivery faults:**
  - 429 → retried after Telegram's `retry_after`.
  - Network errors and 5xx → retried, up to 5 attempts.
  - 400 → failed.
  - 403 (blocked) → the chat and its watchlist are deleted.
  - Claims older than 5 minutes return to the queue; alerts still queued after 24 h are dropped.
- **Concurrency:** dispatchers claim with `FOR UPDATE SKIP LOCKED`, so two at once never send the same alert.
- **Urgent alerts** (priority 0) are delivered right after the scan that found them. Everything else goes out on the
  next cron run (≤ 15 min).

### Rechecks

`/api/cron/alerts` runs every 15 minutes:

1. Housekeeping.
2. Radar eligibility for every watch.
3. Rechecks of watched tokens not checked for `ALERTS_RECHECK_MIN` (default 30).
4. Delivery.

Each recheck is a full scan plus the early-buyers share (Robinhood and Solana). It runs only while at least 25 s of
budget remain. A token's first recheck stores its baseline snapshot, and alerts start from its second scan. Scans
from the site and the radar of a watched token count too.

## Security review

- **Private by default.**
  - Only Telegram user ids listed in `TG_ALLOWED_USERS` are served; an empty list serves nobody.
  - Everyone else gets a single line with their own id (so the owner can add it) and nothing else: no scan, and no
    database row about them.
  - Groups are ignored.
- **Webhook authentication.**
  - Telegram sends `X-Telegram-Bot-Api-Secret-Token`, compared in constant time with `TG_WEBHOOK_SECRET`; anything
    else is 401.
  - Without a bot token the endpoint is 404. The body is capped at the server's `MAX_BODY`.
  - Each `update_id` is handled once, so a Telegram retry cannot run a command twice.
- **Vercel protection.**
  - The deployment is behind Vercel Authentication. The webhook URL carries `x-vercel-protection-bypass` as a query
    parameter, because Telegram cannot send custom headers.
  - The setup endpoint never returns or logs that URL; it is redacted from Telegram error messages too.
- **Secrets.**
  - `TG_BOT_TOKEN` stays inside the Bot API URL (`bot/tg.py` redacts it from errors). `TG_WEBHOOK_SECRET` and
    `CRON_SECRET` are environment variables only.
  - `/api/config` caches whether a token is set, never the token itself.
- **Markup injection.** Tickers, names, reasons and addresses are HTML-escaped before they reach Telegram's HTML
  parse mode (tested with `<b>` / `<script>` tickers).
- **Callback data** is re-validated: the action is whitelisted, the chain is known, the address is well formed, and the
  user must be allowlisted again (anyone can forward a message with buttons).
- **Database.**
  - New tables: RLS on, no policies, and table privileges revoked from `anon` / `authenticated`.
  - Functions: `security invoker`, an empty `search_path`, and execute for `service_role` only.
  - The Supabase security advisor reports only the expected "RLS enabled, no policy" notice.
- **Read-only.** The bot never asks for a wallet, a seed or a key, and never signs anything.

## Scalability notes

- **Recheck throughput** is the main bottleneck: about one scan per cron run fits Vercel's 60 s function. 20 distinct
  watched tokens are rechecked about every 2–5 hours by the cron alone (site and radar scans add to it). Options when
  needed, in order:
  - Raise the workflow to several calls per run.
  - Shard `/api/cron/alerts` by chain.
  - Move rechecks to a long-running worker.
- **The webhook scan is synchronous.** A burst of pasted addresses from several users runs one function per update,
  which is fine for a private bot. A public bot would need a per-user rate limit and a queue.
- **Queue growth:** sent / failed / dropped rows are deleted after 30 days and update ids after 3 days. Snapshots exist
  only for watched tokens.
- **Global alerts** (deployer, crew) fan out to every chat with global alerts on. The per-chat cap bounds the noise,
  and the priority puts them behind urgent watched-token alerts.

## Setup (once)

1. **Create the bot.** Message @BotFather, send `/newbot`, and copy the token.
2. **Set the Vercel environment variables (Production):**
   - `TG_BOT_TOKEN`: the bot token.
   - `TG_ALLOWED_USERS`: your numeric Telegram user id. If you don't know it, message the bot once it is live and it
     replies with it.
   - `TG_WEBHOOK_SECRET`: 32+ random characters of `A-Z a-z 0-9 _ -`.
   - `SITE_URL` (optional): defaults to Vercel's production domain.
3. **Redeploy**, then register the webhook once:

   ```sh
   curl -H "Authorization: Bearer $CRON_SECRET" \
        -H "x-vercel-protection-bypass: $BYPASS" \
        https://crawlmeme.vercel.app/api/cron/telegram
   ```

   The response names the bot, how many users are allowed, and whether the bypass was used.
4. **The alert workflow** (`.github/workflows/alerts.yml`) uses the same `CRON_SECRET` and
   `VERCEL_AUTOMATION_BYPASS_SECRET` repository secrets as the radar workflow. It runs once the workflow is on the
   default branch.

## Tests

- `tests/test_telegram.py` (38 tests, no network):
  - **Access:** the allowlist, refusing strangers, ignoring groups.
  - **Bot commands:** the scan card, chain choice, plain errors, escaping, watch limits, the database being down.
  - **Detection:** diffs, partial scans, suspicious deployers, smart crews, radar eligibility.
  - **Delivery:** priority and grouping, cooldown, dedupe, the hourly cap, 403 / 429 / 400.
  - **Cron:** the budget and early buyers exiting.
  - **Endpoints:** webhook secret and dedupe, cron auth, setup (the bypass never echoed).
  - **Real SQL:** idempotency, watch limits, snapshot swap, cascade, queue dedupe / cooldown / priority / cap / retry,
    `wallet_profile`.
- `tests/test_alerts.py` covers the extended rules (rug_worse, coordinated pack and bundle selling).
- A live scan of a Robinhood token through the bot path rendered the card from real engine output.
