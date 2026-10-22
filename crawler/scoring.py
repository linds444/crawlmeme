"""Scoring models (Phase 8): versioned verdicts, a shadow model beside the live one, and a data-health block.

Models
  crawlmeme-1   the live model: V1's score (detect.score: five parts 35/25/20/10/10, bands, hard and soft gates)
                with Phase 3's coverage cap (a limited scan is never CLEAN). Unchanged.
  crawlmeme-2   crawlmeme-1 plus capped, explained adjustments from evidence V1 never had (Phases 5–7):
                  crew              top holders that top-held 2+ other tokens together          −8 each, −15 max
                  serial deployer   deployer with 2+ other tokens in memory                      −4
                                    … of which 2+ were rated DANGER                             −10, and band ≤ RISKY
                  repeat funder     a non-hub funder of holders here and in other tokens         −5
                  danger regulars   2+ top holders that each sat in 2+ DANGER tokens             −5
                  crew holding ≥ 10% of float now                                                 band ≤ RISKY
                Funding links (Phase 6) already merge operators inside crawlmeme-1, so they are not counted again.
Every scan computes every model. The live one (SCORE_MODEL, default crawlmeme-1) gives the verdict; the others are
shadow verdicts, shown and stored in memory, so a model's accuracy can be compared on real outcomes (Rug / Gem
Replay, Phase 13) before it is promoted. Why crawlmeme-2 is not live yet: memory is new, and adjustments that depend
on it need history to be checked against what actually happened (Phase 1 audit, look-ahead).
"""
import os

from crawler import detect as d

BASE = "crawlmeme-1"
MODELS = ("crawlmeme-1", "crawlmeme-2")
CREW_POINTS, CREW_CAP = 8, 15
SERIAL_POINTS, SERIAL_DANGER_POINTS, SERIAL_DANGER_MIN = 4, 10, 2
REPEAT_FUNDER_POINTS = 5
DANGER_REGULARS_POINTS = 5
CREW_SHARE_CAP = 0.10           # a crew holding ≥ this share of float now: band no better than RISKY
SOFT_CAP_SCORE = 59


def current():
    m = (os.environ.get("SCORE_MODEL") or BASE).strip()
    return m if m in MODELS else BASE


def band_of(score):
    return next((b for lim, b in d.BANDS if score >= lim), "DANGER")


def _v2(v1, memory):
    """crawlmeme-2 from the crawlmeme-1 verdict and result.memory: {"score", "band", "gates", "adjustments"}."""
    out = {"score": v1.get("score"), "band": v1.get("band"), "gates": list(v1.get("gates") or []), "adjustments": []}
    if v1.get("score") is None or not memory:      # no verdict (too early / established / active), or no memory
        return out
    adj, caps = [], []
    crews = memory.get("crews") or []
    if crews:
        pts = min(CREW_CAP, CREW_POINTS * len(crews))
        adj.append({"code": "crew", "points": -pts,
                    "why": f"{len(crews)} crew{'s' if len(crews) > 1 else ''} of top holders seen together in other "
                           f"tokens"})
        big = max(c.get("share", 0.0) for c in crews)
        if big >= CREW_SHARE_CAP:
            caps.append(f"soft: a crew from other tokens holds {big * 100:.1f}% of float")
    dep = memory.get("deployer") or {}
    if dep.get("tokens", 0) >= 2:
        if dep.get("danger", 0) >= SERIAL_DANGER_MIN:
            adj.append({"code": "serial_deployer_danger", "points": -SERIAL_DANGER_POINTS,
                        "why": f"deployer's other tokens: {dep['danger']} of {dep['tokens']} rated DANGER"})
            caps.append(f"soft: deployer has {dep['danger']} DANGER tokens in memory")
        else:
            adj.append({"code": "serial_deployer", "points": -SERIAL_POINTS,
                        "why": f"deployer launched {dep['tokens']} other tokens"})
    if any(r.get("hub") is not True and r.get("other_tokens", 0) > 0 for r in memory.get("repeat_funders") or []):
        adj.append({"code": "repeat_funder", "points": -REPEAT_FUNDER_POINTS,
                    "why": "a non-hub funder of holders here also funded top holders of other tokens"})
    if "danger_regulars" in (memory.get("flags") or []):
        adj.append({"code": "danger_regulars", "points": -DANGER_REGULARS_POINTS,
                    "why": "2+ top holders were in the top of 2+ DANGER tokens each"})
    if not adj and not caps:
        return out
    score = max(0, round(v1["score"] + sum(a["points"] for a in adj)))
    band = band_of(score)
    if caps and score > SOFT_CAP_SCORE:
        score, band = SOFT_CAP_SCORE, band_of(SOFT_CAP_SCORE)
    if v1["band"] == "DANGER":                     # never raises a verdict
        band = "DANGER"
        score = min(score, v1["score"])
    out.update(score=score, band=band, adjustments=adj, gates=out["gates"] + caps)
    return out


def evaluate(v1, memory):
    """{model id: {"score", "band", "gates", "adjustments"}} for every model, from the crawlmeme-1 verdict."""
    base = {"score": v1.get("score"), "band": v1.get("band"), "gates": list(v1.get("gates") or []),
            "adjustments": []}
    return {"crawlmeme-1": base, "crawlmeme-2": _v2(v1, memory)}


def rescore(result, model=None):
    """Every model's verdict for a stored scan result (from its crawlmeme-1 verdict and memory); one when model is
    given. Recomputed, not read back, so a model change applies to old results."""
    scores = (result.get("model") or {}).get("scores") or {}
    v1 = scores.get(BASE) or {"score": result.get("score"), "band": result.get("band"),
                              "gates": result.get("gates") or []}
    out = evaluate(v1, result.get("memory"))
    if model is not None:
        if model not in MODELS:
            raise KeyError(model)
        return {model: out[model]}
    return out


def data_health(cov, forensics, memory, memory_on, market_source, elapsed_s, rpc_requests):
    """What the verdict stands on, in one block, with a confidence label:
    high — full coverage, ≥ 80% of examined wallets read, balances not contradicted by the chain;
    low — limited coverage or fewer than half the wallets read; medium otherwise."""
    cov = cov or {}
    w = cov.get("wallets") or {}
    examined = (cov.get("holders") or {}).get("examined") or 0
    read = w.get("read") or 0
    share = read / examined if examined else None
    hist = (cov.get("holders") or {}).get("history") or {}
    level = cov.get("level")
    if level == "limited" or (share is not None and share < 0.5):
        conf = "low"
    elif level == "full" and (share is None or share >= 0.8) and hist.get("verified") is not False:
        conf = "high"
    else:
        conf = "medium"
    return {
        "confidence": conf, "coverage": level, "reasons": cov.get("reasons") or [],
        "wallets_read": read, "wallets_examined": examined,
        "holders_method": (cov.get("holders") or {}).get("method"),
        "history_verified": hist.get("verified"), "history_source": hist.get("source"),
        "market_source": market_source,
        "priced": (forensics or {}).get("priced"), "price_points": (forensics or {}).get("price_points"),
        "funding_read": (forensics or {}).get("funding_read"),
        "memory": None if not memory_on else {"available": memory is not None,
                                              "tokens": ((memory or {}).get("memory") or {}).get("tokens"),
                                              "scans": ((memory or {}).get("memory") or {}).get("scans")},
        "elapsed_s": elapsed_s, "rpc_requests": rpc_requests,
    }
