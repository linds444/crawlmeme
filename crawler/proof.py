"""Phase 13: historical proof — Rug Replay, Gem Replay and Proof of Work over CrawlMeme's own immutable scan records.

Records: every scan row is written once and never changed (database triggers make scans and market snapshots
append-only, and a measured outcome immutable); each carries a SHA-256 fingerprint of its scan-time record, which
/api/proof/scan re-checks. A tracker (GET /api/cron/track, GitHub Actions every 30 minutes) takes a market snapshot of
every token scanned within the longest window, so peaks and troughs are observed, not guessed.

Eligible scan: each token's FIRST scan with a verdict (CLEAN / OK / RISKY / DANGER) and a price, inside the chosen
period (one row per token: a token scanned twenty times is one case, not twenty). Its reference is the market cap at
scan (or the price when the market cap is unknown — same ratio for a fixed supply).

Within the observation window (scan time, scan time + window], from snapshots taken after the scan:
  trough  lowest market cap seen        peak  highest market cap seen        end  the last snapshot in the window
Event (configurable):
  rug  the market cap fell at least DROP (default 90%) — at any point (measure "any") or at the window end ("end")
  gem  the market cap rose to at least MULT times (default 3x) — at any point or at the window end
Resolution: an event seen is a resolved positive as soon as it is seen ("any"); a negative needs the window to have
passed and a snapshot in its last quarter (otherwise "unresolved": not counted as either). With "end", both need that.
Prediction: Rug Replay — the verdict was DANGER; Gem Replay — the verdict was CLEAN (of the chosen model, live or
shadow, as stored at scan time).
Metrics over resolved cases: true / false positives and negatives, precision, recall (each with a Wilson 95% interval),
the base rate, coverage (resolved / eligible) and the unresolved count; the full denominator is always shown.
Past results describe what happened to these scanned tokens. They do not make a verdict a prediction.
"""
import math, os, threading, time, urllib.parse

from crawler import market, memory_db

WINDOWS_H = (24, 72, 168, 720)
DEFAULT = {"view": "all", "window_h": 168, "drop": 0.90, "mult": 3.0, "measure": "any", "since_days": 30,
           "chain": None, "model": "crawlmeme-1", "min_mcap": 0.0, "cls": None}
LATE = 0.75               # a negative needs a snapshot in the last quarter of the window
CACHE_S = 60.0
MODELS = ("crawlmeme-1", "crawlmeme-2", "crawlmeme-3")
EXPLORER = {"robinhood": "https://robinhoodchain.blockscout.com/token/", "base": "https://basescan.org/token/",
            "bsc": "https://bscscan.com/token/", "solana": "https://solscan.io/token/"}
GT_NET = {"robinhood": "robinhood", "base": "base", "bsc": "bsc", "solana": "solana"}

# tracker
TRACK_WINDOW_H = max(WINDOWS_H[:3])   # tokens scanned within 7 days are tracked (the 30-day window is sparser)
TRACK_GAP_MIN = 25        # a token is sampled at most once per this many minutes
TRACK_BATCH = 30          # tokens per DexScreener request (/tokens/v1 maximum)
TRACK_BUDGET_S = 45.0
TRACK_PAUSE_S = 0.5

_cache, _lock = {}, threading.Lock()


def _ts(v):
    from datetime import datetime, timezone
    if not v:
        return None
    try:
        t = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    except ValueError:
        return None
    return (t if t.tzinfo else t.replace(tzinfo=timezone.utc)).timestamp()


def wilson(k, n, z=1.96):
    """95% Wilson interval of a proportion k/n: (low, high), or None for n = 0."""
    if not n:
        return None
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (max(0.0, c - h), min(1.0, c + h))


def _f(q, k, lo, hi, default):
    try:
        v = float((q.get(k) or [""])[0])
    except (TypeError, ValueError):
        return default
    return min(hi, max(lo, v))


def params(q):
    view = (q.get("view") or ["all"])[0]
    model = (q.get("model") or [DEFAULT["model"]])[0]
    measure = (q.get("measure") or ["any"])[0]
    cls = (q.get("cls") or [""])[0].upper()
    return {"view": view if view in ("rug", "gem", "all") else "all",
            "window_h": int(_f(q, "window_h", 1, 720, DEFAULT["window_h"])),
            "drop": _f(q, "drop", 0.5, 0.999, DEFAULT["drop"]),
            "mult": _f(q, "mult", 1.5, 100, DEFAULT["mult"]),
            "measure": measure if measure in ("any", "end") else "any",
            "since_days": int(_f(q, "since_days", 1, 365, DEFAULT["since_days"])),
            "chain": (q.get("chain") or [""])[0].lower() or None,
            "model": model if model in MODELS else DEFAULT["model"],
            "min_mcap": _f(q, "min_mcap", 0, 1e12, 0.0),
            "cls": cls if cls in ("TP", "FP", "FN", "TN", "UNRESOLVED") else None}


