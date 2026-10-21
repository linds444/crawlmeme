"""EVM multi-provider client (crawler/chains/evm/client.py) against a fake transport. No network.
Refusal texts below are the ones real providers returned on 2026-10-21 (see docs/phase-2-chains.md)."""
import json, time, unittest
import unittest.mock

import fakes  # noqa: F401  (sets dummy RPC env, forbids real adapter RPC)
from crawler.chains import priority
from crawler.chains.evm import abi
from crawler.chains.evm.client import EvmClient, NoProvider, Provider, RpcError, classify, span_hint
from crawler.chains.evm.keccak import keccak256, selector, topic


class FakeNode:
    """A provider: answers eth_blockNumber and eth_getLogs over a synthetic log set; can refuse like real nodes."""

    def __init__(self, head=1_000_000, max_span=None, addressless=True, depth=None, down=False, rate_limit=0,
                 span_message=None, cap=None):
        self.head, self.max_span, self.addressless, self.depth = head, max_span, addressless, depth
        self.down, self.rate_limit, self.cap = down, rate_limit, cap
        self.span_message = span_message or "query spans {n} blocks, but only {m} are allowed for this request"
        self.calls = []
        self.logs = [{"blockNumber": hex(b), "address": "0xt", "topics": [abi.TRANSFER_TOPIC], "data": "0x01"}
                     for b in range(head - 50_000, head + 1, 7)]

    def answer(self, req):
        m, params = req["method"], req.get("params") or []
        self.calls.append(m)
        if m == "eth_blockNumber":
            return {"result": hex(self.head)}
        if m == "eth_getLogs":
            f = params[0]
            lo, hi = int(f["fromBlock"], 16), int(f["toBlock"], 16)
            if not self.addressless and "address" not in f:
                return {"error": {"code": -32701, "message": "Please specify an address in your request or, to "
                                                               "remove restrictions, order a dedicated full node"}}
            if self.depth is not None and lo < self.head - self.depth:
                return {"error": {"code": -32602, "message": "Archive requests require a personal token."}}
            if self.max_span and hi - lo + 1 > self.max_span:
                return {"error": {"code": -32602, "message": self.span_message.format(n=hi - lo + 1, m=self.max_span)}}
            out = [lg for lg in self.logs if lo <= int(lg["blockNumber"], 16) <= hi]
            return {"result": out[:self.cap] if self.cap else out}
        if m == "eth_getTransactionReceipt":
            return {"result": {"logs": []}}
        if m == "eth_call":
            if params[0]["data"] == "0xdead":
                return {"error": {"code": 3, "message": "execution reverted"}}
            return {"result": "0x" + "0" * 63 + "1"}
        return {"error": {"code": -32601, "message": "method not found"}}

    def __call__(self, url, body, timeout):
        if self.down:
            raise ConnectionError("refused")
        if self.rate_limit:
            self.rate_limit -= 1
            return 429, b'{"error":"too many requests"}'
        req = json.loads(body)
        if isinstance(req, list):
            return 200, json.dumps([{"jsonrpc": "2.0", "id": r["id"]} | self.answer(r) for r in req]).encode()
        return 200, json.dumps({"jsonrpc": "2.0", "id": req["id"]} | self.answer(req)).encode()


def client(*nodes, **kw):
    """EvmClient over named fake nodes; the transport dispatches on the provider URL."""
    by_url = {f"https://node{i}.test/SECRETKEY": n for i, n in enumerate(nodes)}
    providers = [Provider(f"node{i}", url, rps=0, **kw.get(f"p{i}", {})) for i, url in enumerate(by_url)]
    c = EvmClient("testchain", providers, transport=lambda url, body, t: by_url[url](url, body, t), bg_rps=0, crit_rps=0)
    return c, providers


class TestKeccakAndAbi(unittest.TestCase):

    def test_vectors(self):
        self.assertEqual(keccak256(b"").hex(), "c5d2460186f7233c927e7db2dcc703c0e500b653ca82273b7bfad8045d85a470")
        self.assertEqual(keccak256(b"abc").hex(), "4e03657aea45a94fc7d47ba826c8d667c0d1e6e33a64a036ec44f58fa12d6c45")
        self.assertEqual(len(keccak256(b"x" * 300)), 32)                       # multi-block input

    def test_known_signatures(self):
        self.assertEqual(topic("Transfer(address,address,uint256)"), abi.TRANSFER_TOPIC)
        self.assertEqual(selector("balanceOf(address)"), "0x70a08231")
        self.assertEqual(selector("totalSupply()"), "0x18160ddd")

    def test_decoders(self):
        ws = abi.words("0x" + abi.enc_uint(64) + abi.enc_addr("0x" + "ab" * 20)
                       + abi.enc_uint(3) + b"abc".hex().ljust(64, "0"))
        self.assertEqual(abi.addr(ws[1]), "0x" + "ab" * 20)
        self.assertEqual(abi.dyn_string(ws, 0), "abc")
        self.assertEqual(abi.i256("f" * 64), -1)
        self.assertEqual(abi.call_data("0x70a08231", "0x" + "11" * 20), "0x70a08231" + "0" * 24 + "11" * 20)


