"""Phase 15: personal Telegram alerts — detection, queueing with dedupe / cooldown / priority, and delivery.

Sources (every alert is a change between real scans or a fact from memory, never a forecast):
  on_scan(result)   called after every recorded scan (page, bot, radar discovery, alert recheck):
                    - watched tokens: the new alert snapshot is swapped in and diffed against the previous one
                      (alerts.diff: verdict worse, rug appeared / worse / gone, largest operator selling, early buyers
                      exiting, coordinated wallets); one alert per change per watching chat
                    - any fresh token (launched within FRESH_H): a known suspicious deployer launching again, or a
                      smart crew (a recurring holder crew whose shared tokens went up) entering it; sent to the chats
                      with global alerts on
  cron()            /api/cron/alerts (GitHub Actions every 15 min): housekeeping, radar eligibility of watched tokens,
                    rechecks of the watched tokens due (scan + early buyers), then delivery
Queue (Supabase tg_alerts): the database refuses a duplicate dedupe key per chat, and an alert of the same kind for the
same token inside its cooldown. Delivery claims the most urgent first (priority 0 → 3), merges a chat's alerts about
one token into one message, honours a per-chat hourly cap, Telegram's 429 retry_after, and a global send rate.
A chat that blocked the bot (403) is removed with its watchlist.
"""
import html, os, time

from crawler import alerts
from crawler import tg_store as store

FRESH_H = 48              # global alerts only for tokens launched within this many hours
RECHECK_MIN = 30          # default ALERTS_RECHECK_MIN: minutes between rechecks of one watched token
HOURLY_CAP = 20           # default TG_ALERT_HOURLY_CAP: alerts per chat per hour (the rest wait)
SEND_RATE = 20            # messages per second at most (Telegram allows ~30 to different chats)
SEND_RETRIES = 5          # attempts before an alert is marked failed
SCAN_EST_S = 25           # a recheck is only started with at least this much budget left
RADAR_WINDOW_MIN = 1440   # watched token "on the radar": active in the default radar filters over 24 h
SUSPICIOUS_RUGGED = 1     # deployer: ≥ this many earlier tokens labelled rugged by the market ...
SUSPICIOUS_DANGER = 2     # ... or ≥ this many earlier tokens rated DANGER
SMART_CREW_UP = 2         # crew: ≥ this many of its shared earlier tokens labelled up

# kind: (priority, cooldown seconds); priority 0 is the most urgent
KINDS = {
    "rug_appeared": (0, 2 * 3600), "rug_worse": (0, 2 * 3600), "operator_sold": (0, 3600),
    "verdict_worse": (1, 2 * 3600), "early_dropped": (1, 3 * 3600), "coordinated": (1, 3 * 3600),
    "deployer_repeat": (1, 0), "rug_gone": (2, 2 * 3600), "smart_crew": (2, 0), "radar_eligible": (2, 12 * 3600),
}
HEAD = {"deployer_repeat": "🚩", "smart_crew": "🧠", "radar_eligible": "🛰"}


def _env_int(name, default):
    try:
        v = int(os.environ.get(name, "").strip())
        return v if v > 0 else default
    except ValueError:
        return default


def bot_token():
    return os.environ.get("TG_BOT_TOKEN", "").strip()


def enabled():
    """Alerts need the bot token (delivery) and the memory database (queue)."""
    return bool(bot_token()) and store.enabled()


def site_url():
    """Public base URL for report links: SITE_URL, else Vercel's production domain; None when unknown."""
    url = os.environ.get("SITE_URL", "").strip().rstrip("/")
    if url:
        return url
    host = os.environ.get("VERCEL_PROJECT_PRODUCTION_URL", "").strip()
    return f"https://{host}" if host else None


def report_url(chain, token):
    base = site_url()
    if not base:
        return None
    return f"{base}/?ca={token}" + ("" if chain == "solana" else f"&chain={chain}")


def _row(chat_id, chain, token, kind, key, line, snap=None, ticker=None, priority=None):
    pr, cool = KINDS[kind]
    return {"chat_id": int(chat_id), "chain": chain, "token": token, "kind": kind,
            "priority": pr if priority is None else priority, "dedupe_key": key, "cooldown_s": cool,
            "body": {"line": line, "ticker": ticker or (snap or {}).get("ticker"),
                     "now": {k: (snap or {}).get(k) for k in ("band", "score", "rug", "rug_drop")} if snap else None}}


