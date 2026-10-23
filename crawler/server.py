"""rh-crawler HTTP server (standard library, ThreadingHTTPServer).
Read-only: the scan runs in the background, the browser polls for events.

  POST /api/scan {"token": "0x...", "chain"?}  -> {"job": id, "chain": resolved chain}  (409 ambiguous_chain: {"chains"})
  GET  /api/events?job=ID&after=N        -> {"events": [...], "done": bool}  (events with i >= N)
  GET  /api/result?job=ID                -> {"done", "result" | "error"}
  POST /api/scan with Accept: text/event-stream  -> the scan's events as Server-Sent Events in the same response
  GET  /api/rescore?job=ID[&model=M]    -> {"live", "models": {id: {"score", "band", "gates", "adjustments"}}}
  GET  /api/stream?job=ID[&after=N]      -> Server-Sent Events of a job (Last-Event-ID resumes); docs/events.md
  GET  /api/holders?job=ID&offset=0&limit=100 -> {"total", "complete", "items": [{"rank", "wallet", "balance", "share",
                                            "share_supply", "analysed"}]}  every holder, largest first (limit ≤ 1000)
  GET  /api/chart?token=CA               -> {"token", "chain", "pool", "dex", "timeframe", "candles", "price_usd"[, "stale_at" | "unavailable"]}
                                            GeckoTerminal candles [[ts, o, h, l, c, v]] oldest to newest; not part of the scan
  GET  /proof                            -> proof.html (Phase 13: Rug Replay, Gem Replay, Proof of Work)
  GET  /api/proof?view=&window_h=&…       -> proof rows and metrics (proof.py); /gems and /replay redirect to /proof
  GET  /api/proof/scan?id=                -> one immutable scan record with its fingerprint check and later market
  GET  /api/history?chain=&token=       -> every scan record of one token, newest first, and its newest market snapshot
  GET  /i18n.js                          -> localization layer shared by every page (static/i18n.js)
  GET  /api/cron/track                    -> Phase 13 market tracker (Authorization: Bearer $CRON_SECRET)
  GET  /radar                            -> radar.html (Phase 12 launch radar)
  GET  /api/radar?window=&chain=&…        -> radar tokens with status and reasons (radar.py)
  GET  /api/cron/radar?chain=             -> Phase 12 discovery (Authorization: Bearer $CRON_SECRET; GitHub Actions)
  GET  /api/cron/outcomes                 -> Phase 11 outcome worker (Authorization: Bearer $CRON_SECRET; Vercel Cron)
  GET  /api/early?token=CA               -> {"token", "chain", "available", "buyers": [...], "summary": {...}[, "stale_at"]}
                                            first 20 buyers and their current status (early.py); not part of the scan
  GET  /api/recent?limit=12              -> {"items": [{"token", "chain", "launchpad", "ticker", "name", "score", "band", "rug", "ts"}]}
                                            "Recently scanned" feed: latest unique tokens, newest first
  GET  /api/config                       -> {"solana": bool, "alerts": bool, "flap": bool, "bankr": bool, "trade": {"robinhood": template, "solana": template}}
                                            Trade on Axiom link templates ({address}), env TRADE_URL_* (trade.py)
  GET  /api/index?token=CA               -> {"token", "state": ready|building|queued|queue_full|too_large|unavailable|none, "eta_s"[, "position"]} (BANKR_ENABLED)
                                            is the full Bankr holder index ready? (the site rescans when ready)
  GET  /api/replay                       -> {"items": [...], "stats": {...}, "since"} (REPLAY_ENABLED, otherwise 404; cached 60 s)
                                            Rug Replay: DANGER tokens that dropped ≥ 90% after the scan, and shares over 7 days
  GET  /replay                           -> replay.html (REPLAY_ENABLED, otherwise 404)
  GET  /                                 -> index.html
  GET  /favicon.svg, /favicon.png, /apple-touch-icon.png, /favicon.ico  -> icons from static/
  GET  /health
  HEAD — on any GET path: same status and headers, no body

Telegram bot and personal alerts (Phase 15; bot/core.py, crawler/tg_alerts.py, crawler/tg_store.py):
  POST /api/telegram                     -> Telegram webhook (X-Telegram-Bot-Api-Secret-Token == TG_WEBHOOK_SECRET,
                                            otherwise 401; each update id handled once); 404 without TG_BOT_TOKEN
  GET  /api/cron/alerts                  -> alert run: radar eligibility, rechecks of watched tokens, delivery
                                            (Authorization: Bearer $CRON_SECRET; GitHub Actions every 15 min)
  GET  /api/cron/telegram                -> registers the webhook and the command menu with Telegram (same secret)

Token result is cached for 10 minutes: a repeat scan returns the saved events immediately.
Chart is cached for 10 minutes (without candles — 1 minute); a GeckoTerminal failure gives empty candles, not an error.
/api/config is cached in memory for 30 seconds (Cache-Control: public, max-age=30 — for Cloudflare); failures are not cached.
New scans per IP (CF-Connecting-IP) — at most SCAN_RATE_PER_MIN per minute, otherwise 429 rate_limited with Retry-After;
user agent crawlscan-bot — SCAN_RATE_BOT_PER_MIN.
The /api/recent feed is cached for 15 seconds (a scan write resets the cache; a read that overlapped a write is not cached);
written after the scan finishes, a database failure does not break the scan (logged as "recent: not saved").
With TG_BOT_TOKEN and the memory database, every finished scan goes through tg_alerts.on_scan after the memory
write: watched tokens are diffed against their previous scan, fresh tokens checked against memory (suspicious deployer,
smart crew); alerts are queued in Supabase, urgent ones delivered at once, the rest by the alert cron.
With OPMEM_ENABLED=true scan and recheck results go to the operator memory queue (opmem.py: own database
memory.db, own writer thread, a token at most once per OPMEM_MIN_INTERVAL_H); the scan does not wait for the write.
With REPLAY_ENABLED=true — Rug Replay (replay.py): its own thread every REPLAY_CHECK_MIN checks the market cap of DANGER tokens from
memory.db against DexScreener (no RPC), /replay and /api/replay; the scan and bot are not touched.
At most 3 concurrent scans (the rest wait for a slot), at most SCAN_QUEUE_MAX in the queue; beyond the queue — 503 busy
with Retry-After (cache and an already running scan of the same token are served as before). Process memory (memguard.py) above the soft
threshold after cache reset — scans up to MAX_CONCURRENT without a queue, above the hard one — 503 busy. Once a minute the log gets
a line with memory, number of active scans and per-minute counters.
PORT from env, default 8000.
"""
import base64, hashlib, hmac, json, os, re, threading, time, urllib.error, uuid
from email.utils import formatdate, parsedate_to_datetime
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs, quote

