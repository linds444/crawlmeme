"""four.meme launchpad plugin (BNB Chain), TokenManager V2 tokens.

How a four.meme token trades (verified on mainnet 2026-10-21, events decoded from TokenManager2 logs):
  - TokenManager2 (proxy) mints the whole supply (1B, 18 decimals) to itself and sells it on a bonding curve:
    buys move tokens TokenManager2 → buyer and emit TokenPurchase(token, account, price, amount, cost, fee, offers,
    funds); sells emit TokenSale (same fields); TokenCreate(creator, token, requestId, name, symbol, totalSupply,
    launchTime, launchFee) is emitted in the creation transaction. All fields are non-indexed.
  - when the curve fills, liquidity moves to PancakeSwap (getTokenInfo.liquidityAdded = true; Helper3.getPancakePair).
Identification: TokenManagerHelper3.getTokenInfo(token): version 2 and tokenManager = TokenManager2 (one eth_call).
Liquidity: on the curve, sell impact comes from Helper3.trySell quotes (exact curve maths, fees included); after
graduation, the PancakeSwap V2 pair reserves (constant product is the pool's real formula there).
Version 1 tokens (TokenManager V1, 2024) use other events and are not covered.
"""
from crawler.chains.evm import abi, v4
from crawler.chains.evm.keccak import selector, topic

NAME = "fourmeme"
TOKEN_MANAGER2 = "0x5c952063c7fc8610ffdb798152d69f0b9550762b"
HELPER3 = "0xf251f83e40a78868fcfa3fa4599dad6494e46034"
SEL_TOKEN_INFO = selector("getTokenInfo(address)")              # 0x1f69565f
SEL_TRY_SELL = selector("trySell(address,uint256)")
SEL_PANCAKE_PAIR = selector("getPancakePair(address)")
SEL_RESERVES = selector("getReserves()")                       # 0x0902f1ac
SEL_TOKEN0 = selector("token0()")
TOKEN_CREATE = topic("TokenCreate(address,address,uint256,string,string,uint256,uint256,uint256)")
TOKEN_PURCHASE = topic("TokenPurchase(address,address,uint256,uint256,uint256,uint256,uint256,uint256)")
TOKEN_SALE = topic("TokenSale(address,address,uint256,uint256,uint256,uint256,uint256,uint256)")
INFO_FIELDS = ("version", "token_manager", "quote", "last_price", "trading_fee_rate", "min_trading_fee",
               "launch_time", "offers", "max_offers", "funds", "max_funds", "liquidity_added")
ENTRY_TOL = 0.06


def token_info(ad, token):
    ws = abi.words(ad.call(HELPER3, abi.call_data(SEL_TOKEN_INFO, token)) or "0x")
    if len(ws) < 12:
        return None
    vals = [abi.u256(w) for w in ws[:12]]
    out = dict(zip(INFO_FIELDS, vals))
    out["token_manager"], out["quote"] = abi.addr(ws[1]), abi.addr(ws[2])
    out["liquidity_added"] = bool(vals[11])
    return out


def detect(ad, token):
    if ad.CHAIN != "bsc":
        return None
    try:
        st = token_info(ad, token)
    except Exception:
        return None
    if not st or st["version"] != 2 or st["token_manager"] != TOKEN_MANAGER2:
        return None
    return st


def _pair(ad, token):
    try:
        p = abi.addr(abi.words(ad.call(HELPER3, abi.call_data(SEL_PANCAKE_PAIR, token)))[0])
    except Exception:
        return None
    return None if p == abi.ZERO else p


