"""Flap launchpad plugin for BNB Chain (the Robinhood Flap adapter, chains/flap.py, stays as it is).

Same Portal contract family as on Robinhood (verified 2026-10-21: identical TokenCreated / TokenBought topics,
getTokenV8Safe returns the same 18-field state and reverts for non-Flap tokens). Ported from chains/flap.py:
  - curve: tokens sit on the Portal, price is a virtual constant product (r + reserve)(h + 1e9 − circulating) = k,
    so the token reserve for sell impact is h + 1e9 − circulating (the Portal balance is not the reserve);
  - after graduation (status DEX): a PancakeSwap V2 pair (mainPool / state.pool), reserve via getReserves;
  - tax tokens (suffix 7777): on the curve the tax is taken in BNB (all of q reaches the curve); after graduation in
    tokens: q × (1 − sellTax) reaches the pair → q_factor;
  - buy: TokenBought(ts, token, buyer, amount, eth, fee, postPrice) on the Portal, or a V2 Swap of the pair.
Not ported: the tax recipient lookup (Robinhood TaxTokenHelper); the BNB helper is not identified yet, so
tax_recipient is reported as unknown rather than guessed.
Portal: 0xe2cE6ab80874Fa9Fa2aAE65D277Dd6B8e65C9De0 (four-flap-meme-sdk constants, verified on-chain).
"""
from crawler.chains.evm import abi
from crawler.chains.evm.keccak import selector

NAME = "flap"
PORTALS = {"bsc": "0xe2ce6ab80874fa9fa2aae65d277dd6b8e65c9de0"}
SEL_STATE = "0x62fafcca"                         # getTokenV8Safe(address) (selector as used on Robinhood)
SEL_TAX_PROCESSOR = selector("taxProcessor()")   # 0xf3635019
SEL_MAIN_POOL = selector("mainPool()")           # 0xa5a302d3
SEL_TOKEN0 = selector("token0()")
SEL_RESERVES = selector("getReserves()")
TOKEN_CREATED = "0x504e7f360b2e5fe33cbaaae4c593bc55305328341bf79009e43e0e3b7f699603"
TOKEN_BOUGHT = "0xa800a2038683844fac66747f771bfdfae862eb28b16bcfa387afa9fbacce8ff7"
V2_SWAP = "0xd78ad95fa46c994b6551d0da85fc275fe613ce37657fb8d5e3d130840159d822"
STATE_FIELDS = ("status", "reserve", "circulating", "price", "version", "r", "h", "k", "dex_thresh", "quote",
                "native_swap", "extension", "buy_tax", "sell_tax", "pool", "progress", "lp_fee", "dex_id")
STATUS_DEX = 4
SUPPLY = 10 ** 27                                # 1e9 tokens × 1e18: curve constant of every Flap token
ENTRY_TOL = 0.06
SUFFIXES = ("8888", "7777")                      # vanity suffixes: 8888 no tax, 7777 taxed


def detect(ad, token):
    portal = PORTALS.get(ad.CHAIN)
    if not portal or not token.endswith(SUFFIXES):   # no request for anything else
        return None
    st_raw, tp_raw, pool_raw = ad.batch_calls([(portal, SEL_STATE + abi.enc_addr(token)),
                                               (token, SEL_TAX_PROCESSOR), (token, SEL_MAIN_POOL)])
    ws = abi.words(st_raw or "0x")
    if len(ws) < len(STATE_FIELDS) or abi.u256(ws[0]) == 0:
        return None
    st = {k: abi.u256(w) for k, w in zip(STATE_FIELDS, ws)}
    st["quote"], st["pool"], st["extension"] = abi.addr(ws[9]), abi.addr(ws[14]), abi.addr(ws[11])
    tp, mp = abi.words(tp_raw or "0x"), abi.words(pool_raw or "0x")
    st["tax_processor"] = abi.addr(tp[0]) if tp and abi.u256(tp[0]) else None
    st["main_pool"] = abi.addr(mp[0]) if mp and abi.u256(mp[0]) else (st["pool"] if st["pool"] != abi.ZERO else None)
    st["portal"] = portal
    return st