from crawler import db
from crawler import detect
from crawler import memory_db
from crawler import scoring
from crawler.chains import bankr, flap, priority, registry
from crawler.chains.evm import networks as evm_networks
from crawler import early
from crawler import outcomes
from crawler import radar
from crawler import proof
from crawler import engine
from crawler import market
from crawler import memguard
from crawler import opmem
from crawler import replay
from crawler import trade
from crawler.recent_store import RecentStore
from crawler import tg_alerts
from crawler import tg_store
from bot.tg import Telegram, TelegramError

CACHE_TTL = 600            # seconds: per-token result cache
MAX_CONCURRENT = 3         # concurrent scans by default (env MAX_CONCURRENT)
JOB_TTL = 3600             # seconds: old jobs are removed from memory
WEB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web_v1")   # V1 pages and icons
ICONS = {                  # path -> (file in static/, content-type); .ico serves the same PNG
    "/favicon.svg": ("favicon.svg", "image/svg+xml"),
    "/favicon.png": ("favicon.png", "image/png"),
    "/apple-touch-icon.png": ("favicon.png", "image/png"),
    "/favicon.ico": ("favicon.png", "image/png"),
}
ICON_CACHE = "public, max-age=86400"
ASSETS = {                 # path -> (file in static/, content-type): page scripts shared by every page
    "/i18n.js": ("i18n.js", "text/javascript; charset=utf-8"),
}
ASSET_CACHE = "public, max-age=300"   # short: a deploy reaches open tabs within minutes
HISTORY_LIMIT = 20         # scans per token in /api/history
HTML_CACHE = "no-cache"    # page: the browser stores it but revalidates every time (ETag / Last-Modified → 304)
_PAGE = {}                 # (path, mtime_ns, size) -> (body, etag, last_modified)

CHART_TTL = 600            # seconds: per-token chart cache
CHART_EMPTY_TTL = 60       # seconds: cache for a chart without candles (GT did not respond or the token is too fresh)
CHART_STALE_TTL = 6 * 3600 # seconds: the last successful chart is served (with stale_at) while GT is not responding
CHART_MAX = 500            # charts in memory, old ones are evicted
RECENT_TTL = 15            # seconds: /api/recent feed cache (and max-age for Cloudflare)
STATUS_TTL = 30            # seconds: cache for /api/config (and max-age)
STATUS_CACHE = f"public, max-age={STATUS_TTL}"
REPLAY_TTL = 60            # seconds: /api/replay cache (and max-age)
RECENT_DEFAULT = 12        # feed entries by default

JOBS = {}                  # job_id -> {"token", "chain", "events", "done", "result", "error", "ts"}
CHARTS = {}                # token -> {"ok": (ts, successful response) | None, "miss": (ts, empty response) | None}
RECENT = {}                # limit -> (ts, /api/recent response)
_RECENT_GEN = [0]          # feed generation: +1 on every scan write (under _lock)
BY_TOKEN = {}              # token -> job_id of the latest scan
_lock = threading.Lock()
_cv = threading.Condition(_lock)   # notified on every new job event and when a job finishes (event streams wait on it)
STREAM_HEARTBEAT_S = 10            # comment line on an idle stream: keeps proxies from closing it
STORES_WAIT_S = 8.0       # a stream waits this long for the job's stores (memory write) after the result
STREAM_MAX_S = 120                 # a stream never outlives this (a scan's budget is 25 s)
_STREAMS = [0]                     # open event streams (cap: STREAMS_MAX)


def _env_int(name, default):
    try:
        v = int(os.environ.get(name, "").strip())
        return v if v > 0 else default
    except ValueError:
        return default


MAX_CONCURRENT = _env_int("MAX_CONCURRENT", MAX_CONCURRENT)
_sem = threading.BoundedSemaphore(MAX_CONCURRENT)
SCAN_QUEUE_MAX = 6         # new scans waiting for a slot beyond MAX_CONCURRENT (env SCAN_QUEUE_MAX); beyond that — 503 busy
SCAN_QUEUE_MAX = _env_int("SCAN_QUEUE_MAX", SCAN_QUEUE_MAX)
_ACTIVE = [0]              # scans running or waiting for a slot (under _lock)
_STATS = {"started": 0, "busy": 0}   # new scans and busy refusals since the last memguard line (under _lock)
RETRY_AFTER = 5            # seconds: Retry-After header on 503 busy
MAX_BODY = 64 * 1024       # bytes: largest accepted request body (larger -> 413 without reading it)
BUSY = {"error": "busy", "message": "Scanner is busy, try again in a few seconds"}
# rate of new scans per IP (CF-Connecting-IP): regular client / CrawlScan bot by user agent;
# a request with a valid X-Alerts-Secret (our bot) — no limit
SCAN_RATE_PER_MIN = _env_int("SCAN_RATE_PER_MIN", 10)
SCAN_RATE_BOT_PER_MIN = _env_int("SCAN_RATE_BOT_PER_MIN", 120)
STREAMS_MAX = _env_int("STREAMS_MAX", 64)   # concurrent event streams (each holds a server thread while open)
RATE_WINDOW = 60           # seconds
RATE_MAX_CLIENTS = 20_000  # IPs in memory; above that — forget them all (the limit is soft)
RATE_LIMITED = {"error": "rate_limited", "message": "Too many scans from your address, try again in a minute"}
_RATE = {}                 # client -> [times of new scans within RATE_WINDOW]

for _a in engine.CHAINS.values():       # above the memory threshold memguard drops scan history and transfer caches
    if hasattr(_a, "drop_caches"):
        memguard.on_relieve(_a.drop_caches)
memguard.on_relieve(bankr.drop_caches)  # ... and Bankr indexes (a running build is interrupted)
bankr.memory_high = memguard.near       # Bankr index build pauses near the memory threshold
bankr.after_build = memguard.trim       # ... and after it the page memory is returned to the OS


class Busy(Exception):
    """New scan not accepted: queue is full or memory is above MEMORY_SOFT_LIMIT_MB."""


class RateLimited(Exception):
    """Too many new scans from one IP; args[0] — seconds until allowed again."""


def _rate_take(client, now):
    """Count a new client scan (client = (key, per-minute limit) or None — no limit). Under _lock.
    Limit exhausted — RateLimited(seconds until freed)."""
    if client is None:
        return
    key, limit = client
    hits = [t for t in _RATE.get(key, ()) if now - t < RATE_WINDOW]
    if len(hits) >= limit:
        _RATE[key] = hits
        raise RateLimited(max(1, int(RATE_WINDOW - (now - hits[0])) + 1))
    if key not in _RATE and len(_RATE) >= RATE_MAX_CLIENTS:
        _RATE.clear()
    hits.append(now)
    _RATE[key] = hits


