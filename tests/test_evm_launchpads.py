"""Clanker and four.meme plugins on recorded mainnet traffic (tests/fixtures/evm/*.json.gz, recorder in the same
folder), replayed through the real EvmClient with no network. A request that was not recorded fails the test, so
any change in what the adapter reads is visible here."""
import gzip, json, os, unittest
from contextlib import ExitStack
from unittest import mock

import fakes  # noqa: F401  (dummy RPC env; forbids real Pons adapter RPC)
from crawler import detect as d
from crawler import engine, market
from crawler.chains import bankr, registry
from crawler.chains.evm import abi, networks, v4
from crawler.chains.evm.client import EvmClient
from crawler.chains.evm.launchpads import clanker, fourmeme
from crawler.chains.evm.token import EvmTokenAdapter

FIX = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "evm")
PLUGINS = {"clanker": clanker, "fourmeme": fourmeme}


def load(name):
    with gzip.open(os.path.join(FIX, name + ".json.gz"), "rt", encoding="utf-8") as f:
        return json.load(f)


def replay_adapter(fx):
    book = fx["responses"]

    def transport(url, body, timeout):
        req = json.loads(body)

        def one(q):
            k = json.dumps([q["method"], q.get("params", [])], sort_keys=True)
            if k not in book:
                return {"jsonrpc": "2.0", "id": q.get("id"), "error": {"code": -32000, "message": f"not recorded: {k[:160]}"}}
            return {"jsonrpc": "2.0", "id": q.get("id")} | book[k]

        out = [one(q) for q in req] if isinstance(req, list) else one(req)
        return 200, json.dumps(out).encode()

    providers = networks.providers(fx["chain"], env={})
    for p in providers:
        p.throttle = type(p.throttle)(0)          # no rate limiting in replay
    client = EvmClient(fx["chain"], providers, transport=transport, bg_rps=0, crit_rps=0)
    client.head = lambda: fx["head"]
    client._head = (float("inf"), fx["head"])
    return EvmTokenAdapter(networks.NETWORKS[fx["chain"]], client, PLUGINS[fx["launchpad"]])


class FixtureCase:
    NAME = None

    @classmethod
    def setUpClass(cls):
        cls.fx = load(cls.NAME)
        cls.ad = replay_adapter(cls.fx)
        cls.token = cls.fx["token"]
        cls.state = cls.ad.detect(cls.token)
        cls.launch = cls.ad.get_launch(cls.token)
        cls.facts = cls.ad.token_facts(cls.token, cls.launch)
        cls.base = d.supply_base(cls.facts["transfers"], cls.facts["supply"], cls.facts["excluded"])
        cls.holders = d.top_holders(cls.base)

    def test_identified_and_launch_located(self):
        self.assertIsNotNone(self.state)
        self.assertGreater(self.launch["block"], 0)
        first = self.facts["transfers"][0]
        self.assertEqual(first["frm"], abi.ZERO)                       # history starts with the mint
        self.assertEqual(first["block"], self.launch["block"])
        self.assertTrue(self.launch["deployer"].startswith("0x") and len(self.launch["deployer"]) == 42)

    def test_balances_conserve_supply(self):
        """Transfers since the mint reconstruct exactly the minted supply (nothing missed or double counted)."""
        bal = {}
        for t in self.facts["transfers"]:
            bal[t["frm"]] = bal.get(t["frm"], 0) - t["amount"]
            bal[t["to"]] = bal.get(t["to"], 0) + t["amount"]
        minted = -bal.pop(abi.ZERO, 0)
        self.assertEqual(minted, self.facts["supply"])
        self.assertTrue(all(v >= 0 for v in bal.values()))
        self.assertEqual(sum(bal.values()), self.facts["supply"])

    def test_infrastructure_excluded(self):
        held = {w for w, _, _ in self.holders}
        self.assertFalse(held & set(self.launch["infra"]))
        self.assertFalse(held & set(self.launch["market"]))

    def test_impact_curve_shape(self):
        curve = self.facts.get("impact_curve") or []
        self.assertTrue(curve, "no impact curve from the pool quotes")
        qs = [q for q, _ in curve]
        drops = [x for _, x in curve]
        self.assertEqual(qs, sorted(qs))
        self.assertEqual(drops, sorted(drops))                          # monotonic
        self.assertTrue(all(0.0 <= x <= 1.0 for x in drops))

    def test_engine_verdict(self):
        """Full engine scan over the replayed adapter; market and wallet history pinned (not part of the fixture)."""
        env = {"CLANKER_ENABLED": "true", "FOURMEME_ENABLED": "true", "BSC_ENABLED": "true", "BASE_ENABLED": "true"}
        with ExitStack() as st:
            st.enter_context(mock.patch.dict(os.environ, env))
            st.enter_context(mock.patch.dict(registry._ADAPTERS,
                                             {(self.fx["chain"], self.fx["launchpad"]): self.ad}, clear=True))
            st.enter_context(mock.patch.object(market, "fetch_market", return_value={}))
            st.enter_context(mock.patch.object(market, "established_get", return_value=None))
            st.enter_context(mock.patch.object(self.ad, "wallet_distinct_tokens", return_value=4))
            st.enter_context(mock.patch.object(self.ad, "is_contract", side_effect=lambda a: {x: False for x in a}))
            res = engine.scan(self.token, chain=self.fx["chain"])
        self.assertEqual(res["chain"], self.fx["chain"])
        self.assertEqual(res["launchpad"], self.fx["launchpad"])
        self.assertIn(self.fx["launchpad"], res)
        json.dumps(res)                                                 # the result is JSON-serialisable
        self.check_verdict(res)

    def check_verdict(self, res):
        raise NotImplementedError