def launch(ad, token, st, first):
    logs = ad.receipts([first["tx"]], {first["tx"]: first["block"]})[first["tx"]]
    created = None
    for l in logs:
        if l["address"].lower() == TOKEN_MANAGER2 and (l.get("topics") or [""])[0].lower() == TOKEN_CREATE:
            ws = abi.words(l["data"])
            if abi.addr(ws[1]) == token:
                created = {"creator": abi.addr(ws[0]), "name": abi.dyn_string(ws, 3), "symbol": abi.dyn_string(ws, 4),
                           "total_supply": abi.u256(ws[5]), "launch_time": abi.u256(ws[6]),
                           "launch_fee": abi.u256(ws[7])}
    if created is None:
        return None
    pair = _pair(ad, token) if st["liquidity_added"] else None
    quote = st["quote"] if st["quote"] != abi.ZERO else ad.net.wrapped_native
    market = {TOKEN_MANAGER2} | set(ad.net.routers) | ({pair} if pair else set())
    return {"block": first["block"], "tx": first["tx"], "deployer": created["creator"], "pair": quote,
            "native_quote": st["quote"] == abi.ZERO, "pancake_pair": pair, "market": sorted(market),
            "infra": sorted({TOKEN_MANAGER2, HELPER3}), "created": created, "curve": TOKEN_MANAGER2,
            "phase": "dex" if st["liquidity_added"] else "bonding_curve"}


def liquidity(ad, token, launch, circulating, balances):
    st = token_info(ad, token)   # current state: the curve may have filled since detection
    if st and st["liquidity_added"]:
        pair = launch.get("pancake_pair") or _pair(ad, token)
        if not pair:
            return {"reserve": 0, "reserve_ok": False}
        ws = abi.words(ad.call(pair, SEL_RESERVES))
        t0 = abi.addr(abi.words(ad.call(pair, SEL_TOKEN0))[0])
        reserve = abi.u256(ws[0] if t0 == token else ws[1])
        return {"reserve": reserve, "reserve_ok": reserve > 0}
    if circulating <= 0:
        return {"reserve": 0, "reserve_ok": False}
    qs = v4.sell_amounts(circulating)
    calls = [(HELPER3, abi.call_data(SEL_TRY_SELL, token, q)) for q in qs]
    try:
        raw = ad.client.call("eth_call", [{"to": ad.net.multicall3, "data": v4.encode_try_aggregate(calls),
                                           "gas": hex(v4.GAS)}, "latest"])
        curve = v4.impact_curve(qs, v4.decode_try_aggregate(raw), out_word=2)
    except Exception as e:
        print(f"fourmeme: curve quotes failed for {token}: {type(e).__name__}", flush=True)
        curve = []
    reserve = max(0, balances.get(TOKEN_MANAGER2, 0))
    return {"reserve": reserve, "reserve_ok": bool(curve), "impact_curve": curve or None}


def buy_quote(ad, token, logs, t, launch):
    """Curve buy: TokenPurchase of this token whose amount matches the receipt (cost = BNB in, if the quote is BNB).
    After graduation: the token came from the PancakeSwap pair; quote = largest wrapped-native transfer in the tx."""
    for l in logs:
        if l["address"].lower() == TOKEN_MANAGER2 and (l.get("topics") or [""])[0].lower() == TOKEN_PURCHASE:
            ws = abi.words(l["data"])
            if abi.addr(ws[0]) == token and abs(abi.u256(ws[3]) - t["amount"]) <= ENTRY_TOL * t["amount"]:
                return True, abi.u256(ws[4]) if launch.get("native_quote") else None
    pair = launch.get("pancake_pair")
    if pair and any((p := ad._parse(l)) and l["address"].lower() == token and p["frm"] == pair for l in logs):
        wn = ad.net.wrapped_native
        ins = [int(l["data"], 16) for l in logs if l["address"].lower() == wn and (p := ad._parse(l)) and p["to"] == pair]
        return True, (max(ins) if ins and launch.get("native_quote") else None)
    return False, None


def info(ad, token, launch):
    st = token_info(ad, token) or {}
    mo = st.get("max_offers") or 0
    return {"token_manager": TOKEN_MANAGER2, "phase": "dex" if st.get("liquidity_added") else "bonding_curve",
            "progress": round(1 - st.get("offers", 0) / mo, 4) if mo else None,
            "funds": st.get("funds"), "max_funds": st.get("max_funds"), "quote": launch.get("pair"),
            "native_quote": launch.get("native_quote"), "pancake_pair": launch.get("pancake_pair"),
            "creator": launch["created"]["creator"], "launch_fee": launch["created"]["launch_fee"]}
