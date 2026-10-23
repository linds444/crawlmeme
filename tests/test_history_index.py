"""Resumable token history index (crawler/chains/evm/history_index.py + EvmTokenAdapter.token_transfers).
A fake node serves a synthetic ERC-20 history; no network."""
import json, os, tempfile, time, unittest
from unittest import mock

import fakes  # noqa: F401
from crawler.chains.evm import abi, history_index
from crawler.chains.evm.client import EvmClient, Provider
from crawler.chains.evm.networks import NETWORKS
from crawler.chains.evm.token import EvmTokenAdapter, HistoryIndexing, conservation

TOKEN = "0x" + "77" * 20
SUPPLY = 10 ** 27
W = ["0x" + f"{i:02x}" * 20 for i in range(1, 9)]


def word(x):
    return "0x" + format(x, "064x")


class Chain:
    """Synthetic token: mint to W[0] at block 1000, then a transfer every 10 blocks up to the head."""

    def __init__(self, head=3000, extra_supply=0):
        self.head, self.extra_supply = head, extra_supply
        self.logs, self.calls = [], []
        self.add(1000, abi.ZERO, W[0], SUPPLY)
        for i, b in enumerate(range(1010, 2900, 10)):
            self.add(b, W[0], W[(i % 7) + 1], 10 ** 18 * (i + 1))   # always from the minted holder: no overdraft

    def add(self, block, frm, to, amount):
        self.logs.append({"address": TOKEN, "blockNumber": hex(block), "logIndex": hex(len(self.logs)),
                          "transactionHash": "0x" + format(len(self.logs), "064x"),
                          "topics": [abi.TRANSFER_TOPIC, abi.topic_for(frm), abi.topic_for(to)], "data": word(amount)})

    def answer(self, q):
        m, p = q["method"], q.get("params") or []
        self.calls.append((m, p))
        if m == "eth_blockNumber":
            return {"result": hex(self.head)}
        if m == "eth_call":
            data = p[0]["data"]
            if data.startswith("0x70a08231"):        # balanceOf(holder) at the requested block: from the true history
                who = "0x" + data[-40:]
                at = int(p[1], 16) if p[1] != "latest" else self.head
                bal = 0
                for l in self.logs:
                    if int(l["blockNumber"], 16) <= at:
                        amt = int(l["data"], 16)
                        bal += amt if abi.addr(l["topics"][2]) == who else 0
                        bal -= amt if abi.addr(l["topics"][1]) == who else 0
                return {"result": word(bal)}
            return {"result": word(SUPPLY + self.extra_supply)}
        if m == "eth_getLogs":
            lo, hi = int(p[0]["fromBlock"], 16), int(p[0]["toBlock"], 16)
            if hi - lo + 1 > 500:
                return {"error": {"code": -32602, "message": "but only 500 are allowed for this request"}}
            return {"result": [l for l in self.logs if lo <= int(l["blockNumber"], 16) <= hi]}
        return {"error": {"code": -32601, "message": "method not found"}}

    def __call__(self, url, body, timeout):
        q = json.loads(body)
        out = [{"jsonrpc": "2.0", "id": x["id"]} | self.answer(x) for x in q] if isinstance(q, list) else \
            {"jsonrpc": "2.0", "id": q["id"]} | self.answer(q)
        return 200, json.dumps(out).encode()

    def log_ranges(self):
        return [(int(p[0]["fromBlock"], 16), int(p[0]["toBlock"], 16)) for m, p in self.calls if m == "eth_getLogs"]