# ---------------------------------------------------------------------------------------------------------------
# detection
# ---------------------------------------------------------------------------------------------------------------
def watch_rows(snap, prev, chats):
    """Alerts for the chats watching a token from the diff of two snapshots."""
    rows = []
    for c in alerts.diff(prev, snap):
        pr = 0 if c["kind"] == "verdict_worse" and c.get("to") == "DANGER" else None
        key = f"{c['kind']}:{c.get('signal', '')}:{snap['chain']}:{snap['token']}:{snap['ts']}"
        rows += [_row(chat, snap["chain"], snap["token"], c["kind"], key, c["text"], snap, priority=pr)
                 for chat in chats]
    return rows


def _fresh(result, now):
    ts = (result.get("launch") or {}).get("ts")
    return isinstance(ts, (int, float)) and now - ts <= FRESH_H * 3600


def global_rows(result, chats, labels=None, now=None):
    """Suspicious deployer launching again / smart crew entering, for a fresh token. labels(chain, tokens) ->
    {token: [outcome labels]} (memory); only called when the token has a crew."""
    now = now or time.time()
    mem = result.get("memory") or {}
    if not chats or not mem or not _fresh(result, now):
        return []
    chain, token = result.get("chain"), result.get("token")
    ticker = (result.get("header") or {}).get("ticker")
    rows = []
    dep = mem.get("deployer") or {}
    rugged, danger, n = dep.get("rugged", 0), dep.get("danger", 0), dep.get("tokens", 0)
    if rugged >= SUSPICIOUS_RUGGED or danger >= SUSPICIOUS_DANGER:
        why = ", ".join(x for x in (f"{rugged} rugged" if rugged else "", f"{danger} rated DANGER" if danger else "") if x)
        line = f"Known suspicious deployer launched again: {n} earlier token{'s' if n != 1 else ''} in memory ({why})"
        rows += [_row(c, chain, token, "deployer_repeat", f"deployer:{chain}:{token}", line, ticker=ticker)
                 for c in chats]
    crews = mem.get("crews") or []
    if crews and labels:
        lab = labels(chain, sorted({t for cr in crews for t in cr.get("tokens") or []})) or {}
        for cr in crews:
            up = [t for t in cr.get("tokens") or [] if "up" in (lab.get(t) or [])]
            if len(up) >= SMART_CREW_UP:
                ws = cr.get("wallets") or []
                line = (f"Smart crew entered: {len(ws)} wallets that held {len(up)} earlier tokens that went up "
                        f"together now hold {cr.get('share_supply', 0) * 100:.1f}% of supply")
                rows += [_row(c, chain, token, "smart_crew", f"crew:{chain}:{token}", line, ticker=ticker)
                         for c in chats]
                break   # one crew alert per token
    return rows


def on_scan(result, early_share=None, deliver=True, send=None):
    """Alerts from one finished scan: watched-token diff and global memory alerts → queue; urgent ones are delivered
    at once (deliver). Never raises: the scan never depends on alerts. → number of alerts queued."""
    if not enabled() or not isinstance(result, dict) or not result.get("token"):
        return 0
    try:
        rows = []
        partial = result.get("partial_scan") or result.get("band") == "TOO_ACTIVE"
        snap = None if partial else alerts.snapshot(result, early_share)
        if snap:
            sw = store.snapshot_swap(snap["chain"], snap["token"], snap)
            if sw and sw.get("watched"):
                rows += watch_rows(snap, sw.get("prev"), sw.get("chats") or [])
        mem = result.get("memory") or {}
        dep = mem.get("deployer") or {}
        if _fresh(result, time.time()) and (mem.get("crews") or dep.get("rugged") or dep.get("danger")):
            rows += global_rows(result, store.global_chats() or [], store.token_labels)
        n = store.enqueue(rows) or 0
        if n and deliver and any(r["priority"] == 0 for r in rows):
            dispatch(send=send, budget=4.0)
        return n
    except Exception as e:   # logged by type only: a token or URL never reaches the log
        print(f"alerts: on_scan failed: {type(e).__name__}: {e}", flush=True)
        return 0