def ratios(row):
    """(trough, peak, end) as multiples of the scan-time value: market cap when both sides have it, else price."""
    m0 = row.get("market") or {}
    last = row.get("last") or {}
    base_mcap = m0.get("mcap_usd") or m0.get("fdv_usd")
    if base_mcap and row.get("trough_mcap") is not None and row.get("peak_mcap") is not None:
        end = last.get("mcap_usd") / base_mcap if last.get("mcap_usd") is not None else None
        return row["trough_mcap"] / base_mcap, row["peak_mcap"] / base_mcap, end, "mcap"
    p0 = m0.get("price_usd")
    if p0 and row.get("trough_price") is not None:
        end = last.get("price_usd") / p0 if last.get("price_usd") is not None else None
        return row["trough_price"] / p0, row["peak_price"] / p0, end, "price"
    return None, None, None, None


def band_of(row, model):
    if model == "crawlmeme-1" or not row.get("scores"):
        return row.get("band")
    return ((row.get("scores") or {}).get(model) or {}).get("band") or row.get("band")


def classify(row, p, now=None):
    """{"rug": TP | FP | FN | TN | UNRESOLVED, "gem": …, "trough", "peak", "end", "basis", "resolved_at"}."""
    now = now or time.time()
    trough, peak, end, basis = ratios(row)
    t0 = _ts(row.get("scanned_at")) or now
    elapsed = now >= t0 + p["window_h"] * 3600
    late = (_ts(row.get("last_obs")) or 0) >= t0 + LATE * p["window_h"] * 3600
    complete = elapsed and late
    band = band_of(row, p["model"])
    out = {"trough": trough, "peak": peak, "end": end, "basis": basis, "band_model": band,
           "window_complete": complete}
    for kind, predicted, hit in (
            ("rug", band == "DANGER", lambda r: r is not None and r <= 1 - p["drop"] + 1e-12),
            ("gem", band == "CLEAN", lambda r: r is not None and r >= p["mult"] - 1e-12)):
        seen = trough if kind == "rug" else peak
        if p["measure"] == "end":
            actual = (hit(end) if complete else None)
        else:
            actual = True if hit(seen) else (False if complete else None)
        out[kind] = ("UNRESOLVED" if actual is None else
                     "TP" if predicted and actual else "FP" if predicted else "FN" if actual else "TN")
    return out


def metrics(classes):
    c = {k: sum(1 for x in classes if x == k) for k in ("TP", "FP", "FN", "TN", "UNRESOLVED")}
    resolved = c["TP"] + c["FP"] + c["FN"] + c["TN"]
    pos_pred, pos_act = c["TP"] + c["FP"], c["TP"] + c["FN"]
    return {**c, "eligible": len(classes), "resolved": resolved,
            "coverage": resolved / len(classes) if classes else None,
            "precision": c["TP"] / pos_pred if pos_pred else None, "precision_ci": wilson(c["TP"], pos_pred),
            "recall": c["TP"] / pos_act if pos_act else None, "recall_ci": wilson(c["TP"], pos_act),
            "base_rate": pos_act / resolved if resolved else None,
            "predicted_positive": pos_pred, "actual_positive": pos_act}


def link(row):
    tok = row["token"]
    chain = "" if row["chain"] == "solana" else "&chain=" + urllib.parse.quote(row["chain"])   # EVM: same address on several chains
    return {"scan": "/?ca=" + urllib.parse.quote(tok) + chain, "record": f"/api/proof/scan?id={row['scan_id']}",
            "explorer": EXPLORER.get(row["chain"], "") + tok if row["chain"] in EXPLORER else None,
            "market": f"https://www.geckoterminal.com/{GT_NET.get(row['chain'], row['chain'])}/tokens/{tok}"}


def report(rows, p, now=None):
    now = now or time.time()
    items = []
    for r in rows:
        m0 = r.get("market") or {}
        mcap0 = m0.get("mcap_usd") or m0.get("fdv_usd")
        if p["min_mcap"] and (mcap0 or 0) < p["min_mcap"]:
            continue
        c = classify(r, p, now)
        items.append({"scan_id": r["scan_id"], "chain": r["chain"], "token": r["token"], "ticker": r.get("ticker"),
                      "launchpad": r.get("launchpad"), "scanned_at": r["scanned_at"], "band": c["band_model"],
                      "score": ((r.get("scores") or {}).get(p["model"]) or {}).get("score", r.get("score"))
                      if p["model"] != "crawlmeme-1" else r.get("score"),
                      "mcap_at_scan": mcap0, "price_at_scan": m0.get("price_usd"),
                      "later_mcap": (r.get("last") or {}).get("mcap_usd"), "later_at": (r.get("last") or {}).get("observed_at"),
                      "peak_mcap": r.get("peak_mcap"), "peak_at": r.get("peak_at"),
                      "trough_mcap": r.get("trough_mcap"), "trough_at": r.get("trough_at"),
                      "observations": r.get("n") or 0, "evaluated_at": r.get("last_obs"),
                      "trough": c["trough"], "peak": c["peak"], "end": c["end"], "basis": c["basis"],
                      "window_complete": c["window_complete"], "rug": c["rug"], "gem": c["gem"],
                      "record_hash": r.get("record_hash"), "links": link(r)})
    out = {"rug": metrics([i["rug"] for i in items]), "gem": metrics([i["gem"] for i in items])}
    view = p["view"]
    shown = items
    if view == "rug":   # Rug Replay: DANGER-rated tokens that then fell (the hits), unless a class is chosen
        shown = [i for i in items if i["rug"] == (p["cls"] or "TP")]
    elif view == "gem":
        shown = [i for i in items if i["gem"] == (p["cls"] or "TP")]
    elif p["cls"]:
        shown = [i for i in items if p["cls"] in (i["rug"], i["gem"])]
    return {"params": p, "metrics": out, "items": shown[:500], "shown": len(shown), "eligible": len(items)}


