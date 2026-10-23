"""Phase 12: live launch radar — fresh tokens whose latest real scan meets distribution and risk criteria.

Data: the memory brain (Supabase). radar_feed returns every token first seen (launched, or first scanned when the
launch time is unknown) inside the window, with its latest scan, the scan before it, its bundle at both scans, the
newest market snapshot and any measured outcome. Nothing here invents a verdict: every row is a stored scan, shown
with its scan time.

Each token is evaluated (evaluate) into
  active      the latest scan qualifies and nothing since contradicts it
  downgraded  still listed, with the reasons: stale scan, the verdict slipped but still qualifies, operator selling,
              bundle selling, the market moved against the scan
  removed     no longer on the default radar, with the reasons: the verdict left the allowed bands, a probably-rug
              projection, a collapse (price or liquidity), a rugged outcome, data too old
Filters (filter_rows): chain, launchpad, age, market cap, liquidity, minimum score, bundle exposure, largest operator
share, recent price change, scan completeness, bands. Default view: 60 minutes, CLEAN and OK with CLEAN first.

Discovery (discover): GeckoTerminal's newest pools on a chain, fresh and liquid enough, not scanned in the last
RESCAN_MIN minutes, are scanned with the real engine and recorded to memory; the chain's radar tokens get a market
snapshot (so a collapse between scans is seen) and the stalest qualifying ones are rescanned. It runs from
GET /api/cron/radar (GitHub Actions every 30 minutes, Authorization: Bearer CRON_SECRET).
"""
import os, threading, time, urllib.parse
from datetime import datetime, timezone

from crawler import detect as d
from crawler import engine, market, memory_db

BAND_RANK = {"CLEAN": 0, "OK": 1, "RISKY": 2, "DANGER": 3}
DEFAULT_BANDS = ("CLEAN", "OK")
WINDOW_MIN = int(os.environ.get("RADAR_WINDOW_MIN") or 60)          # default view
WINDOW_MAX = 7 * 24 * 60                                              # widest window allowed
STALE_MIN = int(os.environ.get("RADAR_STALE_MIN") or 45)              # scan older than this: downgraded (stale)
EXPIRE_MIN = int(os.environ.get("RADAR_EXPIRE_MIN") or 180)           # older than this: removed (data too old)
OPERATOR_SELL = 0.05      # largest operator's share of float fell by ≥ 5 points between scans: operator selling
BUNDLE_SELL = 0.05        # launch-bundle supply sold grew by ≥ 5% of supply between scans
BUNDLE_HEAVY = 0.25       # launch-bundle supply still concentrated ≥ 25% of supply: downgraded
MARKET_DOWN = 0.5         # price or liquidity at or below half of the scan's: the clean scan is out of date
COLLAPSE_PRICE, COLLAPSE_LIQ = 0.2, 0.1    # collapse: removed
CACHE_S = 10.0            # radar responses per query (the page polls every 15 s)

# discovery
MIN_LIQ_USD = float(os.environ.get("RADAR_MIN_LIQ_USD") or 3000)
NEW_MAX_MIN = 60          # pools created within this many minutes
RESCAN_MIN = 30           # a token scanned more recently than this is not rescanned by discovery
SCAN_START_S = 22.0       # no new scan starts after this many seconds of a run: a scan takes up to ~25 s and the
RUN_MAX_S = 50.0          # whole run must end inside Vercel's 60 s function limit (market refresh stops here)
REFRESH_MAX = 12          # radar tokens per chain that get a market snapshot per run

_cache, _lock = {}, threading.Lock()


def _ts(iso):
    if not iso:
        return None
    try:
        t = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
    except ValueError:
        return None
    return (t if t.tzinfo else t.replace(tzinfo=timezone.utc)).timestamp()


def _ratio(now, then):
    return now / then if now is not None and then else None


