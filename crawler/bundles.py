"""Phase 10: launch bundle analysis from the launch transactions themselves, and the bundle-originated supply traced
through every later transfer. Pure functions (no network): the EVM scan already holds the token's transfer history;
Solana passes the launch transactions' balance changes (chains/solana.launch_participants).

Launch participants (EVM, from transfers in blocks launch … launch + window, or the first BUNDLE_SECONDS if longer):
  per launch-window transaction, every non-market, non-infrastructure address whose balance grew in it:
    buy            the transaction took tokens out of the market (curve, pool, router)
    dev_transfer   the deployer sent tokens in it (supply handed out at launch)
    transfer       received from someone else inside the window
  deployer         what the deployer still holds when the window closes (its kept allocation)
  sniper           a buy after the window but within detect.SNIPER_SECONDS of launch (chains that count snipers)
Pass-through contracts (a router that forwards everything in the same transaction) net to zero and are not
participants.

Tracing (pro-rata): the seeds are what each participant acquired. Every later transfer moves taint in proportion to
the sender's tainted share of its balance, so the totals are conserved exactly:
    initial = held now (any wallet) + sold (reached the market) + burned + infrastructure (lockers, vesting …)
Market and infrastructure are sinks: tokens bought out of a pool afterwards are new money, not bundle supply.
A wallet's purity = its tainted tokens / its balance; supply in wallets with purity ≥ CONCENTRATED is «concentrated»
(still in the bundle's hands); the rest is diluted into wallets that also hold other supply (a lower-confidence claim).

Confidence: high — full history, balances consistent, little dilution; medium — full history but diluted or a few
balance gaps; low — partial history (windowed / still indexing) or Solana (onward transfers not traced).
"""
from collections import defaultdict
from statistics import median

from crawler import detect as d

BURNS = frozenset({"0x" + "0" * 40, "0x000000000000000000000000000000000000dead"})
BUNDLE_SECONDS = 3.0      # the launch window is the adapter's block window or the first 3 s, whichever is longer:
                          # one launch block on Base is 2 s, on BNB 0.45 s and on Robinhood a fraction of that
CONCENTRATED = 0.5        # purity at or above which a wallet's bundle tokens count as still bundle-controlled
DUST = 1e-6               # share of supply below which a holding is not listed
SIMILAR_SPREAD = 0.30     # «uniform buys»: 3+ launch-block buys within ±30% of their median
HOLDERS_LIST = 25
DILUTED_MAX = 0.25        # high confidence needs at most this share of traced supply diluted


def _net_by_tx(rows, skip):
    net = defaultdict(int)
    for t in rows:
        if t["frm"] not in skip:
            net[t["frm"]] -= t["amount"]
        if t["to"] not in skip:
            net[t["to"]] += t["amount"]
    return net


def participants(transfers, launch_block, window, deployer, market, sinks, clock=None, launch_ts=None,
                 snipers=False):
    """({wallet: {"kind", "block", "tx", "acquired"}}, the window's last block)."""
    skip = set(market) | set(sinks)
    end = launch_block + window
    out, by_tx, order = {}, defaultdict(list), []
    last = 0
    for i, t in enumerate(transfers):
        if t["block"] < launch_block:
            continue
        if t["block"] > end:
            ts = clock(t["block"]) if clock and launch_ts is not None else None
            if ts is None or ts - launch_ts > BUNDLE_SECONDS:
                break
            end = t["block"]   # still inside the first BUNDLE_SECONDS
        last = i + 1
        if t["tx"] not in by_tx:
            order.append(t["tx"])
        by_tx[t["tx"]].append(t)
    for h in order:
        rows = by_tx[h]
        from_market = any(t["frm"] in market for t in rows)
        from_dev = deployer is not None and any(t["frm"] == deployer for t in rows)
        for a, v in _net_by_tx(rows, skip).items():
            if v <= 0 or a == deployer:
                continue
            kind = "buy" if from_market else "dev_transfer" if from_dev else "transfer"
            p = out.setdefault(a, {"kind": kind, "block": rows[0]["block"], "tx": h, "acquired": 0})
            p["acquired"] += v
    if snipers and clock and launch_ts is not None:
        sniped, by_tx2 = [], defaultdict(list)
        for t in transfers[last:]:
            ts = clock(t["block"])
            if ts is None or ts - launch_ts > d.SNIPER_SECONDS:
                break
            by_tx2[t["tx"]].append(t)
        for h, rows in by_tx2.items():
            if not any(t["frm"] in market for t in rows):
                continue
            for a, v in _net_by_tx(rows, skip).items():
                if v > 0 and a != deployer and a not in out:
                    sniped.append(a)
                    out[a] = {"kind": "sniper", "block": rows[0]["block"], "tx": h, "acquired": v}
    return out, end