class TestHistoryIndex(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.env = mock.patch.dict(os.environ, {"LOCAL_DB_PATH": os.path.join(self.tmp.name, "crawl.db"),
                                                "HISTORY_INDEX": ""})
        self.env.start()
        self.addCleanup(self.env.stop)

    def adapter(self, chain):
        c = EvmClient("bsc", [Provider("fake", "https://fake.test", rps=0, max_span=500)], transport=chain,
                      bg_rps=0, crit_rps=0)
        return EvmTokenAdapter(NETWORKS["bsc"], c, plugin=type("P", (), {"NAME": "test"})), c

    def test_full_read_then_tail_only(self):
        chain = Chain()
        ad, c = self.adapter(chain)
        tr, info = ad.token_transfers(TOKEN, 1000)
        self.assertTrue(info["verified"])
        self.assertIsNone(info["cached_until"])
        self.assertEqual(len(tr), len(chain.logs))
        chain.calls.clear()
        chain.head = 3200
        chain.add(3100, W[1], W[5], 7)
        c._head = (0.0, None)
        tr2, info2 = ad.token_transfers(TOKEN, 1000)
        ranges = chain.log_ranges()
        self.assertEqual(min(a for a, _ in ranges), 3000 - history_index.REORG_LAG + 1)   # the tail (+ re-read lag)
        self.assertEqual(len(tr2), len(chain.logs))
        self.assertEqual(info2["cached_until"], 3000 - history_index.REORG_LAG)

    def test_budget_cut_resumes_to_identical_history(self):
        chain = Chain()
        ad, c = self.adapter(chain)
        clock = [0.0]
        windows = [0]
        real = c.iter_log_windows

        def slow(*a, **k):   # every window "takes" 1 s of the budget
            for x in real(*a, **k):
                windows[0] += 1
                clock[0] += 1.0
                yield x
        with mock.patch.object(c, "iter_log_windows", side_effect=slow), \
                mock.patch("crawler.chains.evm.token.time.time", side_effect=lambda: clock[0]):
            with self.assertRaises(HistoryIndexing) as cm:
                ad.token_transfers(TOKEN, 1000, until=2.0)        # two windows fit
        self.assertGreater(cm.exception.progress, 0)
        self.assertLess(cm.exception.progress, 1)
        saved = history_index.get().load("bsc", TOKEN)
        self.assertFalse(saved["complete"])
        self.assertEqual(saved["head"], 1999)                       # checkpoint at a window end (500-block windows)
        tr, info = ad.token_transfers(TOKEN, 1000)                 # next scan: resumes and completes
        self.assertTrue(info["verified"])
        self.assertEqual(sorted((t["tx"], t["log_index"]) for t in tr),
                         sorted((l["transactionHash"], int(l["logIndex"], 16)) for l in chain.logs))

    def test_broken_cache_is_reread(self):
        chain = Chain()
        ad, _ = self.adapter(chain)
        tr, _ = ad.token_transfers(TOKEN, 1000)
        idx = history_index.get()
        idx.save("bsc", TOKEN, 1000, 3000, tr[:-5], complete=True, verified=True)   # cache lost 5 transfers
        chain.calls.clear()
        tr2, info = ad.token_transfers(TOKEN, 1000)
        self.assertTrue(info["verified"])
        self.assertEqual(len(tr2), len(chain.logs))
        self.assertEqual(min(a for a, _ in chain.log_ranges()), 1000)                   # full re-read

    def test_unverified_token_is_flagged(self):
        """Balances that do not add up to totalSupply (rebasing / reflection / missing logs) are never trusted."""
        ad, _ = self.adapter(Chain(extra_supply=123))
        _, info = ad.token_transfers(TOKEN, 1000)
        self.assertFalse(info["verified"])
        self.assertEqual(info["conservation"]["supply"] - info["conservation"]["held"], 123)

    def test_lost_holder_transfers_caught_by_balance_check(self):
        """Losing transfers between holders keeps the sum = supply; the balanceOf check catches it."""
        chain = Chain()
        ad, _ = self.adapter(chain)
        tr, _ = ad.token_transfers(TOKEN, 1000)
        check = ad.check_balances(TOKEN, tr[:-5], chain.head)
        self.assertTrue(conservation(tr[:-5], SUPPLY)["ok"])          # the sum alone would have passed
        self.assertFalse(check["balances_ok"])
        self.assertTrue(check["mismatches"])

    def test_conservation_negative_balance(self):
        t = [{"frm": W[0], "to": W[1], "amount": 5, "tx": "0x1", "block": 1, "log_index": 0}]
        self.assertFalse(conservation(t, 0)["ok"])

    def test_disabled_index(self):
        with mock.patch.dict(os.environ, {"HISTORY_INDEX": "off"}):
            self.assertIsNone(history_index.get())

    def test_eviction_keeps_latest(self):
        with mock.patch.dict(os.environ, {"INDEX_MAX_MB": "1"}):
            idx = history_index.get()
            big = [{"frm": "0x" + os.urandom(20).hex(), "to": "0x" + os.urandom(20).hex(), "amount": i,
                    "tx": "0x" + os.urandom(32).hex(), "block": i, "log_index": 0} for i in range(12000)]
            idx.save("bsc", "0xold", 0, 1, big, True, True)
            idx.save("bsc", "0xnew", 0, 1, big, True, True)
            self.assertIsNone(idx.load("bsc", "0xold"))
            self.assertIsNotNone(idx.load("bsc", "0xnew"))


if __name__ == "__main__":
    unittest.main()


class TestEngineIndexing(unittest.TestCase):

    def test_indexing_is_a_clear_scan_error(self):
        from crawler import engine
        err = HistoryIndexing(TOKEN, 0.42, 12345)
        with mock.patch.object(engine, "chain_of", return_value=("bsc", TOKEN)), \
                mock.patch.object(engine.market, "fetch_market", return_value={}), \
                mock.patch.object(engine.market, "established_get", return_value=None), \
                mock.patch.object(engine.registry, "detect",
                                  return_value=(mock.Mock(LAUNCHPAD="x", FACTS_DEADLINE=True,
                                                          get_launch=lambda t: {"block": 1},
                                                          token_facts=mock.Mock(side_effect=err),
                                                          REQUESTS=[0], RPS=8), {})):
            with self.assertRaisesRegex(engine.ScanError, r"42% read so far \(12,345 transfers\)"):
                engine.scan(TOKEN, chain="bsc")
