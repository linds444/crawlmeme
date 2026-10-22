"""Plain DEX tokens: an ERC-20 launched straight into a Uniswap / PancakeSwap pool, with no launchpad.
The last fallback on every EVM chain, after the launchpad plugins (and on Robinhood after Pons / Flap / Bankr).

Identification: a pool of the token against the chain's wrapped native coin or a stablecoin —
  V2: factory.getPair(token, quote); V3: factory.getPool(token, quote, fee) for each fee tier (one batched call);
  V4: an Initialize(id, currency0, currency1, …) event of the PoolManager with the token as a currency.
Launch (for sniper / bundle timing) = the first transfer of the token into one of those pools (liquidity added), not
the mint: a token minted hours before its pool would otherwise have no snipers at all. The history is still read
from the mint (launch["history_from"]) so every balance is exact.
Sell impact, by the deepest pool (quote-token balance; V4 pools only if there is no V2/V3 pool):
  V2 — constant product on the pair's token reserve (that is the pool's real formula);
  V3 — QuoterV2.quoteExactInputSingle over a share grid: drop = 1 − p_after / p_before from sqrtPriceX96After and
       slot0 (concentrated liquidity: x·y=k would be wrong); a reverting quote = the pool cannot absorb the sale;
  V4 — V4Quoter grid (evm/v4.py), pool key from the Initialize event.
Using one pool is conservative (an aggregator could split a large sale across pools); all pools are reported.
Every address below was checked on-chain on 2026-10-21 (factories return the known WETH/stable pools; quoters have
code); sources: Uniswap's deployments (github.com/Uniswap/contracts/deployments/{chain}.md) and PancakeSwap's.
"""
from crawler.chains.evm import abi, v4
from crawler.chains.evm.keccak import selector, topic

NAME = "dex"
DEXES = {
    "robinhood": [
        {"name": "Uniswap", "v2": "0x8bceaa40b9acdfaedf85adf4ff01f5ad6517937f",
         "v3": "0x1f7d7550b1b028f7571e69a784071f0205fd2efa", "fees": (100, 500, 3000, 10000),
         "quoter": "0x33e885ed0ec9bf04ecfb19341582aadcb4c8a9e7",
         "routers": ("0x89e5db8b5aa49aa85ac63f691524311aeb649eba", "0xcaf681a66d020601342297493863e78c959e5cb2",
                     "0x204faca1764b154221e35c0d20abb3c525710498", "0x248a454ac3584c2a48d1fcb28d3910a6b6ea00af")},
    ],
    "base": [
        {"name": "Uniswap", "v2": "0x8909dc15e40173ff4699343b6eb8132c65e18ec6",
         "v3": "0x33128a8fc17869897dce68ed026d694621f6fdfd", "fees": (100, 500, 3000, 10000),
         "quoter": "0x3d4e44eb1374240ce5f1b871ab261cd16335b76a",
         "routers": ("0x4752ba5dbc23f44d87826276bf6fd6b1c372ad24", "0x2626664c2603336e57b271c5c0b26f421741e481",
                     "0xd6145b2d3f379919e8cdeda7b97e37c4b2ca9c40")},
    ],
    "bsc": [
        {"name": "PancakeSwap", "v2": "0xca143ce32fe78f1f7019d7d551a6402fc5350c73",
         "v3": "0x0bfbcf9fa4f9c56b0f40a671ad40e0805a091865", "fees": (100, 500, 2500, 10000),
         "quoter": "0xb048bbc1ee6b733fffcfb9e9cef7375518e25997",
         "routers": ("0x10ed43c718714eb63d5aa57b78b54704e256024e", "0x13f4ea83d0bd40e75c8222255bc855a974568dd4",
                     "0x1b81d678ffb9c0263b24a97847620c99d213eb14")},
        {"name": "Uniswap", "v2": "0x8909dc15e40173ff4699343b6eb8132c65e18ec6",
         "v3": "0xdb1d10011ad0ff90774d0c6bb92e5c5c8b4461f7", "fees": (100, 500, 3000, 10000),
         "quoter": "0x78d78e420da98ad378d7799be8f4af69033eb077",
         "routers": ("0x4752ba5dbc23f44d87826276bf6fd6b1c372ad24", "0xb971ef87ede563556b2ed4b1c0b0019111dd85d2",
                     "0xdc264714f68d84cf29bc605589405e78bdbe7c9f")},
    ],
}
SEL_GET_PAIR = selector("getPair(address,address)")
SEL_GET_POOL = selector("getPool(address,address,uint24)")
SEL_RESERVES = selector("getReserves()")
SEL_TOKEN0 = selector("token0()")
SEL_SLOT0 = selector("slot0()")
SEL_BALANCE = selector("balanceOf(address)")
SEL_QUOTE_V3 = selector("quoteExactInputSingle((address,address,uint256,uint24,uint160))")
V4_INIT = topic("Initialize(bytes32,address,address,uint24,int24,address,uint160,int24)")