def radar_pass(query=None):
    """Watched tokens that became eligible for the radar (active in the default filters over 24 h) since the last
    pass → alerts; the radar state of every watch is stored. → {"watches", "eligible", "changed"}."""
    from crawler import radar
    rows = store.watched()
    if rows is None:
        return {"error": "memory unavailable"}
    if not rows:
        return {"watches": 0, "eligible": 0, "changed": 0}
    feed = (query or radar.query)({"window": [str(RADAR_WINDOW_MIN)]})
    if not feed.get("available"):
        return {"error": feed.get("reason")}
    on = {(i["chain"], i["token"]): i for i in feed.get("items") or [] if i.get("status") == "active"}
    changes, out = [], []
    for w in rows:
        item = on.get((w["chain"], w["token"]))
        active = item is not None
        if active != bool(w.get("radar_active")):
            changes.append({"chat_id": w["chat_id"], "chain": w["chain"], "token": w["token"], "active": active})
        if active and not w.get("radar_active"):
            day = time.strftime("%Y%m%d", time.gmtime())
            line = (f"Now on the launch radar: {item.get('band')} · score {item.get('score')}"
                    + (f" · mcap ${item['mcap_usd']:,.0f}" if item.get("mcap_usd") else ""))
            out.append(_row(w["chat_id"], w["chain"], w["token"], "radar_eligible",
                            f"radar:{w['chain']}:{w['token']}:{day}", line, ticker=w.get("ticker") or item.get("ticker")))
    store.radar_set(changes)
    n = store.enqueue(out) or 0
    return {"watches": len(rows), "eligible": len(on), "changed": len(changes), "queued": n}


# ---------------------------------------------------------------------------------------------------------------
# delivery
# ---------------------------------------------------------------------------------------------------------------
def _e(s):
    return html.escape(str(s), quote=False)


def _short(a):
    return f"{a[:6]}…{a[-4:]}" if a and len(a) > 12 else (a or "")


def compose(group, watched=True):
    """One message for a chat's alerts about one token (most urgent first) → (html, reply_markup)."""
    first = group[0]
    chain, token = first.get("chain"), first.get("token")
    body = [g.get("body") or {} for g in group]
    ticker = next((b.get("ticker") for b in body if b.get("ticker")), None)
    name = f"${_e(ticker)}" if ticker else _e(_short(token))
    icon = HEAD.get(first["kind"], "🔔")
    lines = [f"{icon} <b>{name}</b> · {_e(alerts.CHAIN_NAME.get(chain, chain or ''))}", ""]
    seen = set()
    for b in body:
        if b.get("line") and b["line"] not in seen:
            seen.add(b["line"])
            lines.append(f"• {_e(b['line'])}")
    now = next((b.get("now") for b in reversed(body) if b.get("now")), None)
    if now and now.get("band"):
        lines.append("")
        band = now["band"]
        if now.get("score") is not None:
            lines.append(f"Now: {alerts.BAND_ICON.get(band, '⚪️')} <b>{_e(band)}</b> · score {now['score']}/100")
        else:
            lines.append(f"Now: <b>{_e(band.replace('_', ' '))}</b>")
        if now.get("rug"):
            lines.append("⚠️ probably rug" + (f" −{now['rug_drop'] * 100:.0f}%" if now.get("rug_drop") else ""))
    lines.append(f"<code>{_e(token)}</code>")
    row = []
    url = report_url(chain, token)
    if url:
        row.append({"text": "Full report", "url": url})
    is_watch = any(g["kind"] not in ("deployer_repeat", "smart_crew") for g in group)
    row.append({"text": "🔕 Unwatch", "callback_data": f"unwatch:{chain}:{token}"} if is_watch and watched
               else {"text": "🔔 Watch", "callback_data": f"watch:{chain}:{token}"})
    return "\n".join(lines), {"inline_keyboard": [row]}


def telegram_send():
    """send(chat_id, html, markup) through the Bot API (bot/tg.py). The token stays in the request URL only."""
    from bot.tg import Telegram
    tg = Telegram(bot_token())

    def send(chat_id, text, markup):
        return tg.call("sendMessage", chat_id=chat_id, text=text, parse_mode="HTML", reply_markup=markup,
                       link_preview_options={"is_disabled": True})
    return send


