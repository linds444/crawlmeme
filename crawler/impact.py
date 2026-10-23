"""Phase 9: sell impact and «probably rug» — what the price does if given holders sell into the token's liquidity.
Pure functions over what the scan already read (no network).

Venues (where a sale goes), one model each — the constant-product formula is used only where it is the pool's model:
  constant_product   x*y=k pool (Uniswap V2 / PancakeSwap V2, PumpSwap, Raydium AMM v4 / CPMM, Meteora DAMM):
                     drop = 1 − (R / (R + q))², R — the pool's token reserve
  bonding_curve      launchpad curve priced as x*y=k on virtual reserves (pump.fun, Flap, Pons): same formula, R — the
                     curve's virtual token reserve (what the curve prices off, not its balance)
  quote_curve        concentrated liquidity and hooked pools (Uniswap V3 / V4, Clanker, four.meme): [(q, drop)] from
                     on-chain quoter calls at the current block; linear between points, beyond the last quoted point a
                     constant-product fit through it (marked extrapolated)
  approximate        a pool whose real model was not quoted (Raydium CLMM, Orca Whirlpool, Meteora DLMM / DBC, Raydium
                     LaunchLab, a V4 pool manager balance): the formula on its token balance, exact = False
Several venues: a router splits the sale until every pool reaches the same price, so the aggregate drop d solves
Σ absorb_i(d) = q (absorb_i — tokens pool i takes to fall by d). One venue: its own drop, as before.

Taxes: a sell tax taken in tokens reduces what reaches the pool (q × (1 − t), capped by a max fee); one taken in the
quote coin only reduces proceeds. Sources: launchpad state (Flap), Token-2022 transfer fee (Solana mint), or observed
in the scan's own sell transfers (EVM: the seller's tokens split between the market and a fixed receiver in one tx).

Scenarios (each wallet counted once inside a scenario; scenarios overlap by design, each answers its own question):
  largest_wallet       the biggest analysed holder
  largest_operator     the biggest group of 2+ linked wallets (proven links or a pack), unweighted
  launch_bundle        wallets that entered in the launch bundle window (and snipers where the chain counts them)
  suspicious_combined  the union of linked, transfer, fresh (virgin), bundle, deployer and Phase 7 crew wallets
Locked tokens (vesting) are counted in share but not in what can be sold now.

The «probably rug» marker is a conditional scenario, not a prediction: shown only when V1's behavioural criteria hold
(verdict DANGER from a non-soft rule, reliable liquidity, signals not limited) and the combined suspicious supply would
move the price by ≥ RUG_MIN_DROP.
"""
import math
import statistics

from crawler import detect as d

COMBINED_ORDER = d.RUG_ORDER + ("deployer", "crew")   # a wallet goes to the first matching reason
DEPTH_DROPS = (0.02, 0.10, 0.25, 0.50)                # tokens it takes to move the price this much
SLIPPAGE_SIZES = (0.001, 0.005, 0.01, 0.05)           # sells of this share of circulating
TAX_MIN_SAMPLES = 3       # observed tax: at least this many sells …
TAX_RECEIVER_SHARE = 0.6  # … at least this share of them paying the same receiver …
TAX_MIN = 0.001           # … and a median rate of at least 0.1%
TAX_MAX = 0.99
_ITER = 64                # bisection steps for the aggregate drop


# ---------------------------------------------------------------------------
# venues
# ---------------------------------------------------------------------------
def venue(model, reserve=None, curve=None, label=None, address=None, exact=True):
    """A place a sale goes. reserve — raw token units (constant_product / bonding_curve / approximate);
    curve — [(q raw, drop)] ascending (quote_curve)."""
    v = {"model": model, "label": label, "address": address, "exact": exact and model != "approximate"}
    if model == "quote_curve":
        pts = [(float(q), float(x)) for q, x in curve or [] if q > 0]
        v["curve"] = pts
        v["fit"] = _cp_fit(pts[-1]) if pts else None
    else:
        v["reserve"] = max(0, reserve or 0)
    return v


def _cp_fit(point):
    """Constant-product reserve through (q, drop): beyond the quoted range a CP tail continues the curve."""
    q, x = point
    if x <= 0 or x >= 1:
        return None
    s = math.sqrt(1 - x)
    return q * s / (1 - s)


def _cp_drop(q, r):
    return d.dump_impact(q, r)


def _cp_absorb(x, r):
    """Tokens a constant-product pool takes to fall by x: R × (1/√(1−x) − 1)."""
    if x <= 0:
        return 0.0
    if x >= 1 or r <= 0:
        return math.inf if r > 0 else 0.0
    return r * (1 / math.sqrt(1 - x) - 1)


