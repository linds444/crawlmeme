"""Wallet forensics for the analysed holders (Phase 5). Pure functions: no network, no chain access.

What a scan learns per holder, beyond V1's entry kind / virgin / sniper flags:
  trades   — tokens bought from and sold to the market, received from and sent to other wallets
             (from the token's transfer history the scan already read; on Solana the sampled holder transactions)
  P&L      — each buy and sell priced in USD at the candle of its time (GeckoTerminal OHLCV, the chart's candles),
             plus the balance at the current price: cost, proceeds, holding, P&L, multiple, realised sell multiple.
             An ESTIMATE: candle closes, not the exact fill price; buys outside the candles' range stay unpriced and
             are counted (priced_share), so a partly priced P&L is never shown as exact
  hold     — first time the wallet received the token, hours held; exited or still holding
  funding  — the first native-coin deposit into the wallet before its entry (chains/evm/funding.py, Solana:
             Helius parsed history); top holders sharing one funder are grouped, a holder funded by another
             holder is marked

Block → time: interpolated between blocks whose time the scan already knows (launch, entries, head), so pricing
needs no extra RPC. Block times on these chains are regular enough for 1-minute to 4-hour candles.
Nothing here changes the score (detect.score); the relationship graph (Phase 6) consumes funding edges.
"""
import bisect

VERSION = 1


def make_clock(anchors):
    """block → unix time from [(block, ts)] anchors: linear between neighbours, extrapolated past the ends
    from the nearest two (one anchor: that time for every block). None without anchors."""
    pts = sorted({b: t for b, t in anchors if b is not None and t is not None}.items())
    if not pts:
        return None
    if len(pts) == 1:
        return lambda block, t=pts[0][1]: t
    blocks = [b for b, _ in pts]

    def clock(block):
        i = bisect.bisect_left(blocks, block)
        i = min(max(i, 1), len(pts) - 1)
        (b0, t0), (b1, t1) = pts[i - 1], pts[i]
        return t0 + (block - b0) * (t1 - t0) / (b1 - b0) if b1 != b0 else t0
    return clock


def price_lookup(candles):
    """ts → USD close of the candle covering ts ([[ts, o, h, l, c, v]] oldest first); None before the first candle.
    After the last candle: the last close (no trades since)."""
    rows = sorted((c[0], c[4]) for c in candles or [] if len(c) >= 5 and c[4])
    if not rows:
        return lambda ts: None
    starts = [r[0] for r in rows]

    def price(ts):
        i = bisect.bisect_right(starts, ts) - 1
        return rows[i][1] if i >= 0 else None
    return price


def flows(transfers, wallets, market, burn=()):
    """{wallet: {"bought", "sold", "received", "sent", "buys": [(block, amount)], "sells": [...],
    "first_in", "last_out"}} — market = AMM pools / curves / routers (detect market set); burn — never "sent"."""
    want, mkt, dead = set(wallets), set(market), set(burn)
    out = {w: {"bought": 0, "sold": 0, "received": 0, "sent": 0, "buys": [], "sells": [], "first_in": None,
               "last_out": None} for w in wallets}
    for t in transfers:
        frm, to, amt, block = t["frm"], t["to"], t["amount"], t["block"]
        if amt <= 0:
            continue
        if to in want:
            f = out[to]
            if frm in mkt:
                f["bought"] += amt
                f["buys"].append((block, amt))
            else:
                f["received"] += amt
            if f["first_in"] is None or block < f["first_in"]:
                f["first_in"] = block
        if frm in want:
            f = out[frm]
            if to in mkt:
                f["sold"] += amt
                f["sells"].append((block, amt))
            elif to not in dead:
                f["sent"] += amt
            if f["last_out"] is None or block > f["last_out"]:
                f["last_out"] = block
    return out