class TestRobinhoodClanker(FixtureCase, unittest.TestCase):
    """MFISH (Robinhood Clanker v4): two buyers, deep single-sided pool."""
    NAME = "robinhood_clanker_mfish"

    def test_pool_key(self):
        key = self.launch["pool_key"]
        self.assertEqual(key["fee"], v4.DYNAMIC_FEE_FLAG)
        self.assertEqual(key["tick_spacing"], 200)
        self.assertEqual(key["currency0"], networks.NETWORKS["robinhood"].wrapped_native)
        self.assertEqual(key["hooks"], self.state["hook"])

    def test_entries_are_v4_buys(self):
        e = self.ad.classify_entries(self.token, self.facts["transfers"], [w for w, _, _ in self.holders])
        self.assertEqual({v["kind"] for v in e.values()}, {"buy"})

    def check_verdict(self, res):
        self.assertEqual(res["band"], d.TOO_EARLY)                       # 2 holders


class TestBscFourMeme(FixtureCase, unittest.TestCase):
    """FLIX (four.meme, bonding curve, 30 minutes old): 3 holders left after 58 buys."""
    NAME = "bsc_fourmeme_flix"

    def test_curve_phase_and_native_quote(self):
        self.assertEqual(self.launch["phase"], "bonding_curve")
        self.assertTrue(self.launch["native_quote"])
        self.assertIn(fourmeme.TOKEN_MANAGER2, self.facts["excluded"])

    def test_entries_have_bnb_cost(self):
        e = self.ad.classify_entries(self.token, self.facts["transfers"], [w for w, _, _ in self.holders])
        buys = [v for v in e.values() if v["kind"] == "buy"]
        self.assertTrue(buys)
        self.assertTrue(any(v["eth_in"] for v in buys))                # TokenPurchase.cost in wei of BNB

    def check_verdict(self, res):
        self.assertEqual(res["band"], d.TOO_EARLY)


class TestBaseClanker(FixtureCase, unittest.TestCase):
    """EYESOS (Base Clanker v4, 30 minutes old): 24 holders, V4 quoter impact."""
    NAME = "base_clanker_eyesos"

    def check_verdict(self, res):
        self.assertIn(res["band"], ("CLEAN", "OK", "RISKY", "DANGER"))
        self.assertIsInstance(res["score"], int)
        self.assertIsNotNone(res["metrics"]["impact"])                  # from the quote curve, not unmeasured
        self.assertTrue(res["headline"].startswith(f"{len(res['holders'])} wallets → "))


class TestV4Helpers(unittest.TestCase):

    def test_selectors_match_upstream_bankr(self):
        self.assertEqual(v4.SEL_QUOTE, bankr.SEL_QUOTE)
        self.assertEqual(v4.SEL_TRY_AGGREGATE, bankr.SEL_TRY_AGGREGATE)
        self.assertEqual(clanker.SEL_DEPLOYMENT_INFO, "0x22adcdb1")
        self.assertEqual(clanker.TOKEN_CREATED, "0x9299d1d1a88d8e1abdc591ae7a167a6bc63a8f17d695804e9091ee33aa89fb67")
        self.assertEqual(fourmeme.TOKEN_PURCHASE, "0x7db52723a3b2cdd6164364b3b766e65e540d7be48ffa89582956d8eaebe62942")
        self.assertEqual(v4.ERR_NOT_ENOUGH_LIQUIDITY, "0x7a5ed734")

    def test_try_aggregate_encoding_matches_bankr(self):
        calls = [("0x" + "11" * 20, "0xaabbcc"), ("0x" + "22" * 20, "0x" + "ff" * 70)]
        self.assertEqual(v4.encode_try_aggregate(calls), bankr._encode_try_aggregate(calls))

    def test_dry_pool_is_full_drop(self):
        qs = v4.sell_amounts(10 ** 24)
        dry = (False, v4.ERR_UNEXPECTED_REVERT + "0" * 62 + "20" + "0" * 62 + "24" + v4.ERR_NOT_ENOUGH_LIQUIDITY[2:])
        curve = v4.impact_curve(qs, [dry] * len(qs))
        self.assertTrue(curve)
        self.assertTrue(all(x == 1.0 for _, x in curve))

    def test_unknown_failure_is_not_a_drop(self):
        qs = v4.sell_amounts(10 ** 24)
        self.assertEqual(v4.impact_curve(qs, [(False, "0x")] * len(qs)), [])

    def test_clanker_detect_rejects_other_tokens(self):
        class A:
            CHAIN = "base"

            def call(self, to, data):
                return "0x" + abi.enc_uint(32) + abi.enc_addr(abi.ZERO) * 3 + abi.enc_uint(128) + abi.enc_uint(0)
        self.assertIsNone(clanker.detect(A(), "0x" + "ab" * 20))

    def test_fourmeme_detect_only_v2_manager(self):
        class A:
            CHAIN = "bsc"

            def __init__(self, version, manager):
                self.v, self.m = version, manager

            def call(self, to, data):
                return "0x" + abi.enc_uint(self.v) + abi.enc_addr(self.m) + abi.enc_uint(0) * 10
        self.assertIsNotNone(fourmeme.detect(A(2, fourmeme.TOKEN_MANAGER2), "0x" + "ab" * 20))
        self.assertIsNone(fourmeme.detect(A(1, fourmeme.TOKEN_MANAGER2), "0x" + "ab" * 20))
        self.assertIsNone(fourmeme.detect(A(2, "0x" + "cd" * 20), "0x" + "ab" * 20))
        a = A(2, fourmeme.TOKEN_MANAGER2)
        a.CHAIN = "base"
        self.assertIsNone(fourmeme.detect(a, "0x" + "ab" * 20))


if __name__ == "__main__":
    unittest.main()
