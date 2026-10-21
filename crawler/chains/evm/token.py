"""Generic EVM token adapter: the engine's adapter contract for any ERC-20 on any configured EVM network, with the
launchpad-specific parts (how the token launched, where its liquidity is, how a buy looks) delegated to a launchpad
plugin (crawler/chains/evm/launchpads/*). Read-only: no keys, no signing.

Same facts and semantics as the Robinhood Pons adapter (chains/robinhood.py), so detect.py and the scoring are
unchanged; differences are only in how data is fetched, chosen per provider capability (evm/client.py):
  launch block   — Alchemy Transfers API (first transfer of the token) if available, otherwise a binary search of
                   eth_getCode over an archive provider, otherwise inside the recent window of a public node
  token history  — eth_getLogs where a provider covers [launch, head], otherwise alchemy_getAssetTransfers;
                   neither → HistoryUnavailable (explicit: the scan does not pretend to have the full history)
  wallet history — address-less eth_getLogs where allowed (Robinhood), otherwise alchemy_getAssetTransfers.
                   Definition (EVM-generic): distinct ERC-20 contracts the wallet sent or received before entry,
                   excluding the scanned token, wrapped native and stablecoins, zero-value transfers excluded.
                   Airdrop spam can only raise the count (fewer wallets flagged virgin), never fake a virgin.

Plugin interface (module or object):
  NAME; detect(ad, token) → state | None (cheap, current state);
  launch(ad, token, state, first) → launch dict (block, tx, deployer, pair, market, infra, … plugin fields);
  liquidity(ad, token, launch, circulating) → {"reserve", "reserve_ok", "impact_curve"?, "q_factor"?};
  buy_quote(ad, token, logs, transfer, launch) → (is_buy, quote_in | None);
  info(ad, token, launch) → dict for the result (pool, fees, curve progress, …).
"""
import os, threading

from crawler.chains import priority
from crawler.chains.evm import abi
from crawler.chains.evm.client import NoProvider
from crawler.chains.evm.keccak import topic

DELEGATION_PREFIX = "0xef0100"            # EIP-7702 delegation code: still a wallet, not a contract
V4_SWAP = topic("Swap(bytes32,address,int128,int128,uint160,uint128,int24,uint24)")
SCAN_MAX_LOGS = 150_000
HIST_TX_CAP = 400                         # wallet transfer rows read per direction for the history count
CACHE_MAX = 50_000
ENTRY_TOL = 0.06                          # buy amount vs swap side tolerance (fees, router tax)


class HistoryUnavailable(Exception):
    """No configured provider can read this token's full transfer history (e.g. older than a public node's window and
    no ALCHEMY_API_KEY). The scan reports it instead of scoring a partial history as if it were complete."""


class HistoryTooLarge(Exception):
    """Token history longer than SCAN_MAX_LOGS transfers."""


try:
    SCAN_MAX_LOGS = max(0, int(os.environ.get("SCAN_MAX_LOGS", "").strip() or SCAN_MAX_LOGS))
except ValueError:
    pass


