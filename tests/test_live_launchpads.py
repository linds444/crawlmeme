"""Mainnet integration tests: one live scan per supported (chain, launchpad), on a token found on-chain right now.

Skipped unless CRAWLER_LIVE=1 (CI runs offline). They read only public data; public endpoints suffice for fresh
tokens, keys (ALCHEMY_API_KEY, CRAWLER_RPC) widen what can be scanned. A launchpad is advertised as supported
(chains/registry.py) only while its case here passes.

  CRAWLER_LIVE=1 python -m unittest tests/test_live_launchpads.py -v

Each case: discover the most-traded token launched in the last few thousand blocks from the launchpad's own events
(no third-party list), then a full engine.scan with chain auto-detection, and check what can be checked
independently: chain and launchpad identification, supply conservation of the reconstructed balances, infrastructure
exclusion, a verdict with a known band, and the time budget.
"""
import os, time, unittest
from collections import Counter
from contextlib import ExitStack
from unittest import mock

LIVE = os.environ.get("CRAWLER_LIVE") == "1"
ENV = {"CLANKER_ENABLED": "true", "FOURMEME_ENABLED": "true", "FLAP_ENABLED": "true", "BSC_ENABLED": "true",
       "BASE_ENABLED": "true", "DEX_ENABLED": "true"}
if LIVE:
    os.environ.setdefault("CRAWLER_RPC", "https://rpc.mainnet.chain.robinhood.com")


@unittest.skipUnless(LIVE, "set CRAWLER_LIVE=1 for mainnet integration tests")
class TestLive(unittest.TestCase):

    def setUp(self):
        self.stack = ExitStack()
        self.stack.enter_context(mock.patch.dict(os.environ, ENV))

    def tearDown(self):
        self.stack.close()

    def fresh(self, chain, address, topic_created, created_word, topic_trade=None, trade_word=None, back=3000,
              topics_created=None):
        """Most-traded token created in the last `back` blocks, from the launchpad's own logs."""
        from crawler.chains.evm import abi, networks
        c = networks.client(chain)
        h = c.head()
        made, trades = [], Counter()
        for page in c.iter_logs(h - back, h, address=address):
            for lg in page:
                t0 = lg["topics"][0].lower()
                if t0 == topic_created:
                    made.append(abi.addr(lg["topics"][1]) if created_word is None else
                                abi.addr(abi.words(lg["data"])[created_word]))
                elif topic_trade and t0 == topic_trade:
                    trades[abi.addr(abi.words(lg["data"])[trade_word])] += 1
        self.assertTrue(made, f"no {chain} launch in the last {back} blocks")
        if not trades:   # no trade event on the launchpad (Clanker): rank the newest launches by their own transfers
            lo = max(h - back, h - (c.providers[0].archive_depth or back))
            for i in range(0, min(len(made), 40), 5):   # publicnode refuses filters with many addresses
                part = made[-40:][i:i + 5]
                for page in c.iter_logs(lo, h, address=part, topics=[abi.TRANSFER_TOPIC]):
                    for lg in page:
                        trades[lg["address"].lower()] += 1
        ranked = [t for t, _ in trades.most_common() if t in made]
        return ranked[0] if ranked else made[-1]

    def scan_and_check(self, token, chain, launchpad):
        from crawler import detect as d
        from crawler import engine
        t0 = time.time()
        res = engine.scan(token)                               # chain found from the address (eth_getCode probe)
        elapsed = time.time() - t0
        self.assertEqual(res["chain"], chain)
        self.assertEqual(res.get("launchpad"), launchpad)
        self.assertIn(res["band"], ("CLEAN", "OK", "RISKY", "DANGER", d.TOO_EARLY))
        self.assertLess(elapsed, engine.BUDGET + 15)           # budget + market/meta stragglers
        holders = {h["wallet"] for h in res["holders"]}
        launch = res["launch"]
        self.assertFalse(holders & set(launch.get("infra") or []))
        self.assertFalse(holders & set(launch.get("market") or []))
        self.assertLessEqual(res["circulating"], res["supply"])
        print(f"\n  {chain}/{launchpad} {token}: {res['band']} {res['score']} — {res['headline']} ({elapsed:.1f}s)")
        return res

    def test_robinhood_clanker(self):
        from crawler.chains.evm.launchpads import clanker
        tok = self.fresh("robinhood", clanker.FACTORIES["robinhood"], clanker.TOKEN_CREATED, None, back=2_000_000)
        self.scan_and_check(tok, "robinhood", "clanker")

    def test_base_clanker(self):
        from crawler.chains.evm.launchpads import clanker
        tok = self.fresh("base", clanker.FACTORIES["base"], clanker.TOKEN_CREATED, None, back=3000)
        self.scan_and_check(tok, "base", "clanker")

    def test_bsc_fourmeme(self):
        from crawler.chains.evm.launchpads import fourmeme as fm
        tok = self.fresh("bsc", fm.TOKEN_MANAGER2, fm.TOKEN_CREATE, 1, fm.TOKEN_PURCHASE, 0, back=4000)
        self.scan_and_check(tok, "bsc", "fourmeme")

    def test_bsc_flap(self):
        from crawler.chains.evm.launchpads import flap
        tok = self.fresh("bsc", flap.PORTALS["bsc"], flap.TOKEN_CREATED, 3, flap.TOKEN_BOUGHT, 1, back=3000)
        self.scan_and_check(tok, "bsc", "flap")

    def test_robinhood_uniswap_v2(self):
        """A token launched straight into a Uniswap V2 pair against WETH (no launchpad): the DEX fallback."""
        from crawler.chains.evm import abi, networks
        from crawler.chains.evm.keccak import topic
        from crawler.chains.evm.launchpads import dex
        c = networks.client("robinhood")
        h, weth = c.head(), networks.NETWORKS["robinhood"].wrapped_native
        pairs = []
        for page in c.iter_logs(h - 1_000_000, h, address=dex.DEXES["robinhood"][0]["v2"],
                                topics=[topic("PairCreated(address,address,address,uint256)")]):
            for lg in page:
                t0, t1 = abi.addr(lg["topics"][1]), abi.addr(lg["topics"][2])
                tok = t1 if t0 == weth else t0 if t1 == weth else None
                if tok and not tok.endswith(("7777", "8888", "ba3")):     # not Flap / Bankr
                    pairs.append(tok)
        self.assertTrue(pairs)
        counts = Counter()
        for tok in pairs[-20:]:
            for page in c.iter_logs(h - 1_000_000, h, address=tok, topics=[abi.TRANSFER_TOPIC]):
                counts[tok] += len(page)
        for tok, _ in counts.most_common(5):   # Pons tokens migrate to V4, Clanker uses V4: the first V2-only wins
            from crawler import engine
            try:
                res = engine.scan(tok)
            except engine.ScanError:
                continue
            if res.get("launchpad") == "dex":
                self.assertTrue(res["dex"]["pools"])
                self.scan_and_check(tok, "robinhood", "dex")
                return
        self.skipTest("no recent V2-only token without a launchpad")

    def test_bsc_clanker(self):
        """Clanker launches on BNB are rare: skipped when none happened inside the public node's window."""
        from crawler.chains.evm.launchpads import clanker
        try:
            tok = self.fresh("bsc", clanker.FACTORIES["bsc"], clanker.TOKEN_CREATED, None, back=7000)
        except AssertionError:
            self.skipTest("no BNB Clanker launch in the recent public window (needs ALCHEMY_API_KEY for older ones)")
        self.scan_and_check(tok, "bsc", "clanker")


if __name__ == "__main__":
    unittest.main()