def serverless():
    """Serverless mode (Vercel: VERCEL=1, or SERVERLESS=1): no background threads survive a response and instances
    share no memory, so a scan runs inside POST /api/scan and the job id encodes (chain, token): an instance that
    never saw the job re-runs the scan instead of answering 404 (a rare duplicate scan, never a dead page)."""
    return (os.environ.get("SERVERLESS") or os.environ.get("VERCEL") or "").strip() in ("1", "true")


def _sjob_id(chain, token):
    return "s" + base64.urlsafe_b64encode(f"{chain}:{token}".encode()).decode().rstrip("=")


def _sjob_target(jid):
    """(chain, token) from a serverless job id, or None."""
    if not jid.startswith("s") or len(jid) > 200:
        return None
    try:
        chain, _, token = base64.urlsafe_b64decode(jid[1:] + "=" * (-len(jid[1:]) % 4)).decode().partition(":")
    except (ValueError, UnicodeDecodeError):
        return None
    return (chain, token) if chain and token else None


def _key(chain, token):
    """BY_TOKEN key: the bare address for Robinhood and Solana (V1 behaviour), chain:address for chains added later
    (the same 0x address can exist on several EVM chains)."""
    return token if chain in ("robinhood", "solana") else f"{chain}:{token}"


def _run(job_id, token):
    try:
        with priority.live():   # live scan: background work (early buyers, replay) yields to it
            _run_job(job_id, token)
    finally:
        with _lock:
            _ACTIVE[0] -= 1


def _run_job(job_id, token):
    job = JOBS[job_id]

    def emit(e):
        with _cv:
            job["events"].append(e)
            _cv.notify_all()

    with _sem:
        try:
            holders, memo = [], {}
            res = engine.scan(token, emit, chain=job["chain"], holders_out=holders, memory_out=memo)
            with _lock:
                job["result"] = res
                job["holders_all"] = holders
        except engine.ScanError as e:
            err = str(e)
        except urllib.error.HTTPError as e:   # provider refused after its retries: say so plainly
            err = ("the RPC provider is rate-limiting this server right now — try again in a minute"
                   if e.code == 429 else f"scan failed: RPC answered HTTP {e.code}")
        except Exception as e:  # RPC failure etc.: return the text, the process lives on
            err = f"scan failed: {e}"
        else:
            err = None
    try:
        if not err:
            # before done: a client that reacts to the verdict (the page's /api/early) must find the snapshot it
            # writes into. Local writes only; they log their own failures and never fail the scan.
            opmem.record(res)       # queue only (OPMEM_ENABLED), the write happens in its own thread
            record_recent(res)
            with _cv:               # local stores done: streams send the result now, the remote write follows
                job["ready"] = True
                _cv.notify_all()
            memory_db.record(res, memo)   # memory brain (Supabase): one RPC; on Vercel nothing runs after the
                                          # response, so the stream stays open until this is done (_stream)
            tg_alerts.on_scan(res, early.known_share(res.get("chain"), res.get("token")))   # Phase 15, never raises
    finally:                        # the job ends whatever a store did
        with _cv:
            if err:
                job["error"] = err
                job["events"].append({"i": len(job["events"]), "t": int((time.time() - job["ts"]) * 1000),
                                      "type": "error", "spider": None, "wallet": None, "detail": err,
                                      "chain": job["chain"]})
            job["done"] = True
            _cv.notify_all()


HOLDERS_PAGE, HOLDERS_PAGE_MAX = 100, 1000   # /api/holders page size: default and maximum


def holders_page(job, offset, limit):
    """/api/holders body (under _lock): every holder by balance, largest first. balance is a decimal string of raw
    token units (it can exceed a JS number's exact range); share — of circulating supply, share_supply — of total
    supply; analysed — in the top the scan examined in depth. Not done or failed: no items."""
    res, rows = job["result"], job.get("holders_all")
    if not job["done"] or not res or rows is None:
        return {"done": job["done"], "error": job["error"], "total": None, "complete": None, "items": []}
    circ, supply = res.get("circulating") or 0, res.get("supply") or 0
    top = {h["wallet"] for h in res.get("holders") or []}
    items = [{"rank": offset + i + 1, "wallet": w, "balance": str(b), "share": b / circ if circ else None,
              "share_supply": b / supply if supply else None, "analysed": w in top}
             for i, (w, b) in enumerate(rows[offset:offset + limit])]
    return {"done": True, "error": None, "total": len(rows), "complete": res.get("holders_complete"),
            "offset": offset, "items": items}


def sse(event=None, data=None, id_=None, comment=None):
    """One Server-Sent Events frame (docs/events.md). data is JSON on a single line, so it never needs splitting."""
    lines = []
    if comment is not None:
        lines.append(f": {comment}")
    if id_ is not None:
        lines.append(f"id: {id_}")
    if event:
        lines.append(f"event: {event}")
    if data is not None:
        lines.append("data: " + json.dumps(data, separators=(",", ":")))
    return ("\n".join(lines) + "\n\n").encode()


def wants_stream(headers):
    return "text/event-stream" in (headers.get("accept") or "").lower()


def last_event_after(headers, q):
    """Index of the first event to send: after Last-Event-ID (a reconnecting EventSource) or ?after=N."""
    try:
        lei = headers.get("last-event-id")
        if lei not in (None, ""):
            return max(0, int(lei) + 1)
        return max(0, int((q.get("after") or ["0"])[0]))
    except ValueError:
        return 0


RESCAN_MIN_S = 60          # a rescan on demand (radar) reuses a result younger than this, never an older one


def _reuse(token, now, fresh=False):
    """job_id of a fresh cache entry or a running scan of the token, otherwise None (under _lock).
    fresh — a rescan on demand: a running scan is joined, a finished one only if younger than RESCAN_MIN_S."""
    jid = BY_TOKEN.get(token)
    job = JOBS.get(jid)
    ttl = RESCAN_MIN_S if fresh else CACHE_TTL
    if job and (not job["done"] or (job["result"] and now - job["ts"] < ttl and not _index_ready(job["result"]))):
        return jid
    return None


def _index_ready(result):
    """Partial Bankr scan (holder index was building), but the index is now ready — cache is stale: a new scan will be full."""
    ps = result.get("partial_scan")
    return bool(ps) and bankr.index_status(result["token"])["state"] == "ready"


def _busy(why):
    with _lock:
        _STATS["busy"] += 1
    raise Busy(why)