def evaluate(row, now=None, bands=DEFAULT_BANDS):
    """{"status": active | downgraded | removed, "reasons": [...], "scan_age_min", "price_change", "liq_change"}."""
    now = now or time.time()
    scan, prev = row.get("scan") or {}, row.get("prev") or {}
    b, pb = row.get("bundle") or {}, row.get("prev_bundle") or {}
    removed, down = [], []
    age = (now - _ts(scan.get("scanned_at"))) / 60 if _ts(scan.get("scanned_at")) else None
    band = scan.get("band")
    if band not in bands:
        removed.append(f"verdict {str(band).lower()}" + (f" (was {prev['band'].lower()})" if prev.get("band") else ""))
    elif prev.get("band") in BAND_RANK and BAND_RANK.get(band, 9) > BAND_RANK[prev["band"]]:
        down.append(f"verdict slipped from {prev['band'].lower()} to {band.lower()}")
    if scan.get("rug"):
        removed.append("probably-rug projection on the latest scan")
    po, co = prev.get("largest_operator_share"), scan.get("largest_operator_share")
    if po is not None and co is not None and po - co >= OPERATOR_SELL:
        down.append(f"largest operator's share fell {100 * (po - co):.1f} points (selling or moving)")
    ps, cs = pb.get("sold_share"), b.get("sold_share")
    if ps is not None and cs is not None and cs - ps >= BUNDLE_SELL:
        down.append(f"launch bundle sold another {100 * (cs - ps):.1f}% of supply")
    if (b.get("concentrated_share") or 0) >= BUNDLE_HEAVY:
        down.append(f"launch bundle still holds {100 * b['concentrated_share']:.0f}% of supply")
    m0, m1 = scan.get("market") or {}, row.get("market_now") or {}
    pr = lr = None
    if m1 and _ts(m1.get("observed_at")) and _ts(scan.get("scanned_at")) and \
            _ts(m1["observed_at"]) > _ts(scan["scanned_at"]):
        pr, lr = _ratio(m1.get("price_usd"), m0.get("price_usd")), _ratio(m1.get("liquidity_usd"), m0.get("liquidity_usd"))
        if (pr is not None and pr <= COLLAPSE_PRICE) or (lr is not None and lr <= COLLAPSE_LIQ):
            removed.append("collapsed since the scan"
                           + (f": price {100 * (pr - 1):+.0f}%" if pr is not None else "")
                           + (f", liquidity {100 * (lr - 1):+.0f}%" if lr is not None else ""))
        elif (pr is not None and pr <= MARKET_DOWN) or (lr is not None and lr <= MARKET_DOWN):
            down.append("market moved against the scan"
                        + (f": price {100 * (pr - 1):+.0f}%" if pr is not None else "")
                        + (f", liquidity {100 * (lr - 1):+.0f}%" if lr is not None else ""))
    if (row.get("outcome") or {}).get("label") == "rugged":
        removed.append("outcome: rugged")
    if age is not None and age > EXPIRE_MIN:
        removed.append(f"latest scan is {age / 60:.1f} h old")
    elif age is not None and age > STALE_MIN:
        down.append(f"scan is {age:.0f} min old (stale)")
    status = "removed" if removed else "downgraded" if down else "active"
    return {"status": status, "reasons": removed + down, "scan_age_min": None if age is None else round(age, 1),
            "price_change": None if pr is None else pr - 1, "liq_change": None if lr is None else lr - 1}


def _num(q, k):
    try:
        v = (q.get(k) or [""])[0]
        return float(v) if v != "" else None
    except (TypeError, ValueError):
        return None


def params(q):
    """Validated query: every bound optional; window clamped to [1 min, 7 days]."""
    bands = tuple(b for b in ((q.get("bands") or [""])[0].upper().split(",")) if b in BAND_RANK) or DEFAULT_BANDS
    w = _num(q, "window")
    return {"window": int(min(WINDOW_MAX, max(1, w))) if w else WINDOW_MIN,
            "chain": (q.get("chain") or [""])[0].lower() or None,
            "launchpad": (q.get("launchpad") or [""])[0].lower() or None,
            "age_min": _num(q, "age_min"), "age_max": _num(q, "age_max"),
            "mcap_min": _num(q, "mcap_min"), "mcap_max": _num(q, "mcap_max"),
            "liq_min": _num(q, "liq_min"), "score_min": _num(q, "score_min"),
            "bundle_max": _num(q, "bundle_max"), "operator_max": _num(q, "operator_max"),
            "change_min": _num(q, "change_min"), "change_max": _num(q, "change_max"),
            "complete": (q.get("complete") or [""])[0] in ("1", "true", "yes"),
            "bands": bands, "show": (q.get("show") or ["active"])[0]}