class TestClassify(unittest.TestCase):

    def test_real_messages(self):
        cases = {
            "query spans 89337724 blocks (5242880 to 94580603), but only 10000000 are allowed for this request": "span",
            "ranges over 10000 blocks are not supported on free plan": "span",
            "query exceeds max results 20000, retry with the range 52913616-52914145": "span",
            "Under the Free tier plan, you can make eth_getLogs requests with up to a 10 block range.": "span",
            "Please specify an address in your request or, to remove restrictions, order a dedicated full node": "addressless",
            "Archive requests require a personal token. Get one at: https://www.allnodes.com": "archive",
            "request limit reached": "rate",
            "Your app has exceeded its compute units per second capacity": "rate",
            "execution reverted": "fatal",
            "the method alchemy_getAssetTransfers does not exist/is not available": "unsupported",
            "Method not found": "unsupported",
        }
        for msg, kind in cases.items():
            self.assertEqual(classify(-32602, msg), kind, msg)

    def test_span_hints(self):
        self.assertEqual(span_hint("but only 10000000 are allowed"), 10_000_000)
        self.assertEqual(span_hint("ranges over 10000 blocks are not supported"), 10_000)
        self.assertEqual(span_hint("retry with the range 52913616-52914145"), 530)
        self.assertEqual(span_hint("requests with up to a 10 block range"), 10)


