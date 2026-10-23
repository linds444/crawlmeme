"""Clanker v4 launchpad plugin (Base, BNB Chain, Robinhood Chain).

How a Clanker v4 token launches (verified on mainnet launch transactions, 2026-10-21):
  - the factory mints the whole supply (100B, 18 decimals) to itself, sends the LP share to the LP locker, which
    places it into a Uniswap V4 pool (PoolManager singleton) as single-sided liquidity in tick ranges;
  - optional extensions take a share first (vault: dev vesting, airdrop, dev buy);
  - the pool key is (paired token, token, fee 0x800000 = dynamic fee set by the hook, tick spacing 200, Clanker hook);
  - TokenCreated(msgSender, token indexed, tokenAdmin indexed, …, startingTick, poolHook, poolId, pairedToken,
    locker, mevModule, extensionsSupply, extensions[]) is emitted by the factory in the creation transaction.
Identification: factory.tokenDeploymentInfo(token).token == token (one eth_call on current state, any node).
Liquidity: concentrated, so sell impact comes from V4Quoter quotes (evm/v4.py), never from x·y=k.

Factories (checked on-chain: tokenDeploymentInfo returns the token for Clanker tokens and zero for others):
  base       0xE85A59c628F7d27878ACeB4bf3b35733630083a9  (Clanker v4.0.0, official repo README)
  bsc        0xea30438E0B5f99096cb05A8Da63be55A6A298F6a  (clanker.world API factory_address, verified on-chain)
  robinhood  0xD3f2cC1731b7Fd17f28798835C2E02f0a1839A94  (found from TokenCreated logs, matches clanker.world)
Older Clanker versions (v0–v3.1, Base only) are not covered: tokenDeploymentInfo does not exist there.
"""
from crawler.chains.evm import abi, v4
from crawler.chains.evm.keccak import selector, topic

NAME = "clanker"
FACTORIES = {
    "base": "0xe85a59c628f7d27878aceb4bf3b35733630083a9",
    "bsc": "0xea30438e0b5f99096cb05a8da63be55a6a298f6a",
    "robinhood": "0xd3f2cc1731b7fd17f28798835c2e02f0a1839a94",
}
SEL_DEPLOYMENT_INFO = selector("tokenDeploymentInfo(address)")              # 0x22adcdb1
TOKEN_CREATED = topic("TokenCreated(address,address,address,string,string,string,string,string,int24,address,"
                      "bytes32,address,address,address,uint256,address[])")  # 0x9299d1d1…
INITIALIZE = topic("Initialize(bytes32,address,address,uint24,int24,address,uint160,int24)")


def detect(ad, token):
    """{"factory", "hook", "locker", "extensions"} for a Clanker v4 token on this chain, else None."""
    f = FACTORIES.get(ad.CHAIN)
    if not f:
        return None
    try:
        r = ad.call(f, abi.call_data(SEL_DEPLOYMENT_INFO, token))
    except Exception:
        return None
    ws = abi.words(r or "0x")
    # DeploymentInfo is a dynamic tuple: word 0 = offset, then token, hook, locker, extensions offset
    if len(ws) < 5 or abi.addr(ws[1]) != token.lower():
        return None
    head = 1
    return {"factory": f, "hook": abi.addr(ws[head + 1]), "locker": abi.addr(ws[head + 2]),
            "extensions": _tuple_addresses(ws, head, 3)}


def _tuple_addresses(ws, head, i):
    """address[] at tuple field i (offset relative to the tuple start at word head)."""
    off = head + abi.u256(ws[head + i]) // 32
    n = abi.u256(ws[off])
    return [abi.addr(w) for w in ws[off + 1:off + 1 + n]]


def decode_created(log):
    """TokenCreated log → dict (non-indexed head: msgSender, 5 string offsets, startingTick, poolHook, poolId,
    pairedToken, locker, mevModule, extensionsSupply, extensions offset)."""
    ws = abi.words(log["data"])
    return {"token": abi.addr(log["topics"][1]), "admin": abi.addr(log["topics"][2]), "msg_sender": abi.addr(ws[0]),
            "name": abi.dyn_string(ws, 2), "symbol": abi.dyn_string(ws, 3), "starting_tick": abi.i256(ws[6]),
            "hook": abi.addr(ws[7]), "pool_id": "0x" + ws[8], "pair": abi.addr(ws[9]), "locker": abi.addr(ws[10]),
            "mev_module": abi.addr(ws[11]), "extensions_supply": abi.u256(ws[12]),
            "extensions": abi.dyn_addresses(ws, 13)}