class EvmTokenAdapter:
    PACK_WINDOW = 0           # pack: same block (as on Robinhood; BNB/Base blocks are 0.75–2 s)
    BUNDLE_WINDOW = 0         # launch bundle: entry in the launch block
    RUG_SNIPERS = True
    HISTORY_PARALLEL = 4
    FACTS_DEADLINE = True     # token_facts(token, launch, deadline)

    def __init__(self, net, client, plugin):
        self.net, self.client, self.plugin = net, client, plugin
        self.CHAIN = net.key
        self.LAUNCHPAD = plugin.NAME
        self.REQUESTS, self.RATE_LIMITED = client.REQUESTS, client.RATE_LIMITED
        self.RPS = max((p.rps for p in client.providers), default=8)
        self._launch, self._state, self._ts, self._receipts, self._code = {}, {}, {}, {}, {}
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ identification
    def detect(self, token):
        token = token.lower()
        st = self.plugin.detect(self, token)
        if st is not None:
            self._state[token] = st
        return st

    def get_launch(self, token):
        token = token.lower()
        if token in self._launch:
            return dict(self._launch[token])
        st = self._state.get(token) or self.detect(token)
        if st is None:
            return None
        first = self.first_transfer(token)
        if first is None:
            raise HistoryUnavailable(f"{self.net.name}: cannot locate the launch of {token} with the configured RPC "
                                     f"providers (set ALCHEMY_API_KEY or {self.CHAIN.upper()}_RPC_URLS with an "
                                     f"archive node)")
        launch = self.plugin.launch(self, token, st, first)
        if launch is None:
            return None
        launch.setdefault("launchpad", self.plugin.NAME)
        with self._lock:
            self._launch[token] = launch
        return dict(launch)

    # ------------------------------------------------------------------ low-level reads
    def call(self, to, data):
        return self.client.eth_call(to, data)

    def batch_calls(self, calls):
        """[(to, data)] → results (None for reverted items)."""
        return self.client.batch([("eth_call", [{"to": t, "data": d}, "latest"]) for t, d in calls])

    def block_number(self):
        return self.client.head()

    def token_supply(self, token):
        return abi.u256(self.call(token, "0x18160ddd"))

    def token_meta(self, token):
        name, sym = self.batch_calls([(token, "0x06fdde03"), (token, "0x95d89b41")])
        return {"name": abi.string_result(name), "symbol": abi.string_result(sym)}

    def block_timestamps(self, blocks, chunk=100):
        blocks = sorted(set(b for b in blocks if b is not None))
        out = {b: self._ts[b] for b in blocks if b in self._ts}
        need = [b for b in blocks if b not in out]
        for i in range(0, len(need), chunk):
            part = need[i:i + chunk]
            res = self.client.batch([("eth_getBlockByNumber", [hex(b), False]) for b in part],
                                    need={"from_block": min(part)})
            for b, blk in zip(part, res):
                if blk:
                    out[b] = int(blk["timestamp"], 16)
        if len(self._ts) > CACHE_MAX:
            self._ts.clear()
        self._ts.update(out)
        return out

    def receipts(self, txs, blocks=None, chunk=50):
        """{tx: receipt logs}; blocks — {tx: block} so archive-limited providers are skipped for old receipts."""
        out = {h: self._receipts[h] for h in txs if h in self._receipts}
        need = [h for h in txs if h not in out]
        for i in range(0, len(need), chunk):
            part = need[i:i + chunk]
            lo = min((blocks or {}).get(h, 0) for h in part) if blocks else 0
            res = self.client.batch([("eth_getTransactionReceipt", [h]) for h in part], need={"from_block": lo})
            for h, rc in zip(part, res):
                out[h] = (rc or {}).get("logs", [])
                if rc:
                    if len(self._receipts) > CACHE_MAX:
                        self._receipts.clear()
                    self._receipts[h] = out[h]
        return out

    def is_contract(self, addresses, chunk=100):
        addrs = {a.lower() for a in addresses}
        need = sorted(addrs - self._code.keys())
        for i in range(0, len(need), chunk):
            part = need[i:i + chunk]
            for a, code in zip(part, self.client.batch([("eth_getCode", [a, "latest"]) for a in part])):
                if code is not None:
                    code = code.lower()
                    self._code[a] = code not in ("0x", "0x0", "") and not code.startswith(DELEGATION_PREFIX)
        return {a: self._code.get(a, False) for a in addrs}

    # ------------------------------------------------------------------ launch location
    def _has_asset_transfers(self):
        return any(p.asset_transfers for p in self.client.providers)

    def first_transfer(self, token):
        """{"block", "tx", "frm", "to", "amount", "log_index"} of the token's first Transfer (the mint at creation),
        or None when no configured provider can see it. Sources, cheapest first:
          1. Alchemy Transfers API: first transfer of the contract (one request)
          2. a provider with full log history (Robinhood public node): earliest Transfer log, scanned left-first
          3. a provider with full historical state: binary search of eth_getCode, then the logs of that block
          4. the recent window of a public node: a mint from the zero address inside the window (fresh tokens)"""
        if self._has_asset_transfers():
            try:
                rows, _ = self.client.asset_transfers(max_count=1, contractAddresses=[token], fromBlock=0,
                                                      toBlock="latest", order="asc")
                if rows:
                    b = int(rows[0]["blockNum"], 16)
                    return self._first_log_in(token, b, b) or self._row_transfer(rows[0])
            except NoProvider:
                pass
        head = self.client.head()
        if any(p.archive_depth is None for p in self.client.providers):
            try:
                return self._first_log_in(token, 0, head)
            except NoProvider:
                pass
        if any(p.state_depth is None for p in self.client.providers):
            b = self.creation_block(token)
            if b is not None:
                return self._first_log_in(token, b, b)
        depth = max((p.archive_depth or 0 for p in self.client.providers), default=0)
        if depth:
            first = self._first_log_in(token, max(0, head - depth), head)
            if first and first["frm"] == abi.ZERO:
                return first
        return None

    def creation_block(self, token):
        """First block where the token has code: binary search of eth_getCode at historical blocks (full archive)."""
        def has_code(b):
            code = self.client.call("eth_getCode", [token, hex(b)], need={"state_block": b})
            return code not in (None, "0x", "0x0", "")

        lo, hi = 0, self.client.head()
        try:
            if not has_code(hi):
                return None
            while lo < hi:
                mid = (lo + hi) // 2
                if has_code(mid):
                    hi = mid
                else:
                    lo = mid + 1
        except NoProvider:
            return None
        return lo

    def _first_log_in(self, token, lo, hi):
        for page in self.client.iter_logs(lo, hi, address=token, topics=[abi.TRANSFER_TOPIC]):
            for lg in page:
                t = self._parse(lg)
                if t:
                    return t
        return None

    @staticmethod
    def _parse(lg):
        tp = lg.get("topics") or []
        if len(tp) < 3 or tp[0].lower() != abi.TRANSFER_TOPIC or len(lg.get("data") or "0x") <= 2:
            return None
        t = {"frm": abi.addr(tp[1]), "to": abi.addr(tp[2]), "amount": int(lg["data"], 16), "tx": lg["transactionHash"],
             "block": int(lg["blockNumber"], 16), "log_index": int(lg.get("logIndex") or "0x0", 16)}
        if lg.get("blockTimestamp"):
            t["ts"] = int(lg["blockTimestamp"], 16)
        return t

    @staticmethod
    def _row_transfer(r):
        """alchemy_getAssetTransfers row → transfer dict (log index from uniqueId "<tx>:log:<n>")."""
        uid = r.get("uniqueId") or ""
        try:
            li = int(uid.rsplit(":", 1)[1])
        except (IndexError, ValueError):
            li = 0
        return {"frm": (r.get("from") or abi.ZERO).lower(), "to": (r.get("to") or abi.ZERO).lower(),
                "amount": int((r.get("rawContract") or {}).get("value") or "0x0", 16), "tx": r["hash"],
                "block": int(r["blockNum"], 16), "log_index": li}

    # ------------------------------------------------------------------ token history
    def _logs_cover(self, lo):
        head = self.client.head()
        return any(p.archive_depth is None or lo >= head - p.archive_depth for p in self.client.providers)

    def token_transfers(self, token, from_block, to_block=None, cap=SCAN_MAX_LOGS):
        """All transfers of the token in [from_block, to_block] in chain order (see module doc for the source)."""
        hi = self.client.head() if to_block is None else to_block
        out = []
        if self._logs_cover(from_block):
            try:
                for page in self.client.iter_logs(from_block, hi, address=token, topics=[abi.TRANSFER_TOPIC]):
                    for lg in page:
                        t = self._parse(lg)
                        if t:
                            out.append(t)
                    if cap and len(out) > cap:
                        raise HistoryTooLarge(len(out))
                out.sort(key=lambda t: (t["block"], t["log_index"]))
                return out
            except NoProvider:
                out = []
        if not self._has_asset_transfers():
            raise HistoryUnavailable(f"{self.net.name}: the token history starts at block {from_block}, older than "
                                     f"the configured RPC providers serve; set ALCHEMY_API_KEY or "
                                     f"{self.CHAIN.upper()}_RPC_URLS with an archive node")
        rows, more = self.client.asset_transfers(max_count=(cap + 1) if cap else None, contractAddresses=[token],
                                                 fromBlock=from_block, toBlock=hi, order="asc")
        if more or (cap and len(rows) > cap):
            raise HistoryTooLarge(len(rows))
        out = [self._row_transfer(r) for r in rows]
        out.sort(key=lambda t: (t["block"], t["log_index"]))
        return out

    def token_facts(self, token, launch, deadline=None):
        token = token.lower()
        transfers = self.token_transfers(token, launch["block"])
        supply = self.token_supply(token)
        excluded = set(launch["infra"]) | set(launch["market"]) | set(self.net.burn)
        bal = {}
        for t in transfers:
            bal[t["frm"]] = bal.get(t["frm"], 0) - t["amount"]
            bal[t["to"]] = bal.get(t["to"], 0) + t["amount"]
        circulating = sum(v for a, v in bal.items() if v > 0 and a not in excluded)
        liq = self.plugin.liquidity(self, token, launch, circulating, bal)
        facts = {"supply": supply, "transfers": transfers, "excluded": excluded, "market": set(launch["market"]),
                 "base": None, "reserve": liq.get("reserve", 0), "reserve_ok": liq.get("reserve_ok", False),
                 "launchpad": self.plugin.NAME, self.plugin.NAME: self.plugin.info(self, token, launch)}
        for k in ("impact_curve", "q_factor", "locked", "dev"):
            if liq.get(k) is not None:
                facts[k] = liq[k]
        return facts

    # ------------------------------------------------------------------ entries
    def classify_entries(self, token, transfers, wallets, chunk=50):
        """First receipt of the token by each wallet: buy (the launchpad plugin recognises a market purchase in the
        transaction receipt) or transfer. Same output as robinhood.classify_entries."""
        token = token.lower()
        want = {w.lower() for w in wallets}
        first = {}
        for t in transfers:
            if t["to"] in want and t["to"] not in first:
                first[t["to"]] = t
        launch = self._launch.get(token) or {}
        rcpt = self.receipts(sorted({t["tx"] for t in first.values()}),
                             {t["tx"]: t["block"] for t in first.values()}, chunk)
        out = {}
        for w, t in first.items():
            is_buy, quote = self.plugin.buy_quote(self, token, rcpt.get(t["tx"], []), t, launch)
            out[w] = {"kind": "buy" if is_buy else "transfer", "tx": t["tx"], "block": t["block"], "via": t["frm"],
                      "eth_in": quote if is_buy else None}
        return out

    def v4_buy_quote(self, token, logs, t, launch):
        """Shared helper for V4 launch pools: the token came out of the PoolManager in a Swap whose token side matches
        the wallet's receipt → buy; quote side = native in if the pair is wrapped native."""
        pm = self.net.v4_pool_manager
        moves = [p for l in logs if (p := self._parse(l)) and l["address"].lower() == token and p["frm"] == pm]
        if not moves:
            return False, None
        best, err = None, ENTRY_TOL
        for l in logs:
            if l["address"].lower() != pm or (l.get("topics") or [""])[0].lower() != V4_SWAP:
                continue
            ws = abi.words(l["data"])
            a0, a1 = abs(abi.i256(ws[0])), abs(abi.i256(ws[1]))
            for tok_side, quote_side in ((a0, a1), (a1, a0)):
                for m in moves:
                    if tok_side and abs(tok_side - m["amount"]) / m["amount"] < err:
                        err, best = abs(tok_side - m["amount"]) / m["amount"], (m["amount"], quote_side)
        if best is None:
            return True, None          # token from the pool, swap not decoded (another pool / hook flow)
        native = launch.get("pair") == self.net.wrapped_native
        ok = abs(best[0] - t["amount"]) <= ENTRY_TOL * t["amount"]
        return True, best[1] if native and ok else None

    # ------------------------------------------------------------------ wallet history
    def wallet_distinct_tokens(self, wallet, before_block, skip_token, window=None, cap=4):
        """min(n, cap) distinct ERC-20 contracts in the wallet's transfers before entry (module doc); None — not read."""
        w, skip = wallet.lower(), skip_token.lower()
        if self.CHAIN == "robinhood":
            # same definition as Pons tokens (trades with routers / curves / pools, tested in chains/robinhood.py),
            # so virgin and short-history mean the same for every Robinhood token
            from crawler.chains import robinhood
            return robinhood.wallet_distinct_tokens(w, before_block, skip, window=window, cap=cap)
        ignore = {skip, self.net.wrapped_native} | set(self.net.stables)
        seen = set()
        if any(p.addressless and (p.archive_depth is None) for p in self.client.providers):
            wt = abi.topic_for(w)
            hi = before_block - 1
            lo = 0 if window is None else max(0, before_block - window)
            for tp in ([abi.TRANSFER_TOPIC, wt], [abi.TRANSFER_TOPIC, None, wt]):
                # newest first: stop as soon as cap is reached
                step = 200_000
                top = hi
                while top >= lo and len(seen) < cap:
                    b = max(lo, top - step + 1)
                    for page in self.client.iter_logs(b, top, address=None, topics=tp):
                        seen.update(lg["address"].lower() for lg in page if len(lg.get("data") or "0x") > 2
                                    and int(lg["data"], 16) > 0)
                        seen -= ignore
                    top = b - 1
                if len(seen) >= cap:
                    break
            return min(len(seen), cap)
        if not self._has_asset_transfers():
            return None
        for side in ("fromAddress", "toAddress"):
            rows, _ = self.client.asset_transfers(max_count=HIST_TX_CAP, **{side: w}, fromBlock=0,
                                                  toBlock=max(0, before_block - 1), order="desc",
                                                  excludeZeroValue=True)
            seen.update((r.get("rawContract") or {}).get("address", "").lower() for r in rows)
            seen -= ignore | {""}
            if len(seen) >= cap:
                break
        return min(len(seen), cap)