def quotes(ad):
    return [ad.net.wrapped_native] + sorted(ad.net.stables)


def find_pools(ad, token):
    """[{"kind": "v2"|"v3", "dex", "address", "quote", "fee"?}] via one batch of factory calls."""
    calls, meta = [], []
    for dx in DEXES.get(ad.CHAIN, []):
        for q in quotes(ad):
            if q == token:
                continue
            calls.append((dx["v2"], abi.call_data(SEL_GET_PAIR, token, q)))
            meta.append({"kind": "v2", "dex": dx["name"], "quote": q, "dx": dx})
            for fee in dx["fees"]:
                calls.append((dx["v3"], abi.call_data(SEL_GET_POOL, token, q, fee)))
                meta.append({"kind": "v3", "dex": dx["name"], "quote": q, "fee": fee, "dx": dx})
    out = []
    for m, r in zip(meta, ad.batch_calls(calls)):
        ws = abi.words(r or "0x")
        if ws and abi.addr(ws[0]) != abi.ZERO:
            out.append(m | {"address": abi.addr(ws[0])})
    return out


def find_v4_pools(ad, token, from_block):
    """V4 pools with the token as currency0 or currency1 against a quote token, from PoolManager Initialize events."""
    pm, out = ad.net.v4_pool_manager, []
    qs = set(quotes(ad)) | {abi.ZERO}     # V4 can pair with native ETH/BNB directly (currency 0x0)
    for pos in (2, 3):
        topics = [V4_INIT, None, None, None]
        topics[pos] = abi.topic_for(token)
        for page in ad.client.iter_logs(from_block, ad.client.head(), address=pm, topics=topics[:pos + 1]):
            for lg in page:
                c0, c1 = abi.addr(lg["topics"][2]), abi.addr(lg["topics"][3])
                other = c1 if c0 == token else c0
                if other not in qs:
                    continue
                ws = abi.words(lg["data"])
                out.append({"kind": "v4", "dex": "Uniswap", "quote": other, "address": pm,
                            "pool_id": lg["topics"][1],
                            "key": v4.pool_key(c0, c1, abi.u256(ws[0]), abi.i256(ws[1]), abi.addr(ws[2]))})
    return out


def detect(ad, token):
    if ad.CHAIN not in DEXES:
        return None
    try:
        pools = find_pools(ad, token)
    except Exception:
        return None
    if not pools:   # no V2/V3 pool: look for a V4 pool from the token's first transfer on
        try:
            first = ad.first_transfer(token)
            pools = find_v4_pools(ad, token, first["block"]) if first else []
        except Exception:
            pools = []
    if not pools:
        return None
    return {"pools": [{k: v for k, v in p.items() if k != "dx"} | {"routers": list(p["dx"]["routers"])
                      if "dx" in p else []} for p in pools]}