def launch(ad, token, st, first):
    """Launch facts from the creation transaction (first transfer = the factory's mint)."""
    logs = ad.receipts([first["tx"]], {first["tx"]: first["block"]})[first["tx"]]
    created = next((decode_created(l) for l in logs if l["address"].lower() == st["factory"]
                    and (l.get("topics") or [""])[0].lower() == TOKEN_CREATED
                    and abi.addr(l["topics"][1]) == token), None)
    if created is None:
        return None
    pm = ad.net.v4_pool_manager
    key = None
    for l in logs:
        if l["address"].lower() == pm and (l.get("topics") or [""])[0].lower() == INITIALIZE \
                and token in (abi.addr(l["topics"][2]), abi.addr(l["topics"][3])):
            ws = abi.words(l["data"])
            key = v4.pool_key(abi.addr(l["topics"][2]), abi.addr(l["topics"][3]), abi.u256(ws[0]), abi.i256(ws[1]),
                              abi.addr(ws[2]))
            break
    if key is None:   # Initialize not in this tx (should not happen for v4): standard Clanker key
        c0, c1 = sorted([token, created["pair"]], key=lambda a: int(a, 16))
        key = v4.pool_key(c0, c1, v4.DYNAMIC_FEE_FLAG, 200, created["hook"])
    infra = {st["factory"], st["locker"], created["locker"], created["hook"], created["mev_module"],
             *st["extensions"], *created["extensions"]} - {abi.ZERO}
    # dev buy in the creation tx: tokens leave the PoolManager (directly or via the dev-buy extension) to a wallet →
    # that wallet is the deployer (as for Pons: whoever got the dev buy); otherwise the token admin
    src = {pm} | infra
    dev_buy = next((p["to"] for l in logs if l["address"].lower() == token and (p := ad._parse(l))
                    and p["frm"] in src and p["to"] not in src and p["to"] != pm), None)
    return {"block": first["block"], "tx": first["tx"], "deployer": dev_buy or created["admin"],
            "admin": created["admin"], "pair": created["pair"], "pool_key": key, "token_is0": key["currency0"] == token,
            "pool_id": created["pool_id"], "market": sorted({pm} | set(ad.net.routers)), "infra": sorted(infra),
            "created": created, "curve": pm}


def liquidity(ad, token, launch, circulating, balances):
    """Impact curve from V4Quoter quotes on the token's pool; reserve = tokens in the PoolManager (display only)."""
    pm = ad.net.v4_pool_manager
    reserve = max(0, balances.get(pm, 0))
    curve = []
    if circulating > 0:
        qs = v4.sell_amounts(circulating)
        try:
            raw = ad.client.call("eth_call", v4.quote_call(ad.net.v4_quoter, ad.net.multicall3, launch["pool_key"],
                                                           launch["token_is0"], qs))
            curve = v4.impact_curve(qs, v4.decode_try_aggregate(raw))
        except Exception as e:   # quotes unavailable: impact unknown, not zero
            print(f"clanker: quotes failed for {token} on {ad.CHAIN}: {type(e).__name__}", flush=True)
    return {"reserve": reserve, "reserve_ok": bool(curve), "impact_curve": curve or None,
            "venues": [{"model": "quote_curve", "curve": curve, "label": "Clanker v4 pool (V4Quoter)",
                        "address": launch.get("pool_id")}] if curve else []}


def buy_quote(ad, token, logs, t, launch):
    return ad.v4_buy_quote(token, logs, t, launch)


def info(ad, token, launch):
    c = launch["created"]
    key = launch["pool_key"]
    return {"factory": FACTORIES[ad.CHAIN], "pool_id": launch["pool_id"], "pool_manager": ad.net.v4_pool_manager,
            "hook": c["hook"], "locker": c["locker"], "mev_module": c["mev_module"], "pair": c["pair"],
            "fee": "dynamic" if key["fee"] & v4.DYNAMIC_FEE_FLAG else key["fee"], "tick_spacing": key["tick_spacing"],
            "starting_tick": c["starting_tick"], "admin": c["admin"], "msg_sender": c["msg_sender"],
            "extensions": c["extensions"], "extensions_supply": c["extensions_supply"]}