def dispatch(send=None, budget=20.0, limit=60, clock=time.monotonic, sleep=time.sleep):
    """Deliver queued alerts until the queue is empty or the budget is spent → {"claimed", "sent", "failed",
    "retry", "messages"}."""
    from bot.tg import TelegramError
    t0 = clock()
    out = {"claimed": 0, "sent": 0, "failed": 0, "retry": 0, "messages": 0}
    if not enabled():
        return out
    send = send or telegram_send()
    cap = _env_int("TG_ALERT_HOURLY_CAP", HOURLY_CAP)
    while clock() - t0 < budget:
        rows = store.claim(limit, cap)
        if not rows:
            break
        out["claimed"] += len(rows)
        groups = {}
        for r in rows:
            groups.setdefault((r["chat_id"], r.get("chain"), r.get("token")), []).append(r)
        results, gone = [], set()
        for (chat_id, _, _), group in groups.items():
            ids = [g["id"] for g in group]
            if chat_id in gone or clock() - t0 >= budget:   # out of time: back to the queue untouched
                results += [{"id": i, "status": "queued", "retry_s": 0} for i in ids]
                continue
            text, markup = compose(group)
            try:
                send(chat_id, text, markup)
                results += [{"id": i, "status": "sent"} for i in ids]
                out["sent"] += len(ids)
                out["messages"] += 1
            except TelegramError as e:
                if e.code == 403:                 # the user blocked the bot: drop the chat and its watchlist
                    gone.add(chat_id)
                    store.chat_gone(chat_id)
                    out["failed"] += len(ids)
                    continue                      # rows were deleted with the chat
                retry = e.code in (0, 429) or e.code >= 500
                attempts = max(g.get("attempts") or 1 for g in group)
                if retry and attempts < SEND_RETRIES:
                    wait = int(e.retry_after or 30 * attempts)
                    results += [{"id": i, "status": "queued", "retry_s": wait, "error": str(e)[:200]} for i in ids]
                    out["retry"] += len(ids)
                    if e.code == 429:
                        sleep(min(float(e.retry_after or 1), 5.0))
                else:
                    results += [{"id": i, "status": "failed", "error": str(e)[:200]} for i in ids]
                    out["failed"] += len(ids)
            sleep(1.0 / SEND_RATE)
        store.done(results)
        if len(rows) < limit:
            break
    return out


# ---------------------------------------------------------------------------------------------------------------
# cron
# ---------------------------------------------------------------------------------------------------------------
def early_share(chain, token, early_get=None):
    """Supply share the first buyers hold now (early.py), or None when not measurable here."""
    if chain not in ("robinhood", "solana"):   # early.get resolves the chain from the bare address
        return None
    try:
        from crawler import early
        d = (early_get or early.get)(token)
        s = d.get("summary") if d.get("available") and not d.get("error") else None
        return s.get("held_share_supply", s.get("now_share_supply")) if s else None
    except Exception as e:
        print(f"alerts: early buyers not read: {type(e).__name__}", flush=True)
        return None


def cron(scan, budget=48.0, clock=time.monotonic, send=None, query=None, early_get=None):
    """One alerts run. scan(chain, token) -> engine result, already recorded in the site's stores (not in alerts:
    on_scan runs here, with the early buyers share). → summary, also stored as tg_state 'alerts_cron'."""
    t0 = clock()
    out = {"rechecked": [], "failed": []}
    out["housekeeping"] = store.housekeeping()
    try:
        out["radar"] = radar_pass(query)
    except Exception as e:
        out["radar"] = {"error": type(e).__name__}
    due = store.watch_due(_env_int("ALERTS_RECHECK_MIN", RECHECK_MIN), 50) or []
    out["due"] = len(due)
    for d in due:
        if budget - (clock() - t0) < SCAN_EST_S:
            break
        try:
            res = scan(d["chain"], d["token"])
            share = early_share(d["chain"], d["token"], early_get) if budget - (clock() - t0) > 12 else None
            n = on_scan(res, early_share=share, deliver=False)
            out["rechecked"].append({"chain": d["chain"], "token": d["token"], "band": res.get("band"), "queued": n})
        except Exception as e:
            out["failed"].append({"chain": d["chain"], "token": d["token"], "error": type(e).__name__})
        store.watch_checked(d["chain"], d["token"])   # a failing token waits its turn instead of blocking the queue
    out["dispatch"] = dispatch(send=send, budget=max(2.0, budget - (clock() - t0)))
    out["elapsed_s"] = round(clock() - t0, 1)
    store.state_set("alerts_cron", {"due": out["due"], "rechecked": len(out["rechecked"]),
                                    "failed": len(out["failed"]), "sent": out["dispatch"]["sent"]})
    return out