def launch(ad, token, st, first):
    pools = st["pools"]
    addrs = sorted({p["address"] for p in pools})
    liq = ad.first_transfer_to(token, addrs, first["block"])   # first liquidity added = trading starts
    routers = sorted({r for p in pools for r in p.get("routers", [])} | set(ad.net.routers))
    market = sorted(set(addrs) | {ad.net.v4_pool_manager} | set(routers))
    deployer = first["to"]
    if ad.is_contract([deployer]).get(deployer):   # minted to a contract: the creation tx sender deployed it
        tx = ad.client.call("eth_getTransactionByHash", [first["tx"]], need={"from_block": first["block"]}) or {}
        deployer = (tx.get("from") or deployer).lower()
    return {"block": liq["block"] if liq else first["block"], "tx": liq["tx"] if liq else first["tx"],
            "history_from": first["block"], "deployer": deployer, "pair": pools[0]["quote"],
            "pools": pools, "market": market, "infra": [], "curve": addrs[0],
            "liquidity_found": liq is not None}


def _depth(ad, pools):
    """Quote-token balance of every V2/V3 pool (one batch): the deepest one prices the sell impact."""
    v23 = [p for p in pools if p["kind"] in ("v2", "v3")]
    res = ad.batch_calls([(p["quote"], abi.call_data(SEL_BALANCE, p["address"])) for p in v23]) if v23 else []
    for p, r in zip(v23, res):
        ws = abi.words(r or "0x")
        p["quote_balance"] = abi.u256(ws[0]) if ws else 0
    return sorted(v23, key=lambda p: (p["quote"] == ad.net.wrapped_native, p["quote_balance"]), reverse=True)


VENUES_MAX = 3   # pools priced for the aggregate sell impact (each costs 2-3 eth_calls)


def _venue(ad, token, p, circulating):
    """impact.py venue for one V2 / V3 pool, or None (unreadable: the pool is left out, never guessed)."""
    try:
        if p["kind"] == "v2":
            t0 = abi.addr(abi.words(ad.call(p["address"], SEL_TOKEN0))[0])
            ws = abi.words(ad.call(p["address"], SEL_RESERVES))
            reserve = abi.u256(ws[0] if t0 == token else ws[1])
            return {"model": "constant_product", "reserve": reserve, "label": f"{p['dex']} V2 pair",
                    "address": p["address"]} if reserve > 0 else None
        curve = _v3_curve(ad, token, p, circulating)
        return {"model": "quote_curve", "curve": curve, "label": f"{p['dex']} V3 pool (QuoterV2)",
                "address": p["address"]} if curve else None
    except Exception as e:
        print(f"dex: pool {p['address']} not priced for {token}: {type(e).__name__}", flush=True)
        return None


def _v3_curve(ad, token, pool, circulating):
    """[(q, drop)] for selling q into a V3 pool: QuoterV2 sqrtPriceX96After vs slot0 (one multicall)."""
    t0 = abi.addr(abi.words(ad.call(pool["address"], SEL_TOKEN0))[0])
    sqrt0 = abi.u256(abi.words(ad.call(pool["address"], SEL_SLOT0))[0])
    if not sqrt0:
        return []
    qs = [max(1, int(circulating * f)) for f in v4.GRID]
    calls = [(pool["quoter"], SEL_QUOTE_V3 + abi.enc_addr(token) + abi.enc_addr(pool["quote"]) + abi.enc_uint(q)
              + abi.enc_uint(pool["fee"]) + abi.enc_uint(0)) for q in qs]
    raw = ad.client.call("eth_call", [{"to": ad.net.multicall3, "data": v4.encode_try_aggregate(calls),
                                       "gas": hex(v4.GAS)}, "latest"])
    out, worst = [], 0.0
    for q, (ok, ret) in zip(qs, v4.decode_try_aggregate(raw)):
        if ok and len(abi.words(ret)) >= 2:
            after = abi.u256(abi.words(ret)[1])
            ratio = (after / sqrt0) ** 2 if t0 == token else (sqrt0 / after) ** 2 if after else 0.0
            worst = max(worst, min(1.0, max(0.0, 1 - ratio)))
        else:
            worst = 1.0                     # the pool cannot absorb this sale
        out.append((q, worst))
    return out


