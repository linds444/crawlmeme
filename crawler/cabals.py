"""Phase 7: repeat operators and cabals — the scan's top holders, funders and deployer seen again across tokens.
Pure function over memory_db.lookup (no network). Reads only OTHER tokens' scans (the SQL excludes this token), so a
token's own history never counts as evidence about it.

  crews            wallets that were top holders TOGETHER in ≥ CREW_MIN_TOKENS other tokens (union of such pairs).
                   Two wallets sharing one other token is common for active traders; sharing several is a crew
  repeat_funders   funders of this scan's holders that also funded top holders of other tokens; hubs (exchange-like,
                   from this scan's hub check or memory's) are listed apart and never called a cabal
  seen_wallets     each top holder's appearances among other tokens' top holders, with those tokens' verdicts
  deployer         the deployer's other tokens in memory and their verdicts (serial launcher)
  flags            short labels for the page: crew, serial_deployer, repeat_funder, danger_regulars
Nothing here changes the score: memory is young, and the profitability-backed «smart crew» model needs enough
history to evaluate without look-ahead (Phase 1 audit). The page shows it as evidence beside the verdict.
"""
CREW_MIN_TOKENS = 2         # pairs of holders top-holding ≥ this many other tokens together form a crew
SERIAL_DEPLOYER = 2         # deployer with ≥ this many other tokens in memory
DANGER_REGULAR = 2          # a holder that sat in the top of ≥ this many DANGER tokens
VERSION = 1


def _crews(pairs, share, ratio):
    strong = [p for p in pairs if p.get("together", 0) >= CREW_MIN_TOKENS]
    parent = {}

    def find(x):
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x
    for p in strong:
        parent[find(p["a"])] = find(p["b"])
    groups, tokens = {}, {}
    for p in strong:
        r = find(p["a"])
        groups.setdefault(r, set()).update((p["a"], p["b"]))
        tokens.setdefault(r, set()).update(p.get("tokens") or [])
    out = []
    for r, ws in groups.items():
        s = sum(share.get(w, 0.0) for w in ws)
        out.append({"wallets": sorted(ws, key=lambda w: -share.get(w, 0.0)), "tokens": sorted(tokens[r]),
                    "together": max(p["together"] for p in strong if find(p["a"]) == r),
                    "share": s, "share_supply": s * ratio})
    out.sort(key=lambda c: -c["share"])
    return out


def analyse(mem, holders, ratio, funders, funder_out=None, hub_min=100):
    """result["memory"] from a memory_lookup answer; None when memory was not read.
    holders — [(wallet, balance, share)] analysed, largest first; funders — {wallet: forensics funding | None};
    funder_out — this scan's hub check {funder: outgoing}."""
    if mem is None:
        return None
    share = {w: s for w, _, s in holders}
    seen = mem.get("seen") or {}
    stats = mem.get("funder_stats") or {}
    funder_out = funder_out or {}

    def is_hub(f):
        n = funder_out.get(f)
        if n is None:
            n = (stats.get(f) or {}).get("outgoing")
        return None if n is None else n >= hub_min

    seen_wallets = [{"wallet": w, "rank": i + 1, "tokens": seen[w]["tokens"], "danger": seen[w]["danger"],
                     "list": seen[w].get("list") or []}
                    for i, (w, _, _) in enumerate(holders) if w in seen]
    funded = {}
    for w, f in funders.items():
        if f and f.get("found") and f.get("funder"):
            funded.setdefault(f["funder"], []).append(w)
    repeat_funders = []
    for f, info in (mem.get("funders") or {}).items():
        repeat_funders.append({"funder": f, "holders_now": sorted(funded.get(f, []), key=lambda w: -share.get(w, 0)),
                               "other_tokens": info.get("tokens", 0), "other_wallets": info.get("wallets", 0),
                               "token_list": info.get("token_list") or [], "hub": is_hub(f)})
    repeat_funders.sort(key=lambda r: (r["hub"] is True, -r["other_tokens"]))
    crews = _crews(mem.get("pairs") or [], share, ratio)
    dep = mem.get("deployer") or {"tokens": 0, "list": []}
    dep_danger = sum(1 for t in dep.get("list") or [] if t.get("band") == "DANGER")
    flags = []
    if crews:
        flags.append("crew")
    if dep.get("tokens", 0) >= SERIAL_DEPLOYER:
        flags.append("serial_deployer")
    if any(r["hub"] is not True and r["other_tokens"] > 0 for r in repeat_funders):
        flags.append("repeat_funder")
    if sum(1 for s in seen_wallets if s["danger"] >= DANGER_REGULAR) >= 2:
        flags.append("danger_regulars")
    return {"version": VERSION, "memory": mem.get("memory") or {}, "elapsed_ms": mem.get("elapsed_ms"),
            "seen_wallets": seen_wallets, "crews": crews, "repeat_funders": repeat_funders,
            "deployer": {"tokens": dep.get("tokens", 0), "danger": dep_danger, "list": dep.get("list") or []},
            "flags": flags}