def scan_stats():
    """For the once-a-minute memguard line: (active scans, line tail); counters are reset."""
    with _lock:
        active, started, busy = _ACTIVE[0], _STATS["started"], _STATS["busy"]
        _STATS["started"] = _STATS["busy"] = 0
    return active, f", last min: {started} started, {busy} busy"


def start_scan(token, client=None, chain=None, background=False, fresh=False):
    """job_id: fresh cache for the token or an already running scan of the same token, otherwise a new one.
    New only if there is room in the queue and memory is below the threshold, otherwise Busy; and if client (key, per-minute limit)
    has not exhausted the new-scan limit, otherwise RateLimited. Cache hits and joining a running scan do not use the limit.
    chain — explicit chain for a 0x address (engine.chain_of); without it an address found on several enabled chains
    raises engine.AmbiguousChain.
    background — run the scan in a thread even in serverless mode (the caller streams its events meanwhile).
    fresh — rescan on demand (radar): the cache is used only if under RESCAN_MIN_S old; uses the rate limit."""
    chain, token = engine.chain_of(token, chain)
    key = _key(chain, token)
    now = time.time()
    with _lock:
        for jid in [j for j, v in JOBS.items() if v["done"] and now - v["ts"] > JOB_TTL]:
            k = JOBS[jid].get("key", JOBS[jid]["token"])
            if BY_TOKEN.get(k) == jid:
                del BY_TOKEN[k]
            del JOBS[jid]
        jid = _reuse(key, now, fresh)
        if jid:
            return jid
        active = _ACTIVE[0]
    if active >= MAX_CONCURRENT + SCAN_QUEUE_MAX:
        _busy("queue")
    if memguard.over(live=active, cap=MAX_CONCURRENT):   # outside _lock: cache reset and gc do not block other requests
        _busy("memory")
    with _lock:
        jid = _reuse(key, now, fresh)
        if jid:
            return jid
        if _ACTIVE[0] >= MAX_CONCURRENT + SCAN_QUEUE_MAX:
            _STATS["busy"] += 1
            raise Busy("queue")
        _rate_take(client, now)
        _ACTIVE[0] += 1
        _STATS["started"] += 1
        jid = _sjob_id(chain, token) if serverless() else uuid.uuid4().hex[:12]
        JOBS[jid] = {"token": token, "chain": chain, "key": key, "events": [], "done": False, "result": None,
                     "error": None, "ts": now}
        BY_TOKEN[key] = jid
    if serverless() and not background:   # the scan must finish inside this request (see serverless())
        _run(jid, token)
        return jid
    try:
        threading.Thread(target=_run, args=(jid, token), daemon=True).start()
    except RuntimeError:   # can't start new thread: same as a full queue
        with _lock:
            _ACTIVE[0] -= 1
            JOBS.pop(jid, None)
            if BY_TOKEN.get(key) == jid:
                del BY_TOKEN[key]
        raise Busy("thread") from None
    return jid


def radar_chains():
    """Chains discovery may scan: Robinhood always, Solana with SOLANA_ENABLED, BNB / Base when enabled."""
    out = ["robinhood"] + (["solana"] if engine.solana_enabled() else [])
    return out + [c for c in evm_networks.enabled_chains() if c not in out]


def chains_config():
    """/api/config chains: enabled chains with their enabled launchpads (frontend chain selector)."""
    out = []
    for c in evm_networks.enabled_chains():
        pads = (["pons"] + (["flap"] if flap.enabled() else []) + (["bankr"] if bankr.enabled() else [])
                if c == "robinhood" else [])
        out.append({"chain": c, "name": evm_networks.NETWORKS[c].name, "chain_id": evm_networks.NETWORKS[c].chain_id,
                    "launchpads": pads + registry.launchpad_names(c)})
    if engine.solana_enabled():
        out.append({"chain": "solana", "name": "Solana", "chain_id": None, "launchpads": ["pumpfun"]})
    return out


def scan_flags(token):
    """{wallet: ["operator", "virgin"]} from the latest finished scan of the token (top-20 only, the ones the scan checked);
    no scan — None. operator — wallet in an operator of 2+ wallets."""
    with _lock:
        job = JOBS.get(BY_TOKEN.get(token))
        res = job and job["done"] and job["result"]
    if not res or not res.get("holders"):
        return None
    multi = {w for o in res.get("operators") or [] if len(o.get("wallets") or []) > 1 for w in o["wallets"]}
    return {h["wallet"]: [f for f, on in (("operator", h["wallet"] in multi),
                                          ("virgin", (h.get("signals") or {}).get("virgin"))) if on]
            for h in res["holders"]}


def get_chart(token):
    """/api/chart response: address via engine.chain_of (ScanError — bad address / Solana disabled),
    candles from market.fetch_chart with cache. Any GT failure is not an exception: unavailable = True (no candles because of
    a failure, not because there are few trades) or the last successful chart younger than
    CHART_STALE_TTL with a stale_at field (unix time when it was fetched), otherwise empty. An empty response does not
    overwrite a successful cache entry."""
    chain, token = engine.chain_of(token)
    now = time.time()

    def fallback(e):
        ok = e.get("ok")
        if ok and now - ok[0] < CHART_STALE_TTL:
            return ok[1] | {"stale_at": int(ok[0])}
        return e["miss"][1]

    with _lock:
        e = CHARTS.get(token) or {"ok": None, "miss": None}
        if e["ok"] and now - e["ok"][0] < CHART_TTL:
            return e["ok"][1]
        if e["miss"] and now - e["miss"][0] < CHART_EMPTY_TTL:
            return fallback(e)
    try:
        c = market.fetch_chart(token, engine.GT_NETWORK[chain])
    except Exception:
        c = {"failed": True}
    out = {"token": token, "chain": chain, "pool": c.get("pool"), "dex": c.get("dex"),
           "timeframe": c.get("timeframe"), "candles": c.get("candles") or [], "price_usd": c.get("price_usd")}
    if c.get("failed") and not out["candles"]:
        out["unavailable"] = True
    with _lock:
        e = CHARTS.get(token) or {"ok": None, "miss": None}
        res = out
        if out["candles"]:
            e = {"ok": (now, out), "miss": None}
        else:
            e = e | {"miss": (now, out)}
            res = fallback(e)
            print(f"chart: {token} no candles from gt -> "
                  + (f"stale cache from {time.strftime('%H:%M', time.gmtime(res['stale_at']))} UTC"
                     if "stale_at" in res else "empty"), flush=True)
        CHARTS[token] = e
        if len(CHARTS) > CHART_MAX:
            last = lambda kv: max(x[0] for x in (kv[1]["ok"], kv[1]["miss"]) if x)
            for t, _ in sorted(CHARTS.items(), key=last)[:len(CHARTS) - CHART_MAX]:
                del CHARTS[t]
    return res