def query(q, now=None):
    p = params(q)
    key = urllib.parse.urlencode(sorted((k, str(v)) for k, v in p.items()))
    t = time.time()
    with _lock:
        hit = _cache.get(key)
        if hit and t - hit[0] < CACHE_S:
            return hit[1]
    if not memory_db.enabled():
        return {"available": False, "reason": "memory is off", "params": p}
    until = now or t
    rows = memory_db.call("proof_rows", {"p_since": memory_db.iso(until - p["since_days"] * 86400),
                                         "p_until": memory_db.iso(until), "p_window_h": p["window_h"],
                                         "p_chain": p["chain"], "p_limit": 5000}, timeout=10.0)
    if rows is None:
        return {"available": False, "reason": "memory unavailable", "params": p}
    out = {"available": True, "generated_at": int(t), **report(rows, p, now)}
    with _lock:
        _cache[key] = (t, out)
        if len(_cache) > 100:
            _cache.pop(min(_cache, key=lambda k: _cache[k][0]))
    return out


# ---------------------------------------------------------------------------
# tracker
# ---------------------------------------------------------------------------
def fetch_batch(chain, tokens, budget=8.0):
    """{token: {"price_usd", "mcap_usd", "fdv_usd", "liquidity_usd", "source"}} from one DexScreener request (up to
    TRACK_BATCH tokens): price and market cap from the token's most liquid pair as base, liquidity summed over its
    pairs. A token DexScreener does not list is absent (no data is not zero)."""
    net = market.DS_CHAIN.get(chain)
    if not net or not tokens:
        return {}
    key = (lambda x: x) if chain == "solana" else (lambda x: (x or "").lower())
    want = {key(t): t for t in tokens}
    f = lambda v: float(v) if v not in (None, "") else None
    best, liq = {}, {}
    for pr in market._ds(f"{market.DS}/{net}/{','.join(tokens)}", budget) or []:
        if not isinstance(pr, dict) or pr.get("chainId") != net:
            continue
        tok = want.get(key((pr.get("baseToken") or {}).get("address")))
        if tok is None:
            continue
        lq = f((pr.get("liquidity") or {}).get("usd")) or 0.0
        liq[tok] = liq.get(tok, 0.0) + lq
        if tok not in best or lq > best[tok][0]:
            best[tok] = (lq, pr)
    out = {}
    for tok, (_, pr) in best.items():
        out[tok] = {"price_usd": f(pr.get("priceUsd")), "mcap_usd": f(pr.get("marketCap")) or f(pr.get("fdv")),
                    "fdv_usd": f(pr.get("fdv")), "liquidity_usd": liq.get(tok), "source": "dexscreener"}
    return out


def track(budget=TRACK_BUDGET_S, fetch=None):
    """One tracker pass: {"due", "sampled", "missing", "requests", "elapsed_s"}."""
    t0 = time.time()
    fetch = fetch or fetch_batch
    due = memory_db.call("track_due", {"p_window_h": TRACK_WINDOW_H, "p_gap_min": TRACK_GAP_MIN, "p_limit": 600})
    if due is None:
        raise memory_db.MemoryUnavailable("track_due")
    by_chain = {}
    for d in due:
        by_chain.setdefault(d["chain"], []).append(d["token"])
    rows, reqs, missing = [], 0, 0
    for chain, toks in by_chain.items():
        for i in range(0, len(toks), TRACK_BATCH):
            if time.time() - t0 > budget:
                break
            part = toks[i:i + TRACK_BATCH]
            try:
                got = fetch(chain, part)
            except Exception as e:
                print(f"proof: track {chain}: {type(e).__name__}", flush=True)
                got = {}
            reqs += 1
            missing += len(part) - len(got)
            rows += [{"chain": chain, "token": t, **v} for t, v in got.items()]
            time.sleep(TRACK_PAUSE_S)
    written = memory_db.call("market_snapshot_record", {"p": {"rows": rows}}) if rows else 0
    return {"due": len(due), "sampled": written or 0, "missing": missing, "requests": reqs,
            "elapsed_s": round(time.time() - t0, 1)}