def filter_rows(rows, p, now=None):
    """Evaluate and filter radar_feed rows; CLEAN first, then score, then newest scan."""
    now = now or time.time()
    out = []
    for r in rows:
        scan = r.get("scan") or {}
        ev = evaluate(r, now, p["bands"])
        if p["show"] == "active" and ev["status"] == "removed":
            continue
        if p["show"] == "removed" and ev["status"] != "removed":
            continue
        born = _ts(r.get("launched_at")) or _ts(r.get("first_scan_at"))
        age = (now - born) / 60 if born else None
        m = r.get("market_now") if ev["price_change"] is not None else None
        m = m or scan.get("market") or {}
        checks = [
            (p["chain"], lambda: r.get("chain") == p["chain"]),
            (p["launchpad"], lambda: (r.get("launchpad") or "dex") == p["launchpad"]),
            (p["age_min"], lambda: age is not None and age >= p["age_min"]),
            (p["age_max"], lambda: age is not None and age <= p["age_max"]),
            (p["mcap_min"], lambda: (m.get("mcap_usd") or 0) >= p["mcap_min"]),
            (p["mcap_max"], lambda: m.get("mcap_usd") is not None and m["mcap_usd"] <= p["mcap_max"]),
            (p["liq_min"], lambda: (m.get("liquidity_usd") or 0) >= p["liq_min"]),
            (p["score_min"], lambda: scan.get("score") is not None and scan["score"] >= p["score_min"]),
            (p["bundle_max"], lambda: ((r.get("bundle") or {}).get("concentrated_share") or 0) <= p["bundle_max"]),
            (p["operator_max"], lambda: (scan.get("largest_operator_share") or 0) <= p["operator_max"]),
            (p["change_min"], lambda: ev["price_change"] is not None and ev["price_change"] >= p["change_min"]),
            (p["change_max"], lambda: ev["price_change"] is not None and ev["price_change"] <= p["change_max"]),
            (p["complete"] or None, lambda: scan.get("coverage") == "full"),
        ]
        if any(v is not None and not ok() for v, ok in checks):
            continue
        out.append({"chain": r.get("chain"), "token": r.get("token"), "ticker": r.get("ticker"), "name": r.get("name"),
                    "launchpad": r.get("launchpad"), "age_min": None if age is None else round(age, 1),
                    "launched_at": r.get("launched_at"), "scanned_at": scan.get("scanned_at"),
                    "band": scan.get("band"), "score": scan.get("score"), "coverage": scan.get("coverage"),
                    "confidence": scan.get("confidence"),
                    "largest_operator_share": scan.get("largest_operator_share"),
                    "bundle": r.get("bundle"), "mcap_usd": m.get("mcap_usd"), "liquidity_usd": m.get("liquidity_usd"),
                    "price_usd": m.get("price_usd"), "market_at": m.get("observed_at") or scan.get("scanned_at"),
                    "shadow": {k: v.get("band") for k, v in (scan.get("scores") or {}).items()},
                    **ev})
    out.sort(key=lambda x: (x["status"] != "active", BAND_RANK.get(x["band"], 9), -(x["score"] or 0),
                            -(_ts(x["scanned_at"]) or 0)))
    return out


def query(q, now=None):
    """GET /api/radar response (cached CACHE_S per query string)."""
    p = params(q)
    key = urllib.parse.urlencode(sorted((k, str(v)) for k, v in p.items()))
    t = time.time()
    with _lock:
        hit = _cache.get(key)
        if hit and t - hit[0] < CACHE_S:
            return hit[1]
    if not memory_db.enabled():
        return {"available": False, "reason": "memory is off", "params": p}
    since = (now or t) - p["window"] * 60
    rows = memory_db.call("radar_feed", {"p_since": memory_db.iso(since), "p_limit": 300})
    if rows is None:   # not cached: the next poll tries again
        return {"available": False, "reason": "memory unavailable", "params": p}
    items = filter_rows(rows, p, now)
    out = {"available": True, "params": {k: v for k, v in p.items()}, "generated_at": int(t),
           "counts": {s: sum(1 for i in items if i["status"] == s) for s in ("active", "downgraded", "removed")},
           "tokens_in_window": len(rows), "items": items}
    with _lock:
        _cache[key] = (t, out)
        if len(_cache) > 200:
            _cache.pop(min(_cache, key=lambda k: _cache[k][0]))
    return out