def trace(transfers, seeds, end, deployer, market, sinks, infra):
    """Pro-rata replay. seeds — participants(...)[0]. Returns per-address state, the bundle supply created for each
    participant (its acquisition minus what already arrived tainted from another participant — so nothing is counted
    twice) and the sink totals. The deployer's kept allocation is seeded when the window closes (its balance then)."""
    bal, taint, created = defaultdict(int), defaultdict(float), defaultdict(float)
    hop, seen = {}, set()
    sold = burned = to_infra = 0.0
    gaps = gap_amount = 0
    budget = {(p["tx"], a): p["acquired"] for a, p in seeds.items()}
    direct = defaultdict(lambda: {"sold": 0.0, "moved": 0.0, "burned": 0.0, "infra": 0.0})
    recipients, fresh = set(), set()
    dev_done = deployer is None

    def seed_dev():
        add = max(0.0, bal[deployer] - taint[deployer])
        taint[deployer] += add
        created[deployer] += add
        hop[deployer] = 0

    for t in transfers:
        if not dev_done and t["block"] > end:
            seed_dev()
            dev_done = True
        frm, to, amt = t["frm"], t["to"], t["amount"]
        moved = 0.0
        if frm not in market and frm not in sinks:
            b = bal[frm]
            if b < amt:
                gaps += 1
                gap_amount += amt - max(0, b)
            if taint[frm] > 0:
                moved = taint[frm] * min(1.0, amt / b) if b > 0 else taint[frm]
                taint[frm] -= moved
            bal[frm] = b - amt
        if to in market or to in sinks:
            kind = "sold" if to in market else "infra" if to in infra else "burned"
            if kind == "sold":
                sold += moved
            elif kind == "infra":
                to_infra += moved
            else:
                burned += moved
            if frm in seeds or frm == deployer:
                direct[frm][kind] += moved
            continue
        new_wallet = to not in seen
        seen.add(to)
        bal[to] += amt
        key = (t["tx"], to)
        flowed = moved   # bundle supply that arrived from a tainted sender (not created here)
        if key in budget:   # the participant's acquisition: whatever did not already arrive tainted is new
            take = min(budget[key], amt)
            budget[key] -= take
            if budget[key] <= 0:
                del budget[key]
            add = take - moved
            if add > 0:
                created[to] += add
                moved += add
                hop[to] = 0
        if moved > 0:
            taint[to] += moved
            if to not in hop or hop[to] > 0:
                hop[to] = min(hop.get(to, 1 << 30), hop.get(frm, 0) + 1)
            if flowed > 0 and (frm in seeds or frm == deployer) and to != frm:
                direct[frm]["moved"] += flowed
                recipients.add(to)
                if new_wallet and to not in seeds:
                    fresh.add(to)
    if not dev_done:   # the history ends inside the window
        seed_dev()
    return {"bal": bal, "taint": taint, "created": created, "hop": hop, "sold": sold, "burned": burned,
            "infra": to_infra, "gaps": gaps, "gap_amount": gap_amount, "direct": direct, "recipients": recipients,
            "fresh": fresh}


def coordination(parts, transfers, launch_block, funders=None, funder_out=None, hub_min=d.HUB_MIN_OUT):
    """Signals that the participants act together."""
    by_tx = defaultdict(list)
    for a, p in parts.items():
        by_tx[p["tx"]].append(a)
    same_tx = [sorted(ws) for ws in by_tx.values() if len(ws) > 1]
    block_buys = [p["acquired"] for p in parts.values() if p["kind"] == "buy" and p["block"] == launch_block]
    uniform = 0
    if len(block_buys) >= 3:
        m = median(block_buys)
        uniform = sum(1 for v in block_buys if m and abs(v - m) <= SIMILAR_SPREAD * m)
        uniform = uniform if uniform >= 3 else 0
    internal = sum(1 for t in transfers if t["frm"] in parts and t["to"] in parts and t["frm"] != t["to"])
    shared = defaultdict(list)
    for w in parts:
        f = (funders or {}).get(w) or {}
        if f.get("found") and f.get("funder"):
            n = (funder_out or {}).get(f["funder"])
            if n is not None and n < hub_min:
                shared[f["funder"]].append(w)
    return {"same_tx": same_tx, "same_tx_wallets": sum(len(g) for g in same_tx),
            "launch_block_buys": len(block_buys), "uniform_buys": uniform, "internal_transfers": internal,
            "shared_funders": [{"funder": f, "wallets": sorted(ws)} for f, ws in shared.items() if len(ws) > 1]}