def wallet_pnl(f, balance, clock, price_at, price_now, decimals, now):
    """P&L, hold time and the realised sell multiple of one wallet (module doc). All USD values are estimates."""
    unit = 10 ** decimals

    def value(trades):
        usd, priced = 0.0, 0
        for block, amt in trades:
            p = price_at(clock(block)) if clock else None
            if p is not None:
                usd += amt / unit * p
                priced += amt
        return usd, priced

    cost, priced_buy = value(f["buys"])
    proceeds, priced_sell = value(f["sells"])
    holding = balance / unit * price_now if price_now is not None and balance > 0 else (0.0 if balance <= 0 else None)
    priced_share = priced_buy / f["bought"] if f["bought"] else None
    first_ts = clock(f["first_in"]) if clock and f["first_in"] is not None else None
    out = {
        "bought": str(f["bought"]), "sold": str(f["sold"]), "received": str(f["received"]), "sent": str(f["sent"]),
        "buys": len(f["buys"]), "sells": len(f["sells"]),
        "free_share": f["received"] / (f["received"] + f["bought"]) if f["received"] + f["bought"] else None,
        "cost_usd": round(cost, 2) if f["bought"] else None,
        "proceeds_usd": round(proceeds, 2) if f["sold"] else None,
        "holding_usd": None if holding is None else round(holding, 2),
        "priced_share": None if priced_share is None else round(priced_share, 4),
        "first_in_ts": None if first_ts is None else int(first_ts),
        "hold_h": None if first_ts is None else round(max(0.0, now - first_ts) / 3600, 1),
        "exited": balance <= 0,
        "pnl_usd": None, "multiple": None, "sell_multiple": None,
    }
    fully_priced = f["bought"] > 0 and priced_buy == f["bought"] and priced_sell == f["sold"] and holding is not None
    if fully_priced and cost > 0:
        out["pnl_usd"] = round(proceeds + holding - cost, 2)
        out["multiple"] = round((proceeds + holding) / cost, 3)
        if f["sold"] > 0:   # sold tokens at the average buy cost (tokens received for free are not in the cost)
            basis = cost / f["bought"] * min(f["sold"], f["bought"])
            out["sell_multiple"] = round(proceeds / basis, 3) if basis > 0 else None
    return out


def funding_groups(funders, share, ratio, holders, funder_out=None, hub_min=None):
    """Funding facts over the analysed holders. funders: {wallet: {"funder", "found", ...} | None}.
    common — funders that funded 2+ holders (could be the dev, a sniper crew's hub or an exchange hot wallet:
    the scan does not label exchanges), with their wallets' combined share of float and of supply; outgoing / hub —
    the funder's outgoing native transfers (Phase 6 hub check: hub True never links, None = not checked);
    by_holder — holders whose first deposit came from another analysed holder."""
    hset = set(holders)
    groups = {}
    for w, f in funders.items():
        if f and f.get("found") and f.get("funder"):
            groups.setdefault(f["funder"], []).append(w)
    common = []
    for funder, ws in groups.items():
        if len(ws) >= 2:
            s = sum(share.get(w, 0.0) for w in ws)
            n = (funder_out or {}).get(funder)
            common.append({"funder": funder, "wallets": sorted(ws, key=lambda w: -share.get(w, 0.0)),
                           "share": s, "share_supply": s * ratio, "is_holder": funder in hset, "outgoing": n,
                           "hub": None if n is None or hub_min is None else n >= hub_min})
    common.sort(key=lambda g: -g["share"])
    by_holder = [{"wallet": w, "funder": f["funder"]} for w, f in funders.items()
                 if f and f.get("found") and f.get("funder") in hset and f["funder"] != w]
    return common, by_holder


def build(transfers, holders, market, burn, balances, share, ratio, clock, candles, price_now, decimals, now,
          funders, entries, funder_out=None, hub_min=None):
    """result["forensics"]: {"version", "priced", "price_points", "decimals", "wallets": {w: {...}}, "common_funders",
    "funded_by_holder", "funding_read"}. holders — analysed wallets, largest first."""
    price_at = price_lookup(candles)
    fl = flows(transfers, holders, market, burn)
    wallets = {}
    for w in holders:
        rec = wallet_pnl(fl[w], balances.get(w, 0), clock, price_at, price_now, decimals, now)
        rec["entry"] = (entries.get(w) or {}).get("kind")
        f = funders.get(w)
        rec["funding"] = None if f is None else {k: f.get(k) for k in ("funder", "found", "block", "tx", "amount")}
        wallets[w] = rec
    common, by_holder = funding_groups(funders, share, ratio, holders, funder_out, hub_min)
    for g in common:
        for w in g["wallets"]:
            wallets[w]["funding"]["shared_with"] = len(g["wallets"]) - 1
            wallets[w]["funding"]["hub"] = g["hub"]
    return {"version": VERSION, "priced": bool(candles) and price_now is not None,
            "price_points": len(candles or []), "decimals": decimals, "wallets": wallets,
            "common_funders": common, "funded_by_holder": by_holder,
            "funding_read": sum(1 for w in holders if funders.get(w) is not None)}
