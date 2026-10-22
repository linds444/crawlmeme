"""Phase 11 outcome worker: what happened to each scanned token after the scan, measured from market data only.

Every recorded scan with a price gets pending outcomes at 24 h and 7 days (memory_record). This worker reads the due
ones (memory_db.outcomes_due), measures each token's price and liquidity now (market.fetch_market: GeckoTerminal, then
DexScreener, then DexPaprika), labels the change against the market snapshot taken at scan time, and stores it
(memory_db.outcome_record). It runs from GET /api/cron/outcomes (Vercel Cron, Authorization: Bearer CRON_SECRET).

Labels — market facts, never our own verdict (no circular self-labelling), and every scanned token gets one whatever
its verdict (no survivorship bias):
  rugged    liquidity at most RUG_LIQ of what it was and price at most RUG_PRICE of it (liquidity pulled or drained)
  dead      price at most DEAD_PRICE of the scan price, liquidity still there
  up        price at least UP_PRICE times the scan price
  survived  anything else
  unpriced  no market data RETRY_H hours past due (the token is not listed anywhere we can read)
A label is usable only by reads whose as-of time is after its measured_at (memory_lookup), so a backtest at time T
never sees an outcome measured after T.
"""
import time
from datetime import datetime, timezone

from crawler import engine, market, memory_db

RUG_LIQ, RUG_PRICE = 0.10, 0.20
DEAD_PRICE = 0.10
UP_PRICE = 2.0
RETRY_H = 72               # hours past due before a token with no market data is labelled unpriced
BATCH = 60                 # due outcomes read per run
BUDGET_S = 40.0            # seconds per run (Vercel function limit 60 s)


def label(base_price, base_liq, price, liq):
    """(label, price_ratio, liquidity_ratio) or (None, …) when there is nothing to compare."""
    pr = price / base_price if price is not None and base_price else None
    lr = liq / base_liq if liq is not None and base_liq else None
    if pr is None and lr is None:
        return None, None, None
    if lr is not None and lr <= RUG_LIQ and (pr is None or pr <= RUG_PRICE):
        return "rugged", pr, lr
    if pr is not None and pr <= DEAD_PRICE:
        return "dead", pr, lr
    if pr is not None and pr >= UP_PRICE:
        return "up", pr, lr
    return "survived", pr, lr


def _hours_past(due_iso, now):
    try:
        due = datetime.fromisoformat(due_iso.replace("Z", "+00:00"))
    except (AttributeError, TypeError, ValueError):
        return 0.0
    if due.tzinfo is None:
        due = due.replace(tzinfo=timezone.utc)
    return (now - due.timestamp()) / 3600


def run(limit=BATCH, budget=BUDGET_S, fetch=None):
    """One pass: {"due", "measured", "unpriced", "pending", "tokens"}. fetch(token, network) → market dict (tests)."""
    t0 = time.time()
    fetch = fetch or (lambda tok, net: market.fetch_market(tok, net, budget=min(8.0, max(1.0, t0 + budget - time.time()))))
    due = memory_db.outcomes_due(limit)
    seen, rows, unpriced, pending = {}, [], 0, 0
    for o in due:
        if time.time() - t0 > budget:
            pending += 1
            continue
        key = (o["chain"], o["token"])
        if key not in seen:
            try:
                seen[key] = fetch(o["token"], engine.GT_NETWORK.get(o["chain"], o["chain"])) or {}
            except Exception as e:   # one token's market failure never stops the pass
                print(f"outcomes: {o['token']}: {type(e).__name__}", flush=True)
                seen[key] = {}
        m = seen[key]
        lab, pr, lr = label(o.get("price_usd"), o.get("liquidity_usd"), m.get("price_usd"), m.get("liquidity_usd"))
        if lab is None:
            if _hours_past(o.get("due_at") or "", time.time()) < RETRY_H:
                pending += 1          # try again on a later run
                continue
            lab, unpriced = "unpriced", unpriced + 1
        rows.append({"scan_id": o["scan_id"], "horizon_h": o["horizon_h"], "chain": o["chain"], "token": o["token"],
                     "price_ratio": pr, "liquidity_ratio": lr, "label": lab,
                     "basis": {"source": m.get("source"), "price_usd": m.get("price_usd"),
                               "mcap_usd": m.get("mcap_usd"), "fdv_usd": m.get("fdv_usd"),
                               "liquidity_usd": m.get("liquidity_usd"),
                               "scan_price_usd": o.get("price_usd"), "scan_liquidity_usd": o.get("liquidity_usd"),
                               "rules": {"rug_liq": RUG_LIQ, "rug_price": RUG_PRICE, "dead_price": DEAD_PRICE,
                                         "up_price": UP_PRICE}}})
    written = memory_db.outcome_record(rows) if rows else 0
    return {"due": len(due), "measured": written, "unpriced": unpriced, "pending": pending, "tokens": len(seen),
            "elapsed_s": round(time.time() - t0, 1)}