# ---------------------------------------------------------------------------
# discovery
# ---------------------------------------------------------------------------
STABLES = {"solana": {"So11111111111111111111111111111111111111112",
                      "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",    # USDC
                      "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY41kQzDw4mMx4NgHB"},   # USDT
           "base": {"0x833589fcd6edb6e08f4c7c32d4f71b54bda02913"},         # USDC
           "bsc": {"0x55d398326f99059ff775485246999027b3197955",            # USDT
                   "0x8ac76a51cc950d9822d68b83fe1ad9a2a339cecb"}}           # USDC
NATIVE = "0x" + "0" * 40
DP_PAGE = 50


def _quotes(chain):
    """Tokens that are the quote side of a pool on this chain: native, wrapped native, main stablecoins."""
    q = {NATIVE} | STABLES.get(chain, set())
    try:
        net = engine.evm_networks.NETWORKS[chain]
        q |= {net.wrapped_native.lower()} | {a.lower() for a in net.stables}
    except (AttributeError, KeyError):
        pass
    return q


def _gt_new_pools(chain, budget):
    net = engine.GT_NETWORK.get(chain, chain)
    data = market._get(f"{market.GT}/{net}/new_pools?page=1", budget)
    out = []
    for p in (data or {}).get("data") or []:
        a, rel = p.get("attributes") or {}, p.get("relationships") or {}
        base = ((rel.get("base_token") or {}).get("data") or {}).get("id") or ""
        tok = base.split("_", 1)[1] if "_" in base else None
        if tok:
            out.append({"token": tok, "pool": a.get("address"), "created": _ts(a.get("pool_created_at")),
                        "liquidity_usd": float(a.get("reserve_in_usd") or 0),
                        "dex": ((rel.get("dex") or {}).get("data") or {}).get("id"), "source": "gt"})
    return out


def _dp_new_pools(chain, budget):
    """DexPaprika's newest pools. The new token is the side that is not a quote: a known quote coin, or a token that
    is one side of 3+ of the newest pools (a launchpad's quote coin we do not know by address)."""
    net = market.DP_CHAIN.get(engine.GT_NETWORK.get(chain, chain), chain)
    data = market._get(f"{market.DP}/{net}/pools/search?limit={DP_PAGE}&order_by=created_at&sort=desc", budget)
    rows = (data or {}).get("results") or []
    seen = {}
    for p in rows:
        for t in p.get("tokens") or []:
            seen[t.get("id")] = seen.get(t.get("id"), 0) + 1
    norm = (lambda a: a) if chain == "solana" else (lambda a: (a or "").lower())
    quotes = _quotes(chain) | {norm(a) for a, n in seen.items() if n >= 3}
    out = []
    for p in rows:
        ids = [norm(t.get("id")) for t in p.get("tokens") or []]
        side = [a for a in ids if a and a not in quotes]
        if len(ids) == 2 and len(side) == 1:
            out.append({"token": side[0], "pool": p.get("id"), "created": _ts(p.get("created_at")),
                        "liquidity_usd": float(p.get("liquidity_usd") or 0), "dex": p.get("dex_id"), "source": "dp"})
    return out


def new_pools(chain, budget=6.0):
    """The chain's newest pools: [{"token", "pool", "created", "liquidity_usd", "dex", "source"}], from
    GeckoTerminal, or DexPaprika when GeckoTerminal fails (rate limit). Not cached: discovery needs what is new now."""
    try:
        return _gt_new_pools(chain, budget / 2)
    except Exception as e:
        print(f"radar: GeckoTerminal new pools failed for {chain} ({type(e).__name__}), using DexPaprika", flush=True)
    return _dp_new_pools(chain, budget / 2)


