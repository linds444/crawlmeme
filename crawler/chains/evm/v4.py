"""Uniswap V4 sell-impact quotes for concentrated-liquidity pools (Clanker, Bankr), standard library only.

Why quotes and not x·y=k: V4 launch pools hold single-sided liquidity in tick ranges, so the token balance of the
PoolManager says nothing about how far a sale moves the price. The V4Quoter simulates the exact swap (hooks and
dynamic fees included). The impact of selling q is the marginal price after the sale vs before it:
  drop(q) = 1 − p(q) / p0,  p(q) = (out(q·1.01) − out(q)) / (0.01·q)
quoted on a grid of q (shares of circulating supply) in one Multicall3.tryAggregate eth_call.
A quote that reverts with NotEnoughLiquidity(poolId) means the pool cannot absorb that sale at all (single-sided
pool, little quote liquidity bought in yet): from that q on the drop is 1.0 — never skipped as "no data".
"""
from crawler.chains.evm import abi
from crawler.chains.evm.keccak import selector

SEL_QUOTE = selector("quoteExactInputSingle(((address,address,uint24,int24,address),bool,uint128,bytes))")   # 0xaa9d21cb
SEL_TRY_AGGREGATE = selector("tryAggregate(bool,(address,bytes)[])")                                       # 0xbce38bd7
ERR_NOT_ENOUGH_LIQUIDITY = selector("NotEnoughLiquidity(bytes32)")                                           # 0x7a5ed734
ERR_UNEXPECTED_REVERT = selector("UnexpectedRevertBytes(bytes)")                                             # 0x6190b2b0
DYNAMIC_FEE_FLAG = 0x800000
GRID = (0.0005, 0.001, 0.002, 0.0035, 0.005, 0.0075, 0.01, 0.015, 0.02, 0.03, 0.05, 0.075, 0.1, 0.15, 0.2,
        0.3, 0.4, 0.5)    # shares of circulating supply to quote
REF = 1e-5                # reference sale for the price before selling
STEP = 101                # q × 101 / 100 for the marginal price
GAS = 150_000_000         # for the whole multicall (hooks can be expensive)


def pool_key(currency0, currency1, fee, tick_spacing, hooks):
    return {"currency0": currency0.lower(), "currency1": currency1.lower(), "fee": fee, "tick_spacing": tick_spacing,
            "hooks": hooks.lower()}


def quote_data(key, zero_for_one, amount):
    """V4Quoter.quoteExactInputSingle calldata (hookData empty)."""
    ws = [0x20, int(key["currency0"], 16), int(key["currency1"], 16), key["fee"], key["tick_spacing"] % (1 << 256),
          int(key["hooks"], 16), int(zero_for_one), amount, 0x100, 0]
    return SEL_QUOTE + "".join(f"{w:064x}" for w in ws)


def encode_try_aggregate(calls):
    """Multicall3.tryAggregate(false, (address target, bytes callData)[]) calldata."""
    n = len(calls)
    heads, tails, off = [], [], 32 * n
    for target, data in calls:
        b = bytes.fromhex(data[2:])
        el = abi.enc_addr(target) + f"{64:064x}" + f"{len(b):064x}" + b.hex().ljust(((len(b) + 31) // 32) * 64, "0")
        heads.append(f"{off:064x}")
        tails.append(el)
        off += len(el) // 2
    return SEL_TRY_AGGREGATE + f"{0:064x}" + f"{64:064x}" + f"{n:064x}" + "".join(heads) + "".join(tails)


def decode_try_aggregate(res):
    """tryAggregate result → [(success, returnData hex)] in call order."""
    h = res[2:]
    word = lambda i: int(h[64 * i:64 * i + 64], 16)
    arr = word(0) // 32
    n = word(arr)
    out = []
    for k in range(n):
        el = arr + 1 + word(arr + 1 + k) // 32
        ok, off = word(el), word(el + 1) // 32
        ln = word(el + off)
        out.append((bool(ok), "0x" + h[64 * (el + off + 1):64 * (el + off + 1) + 2 * ln]))
    return out


def _not_enough_liquidity(ret):
    """Revert data is NotEnoughLiquidity, bare or wrapped by the quoter in UnexpectedRevertBytes(bytes)."""
    r = (ret or "").lower()
    if r.startswith(ERR_NOT_ENOUGH_LIQUIDITY):
        return True
    return r.startswith(ERR_UNEXPECTED_REVERT) and ERR_NOT_ENOUGH_LIQUIDITY[2:] in r[10:10 + 64 * 3 + 8]


def sell_amounts(circulating):
    """[q] to quote: REF and every GRID point, each followed by q × STEP / 100."""
    qs = []
    for f in (REF,) + GRID:
        q = max(1, int(circulating * f))
        qs += [q, q * STEP // 100]
    return qs


def quote_call(quoter, multicall, key, token_is0, qs):
    """eth_call params quoting the sale of every q in qs (token → pair)."""
    calls = [(quoter, quote_data(key, token_is0, q)) for q in qs]
    return [{"to": multicall, "data": encode_try_aggregate(calls), "gas": hex(GAS)}, "latest"]


def impact_curve(qs, results, out_word=0):
    """[(q, drop)] ascending by q from tryAggregate results (decode_try_aggregate output); [] — price before the sale
    unknown (reference quote failed for another reason). Monotonic: selling more never gives a higher price.
    out_word — index of the amount-out word in each quote result (V4Quoter: 0; four.meme trySell: 2)."""
    if not results or len(results) != len(qs):
        return []
    vals = []
    for ok, ret in results:
        if ok:
            ws = abi.words(ret)
            vals.append(abi.u256(ws[out_word]) if len(ws) > out_word else None)
        else:
            vals.append("dry" if _not_enough_liquidity(ret) else None)
    prices = []
    for i in range(0, len(qs) - 1, 2):
        a, b = vals[i], vals[i + 1]
        if "dry" in (a, b):
            prices.append((qs[i], "dry"))
        elif a is not None and b is not None and b >= a:
            prices.append((qs[i], (b - a) / (qs[i + 1] - qs[i])))
        else:
            prices.append((qs[i], None))
    p0 = prices[0][1]
    if p0 == "dry":
        return [(q, 1.0) for q, _ in prices[1:]]      # cannot even sell the reference amount
    if not p0:
        return []
    out, worst = [], 0.0
    for q, p in prices[1:]:
        if p == "dry":
            worst = 1.0
        elif p is None:
            continue
        else:
            worst = max(worst, min(1.0, max(0.0, 1 - p / p0)))
        out.append((q, worst))
    return out