_stores_lock = threading.Lock()


_recent_stores = {}


def recent_store():
    """Feed store (file LOCAL_DB_PATH), one per path."""
    path = db.local_path()
    with _stores_lock:
        if path not in _recent_stores:
            _recent_stores[path] = RecentStore(path)
        return _recent_stores[path]


def record_recent(result):
    """Write a finished scan to the feed. Called after done: the client already has the verdict.
    Any database failure is only logged — the scan does not depend on the feed."""
    try:
        if recent_store().record(result):
            with _lock:
                _RECENT_GEN[0] += 1
                RECENT.clear()
    except Exception as e:
        print(f"recent: not saved: {type(e).__name__}: {e}", flush=True)


def record_early(body):
    """/api/early measured the first buyers: their share goes into the watched token's alert snapshot (Phase 15), so
    the next scan can tell whether early buyers are exiting. Called after responding; stale or failed measurements are
    not written; a database failure is only logged."""
    if not tg_alerts.enabled():
        return
    try:
        s = body.get("summary") if body.get("available") and not body.get("error") and not body.get("stale_at") else None
        if s:
            tg_store.snapshot_early(body["chain"], body["token"], s.get("held_share_supply", s.get("now_share_supply")))
    except Exception as e:
        print(f"alerts: early share not saved: {type(e).__name__}: {e}", flush=True)


# ---------------------------------------------------------------------------
# Telegram bot (Phase 15)
# ---------------------------------------------------------------------------
def bot_scan(chain, token, alerts_hook=True):
    """A scan for the bot or the alert cron: the engine plus the stores a page scan gets (operator memory, recent feed,
    memory brain)
    and, unless the caller runs it itself (the cron adds the early buyers share), the alert hook."""
    memo = {}
    res = engine.scan(token, chain=chain, memory_out=memo)
    opmem.record(res)
    record_recent(res)
    memory_db.record(res, memo)
    if alerts_hook:
        tg_alerts.on_scan(res)
    return res


def telegram_bot():
    """The bot with this server's scanner, memory and radar (bot/core.py)."""
    from bot.core import Bot
    return Bot(Telegram(tg_alerts.bot_token()), tg_store, bot_scan, lambda tok, hint: engine.chain_of(tok, hint),
               radar.query, radar_chains, tg_alerts.enabled, tg_alerts.report_url, tg_alerts.site_url,
               ambiguous=(engine.AmbiguousChain, engine.ChainUnchecked), scan_errors=(engine.ScanError,))


def telegram_setup():
    """Register the webhook and the command menu with Telegram → (code, body). The webhook URL carries the Vercel
    protection bypass when the deployment is protected, so it is never returned or logged."""
    from bot.core import COMMANDS, allowed_users
    token, secret, site = tg_alerts.bot_token(), os.environ.get("TG_WEBHOOK_SECRET", "").strip(), tg_alerts.site_url()
    missing = [n for n, v in (("TG_BOT_TOKEN", token), ("TG_WEBHOOK_SECRET", secret), ("SITE_URL", site)) if not v]
    if missing:
        return 503, {"error": "not configured", "missing": missing}
    bypass = os.environ.get("VERCEL_AUTOMATION_BYPASS_SECRET", "").strip()
    url = site + "/api/telegram" + (f"?x-vercel-protection-bypass={quote(bypass)}" if bypass else "")
    tg = Telegram(token)
    clean = lambda x: str(x).replace(bypass, "<bypass>") if bypass else str(x)
    try:
        tg.call("setWebhook", url=url, secret_token=secret, allowed_updates=["message", "callback_query"],
                max_connections=10)
        tg.call("setMyCommands", commands=COMMANDS)
        me, info = tg.call("getMe") or {}, tg.call("getWebhookInfo") or {}
    except TelegramError as e:
        return 502, {"error": f"telegram: {clean(e.description)}", "code": e.code}
    return 200, {"ok": True, "bot": me.get("username"), "webhook": site + "/api/telegram", "bypass": bool(bypass),
                 "pending_updates": info.get("pending_update_count"),
                 "last_error": clean(info.get("last_error_message")) if info.get("last_error_message") else None,
                 "allowed_users": len(allowed_users())}


_RESP = {}                 # key -> (ts, body): statuses and config for page polling


def cached_response(key, ttl, fn):
    """Response body from the process cache if younger than ttl, otherwise fn() (exceptions are not cached — the caller catches them)."""
    now = time.time()
    with _lock:
        hit = _RESP.get(key)
    if hit and now - hit[0] < ttl:
        return hit[1]
    body = fn()
    with _lock:
        _RESP[key] = (now, body)
    return body


def scan_client(headers, addr):
    """(key, per-minute limit) for the new-scan limit or None (no limit). IP — CF-Connecting-IP (the site is behind
    Cloudflare), otherwise the connection address. User agent crawlscan-bot (a self-hosted client) — a high limit."""
    ip = (headers.get("CF-Connecting-IP") or "").strip() or addr
    if (headers.get("User-Agent") or "").startswith("crawlscan-bot"):
        return ("bot:" + ip, SCAN_RATE_BOT_PER_MIN)
    return (ip, SCAN_RATE_PER_MIN)


def get_recent(limit):
    """/api/recent response with RECENT_TTL cache. Database failure — empty feed, not an error."""
    now = time.time()
    with _lock:
        hit = RECENT.get(limit)
        if hit and now - hit[0] < RECENT_TTL:
            return hit[1]
        gen = _RECENT_GEN[0]
    try:
        out = {"items": recent_store().recent(limit)}
    except Exception as e:
        print(f"recent: not read: {type(e).__name__}: {e}", flush=True)
        return {"items": []}
    with _lock:
        if _RECENT_GEN[0] == gen:   # a scan was written while we were reading — this response is already stale, do not cache it
            RECENT[limit] = (now, out)
    return out


