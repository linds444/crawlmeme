"""Scan coverage: what the scan could and could not read, and what that does to the verdict. Pure functions.

Why: a verdict is only as good as the data under it. V1 already treats an unread wallet as "not evidence" (virgin
part neutral when most of the top is unread), but the result did not say how complete the scan was, and a scan with
most wallets unread could still come out CLEAN. Here every scan gets an explicit coverage block, and a limited scan
cannot be CLEAN — an explicit, labelled cap applied after detect.score (V1 scoring is unchanged).

Levels:
  full    — holder balances exact (full transfer history, conservation verified where checked) and every
            examined wallet's history read
  partial — some examined wallets unread (≤ UNREAD_LIMITED of them), or holder count a lower bound (Solana top
            accounts), or balances not verifiable on this provider
  limited — more than UNREAD_LIMITED of the examined wallets unread, or the holder history is incomplete
            (windowed reading) or failed verification
Cap: limited → band at most OK (score ≤ LIMITED_MAX_SCORE), with a note naming the cause.
"""
UNREAD_LIMITED = 0.5        # share of examined wallets unread above which the scan is limited
LIMITED_MAX_SCORE = 79      # a limited scan is never CLEAN (80+)


def holders_method(facts):
    """How holder balances were obtained."""
    if facts.get("base") is not None:
        return "top_accounts"            # Solana: getTokenLargestAccounts (top 20), holder count is a lower bound
    hist = (facts.get("flap") or facts.get("bankr") or {}).get("history") or {}
    if hist.get("mode") == "windowed" and not hist.get("top_exact"):
        return "windowed"                # large history read in windows: top holders approximate
    return "full_history"


def assess(facts, holders, entries, data, unread_budget, history_info=None, market_source=None, sources=None):
    """Coverage block for the result.
    holders — [(wallet, balance, share)]; entries — adapter.classify_entries; data — engine per-wallet data
    (distinct_tokens None = history not read); unread_budget — wallets the time budget cut off."""
    examined = [w for w, _, _ in holders]
    cut = [w for w in examined if w in set(unread_budget)]
    no_entry = [w for w in examined if (entries.get(w) or {}).get("kind") is None]
    unavailable = [w for w in examined if w not in cut and w not in no_entry
                   and data.get(w, {}).get("distinct_tokens") is None]
    read = [w for w in examined if data.get(w, {}).get("distinct_tokens") is not None]
    method = holders_method(facts)
    hi = history_info or {}
    verified = hi.get("verified")       # None: not checked (adapters without a history index)
    reasons = []
    n = len(examined)
    unread_share = (n - len(read)) / n if n else 0.0
    if method == "windowed":
        reasons.append("holder history read in windows: top holders approximate")
    if verified is False:
        reasons.append("rebuilt balances do not match the chain (conservation / balanceOf check failed)")
    if unread_share > UNREAD_LIMITED:
        reasons.append(f"{n - len(read)} of {n} examined wallets unread")
    level = "limited" if reasons else "full"
    notes = []
    if level == "full":
        if unread_share > 0:
            level, notes = "partial", [f"{n - len(read)} of {n} examined wallets unread"]
        if method == "top_accounts":
            level = "partial"
            notes.append("holder count is a lower bound (largest accounts only)")
        if verified is None and method == "full_history" and hi:
            level = "partial"
            notes.append("balances not verified against the chain")
    return {
        "level": level, "reasons": reasons + notes,
        "holders": {"method": method, "examined": n, "holders_total_lower_bound": method == "top_accounts",
                    "history": {k: hi.get(k) for k in ("source", "cached_until", "read_from", "head", "verified")}
                    if hi else None},
        "wallets": {"read": len(read), "unread_budget": cut, "unread_unavailable": unavailable,
                    "entry_not_found": no_entry},
        "market_source": market_source,
        "sources": sources or [],
    }


def apply(sc, cov):
    """Cap a score dict (detect.score shape) by coverage: limited → never CLEAN. Returns a new dict."""
    if cov["level"] != "limited" or sc.get("score") is None or sc.get("band") != "CLEAN":
        return sc
    out = dict(sc)
    out["score"] = min(sc["score"], LIMITED_MAX_SCORE)
    out["band"] = "OK"
    out["notes"] = list(sc.get("notes") or []) + ["coverage limited: " + "; ".join(cov["reasons"]) +
                                                  " — not rated CLEAN"]
    out["coverage_capped"] = True
    return out