def launch(ad, token, st, first):
    """Launch = the mint of 1e9 tokens to the Portal; creator from TokenCreated (data: [ts, creator, nonce, token, …])."""
    logs = ad.receipts([first["tx"]], {first["tx"]: first["block"]})[first["tx"]]
    portal = st["portal"]
    creator, bought = None, []
    for l in logs:
        t0 = (l.get("topics") or [""])[0].lower()
        if l["address"].lower() == portal and t0 == TOKEN_CREATED:
            ws = abi.words(l["data"])
            if len(ws) >= 4 and abi.addr(ws[3]) == token:
                creator = abi.addr(ws[1])
        elif l["address"].lower() == token and (p := ad._parse(l)) and p["frm"] == portal:
            bought.append((p["to"], p["amount"]))
    own = {token} | {x for x in (st["tax_processor"], st["main_pool"]) if x}
    market = {portal} | set(ad.net.routers) | ({st["main_pool"]} if st["main_pool"] else set())
    infra = own | {portal}
    buyers = [(w, a) for w, a in bought if w not in infra | market]
    deployer = creator if creator and creator not in infra | market else (buyers[0][0] if buyers else creator)
    return {"block": first["block"], "tx": first["tx"], "deployer": deployer or abi.ZERO, "creator": creator,
            "dev_buy": sum(a for w, a in buyers if w == deployer), "pair": ad.net.wrapped_native,
            "native_quote": st["quote"] == abi.ZERO, "market": sorted(market), "infra": sorted(infra),
            "curve": portal}


def _pair_reserve(ad, token, pool):
    t0, res = ad.batch_calls([(pool, SEL_TOKEN0), (pool, SEL_RESERVES)])
    w0, r = abi.words(t0 or "0x"), abi.words(res or "0x")
    if not w0 or len(r) < 2:
        return 0, None
    is0 = abi.addr(w0[0]) == token
    return abi.u256(r[0] if is0 else r[1]), is0


def liquidity(ad, token, launch, circulating, balances):
    st = detect(ad, token) or ad._state.get(token)   # current state: the curve may have graduated since detection
    dex = st["status"] == STATUS_DEX
    if dex:
        reserve, is0 = _pair_reserve(ad, token, st["main_pool"]) if st["main_pool"] else (0, None)
        launch["token_is0"] = is0
    else:
        reserve = st["h"] + SUPPLY - st["circulating"]
    q_factor = 1.0 - st["sell_tax"] / 10_000 if dex else 1.0
    launch["_state"] = st
    return {"reserve": max(0, reserve), "reserve_ok": reserve > 0, "q_factor": q_factor}


def buy_quote(ad, token, logs, t, launch):
    st = launch.get("_state") or ad._state.get(token) or {}
    pool, buy_tax = st.get("main_pool"), st.get("buy_tax", 0) / 10_000
    portal = PORTALS.get(ad.CHAIN)
    buys = []
    for l in logs:
        t0, a = (l.get("topics") or [""])[0].lower(), l["address"].lower()
        if a == portal and t0 == TOKEN_BOUGHT:
            ws = abi.words(l["data"])
            if len(ws) >= 5 and abi.addr(ws[1]) == token:
                buys.append((abi.u256(ws[3]), abi.u256(ws[4])))
        elif pool and a == pool and t0 == V2_SWAP:
            a0in, a1in, a0out, a1out = [abi.u256(w) for w in (abi.words(l["data"]) + ["0" * 64] * 4)[:4]]
            is0 = launch.get("token_is0")
            sides = [(a0out, a1in), (a1out, a0in)] if is0 is None else [(a0out, a1in) if is0 else (a1out, a0in)]
            buys += [(o, q) for o, q in sides if o > 0]
    if not buys:
        return False, None
    got, quote = min(buys, key=lambda b: abs(b[0] - t["amount"]))
    ok = t["amount"] and abs(got - t["amount"]) <= (ENTRY_TOL + buy_tax) * max(got, t["amount"])
    return True, quote if ok and launch.get("native_quote") else None


def info(ad, token, launch):
    st = launch.get("_state") or ad._state.get(token) or {}
    dex = st.get("status") == STATUS_DEX
    native = st.get("quote") == abi.ZERO
    return {"phase": "dex" if dex else "bonding_curve",
            "phase_text": "dex" if dex else f"bonding curve {st.get('progress', 0) / 1e16:.0f}%",
            "progress": st.get("progress", 0) / 1e18, "version": st.get("version"),
            # quote: BNB (zero address) or an ERC-20 the curve is priced in (e.g. a stablecoin); prices in BNB only for BNB
            "quote": st.get("quote"), "native_quote": native,
            "price_eth": st["price"] / 1e18 if not dex and native and st.get("price") else None,
            "reserve_eth": st["reserve"] / 1e18 if not dex and native else None,
            "tax": {"buy": st.get("buy_tax", 0) / 10_000, "sell": st.get("sell_tax", 0) / 10_000},
            "tax_recipient": None, "tax_recipient_known": False,
            "tax_processor": st.get("tax_processor"), "pool": st.get("main_pool"),
            "reserve_source": "pair_reserves" if dex else "curve_state",
            "q_factor": 1.0 - st.get("sell_tax", 0) / 10_000 if dex else 1.0, "dev_buy": launch.get("dev_buy", 0),
            "portal": PORTALS.get(ad.CHAIN)}
