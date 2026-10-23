"""Alert rules: pure functions, no network or database (Phase 15 builds on the V1 rules).

snapshot(result) keeps the few numbers alerts compare; diff(old, new) lists the important changes between two scans
of a token, each {"kind", "text", ...}. Storage, queueing and delivery live in tg_store / tg_alerts; the token-level
rules here know nothing about chats.

Kinds (thresholds below; every one is a change between two real scans, never a guess):
  verdict_worse    the band fell among DANGER / RISKY / OK / CLEAN. Entering DANGER always; other steps only when the
                   score also moved ≥ VERDICT_MIN_MOVE (80 → 78 across a boundary is noise)
  rug_appeared     the conditional «probably rug» scenario appeared
  rug_worse        it was there and its projected drop grew by ≥ RUG_WORSE_MIN
  rug_gone         it disappeared
  operator_sold    the previous largest operator's wallets hold ≥ OPERATOR_SOLD_MIN less
  early_dropped    the first buyers' supply share fell ≥ EARLY_DROP_MIN (only when measured in both snapshots)
  coordinated      abnormal coordinated wallet behaviour: a new same-block pack holding ≥ PACK_MIN of the float, or
                   the launch bundle selling ≥ BUNDLE_SOLD_MIN more of the supply between two scans
"""
import time

BAND_RANK = {"DANGER": 0, "RISKY": 1, "OK": 2, "CLEAN": 3}   # verdicts with a score; others are not compared
VERDICT_MIN_MOVE = 8         # band change not involving DANGER (CLEAN↔OK, OK↔RISKY) only if the score moved by ≥ 8
OPERATOR_SOLD_MIN = 0.30     # largest operator sold ≥ 30% of their share
OPERATOR_MIN_SHARE = 0.001   # ... if they held ≥ 0.1% of supply (less is noise)
EARLY_DROP_MIN = 0.30        # early buyers' supply share dropped ≥ 30%
EARLY_MIN_SHARE = 0.005      # ... if it was ≥ 0.5% of supply
RUG_WORSE_MIN = 0.15         # projected rug drop grew by ≥ 15 points
PACK_MIN = 0.05              # a same-block pack holding ≥ 5% of the float ...
PACK_RISE = 0.05             # ... that is ≥ 5 points larger than the largest pack of the previous scan
BUNDLE_SOLD_MIN = 0.10       # launch bundle sold ≥ 10% more of the supply between two scans
ROUND = 6                    # decimal places for shares in a snapshot
CHAIN_NAME = {"robinhood": "Robinhood", "solana": "Solana", "base": "Base", "bsc": "BNB Chain"}
BAND_ICON = {"CLEAN": "🟢", "OK": "🟡", "RISKY": "🟠", "DANGER": "🔴"}


def _r(x):
    return round(float(x), ROUND) if isinstance(x, (int, float)) else None


def _largest_pack(result):
    """(share of float, wallets) of the largest same-block pack among the analysed holders; (0.0, 0) when none."""
    share = {h["wallet"]: h.get("share") or 0.0 for h in result.get("holders") or [] if h.get("wallet")}
    best = (0.0, 0)
    for p in result.get("packs") or []:
        ws = p.get("wallets") or []
        s = sum(share.get(w, 0.0) for w in ws)
        if s > best[0]:
            best = (s, len(ws))
    return best


def snapshot(result, early_share=None, ts=None):
    """engine.scan result -> short snapshot, or None (no token or verdict).
    early_share: supply share of the first buyers now, if already measured, else None.
    top: {wallet: supply share} of the scan's top holders: diff uses it to see how much the previous
    largest operator still holds."""
    if not isinstance(result, dict) or not result.get("token") or not result.get("band"):
        return None
    score, rug, ops = result.get("score"), result.get("rug"), result.get("operators") or []
    big = ops[0] if ops else None
    h = result.get("header") or {}
    pack, pack_n = _largest_pack(result)
    sup = (result.get("bundle") or {}).get("supply") or {}
    return {
        "token": result["token"], "chain": result.get("chain") or "robinhood",
        "ticker": h.get("ticker") or h.get("name") or None,
        "ts": int(ts if ts is not None else time.time()),
        "band": result["band"], "score": int(score) if isinstance(score, (int, float)) else None,
        "rug": bool(rug), "rug_drop": _r(rug.get("drop")) if rug else None,
        "operator": {"wallets": list(big["wallets"]), "share_supply": _r(big.get("share_supply"))} if big else None,
        "top": {h["wallet"]: _r(h.get("share_supply")) for h in result.get("holders") or [] if h.get("wallet")},
        "early_share": _r(early_share),
        "pack": _r(pack), "pack_wallets": pack_n,
        "bundle_sold": _r(sup.get("sold_share")), "bundle_held": _r(sup.get("concentrated_share")),
        "mcap_usd": _r(h.get("mcap_usd")), "headline": result.get("headline"),
    }