def candidates(pools, recent, now=None, min_liq=MIN_LIQ_USD, max_age_min=NEW_MAX_MIN):
    """Fresh, liquid, not recently scanned, one per token, most liquid first."""
    now = now or time.time()
    best = {}
    for p in pools:
        if p["token"] in recent or p["liquidity_usd"] < min_liq:
            continue
        if p["created"] is None or now - p["created"] > max_age_min * 60:
            continue
        if p["token"] not in best or p["liquidity_usd"] > best[p["token"]]["liquidity_usd"]:
            best[p["token"]] = p
    return sorted(best.values(), key=lambda p: -p["liquidity_usd"])


def _scan_and_record(token, chain, record):
    memo = {}
    res = engine.scan(token, chain=chain, memory_out=memo)
    record(res, memo)
    return res


def discover(chain, budget=SCAN_START_S, record=None, scan=None, pools=None, now=None):
    """One discovery pass for a chain: {"chain", "candidates", "scanned": [...], "failed", "refreshed", "rescanned"}.
    record(res, memo) — stores a finished scan (the server's stores: recent feed, memory). Each step is bounded by
    the run's time budget; a failure in one token never stops the run."""
    t0 = time.time()
    scan = scan or (lambda tok: _scan_and_record(tok, chain, record))
    out = {"chain": chain, "candidates": 0, "scanned": [], "failed": [], "refreshed": 0, "rescanned": []}
    try:
        pools = new_pools(chain) if pools is None else pools
    except Exception as e:
        out["error"] = f"new pools: {type(e).__name__}"
        pools = []
    toks = list(dict.fromkeys(p["token"] for p in pools))
    recent = set(memory_db.call("radar_recent", {"p_chain": chain, "p_tokens": toks, "p_minutes": RESCAN_MIN})
                 or []) if toks else set()
    cands = candidates(pools, recent, now)
    out["candidates"] = len(cands)
    for c in cands:
        if time.time() - t0 > budget:
            break
        try:
            res = scan(c["token"])
            out["scanned"].append({"token": c["token"], "band": res.get("band"), "score": res.get("score")})
        except Exception as e:
            out["failed"].append({"token": c["token"], "error": type(e).__name__})
    # radar tokens of this chain: a market snapshot each (a collapse between scans is seen), then rescan the stalest
    rows = [r for r in memory_db.call("radar_feed", {"p_since": memory_db.iso((now or t0) - WINDOW_MIN * 60),
                                                     "p_limit": 300}) or [] if r.get("chain") == chain]
    snaps = []
    for r in rows[:REFRESH_MAX]:
        if time.time() - t0 > RUN_MAX_S - 4:
            break
        m = market.fetch_market(r["token"], engine.GT_NETWORK.get(chain, chain), budget=3.0) or {}
        if m.get("price_usd") is not None or m.get("liquidity_usd") is not None:
            snaps.append({"chain": chain, "token": r["token"], "source": m.get("source"),
                          **{k: m.get(k) for k in ("price_usd", "mcap_usd", "fdv_usd", "liquidity_usd")}})
    if snaps:
        out["refreshed"] = memory_db.call("market_snapshot_record", {"p": {"rows": snaps}}) or 0
    stale = sorted((r for r in rows if (r.get("scan") or {}).get("band") in DEFAULT_BANDS
                    and evaluate(r, now)["status"] != "removed"),
                   key=lambda r: _ts((r.get("scan") or {}).get("scanned_at")) or 0)
    for r in stale:
        age = (now or time.time()) - (_ts(r["scan"].get("scanned_at")) or 0)
        if time.time() - t0 > budget or age < RESCAN_MIN * 60:
            break
        try:
            res = scan(r["token"])
            out["rescanned"].append({"token": r["token"], "band": res.get("band"), "score": res.get("score")})
        except Exception as e:
            out["failed"].append({"token": r["token"], "error": type(e).__name__})
    out["elapsed_s"] = round(time.time() - t0, 1)
    return out