def venue_drop(v, q):
    if q <= 0:
        return 0.0
    if v["model"] != "quote_curve":
        return _cp_drop(q, v["reserve"])
    pts = v["curve"]
    if not pts:
        return 1.0
    q_last, x_last = pts[-1]
    if q > q_last:
        if x_last >= 1 or not v["fit"]:
            return 1.0 if x_last >= 1 else x_last
        # CP tail: the price keeps falling from where the quotes stop
        r = v["fit"]
        return 1 - (1 - x_last) * (r + q_last) ** 2 / (r + q) ** 2 if r else x_last
    return d.curve_impact(q, pts)


def venue_absorb(v, x):
    """Tokens this venue takes before its price is x lower (inverse of venue_drop)."""
    if x <= 0:
        return 0.0
    if v["model"] != "quote_curve":
        return _cp_absorb(x, v["reserve"])
    pts = v["curve"]
    if not pts:
        return 0.0
    q0, x0 = 0.0, 0.0
    for q1, x1 in pts:
        if x <= x1:
            return q0 + (q1 - q0) * ((x - x0) / (x1 - x0) if x1 > x0 else 1.0)
        q0, x0 = q1, x1
    q_last, x_last = pts[-1]
    if x_last >= 1 or not v["fit"]:
        return q_last
    r = v["fit"]   # invert the CP tail: (1 − x) = (1 − x_last)(r + q_last)² / (r + q)²
    return (r + q_last) * math.sqrt((1 - x_last) / (1 - x)) - r


def market_drop(venues, q):
    """Price drop after selling q (raw tokens, already net of token-side tax) across all venues. None — no venue."""
    live = [v for v in venues if (v.get("reserve") or 0) > 0 or v.get("curve")]
    if not live:
        return None
    if q <= 0:
        return 0.0
    if len(live) == 1:
        return venue_drop(live[0], q)
    lo, hi = 0.0, 1.0
    for _ in range(_ITER):
        mid = (lo + hi) / 2
        if sum(venue_absorb(v, mid) for v in live) >= q:
            hi = mid
        else:
            lo = mid
    return hi


def market_absorb(venues, x):
    """Tokens (raw, reaching the pools) that move the aggregate price by x."""
    live = [v for v in venues if (v.get("reserve") or 0) > 0 or v.get("curve")]
    return sum(venue_absorb(v, x) for v in live) if live else None


def beyond_quotes(venues, x):
    """True when a quoted venue would have to go past its last quote to reach a price drop of x and there is no
    constant-product tail to continue it: its share of the sale is then a lower bound (the pool is deeper than
    quoted, e.g. a fresh token whose float is tiny next to the pool)."""
    return any(v["model"] == "quote_curve" and v["curve"] and not v["fit"] and v["curve"][-1][1] < 1
               and x > v["curve"][-1][1] for v in venues if v.get("curve"))


def slippage(venues, q, grid=160):
    """Average execution shortfall vs the spot price for a sale of q: 1 − (1/q)∫₀^q (1 − drop(s)) ds.
    Trapezoids on a geometric grid (dense where a thin pool drains in the first fraction of the sale) plus every
    quoted point — exact for the piecewise-linear quote curves. Constant product: 1 − R/(R+q) to within 5e-5."""
    if q <= 0:
        return 0.0
    live = [v for v in venues if (v.get("reserve") or 0) > 0 or v.get("curve")]
    pts = {0.0, float(q)}
    lo = q * 1e-9
    pts.update(lo * (q / lo) ** (i / grid) for i in range(grid + 1))
    pts.update(q * i / 64 for i in range(1, 64))   # and an even grid for the smooth part
    for v in live:
        for _, x in v.get("curve") or []:   # where the aggregate passes each quoted drop
            at = venue_absorb(v, x) if len(live) == 1 else market_absorb(live, x)
            if 0 < at < q:
                pts.add(at)
    xs = sorted(pts)
    ys = [1 - (market_drop(live, x) or 0.0) for x in xs]
    area = sum((xs[i + 1] - xs[i]) * (ys[i] + ys[i + 1]) / 2 for i in range(len(xs) - 1))
    return max(0.0, min(1.0, 1 - area / q))


# ---------------------------------------------------------------------------
# taxes
# ---------------------------------------------------------------------------
def tax(rate, side="token", source=None, exact=True, max_fee=None, samples=None, receiver=None):
    """Sell tax: side "token" — taken from the tokens sold (less reaches the pool), "quote" — from the proceeds."""
    return {"sell": rate, "side": side, "source": source, "exact": exact, "max_fee": max_fee, "samples": samples,
            "receiver": receiver}


def net_into_pool(q, t):
    if not t or t["side"] != "token" or not t["sell"]:
        return q
    fee = q * t["sell"]
    if t.get("max_fee") is not None:
        fee = min(fee, t["max_fee"])
    return max(0.0, q - fee)