def analyse(transfers, launch, window, market, sinks, infra, supply, circulating, holders, history_complete,
            clock=None, snipers=False, funders=None, funder_out=None):
    """result["bundle"] for an EVM token (full transfer history in the scan). holders — analysed top
    [(wallet, balance, share)]; history_complete — the scan read the whole history (coverage)."""
    deployer = launch.get("deployer")
    parts, end = participants(transfers, launch["block"], window, deployer, market, sinks, clock,
                              launch.get("ts"), snipers)
    tr = trace(transfers, parts, end, deployer, market, sinks, infra)
    if deployer is not None and tr["created"].get(deployer, 0) > 0:
        parts[deployer] = {"kind": "deployer", "block": launch["block"], "tx": launch.get("tx"), "acquired": 0}
    for a, p in parts.items():
        p["acquired"] = tr["created"].get(a, 0.0)   # new bundle supply only: a hand-off between participants once
    parts = {a: p for a, p in parts.items() if p["acquired"] > 0}
    if not parts:   # nobody took supply in the launch window: that is a finding, not missing data
        return {"version": 1, "mode": "none", "confidence": "high" if history_complete else "low",
                "limits": [] if history_complete else ["the scan read part of the token's history"],
                "window": {"from_block": launch["block"], "to_block": end, "blocks": end - launch["block"],
                           "seconds": BUNDLE_SECONDS},
                "participants": [], "counts": {}, "coordination": {}, "dispersal": {"holders_now": 0},
                "supply": {"initial_share": 0.0, "traceable_share": 0.0, "concentrated_share": 0.0,
                           "outside_top_share": 0.0, "sold_share": 0.0, "burned_share": 0.0, "infra_share": 0.0},
                "holders_now": [], "conservation": None}
    s = supply or 1
    initial = float(sum(p["acquired"] for p in parts.values()))
    held = {a: v for a, v in tr["taint"].items() if v > 0 and a not in market and a not in sinks}
    total_held = sum(held.values())
    purity = {a: v / tr["bal"][a] if tr["bal"][a] > 0 else 1.0 for a, v in held.items()}
    conc = sum(v for a, v in held.items() if purity[a] >= CONCENTRATED)
    top = {w for w, _, _ in holders}
    out_top = sum(v for a, v in held.items() if a not in top)
    accounted = total_held + tr["sold"] + tr["burned"] + tr["infra"]
    rows = []
    for a, p in sorted(parts.items(), key=lambda kv: -kv[1]["acquired"]):
        dr = tr["direct"][a]
        own = held.get(a, 0.0)
        rows.append({"wallet": a, "kind": p["kind"], "block": p["block"], "tx": p["tx"],
                     "acquired_share": p["acquired"] / s, "held_share": own / s, "sold_share": dr["sold"] / s,
                     "moved_share": dr["moved"] / s, "burned_share": (dr["burned"] + dr["infra"]) / s,
                     "in_top": a in top, "status": _status(p["acquired"], own, dr)})
    now = sorted(held.items(), key=lambda kv: -kv[1])
    holders_now = [{"wallet": a, "share": v / s, "purity": purity[a], "in_top": a in top, "original": a in parts,
                    "hop": tr["hop"].get(a)} for a, v in now[:HOLDERS_LIST] if v / s >= DUST]
    limits = []
    if not history_complete:
        limits.append("the scan read part of the token's history: transfers outside it are not traced")
    if tr["gaps"]:
        limits.append(f"{tr['gaps']} transfers spent more than the replayed balance (history gaps)")
    diluted = (total_held - conc) / total_held if total_held else 0.0
    if diluted > DILUTED_MAX:
        limits.append(f"{diluted * 100:.0f}% of the traced supply sits in wallets that also hold other tokens "
                      "of this coin (diluted): pro-rata attribution")
    conf = ("low" if not history_complete else
            "high" if not tr["gaps"] and diluted <= DILUTED_MAX else "medium")
    return {
        "version": 1, "mode": "traced", "confidence": conf, "limits": limits,
        "window": {"from_block": launch["block"], "to_block": end, "blocks": end - launch["block"],
                   "seconds": BUNDLE_SECONDS},
        "participants": rows,
        "counts": {k: sum(1 for p in parts.values() if p["kind"] == k)
                   for k in ("buy", "dev_transfer", "transfer", "deployer", "sniper")},
        "coordination": coordination(parts, transfers, launch["block"], funders, funder_out),
        "dispersal": {"recipients": len(tr["recipients"]), "fresh_recipients": len(tr["fresh"]),
                      "holders_now": len(held), "max_hops": max((tr["hop"].get(a) or 0 for a in held), default=0)},
        "supply": {"initial_share": initial / s, "traceable_share": total_held / s, "concentrated_share": conc / s,
                   "outside_top_share": out_top / s, "sold_share": tr["sold"] / s, "burned_share": tr["burned"] / s,
                   "infra_share": tr["infra"] / s,
                   "traceable_of_circulating": total_held / circulating if circulating else None},
        "holders_now": holders_now,
        "conservation": {"initial": initial, "accounted": accounted,
                         "residual_share": (initial - accounted) / initial if initial else 0.0,
                         "balance_gaps": tr["gaps"]},
    }