def _pct(x):
    return f"{x * 100:.1f}%"


def diff(old, new):
    """Important changes between snapshots old -> new: [{"kind", "text", ...}]. Either snapshot missing: []."""
    if not old or not new:
        return []
    out = []
    ob, nb = old.get("band"), new.get("band")
    osc, nsc = old.get("score"), new.get("score")
    moved = osc is None or nsc is None or abs(nsc - osc) >= VERDICT_MIN_MOVE
    if ob in BAND_RANK and nb in BAND_RANK and BAND_RANK[nb] < BAND_RANK[ob] and (nb == "DANGER" or moved):
        sc = f" (score {osc} → {nsc})" if osc is not None and nsc is not None else ""
        out.append({"kind": "verdict_worse", "from": ob, "to": nb, "text": f"Verdict worsened: {ob} → {nb}{sc}"})
    od, nd = old.get("rug_drop"), new.get("rug_drop")
    if not old.get("rug") and new.get("rug"):
        out.append({"kind": "rug_appeared", "drop": nd,
                    "text": "Probably rug" + (f": −{nd * 100:.0f}% if suspicious holders sell" if nd else "")})
    elif old.get("rug") and new.get("rug") and od is not None and nd is not None and nd - od >= RUG_WORSE_MIN:
        out.append({"kind": "rug_worse", "from": od, "to": nd,
                    "text": f"Probably rug got worse: −{od * 100:.0f}% → −{nd * 100:.0f}% if suspicious holders sell"})
    elif old.get("rug") and not new.get("rug"):
        out.append({"kind": "rug_gone", "text": "Probably rug is gone" + (f" (was −{od * 100:.0f}%)" if od else "")})
    op = old.get("operator")
    if op and new.get("top") and (op.get("share_supply") or 0) >= OPERATOR_MIN_SHARE:
        before = op["share_supply"]
        # wallet dropped out of the new scan's top: assume it no longer holds a share
        now = sum(new["top"].get(w) or 0 for w in op["wallets"])
        sold = round(1 - now / before, 4)   # rounding: exactly 30% is not lost as 0.2999…
        if sold >= OPERATOR_SOLD_MIN:
            out.append({"kind": "operator_sold", "sold": sold, "from": before, "to": round(now, ROUND),
                        "text": f"Biggest operator sold {sold * 100:.0f}% of their holdings "
                                f"({_pct(before)} → {_pct(now)} of supply)"})
    oe, ne = old.get("early_share"), new.get("early_share")
    if oe is not None and ne is not None and oe >= EARLY_MIN_SHARE:
        fell = round(1 - ne / oe, 4)
        if fell >= EARLY_DROP_MIN:
            out.append({"kind": "early_dropped", "fell": fell, "from": oe, "to": ne,
                        "text": f"Early buyers are exiting: their share fell {fell * 100:.0f}% "
                                f"({_pct(oe)} → {_pct(ne)} of supply)"})
    op_, np_ = old.get("pack") or 0.0, new.get("pack") or 0.0
    if np_ >= PACK_MIN and np_ - op_ >= PACK_RISE:
        out.append({"kind": "coordinated", "signal": "pack", "share": np_,
                    "text": f"Coordinated wallets: a same-block pack of {new.get('pack_wallets') or 0} wallets "
                            f"now holds {_pct(np_)} of float"})
    ob_, nb_ = old.get("bundle_sold"), new.get("bundle_sold")
    if ob_ is not None and nb_ is not None and nb_ - ob_ >= BUNDLE_SOLD_MIN:
        out.append({"kind": "coordinated", "signal": "bundle", "sold": round(nb_ - ob_, 4),
                    "text": f"Coordinated selling: the launch bundle sold another {_pct(nb_ - ob_)} of supply"})
    return out