class H(BaseHTTPRequestHandler):
    def _send(self, code, body, ctype="application/json", cache="no-store", headers=None):
        b = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(code)
        self.send_header("content-type", ctype)
        self.send_header("content-length", str(len(b)))
        self.send_header("cache-control", cache)
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(b)

    def _send_page(self, path):
        """index.html with Cache-Control: no-cache, ETag and Last-Modified: after a deploy the browser sees the new
        page right away, an unchanged one gets 304 with no body. If-None-Match takes precedence over If-Modified-Since; a weak ETag (W/, set by
        Cloudflare when compressing) is compared as a strong one."""
        try:
            st = os.stat(path)
        except FileNotFoundError:
            return self._send(200, b"<h1>rh-crawler</h1>", "text/html; charset=utf-8")
        key = (path, st.st_mtime_ns, st.st_size)
        page = _PAGE.get(key)
        if page is None:
            with open(path, "rb") as f:
                body = f.read()
            page = (body, '"' + hashlib.sha256(body).hexdigest()[:20] + '"', formatdate(st.st_mtime, usegmt=True))
            _PAGE.clear()
            _PAGE[key] = page
        body, etag, modified = page
        inm, ims = self.headers.get("If-None-Match"), self.headers.get("If-Modified-Since")
        if inm is not None:
            fresh = any(t.strip().removeprefix("W/") in (etag, "*") for t in inm.split(","))
        else:
            try:
                fresh = ims is not None and int(st.st_mtime) <= parsedate_to_datetime(ims).timestamp()
            except (TypeError, ValueError):
                fresh = False
        self.send_response(304 if fresh else 200)
        self.send_header("cache-control", HTML_CACHE)
        self.send_header("etag", etag)
        self.send_header("last-modified", modified)
        if fresh:
            self.end_headers()
            return
        self.send_header("content-type", "text/html; charset=utf-8")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _stream_slot(self):
        with _lock:
            if _STREAMS[0] >= STREAMS_MAX:
                return False
            _STREAMS[0] += 1
            return True

    def _stream_release(self):
        with _lock:
            _STREAMS[0] -= 1

    def _stream(self, jid, job, after):
        """Server-Sent Events for one job: `job` first, then every event from index `after` as `scan` (id = event
        index, so a client resumes with Last-Event-ID), then `result` once the job is done; a comment line every
        STREAM_HEARTBEAT_S while idle. The connection closes after `result` (HTTP/1.0: the body ends there)."""
        self.send_response(200)
        self.send_header("content-type", "text/event-stream; charset=utf-8")
        self.send_header("cache-control", "no-store")
        self.send_header("x-accel-buffering", "no")   # reverse proxies (nginx): pass frames through unbuffered
        self.end_headers()
        if self.command == "HEAD":
            return
        until = time.time() + STREAM_MAX_S
        sent = False
        try:
            self.wfile.write(b"retry: 2000\n\n" + sse("job", {"job": jid, "chain": job["chain"], "token": job["token"]}))
            self.wfile.flush()
            while True:
                with _cv:
                    if len(job["events"]) <= after and not job["done"] and (sent or not job.get("ready")):
                        _cv.wait(STREAM_HEARTBEAT_S)
                    new, done, ready = job["events"][after:], job["done"], job.get("ready")
                    final = ({"done": True, "error": job["error"]} if done and job["error"] else
                             {"done": True, "result": job["result"]} if (done or ready) and not sent else None)
                out = b"".join(sse("scan", e, e["i"]) for e in new)
                after += len(new)
                if final is not None:   # the result as soon as it exists; the memory write runs after it
                    out += sse("result", final)
                    sent = True
                elif not new and not sent:   # after the result nothing more is written: it stays the last frame
                    out += sse(comment="ping")
                if out:
                    self.wfile.write(out)
                    self.wfile.flush()
                if done:
                    return
                if time.time() >= until:
                    self.wfile.write(sse("timeout", {"job": jid, "after": after}))
                    self.wfile.flush()
                    return
        except (BrokenPipeError, ConnectionResetError):   # client left: the scan goes on (cache, other listeners)
            pass
        finally:   # a serverless function must not end its response before the job's stores (memory) have run
            self._wait_done(job)

    def _cron_refused(self, what):
        """False when the request carries Authorization: Bearer $CRON_SECRET (constant-time compare); otherwise the
        refusal is sent and True returned. Unset secret: every request is refused (fails closed)."""
        secret = (os.environ.get("CRON_SECRET") or "").strip()
        if not secret:
            self._send(503, {"error": f"{what} not configured (CRON_SECRET)"})
            return True
        got = self.headers.get("authorization") or ""
        if not hmac.compare_digest(got.encode(), ("Bearer " + secret).encode()):
            self._send(401, {"error": "unauthorized"})
            return True
        return False

    def _redirect(self, to):
        """302 to a same-site path (fixed targets only: never a URL taken from the request)."""
        self.send_response(302)
        self.send_header("location", to)
        self.send_header("content-length", "0")
        self.end_headers()

    def _proof_scan(self, q):
        """The supporting data of one proof row: the immutable scan record, a re-check of its fingerprint, every
        later market snapshot and measured outcome."""
        try:
            sid = int((q.get("id") or [""])[0])
        except ValueError:
            return self._send(400, {"error": "id must be a scan id"})
        body = memory_db.call("proof_scan", {"p_scan_id": sid}) if memory_db.enabled() else None
        if not body:
            return self._send(404, {"error": "no such scan"})
        return self._send(200, body)

    def _history(self, q):
        """Every scan record CrawlMeme holds for one token (newest first) and its newest market snapshot. Address
        format only, no RPC: an unknown token is an empty history, not an error."""
        chain = (q.get("chain") or [""])[0].strip().lower()
        token = (q.get("token") or [""])[0].strip()
        if chain not in radar_chains():
            return self._send(400, {"error": "unknown chain"})
        if chain == "solana":
            if not engine.sol.is_address(token):
                return self._send(400, {"error": "not a token address"})
        elif engine.ADDR_RE.match(token):
            token = token.lower()
        else:
            return self._send(400, {"error": "not a token address"})
        if not memory_db.enabled():
            return self._send(200, {"available": False, "reason": "memory is off", "scans": []})
        body = memory_db.call("token_history", {"p_chain": chain, "p_token": token, "p_limit": HISTORY_LIMIT})
        if body is None:
            return self._send(200, {"available": False, "reason": "memory unavailable", "scans": []})
        return self._send(200, {"available": True, "chain": chain, "token": token, **body})

    def _telegram(self):
        """Telegram webhook: secret header in constant time, each update id once (Telegram retries a webhook it thinks
        failed), then the update is handled inside this request (a scan fits the function's time limit)."""
        secret = os.environ.get("TG_WEBHOOK_SECRET", "").strip()
        if not tg_alerts.bot_token() or not secret:
            return self._send(404, {"error": "not found"})
        got = (self.headers.get("X-Telegram-Bot-Api-Secret-Token") or "").encode()
        if not hmac.compare_digest(got, secret.encode()):
            return self._send(401, {"error": "unauthorized"})
        try:
            body = self._body()
        except ValueError:
            return self._send(400, {"error": "bad json"})
        if body is None:
            return self._send(413, {"error": "request body too large"})
        if not isinstance(body, dict) or not isinstance(body.get("update_id"), int):
            return self._send(400, {"error": "not an update"})
        seen = tg_store.update_seen(body["update_id"]) if tg_store.enabled() else None
        if seen is False:
            return self._send(200, {"ok": True, "duplicate": True})
        telegram_bot().handle_update(body)
        return self._send(200, {"ok": True})

    def _cron_alerts(self):
        """Phase 15 alert run (GitHub Actions every 15 min, same secret as the other workers)."""
        if self._cron_refused("alert run"):
            return
        if not tg_alerts.enabled():
            return self._send(503, {"error": "alerts need TG_BOT_TOKEN and the memory database"})
        return self._send(200, tg_alerts.cron(lambda chain, token: bot_scan(chain, token, alerts_hook=False)))

    def _cron_track(self):
        """Phase 13 market tracker (GitHub Actions every 30 min, same secret as the other workers)."""
        if self._cron_refused("market tracker"):
            return
        try:
            return self._send(200, proof.track())
        except memory_db.MemoryUnavailable as e:
            return self._send(503, {"error": f"memory unavailable: {e}"})

    def _cron_outcomes(self):
        """Phase 11 outcome worker (Vercel Cron sends the secret when CRON_SECRET is set)."""
        if self._cron_refused("outcome worker"):
            return
        try:
            return self._send(200, outcomes.run())
        except memory_db.MemoryUnavailable as e:
            return self._send(503, {"error": f"memory unavailable: {e}"})

    def _cron_radar(self, q):
        """Phase 12 radar discovery for one chain (GitHub Actions every 30 min, same secret)."""
        if self._cron_refused("radar discovery"):
            return
        chain = (q.get("chain") or [""])[0].lower()
        if chain not in radar_chains():
            return self._send(400, {"error": "unknown or disabled chain"})
        if not memory_db.enabled():
            return self._send(503, {"error": "memory is off"})

        def record(res, memo):   # the same stores a page scan gets (the recent feed and the memory brain)
            record_recent(res)
            memory_db.record(res, memo)
        return self._send(200, radar.discover(chain, record=record))

    @staticmethod
    def _wait_done(job, limit=STORES_WAIT_S):
        end = time.time() + limit
        with _cv:
            while job.get("ready") and not job["done"] and time.time() < end:
                _cv.wait(max(0.05, end - time.time()))

    def _job(self, q):
        jid = (q.get("job") or [""])[0]
        with _lock:
            job = JOBS.get(jid)
        if job is None and serverless() and (target := _sjob_target(jid)):
            try:   # another instance ran it: scan again here (same result semantics, cached for CACHE_TTL)
                start_scan(target[1], scan_client(self.headers, self.client_address[0]), chain=target[0])
            except (engine.ScanError, Busy, RateLimited):
                return None
            with _lock:
                job = JOBS.get(jid)
        return job

    def do_POST(self):
        u = urlparse(self.path)
        if u.path == "/api/telegram":
            return self._telegram()
        if urlparse(self.path).path != "/api/scan":
            return self._send(404, {"error": "not found"})
        try:
            body = self._body()
            if body is None:
                return self._send(413, {"error": "request body too large"})
            stream = wants_stream(self.headers)
            if stream and not self._stream_slot():
                return self._send(503, BUSY, headers={"retry-after": str(RETRY_AFTER)})
            try:
                jid = start_scan(body.get("token"), scan_client(self.headers, self.client_address[0]),
                                 chain=body.get("chain"), background=stream, fresh=body.get("fresh") is True)
                with _lock:
                    job = JOBS.get(jid)
                if stream and job is not None:   # events as they happen, in this same response (docs/events.md)
                    return self._stream(jid, job, 0)
            finally:
                if stream:
                    self._stream_release()
            # the chain the address resolved to: the page shows the right chain before the first event arrives
            return self._send(200, {"job": jid, "chain": job["chain"] if job else None})
        except engine.AmbiguousChain as e:   # same address on several chains: the client must choose one
            return self._send(409, {"error": "ambiguous_chain", "message": str(e), "chains": e.chains})
        except Busy:
            return self._send(503, BUSY, headers={"retry-after": str(RETRY_AFTER)})
        except RateLimited as e:
            return self._send(429, RATE_LIMITED, headers={"retry-after": str(e.args[0])})
        except engine.ScanError as e:
            return self._send(400, {"error": str(e)})
        except (ValueError, AttributeError):
            return self._send(400, {"error": "bad json"})

    def _body(self):
        """JSON request body, at most MAX_BODY bytes (audit S1): None if larger — nothing is read then.
        ValueError for bad JSON or a bad content-length."""
        n = int(self.headers.get("content-length") or 0)
        if n < 0:
            raise ValueError("negative content-length")
        if n > MAX_BODY:
            return None
        return json.loads(self.rfile.read(n) or b"{}")

    def do_HEAD(self):
        """HEAD = GET without a body: same status and headers (content-length as for GET), the body is not written (_send, _send_page)."""
        return self.do_GET()

    def do_GET(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        if u.path == "/health":
            return self._send(200, {"ok": True})
        if u.path == "/api/cron/alerts":
            return self._cron_alerts()
        if u.path == "/api/cron/telegram":
            if self._cron_refused("telegram setup"):
                return
            return self._send(*telegram_setup())
        if u.path == "/api/config":  # frontend: which chains are enabled (SOLANA_ENABLED), alerts (Telegram configured), Trade on Axiom
            env = tuple(os.environ.get(k) for k in ("SOLANA_ENABLED", "MEMORY_ENABLED", "FLAP_ENABLED", "BANKR_ENABLED",
                                                    "REPLAY_ENABLED", "TRADE_URL_ROBINHOOD", "TRADE_URL_SOLANA",
                                                    "BSC_ENABLED", "BASE_ENABLED", "CLANKER_ENABLED", "FOURMEME_ENABLED"))
            env += (bool(tg_alerts.bot_token()),)   # whether a bot token is set, never the token
            body = cached_response(("config",) + env, STATUS_TTL, lambda: {
                "solana": engine.solana_enabled(), "alerts": tg_alerts.enabled(), "flap": flap.enabled(),
                "bankr": bankr.enabled(), "replay": replay.enabled(), "trade": trade.templates(),
                "chains": chains_config()})
            return self._send(200, body, cache=STATUS_CACHE)
        if u.path == "/api/index":   # Bankr: is the full holder index ready (the site rescans when ready)
            token = (q.get("token") or [""])[0]
            if not bankr.enabled() or not bankr.index_enabled():   # background index disabled — there is none
                return self._send(404, {"error": "not found"})
            try:
                chain, ca = engine.chain_of(token)
            except engine.ScanError as e:
                return self._send(400, {"error": str(e)})
            return self._send(200, {"token": ca, **bankr.index_status(ca)} if chain == "robinhood" else
                              {"token": ca, "state": "none", "eta_s": None})
        if u.path == "/proof":
            return self._send_page(os.path.join(WEB, "proof.html"))
        if u.path == "/api/proof":
            return self._send(200, proof.query(q))
        if u.path == "/api/proof/scan":
            return self._proof_scan(q)
        if u.path == "/api/history":
            return self._history(q)
        if u.path == "/api/cron/track":
            return self._cron_track()
        if u.path == "/gems":
            return self._redirect("/proof?view=gem")
        if u.path in ("/api/replay", "/replay"):   # V1 Rug Replay (self-hosted, REPLAY_ENABLED)
            if not replay.enabled():           # otherwise the page is the Phase 13 proof system's Rug Replay
                return self._redirect("/proof?view=rug") if u.path == "/replay" else \
                    self._send(404, {"error": "not found"})
            if u.path == "/replay":
                return self._send_page(os.path.join(WEB, "replay.html"))
            try:
                body = cached_response(("replay",), REPLAY_TTL, lambda: replay.api(replay.db_path()))
            except Exception as e:   # database failure: not cached, the process lives on
                print(f"replay: not read: {type(e).__name__}: {e}", flush=True)
                return self._send(503, {"error": "temporarily unavailable"})
            return self._send(200, body, cache=f"public, max-age={REPLAY_TTL}")
        if u.path == "/api/chart":
            try:
                return self._send(200, get_chart((q.get("token") or [""])[0]))
            except engine.ScanError as e:
                return self._send(400, {"error": str(e)})
        if u.path == "/api/cron/outcomes":
            return self._cron_outcomes()
        if u.path == "/api/cron/radar":
            return self._cron_radar(q)
        if u.path == "/api/radar":
            return self._send(200, radar.query(q))
        if u.path == "/radar":
            return self._send_page(os.path.join(WEB, "radar.html"))
        if u.path == "/api/early":
            try:
                token = (q.get("token") or [""])[0]
                chain, ca = engine.chain_of(token)
                # with live scan history — like a live scan; without it — background (yields to scans, BACKGROUND_RPS)
                bg = priority.background_rps() > 0 and early.background(chain, ca)
                with priority.background() if bg else priority.live():
                    body = early.get(token, scan_flags(ca))
            except engine.ScanError as e:
                return self._send(400, {"error": str(e)})
            self._send(200, body)
            return record_early(body)
        if u.path == "/api/recent":
            try:
                limit = min(50, max(1, int((q.get("limit") or [str(RECENT_DEFAULT)])[0])))
            except ValueError:
                limit = RECENT_DEFAULT
            return self._send(200, get_recent(limit), cache=f"public, max-age={RECENT_TTL}")
        if u.path == "/api/events":
            job = self._job(q)
            if job is None:
                return self._send(404, {"error": "unknown job"})
            try:
                after = max(0, int((q.get("after") or ["0"])[0]))
            except ValueError:
                after = 0
            with _lock:
                return self._send(200, {"events": job["events"][after:], "done": job["done"]})
        if u.path == "/api/stream":    # join a job's event stream (EventSource; resumes after Last-Event-ID)
            if not self._stream_slot():
                return self._send(503, BUSY, headers={"retry-after": str(RETRY_AFTER)})
            try:
                jid = (q.get("job") or [""])[0]
                job = self._job(q)
                if job is None:
                    return self._send(404, {"error": "unknown job"})
                return self._stream(jid, job, last_event_after(self.headers, q))
            finally:
                self._stream_release()
        if u.path == "/api/rescore":   # a finished scan's verdict under every scoring model (or one: &model=)
            job = self._job(q)
            if job is None:
                return self._send(404, {"error": "unknown job"})
            model = (q.get("model") or [None])[0]
            with _lock:
                res = job["result"] if job["done"] else None
            if res is None:
                return self._send(409 if not job["done"] else 422, {"error": "scan not finished" if not job["done"]
                                                                  else "scan failed: no verdict to rescore"})
            try:
                return self._send(200, {"live": scoring.current(), "models": scoring.rescore(res, model)})
            except KeyError:
                return self._send(400, {"error": "unknown model", "models": list(scoring.MODELS)})
        if u.path == "/api/holders":   # every holder of a finished scan, a page at a time
            job = self._job(q)
            if job is None:
                return self._send(404, {"error": "unknown job"})
            try:
                offset = max(0, int((q.get("offset") or ["0"])[0]))
                limit = min(HOLDERS_PAGE_MAX, max(1, int((q.get("limit") or [str(HOLDERS_PAGE)])[0])))
            except ValueError:
                return self._send(400, {"error": "offset and limit must be integers"})
            with _lock:
                return self._send(200, holders_page(job, offset, limit))
        if u.path == "/api/result":
            job = self._job(q)
            if job is None:
                return self._send(404, {"error": "unknown job"})
            with _lock:
                if not job["done"]:
                    return self._send(200, {"done": False})
                if job["error"]:
                    return self._send(200, {"done": True, "error": job["error"]})
                return self._send(200, {"done": True, "result": job["result"]})
        if u.path in ICONS or u.path in ASSETS:
            name, ctype = ICONS.get(u.path) or ASSETS[u.path]
            try:
                with open(os.path.join(WEB, "static", name), "rb") as f:
                    return self._send(200, f.read(), ctype, ICON_CACHE if u.path in ICONS else ASSET_CACHE)
            except FileNotFoundError:
                return self._send(404, {"error": "not found"})
        if u.path in ("/", "/index.html"):
            return self._send_page(os.path.join(WEB, "index.html"))
        return self._send(404, {"error": "not found"})

    def log_message(self, *a):
        pass


def start_replay_checker():
    """Rug Replay: background check of DANGER token outcomes — only with REPLAY_ENABLED=true."""
    if not replay.enabled():
        return None
    cfg = replay.config()
    print(f"replay: checks every {cfg['check_min']} min, market cap at scan >= ${cfg['min_mcap']:,.0f}, "
          f"clean max {cfg['max_clean_per_hour']}/hour, db {replay.db_path()}", flush=True)
    if not opmem.enabled():
        print("replay: OPMEM_ENABLED is off, new scans are not recorded: only scans already in memory.db are checked",
              flush=True)
    return replay.Checker(replay.db_path(), cfg).start()


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    print(f"rh-crawler on http://0.0.0.0:{port}", flush=True)
    print(memguard.status_line(0), flush=True)
    memguard.start_monitor(scan_stats)
    start_replay_checker()
    ThreadingHTTPServer(("0.0.0.0", port), H).serve_forever()