def observed_tax(transfers, market, skip=()):
    """Sell tax seen in the scan's own transfers (EVM fee-on-transfer tokens): in a sell tx the seller sends A to the
    market and T to one fixed non-market receiver (the token contract or a tax wallet); rate = T / (A + T).
    Sales by a tax receiver itself (the contract swapping its collected tax inside a user's sell) and by skip
    (infrastructure) are not samples. None — fewer than TAX_MIN_SAMPLES sells, no dominant receiver, or a median
    below TAX_MIN: «no tax seen», which is not proof of no tax."""
    by_tx = {}
    for t in transfers:
        if t.get("tx") is not None and t["amount"] > 0:
            by_tx.setdefault(t["tx"], []).append(t)
    sells = []   # (seller, rate, receiver | None)
    for rows in by_tx.values():
        for seller in {t["frm"] for t in rows if t["to"] in market and t["frm"] not in market}:
            if seller in skip:
                continue
            a = sum(t["amount"] for t in rows if t["frm"] == seller and t["to"] in market)
            side = [t for t in rows if t["frm"] == seller and t["to"] not in market and t["to"] != seller]
            if not side:
                sells.append((seller, 0.0, None))
                continue
            rcv = max(side, key=lambda t: t["amount"])["to"]
            fee = sum(t["amount"] for t in side if t["to"] == rcv)
            sells.append((seller, fee / (a + fee), rcv))
    paid = {r for _, _, r in sells if r}
    sells = [s for s in sells if s[0] not in paid]
    if len(sells) < TAX_MIN_SAMPLES:
        return None
    counts = {}
    for _, _, r in sells:
        if r:
            counts[r] = counts.get(r, 0) + 1
    if not counts:
        return None
    rcv, n = max(counts.items(), key=lambda kv: kv[1])
    if n < TAX_RECEIVER_SHARE * len(sells):
        return None
    rate = statistics.median(r for s, r, x in sells)
    if rate < TAX_MIN:
        return None
    return tax(min(rate, TAX_MAX), "token", "observed", exact=False, samples=len(sells), receiver=rcv)


# ---------------------------------------------------------------------------
# scenarios
# ---------------------------------------------------------------------------
def _sellable(q, wallets, locked):
    return max(0, q - sum((locked or {}).get(w, 0) for w in wallets)) if locked else q


def _scenario(kind, wallets, bal, ctx, parts=None, q=None):
    if not wallets and not q:
        return None
    if q is None:
        q = sum(bal[w] for w in wallets)
    circ, ratio = ctx["circulating"], ctx["ratio"]
    sell = _sellable(q, wallets, ctx["locked"])
    into = net_into_pool(sell, ctx["tax"])
    drop = market_drop(ctx["venues"], into) if ctx["venues_ok"] else None
    slip = slippage(ctx["venues"], into) if drop is not None else None
    share = q / circ if circ else 0.0
    out = {"kind": kind, "wallets": list(wallets), "share": share, "share_supply": share * ratio,
           "sellable_share": sell / circ if circ else 0.0, "drop": drop, "slippage": slip,
           # past the last quote with no tail to extend it: the drop is the last quoted one, a lower bound
           "lower_bound": drop is not None and any(v["model"] == "quote_curve" and v["curve"] and not v["fit"]
                                                   and into > v["curve"][-1][0] for v in ctx["venues"]),
           "value_usd": None, "proceeds_usd": None, "level_usd": None}
    unit = ctx["unit_usd"]
    if unit:
        out["value_usd"] = sell * unit
        if slip is not None:
            quote_tax = ctx["tax"]["sell"] if ctx["tax"] and ctx["tax"]["side"] == "quote" else 0.0
            out["proceeds_usd"] = into * unit * (1 - slip) * (1 - quote_tax)
        if drop is not None and ctx["price_usd"]:
            out["level_usd"] = ctx["price_usd"] * (1 - drop)
    if parts is not None:
        out["parts"] = parts
    return out