def liquidity(ad, token, launch, circulating, balances):
    pools = launch["pools"]
    ranked = _depth(ad, pools)
    for p in ranked:
        dx = next(d for d in DEXES[ad.CHAIN] if d["name"] == p["dex"])
        p["quoter"] = dx["quoter"]
    launch["pool_depth"] = [{k: p.get(k) for k in ("kind", "dex", "address", "quote", "fee", "quote_balance")}
                            for p in ranked]
    if circulating <= 0:
        return {"reserve": 0, "reserve_ok": False}
    if ranked:
        # the deepest pool prices the V1 score (unchanged); up to VENUES_MAX pools with liquidity are venues for the
        # Phase 9 aggregate (a router splits a big sale across them)
        venues = [v for v in (_venue(ad, token, p, circulating) for p in
                              [ranked[0]] + [p for p in ranked[1:] if p.get("quote_balance")][:VENUES_MAX - 1]) if v]
        launch["impact_pool"] = ranked[0]["address"]
        top = venues[0] if venues and venues[0]["address"] == ranked[0]["address"] else None
        if ranked[0]["kind"] == "v2":
            reserve = top["reserve"] if top else 0
            return {"reserve": reserve, "reserve_ok": reserve > 0, "venues": venues}
        curve = top["curve"] if top else []
        return {"reserve": max(0, balances.get(ranked[0]["address"], 0)), "reserve_ok": bool(curve),
                "impact_curve": curve or None, "venues": venues}
    v4p = [p for p in pools if p["kind"] == "v4"]
    if v4p:
        p = v4p[0]
        qs = v4.sell_amounts(circulating)
        try:
            raw = ad.client.call("eth_call", v4.quote_call(ad.net.v4_quoter, ad.net.multicall3, p["key"],
                                                           p["key"]["currency0"] == token, qs))
            curve = v4.impact_curve(qs, v4.decode_try_aggregate(raw))
        except Exception:
            curve = []
        launch["impact_pool"] = p["pool_id"]
        return {"reserve": max(0, balances.get(ad.net.v4_pool_manager, 0)), "reserve_ok": bool(curve),
                "impact_curve": curve or None,
                "venues": [{"model": "quote_curve", "curve": curve, "label": f"{p['dex']} V4 pool (V4Quoter)",
                            "address": p["pool_id"]}] if curve else []}
    return {"reserve": 0, "reserve_ok": False}


def buy_quote(ad, token, logs, t, launch):
    """Token out of one of the token's pools in this transaction → buy; quote = wrapped native paid into that pool."""
    pools = {p["address"] for p in launch.get("pools", []) if p["kind"] in ("v2", "v3")}
    moved = [p for l in logs if l["address"].lower() == token and (p := ad._parse(l)) and p["frm"] in pools]
    if not moved:
        if any(p["kind"] == "v4" for p in launch.get("pools", [])):
            return ad.v4_buy_quote(token, logs, t, launch)
        return False, None
    pool = moved[0]["frm"]
    wn = ad.net.wrapped_native
    paid = [int(l["data"], 16) for l in logs if l["address"].lower() == wn and (p := ad._parse(l)) and p["to"] == pool]
    native = any(p["quote"] == wn and p["address"] == pool for p in launch.get("pools", []))
    return True, (max(paid) if paid and native else None)


def info(ad, token, launch):
    pools = launch.get("pool_depth") or [{k: p.get(k) for k in ("kind", "dex", "address", "quote", "fee")}
                                         for p in launch.get("pools", [])]
    return {"dexes": sorted({p["dex"] for p in launch.get("pools", [])}), "pools": pools,
            "impact_pool": launch.get("impact_pool"), "liquidity_block": launch["block"],
            "mint_block": launch.get("history_from"), "liquidity_found": launch.get("liquidity_found")}