class TestRouting(unittest.TestCase):

    def test_failover_on_down_provider(self):
        a, b = FakeNode(down=True), FakeNode()
        c, (pa, pb) = client(a, b)
        self.assertEqual(c.head(), b.head)
        self.assertTrue(pa.cooling(time.time()))
        self.assertGreaterEqual(c.health()["providers"][0]["failovers"], 1)

    def test_rate_limit_retried_then_ok(self):
        a = FakeNode(rate_limit=1)
        c, _ = client(a)
        self.assertEqual(c.head(), a.head)
        self.assertEqual(c.RATE_LIMITED[0], 1)

    def test_deterministic_error_not_failed_over(self):
        a, b = FakeNode(), FakeNode()
        c, _ = client(a, b)
        with self.assertRaises(RpcError):
            c.eth_call("0xt", "0xdead")
        self.assertEqual(b.calls, [])                                          # never asked the second node

    def test_logs_learn_span_from_refusal(self):
        a = FakeNode(max_span=10_000)
        c, (pa,) = client(a)
        logs = c.get_logs(a.head - 49_999, a.head, address="0xt")
        self.assertEqual(len(logs), len([1 for b in range(a.head - 50_000, a.head + 1, 7) if b >= a.head - 49_999]))
        self.assertEqual(pa.max_span, 10_000)
        before = len(a.calls)
        c.get_logs(a.head - 29_999, a.head, address="0xt")                     # planned with the learned span
        self.assertEqual(len(a.calls) - before, 3)

    def test_logs_wide_provider_first_narrow_one_unused(self):
        """Alchemy-Free style 10-block provider second: windows follow the first (wide) provider."""
        wide, narrow = FakeNode(), FakeNode(max_span=10)
        c, _ = client(wide, narrow, p1={"max_span": 10})
        c.get_logs(wide.head - 20_000, wide.head)
        self.assertEqual(narrow.calls, [])
        self.assertLessEqual(wide.calls.count("eth_getLogs"), 2)

    def test_addressless_routed_to_capable_provider(self):
        pub, full = FakeNode(addressless=False), FakeNode()
        c, (ppub, _) = client(pub, full)
        c.get_logs(pub.head - 100, pub.head)                                   # no address
        self.assertFalse(ppub.addressless)                                     # learned from the refusal
        n = pub.calls.count("eth_getLogs")
        c.get_logs(pub.head - 100, pub.head)
        self.assertEqual(pub.calls.count("eth_getLogs"), n)                    # not asked again
        self.assertGreaterEqual(full.calls.count("eth_getLogs"), 2)

    def test_archive_depth_learned(self):
        pub, arch = FakeNode(depth=8_000), FakeNode()
        c, (ppub, _) = client(pub, arch)
        c.get_logs(pub.head - 20_000, pub.head - 19_000, address="0xt")
        self.assertIsNotNone(ppub.archive_depth)
        self.assertLess(ppub.archive_depth, 20_000)
        n = pub.calls.count("eth_getLogs")
        c.get_logs(pub.head - 20_000, pub.head - 19_000, address="0xt")
        self.assertEqual(pub.calls.count("eth_getLogs"), n)
        c.get_logs(pub.head - 500, pub.head, address="0xt")                    # recent range: public node is fine
        self.assertEqual(pub.calls.count("eth_getLogs"), n + 1)

    def test_truncated_page_is_split(self):
        a = FakeNode(cap=None)
        c, _ = client(a)
        from crawler.chains.evm import client as cl
        with unittest.mock.patch.object(cl, "LOG_CAP", 100):
            logs = c.get_logs(a.head - 2_000, a.head)
        self.assertEqual(len(logs), len([1 for b in range(a.head - 50_000, a.head + 1, 7) if b >= a.head - 2_000]))

    def test_span_planned_only_over_providers_serving_logs(self):
        """A provider that denies eth_getLogs (Base official, BNB dataseed) must not dictate the window size."""
        logs_node, no_logs = FakeNode(max_span=1_000), FakeNode()
        c, _ = client(logs_node, no_logs, p0={"max_span": 1_000}, p1={"deny": {"eth_getLogs"}})
        c.get_logs(logs_node.head - 2_999, logs_node.head, address="0xt")
        self.assertEqual(logs_node.calls.count("eth_getLogs"), 3)
        self.assertNotIn("eth_getLogs", no_logs.calls)
        self.assertIn("eth_getLogs", c.providers[1].denied)            # declared denials never expire
        with unittest.mock.patch("time.time", return_value=time.time() + 10_000):
            self.assertIn("eth_getLogs", c.providers[1].denied)

    def test_denied_method_remembered_without_cooldown(self):
        class Forbidding(FakeNode):
            def __call__(self, url, body, timeout):
                if json.loads(body).get("method") == "eth_getTransactionReceipt":
                    return 403, b"forbidden"
                return super().__call__(url, body, timeout)
        a, b = Forbidding(), FakeNode()
        c, (pa, _) = client(a, b)
        c.call("eth_getTransactionReceipt", ["0x1"])
        self.assertIn("eth_getTransactionReceipt", pa.denied)
        self.assertFalse(pa.cooling(time.time()))           # still used for everything else
        with unittest.mock.patch("time.time", return_value=time.time() + 301):
            self.assertNotIn("eth_getTransactionReceipt", pa.denied)   # learned denials expire (403 may be throttling)
        c.head()
        self.assertIn("eth_blockNumber", a.calls)

    def test_bounded_probe_does_not_retry(self):
        """Chain detection probes: one attempt, short timeout, limited providers — a hung node costs seconds."""
        seen = []

        class Hung(FakeNode):
            def __call__(self, url, body, timeout):
                seen.append(timeout)
                raise TimeoutError("hung")
        c, _ = client(Hung(), Hung(), Hung())
        t0 = time.time()
        with self.assertRaises(NoProvider):
            c.call("eth_getCode", ["0x1", "latest"], timeout=4.0, tries=1, max_providers=2)
        self.assertEqual(seen, [4.0, 4.0])                  # two providers, one attempt each, the given timeout
        self.assertLess(time.time() - t0, 1.0)              # no backoff sleeps

    def test_no_provider_for_asset_transfers(self):
        c, _ = client(FakeNode())
        with self.assertRaises(NoProvider):
            c.asset_transfers(contractAddresses=["0xt"])

    def test_secret_url_never_in_errors_or_health(self):
        c, _ = client(FakeNode(down=True))
        try:
            c.head()
        except NoProvider as e:
            self.assertNotIn("SECRETKEY", str(e))
        self.assertNotIn("SECRETKEY", json.dumps(c.health()))
        self.assertNotIn("SECRETKEY", repr(c.providers))

    def test_cancelled_scope_stops_requests(self):
        a = FakeNode()
        c, _ = client(a)
        with priority.scope(priority.Scope(time.time() - 1)):
            with self.assertRaises(priority.Cancelled):
                c.head()
        self.assertEqual(a.calls, [])


if __name__ == "__main__":
    unittest.main()