def analyse(holders, signals, ops, base, venues, venues_ok, tax_info=None, locked=None, memory=None,
            snipers=True, price_usd=None, decimals=18, extra=None):
    """result["sell_impact"]. holders — [(wallet, balance raw, share of circulating)] analysed, largest first;
    venues — [venue(...)]; venues_ok — the liquidity is reliably measured (engine reserve_ok)."""
    circ = base["circulating"]
    ratio = circ / base["supply"] if base.get("supply") else 0.0
    bal = {w: v for w, v, _ in holders}
    unit = price_usd / 10 ** decimals if price_usd else None
    ctx = {"circulating": circ, "ratio": ratio, "locked": locked, "tax": tax_info, "venues": venues,
           "venues_ok": venues_ok and bool(venues), "unit_usd": unit, "price_usd": price_usd}

    sc = {}
    if holders:
        sc["largest_wallet"] = _scenario("largest_wallet", [holders[0][0]], bal, ctx)
    group = next((o for o in sorted(ops, key=lambda o: -o["share"]) if len(o["wallets"]) > 1), None)
    sc["largest_operator"] = (_scenario("largest_operator", [w for w in group["wallets"] if w in bal], bal, ctx)
                              if group else None)
    if sc["largest_operator"]:
        sc["largest_operator"]["level"] = group["level"]
    bundle = [w for w, _, _ in holders
              if signals[w]["launch_bundle"] or (snipers and signals[w]["sniper"])]
    sc["launch_bundle"] = _scenario("launch_bundle", bundle, bal, ctx)

    linked = {w for o in ops if len(o["wallets"]) > 1 for w in o["wallets"]}
    crew = {w for c in (memory or {}).get("crews") or [] for w in c.get("wallets") or []}
    test = {
        "linked": lambda w, s: w in linked,
        "transfer": lambda w, s: s["kind"] == "transfer",
        "virgin": lambda w, s: s["virgin"],
        "bundle": lambda w, s: not s["sold"] and (s["launch_bundle"] or (snipers and s["sniper"])),
        "deployer": lambda w, s: s["is_deployer"],
        "crew": lambda w, s: w in crew,
    }
    taken, parts = [], []
    for kind in COMBINED_ORDER:
        rows = [(w, sh) for w, _, sh in holders if w not in taken and test[kind](w, signals[w])]
        if rows:
            sh = sum(r[1] for r in rows)
            parts.append({"kind": kind, "wallets": [r[0] for r in rows], "share": sh, "share_supply": sh * ratio})
            taken += [r[0] for r in rows]
    order = {w: i for i, (w, _, _) in enumerate(holders)}
    sc["suspicious_combined"] = _scenario("suspicious_combined", sorted(taken, key=order.get), bal, ctx, parts)

    for kind, (ws, q) in (extra or {}).items():   # e.g. bundle_traced (Phase 10): supply traced beyond the top
        sc[kind] = _scenario(kind, list(ws), bal, ctx, q=q)
    live = [v for v in venues if (v.get("reserve") or 0) > 0 or v.get("curve")]
    depth, slip = [], []
    if ctx["venues_ok"]:
        for x in DEPTH_DROPS:
            into = market_absorb(venues, x)
            if into is None:
                continue
            # what a seller must sell for that much to reach the pools (token-side tax comes off first)
            sold = into
            if tax_info and tax_info["side"] == "token" and tax_info["sell"] and tax_info.get("max_fee") is None:
                sold = into / (1 - tax_info["sell"])
            depth.append({"drop": x, "share": sold / circ if circ and math.isfinite(sold) else None,
                          "value_usd": sold * unit if unit and math.isfinite(sold) else None,
                          "more_than": beyond_quotes(venues, x)})   # share is a lower bound: deeper than quoted
        for s in SLIPPAGE_SIZES:
            into = net_into_pool(s * circ, tax_info)
            slip.append({"share": s, "drop": market_drop(venues, into), "slippage": slippage(venues, into),
                         "value_usd": s * circ * unit if unit else None})
    models = sorted({v["model"] for v in live})
    return {
        "version": 1,
        "liquidity": {"venues": [{k: v.get(k) for k in ("model", "label", "address", "exact")}
                                 | {"reserve_share": (v.get("reserve") or 0) / circ if circ and "reserve" in v
                                    else None,
                                    "quoted_to": v["curve"][-1][0] / circ if circ and v.get("curve") else None}
                                 for v in live],
                      "models": models, "aggregated": len(live) > 1,
                      "exact": bool(live) and all(v["exact"] for v in live),
                      "measured": ctx["venues_ok"]},
        "tax": tax_info,
        "depth": depth, "slippage": slip,
        "scenarios": sc,
    }


def marker(si, band, gates, reserve_ok, limited):
    """The «probably rug» marker for the chart (result["rug"]), from the combined suspicious scenario, or None.
    Conditional scenario: if these wallets sell into today's liquidity — not a prediction."""
    behavioral = any(not g.startswith("soft:") for g in gates or [])
    combo = ((si or {}).get("scenarios") or {}).get("suspicious_combined")
    if band != "DANGER" or not reserve_ok or limited or not behavioral or not combo or combo["drop"] is None:
        return None
    if combo["drop"] < d.RUG_MIN_DROP:
        return None
    return {"drop": combo["drop"], "level_factor": 1.0 - combo["drop"], "share": combo["share"],
            "share_supply": combo["share_supply"], "parts": combo["parts"], "wallets": combo["wallets"],
            "level_usd": combo["level_usd"], "conditional": True,
            "basis": "suspicious_combined", "aggregated": si["liquidity"]["aggregated"],
            "exact": si["liquidity"]["exact"]}