def _status(acquired, own, dr):
    left = acquired - own
    if acquired <= 0:
        return "holding"
    if own >= acquired * 0.99:
        return "holding"
    if own <= acquired * 0.01:
        big = max(("sold", dr["sold"]), ("moved", dr["moved"]), ("burned", dr["burned"] + dr["infra"]),
                  key=lambda kv: kv[1])
        return {"sold": "sold_all", "moved": "moved", "burned": "burned"}[big[0]] if big[1] > 0 else "gone"
    return "sold_part" if dr["sold"] >= left / 2 else "moved_part"


def analyse_solana(parts, balances, supply, circulating, holders, entries):
    """result["bundle"] for Solana: participants from the launch transactions (solana.launch_participants), what the
    original wallets hold now from the complete holder list (exact), and top holders whose entry was a transfer from
    a participant (a lower bound of what moved on). Onward transfers are not traced: confidence low."""
    if not parts:
        return None
    s = supply or 1
    top = {w for w, _, _ in holders}
    rows, held_total = [], 0
    for a, p in sorted(parts.items(), key=lambda kv: -kv[1]["acquired"]):
        own = min(balances.get(a, 0), p["acquired"])
        held_total += own
        rows.append({"wallet": a, "kind": p["kind"], "block": p["block"], "tx": p["tx"],
                     "acquired_share": p["acquired"] / s, "held_share": own / s, "sold_share": None,
                     "moved_share": None, "burned_share": None, "in_top": a in top,
                     "status": "holding" if own >= p["acquired"] * 0.99 else "gone" if own <= p["acquired"] * 0.01
                     else "partly_out"})
    downstream = [(w, b) for w, b, _ in holders if w not in parts and (entries.get(w) or {}).get("via") in parts]
    initial = sum(p["acquired"] for p in parts.values())
    by_tx = defaultdict(list)
    for a, p in parts.items():
        by_tx[p["tx"]].append(a)
    same_tx = [sorted(ws) for ws in by_tx.values() if len(ws) > 1]
    held_now = [{"wallet": a, "share": min(balances.get(a, 0), parts[a]["acquired"]) / s, "purity": None,
                 "in_top": a in top, "original": True, "hop": 0} for a in parts if balances.get(a, 0) > 0]
    held_now += [{"wallet": w, "share": b / s, "purity": None, "in_top": True, "original": False, "hop": 1}
                 for w, b in downstream]
    held_now.sort(key=lambda x: -x["share"])
    down = sum(b for _, b in downstream)
    return {
        "version": 1, "mode": "launch_only", "confidence": "low",
        "limits": ["Solana: onward transfers of launch supply are not traced; what left the original wallets is "
                   "split only where a current top holder received it from a participant"],
        "window": {"from_block": min(p["block"] for p in parts.values()),
                   "to_block": max(p["block"] for p in parts.values()), "blocks": None},
        "participants": rows,
        "counts": {k: sum(1 for p in parts.values() if p["kind"] == k)
                   for k in ("buy", "dev_transfer", "transfer", "deployer", "sniper")},
        "coordination": {"same_tx": same_tx, "same_tx_wallets": sum(len(g) for g in same_tx),
                         "launch_block_buys": sum(1 for p in parts.values() if p["kind"] == "buy"),
                         "uniform_buys": 0, "internal_transfers": None, "shared_funders": []},
        "dispersal": {"recipients": None, "fresh_recipients": None, "holders_now": len(held_now), "max_hops": None},
        "supply": {"initial_share": initial / s, "traceable_share": (held_total + down) / s,
                   "concentrated_share": held_total / s, "outside_top_share":
                       sum(min(balances.get(a, 0), p["acquired"]) for a, p in parts.items() if a not in top) / s,
                   "sold_share": None, "burned_share": None, "infra_share": None,
                   "left_share": (initial - held_total) / s,
                   "traceable_of_circulating": (held_total + down) / circulating if circulating else None},
        "holders_now": held_now[:HOLDERS_LIST],
        "conservation": None,
    }
