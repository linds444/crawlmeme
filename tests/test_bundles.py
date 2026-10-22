"""Phase 10: launch bundle detection and bundle-originated supply tracing (crawler/bundles.py), and the engine wiring."""
import unittest

from crawler import bundles, engine
from tests import fakes

ZERO = "0x" + "0" * 40
POOL, ROUTER, LOCK = "pool", "router", "locker"
MARKET = {POOL, ROUTER}
SINKS = {ZERO, LOCK} | bundles.BURNS
INFRA = {LOCK}
SUPPLY = 1000


def T(block, tx, frm, to, amount, i=0):
    return {"block": block, "tx": tx, "frm": frm, "to": to, "amount": amount, "log_index": i}


def history():
    return [
        T(10, "mint", ZERO, "dev", 1000),
        T(10, "lp", "dev", POOL, 700),
        # launch block 10: one tx buys for two wallets through the router; dev hands out supply; a third wallet buys
        T(10, "b1", POOL, ROUTER, 100, 1), T(10, "b1", ROUTER, "w1", 50, 2), T(10, "b1", ROUTER, "w2", 50, 3),
        T(10, "d1", "dev", "w3", 60),
        T(10, "b2", POOL, "w4", 48),
        # after the launch window
        T(11, "x1", POOL, "outsider", 40),
        T(12, "s1", "w1", POOL, 30),                     # w1 sells part
        T(12, "m1", "w2", "fresh1", 25),                 # w2 splits into new wallets
        T(12, "m2", "w2", "fresh2", 25),
        T(13, "m3", "w3", "outsider", 60),               # w3 moves into a wallet holding other supply (diluted)
        T(14, "l1", "w4", LOCK, 20),                     # w4 locks some
        T(15, "bn", "dev", ZERO, 10),                    # dev burns some of its kept allocation
    ]


def analyse(trs=None, complete=True, window=0, holders=(), funders=None, funder_out=None):
    return bundles.analyse(trs or history(), {"block": 10, "deployer": "dev", "tx": "mint", "ts": 1000}, window,
                           MARKET, SINKS, INFRA, SUPPLY, 300, list(holders), complete, funders=funders,
                           funder_out=funder_out)


class TestParticipants(unittest.TestCase):

    def test_kinds_and_pass_through(self):
        parts, _ = bundles.participants(history(), 10, 0, "dev", MARKET, SINKS)
        self.assertEqual({a: p["kind"] for a, p in parts.items()},
                         {"w1": "buy", "w2": "buy", "w3": "dev_transfer", "w4": "buy"})
        self.assertNotIn(ROUTER, parts)                   # router forwards everything: not a participant
        self.assertEqual(parts["w1"]["acquired"], 50)

    def test_time_window(self):
        clock = lambda b: 1000 + (b - 10) * 0.5            # 0.5 s blocks: the first 3 s run to block 16
        trs = history() + [T(16, "late", POOL, "w8", 5), T(17, "later", POOL, "w9", 5)]
        trs.sort(key=lambda t: t["block"])
        parts, end = bundles.participants(trs, 10, 0, "dev", MARKET, SINKS, clock, 1000)
        self.assertEqual(end, 16)
        self.assertIn("w8", parts)
        self.assertNotIn("w9", parts)

    def test_window(self):
        parts, _ = bundles.participants(history(), 10, 1, "dev", MARKET, SINKS)
        self.assertIn("outsider", parts)                  # block 11 is inside a 1-block window


class TestTrace(unittest.TestCase):

    def test_conservation_and_sinks(self):
        b = analyse()
        s = b["supply"]
        # initial: w1 50 + w2 50 + w3 60 + w4 48 + dev kept 1000 − 700 − 60 = 240
        self.assertAlmostEqual(s["initial_share"], 448 / SUPPLY)
        self.assertAlmostEqual(b["conservation"]["residual_share"], 0.0, places=12)
        self.assertAlmostEqual(s["sold_share"], 30 / SUPPLY)
        self.assertAlmostEqual(s["infra_share"], 20 / SUPPLY)
        self.assertAlmostEqual(s["burned_share"], 10 / SUPPLY)
        self.assertAlmostEqual(s["traceable_share"], (448 - 60) / SUPPLY)
        self.assertEqual(b["conservation"]["balance_gaps"], 0)

    def test_dispersal_and_dilution(self):
        b = analyse()
        self.assertEqual(b["dispersal"]["fresh_recipients"], 2)          # fresh1, fresh2
        now = {h["wallet"]: h for h in b["holders_now"]}
        self.assertAlmostEqual(now["fresh1"]["share"], 0.025)
        self.assertEqual(now["fresh1"]["hop"], 1)
        self.assertFalse(now["fresh1"]["original"])
        self.assertAlmostEqual(now["outsider"]["purity"], 0.6)          # 60 bundle of 100 held
        # concentrated: all holders except none — outsider 0.6 purity is still ≥ 0.5
        self.assertAlmostEqual(b["supply"]["concentrated_share"], b["supply"]["traceable_share"])

    def test_participant_outcomes(self):
        rows = {r["wallet"]: r for r in analyse()["participants"]}
        self.assertEqual(rows["w1"]["status"], "sold_part")
        self.assertEqual(rows["w2"]["status"], "moved")
        self.assertAlmostEqual(rows["w2"]["moved_share"], 0.05)
        self.assertEqual(rows["w3"]["status"], "moved")
        self.assertEqual(rows["dev"]["kind"], "deployer")
        self.assertAlmostEqual(rows["dev"]["acquired_share"], 0.24)
        self.assertAlmostEqual(rows["dev"]["burned_share"], 0.01)

    def test_exposure_outside_top(self):
        b = analyse(holders=[("dev", 230, 0.7)])
        self.assertAlmostEqual(b["supply"]["outside_top_share"], (388 - 230) / SUPPLY)

    def test_handoff_between_participants_counts_once(self):
        trs = history()[:7] + [T(10, "h1", "w1", "w2", 50)]   # inside the window w1 hands its buy to w2
        b = analyse(trs)
        self.assertAlmostEqual(b["conservation"]["residual_share"], 0.0, places=12)
        rows = {r["wallet"]: r for r in b["participants"]}
        self.assertAlmostEqual(rows["w2"]["acquired_share"], 0.05)      # w2's own buy only, not w1's again

    def test_confidence(self):
        self.assertEqual(analyse()["confidence"], "high")
        self.assertEqual(analyse(complete=False)["confidence"], "low")
        gap = history() + [T(16, "g", "nobody", "w9", 5)]                # spends what the replay never saw
        b = analyse(gap)
        self.assertEqual(b["confidence"], "medium")
        self.assertEqual(b["conservation"]["balance_gaps"], 1)

    def test_coordination(self):
        f = {"w1": {"found": True, "funder": "F"}, "w2": {"found": True, "funder": "F"},
             "w4": {"found": True, "funder": "HUB"}}
        c = analyse(funders=f, funder_out={"F": 3, "HUB": 500})["coordination"]
        self.assertEqual(c["same_tx"], [["w1", "w2"]])
        self.assertEqual(c["shared_funders"], [{"funder": "F", "wallets": ["w1", "w2"]}])   # the hub never links
        self.assertEqual(c["launch_block_buys"], 3)
        self.assertEqual(c["uniform_buys"], 3)                            # 50, 50, 48 within ±30%

    def test_no_participants(self):
        b = analyse([T(10, "mint", ZERO, POOL, 1000)])
        self.assertEqual((b["mode"], b["participants"], b["supply"]["initial_share"]), ("none", [], 0.0))


class TestSolana(unittest.TestCase):

    def test_launch_only(self):
        parts = {"a": {"kind": "buy", "block": 5, "tx": "t", "acquired": 100},
                 "b": {"kind": "buy", "block": 5, "tx": "t", "acquired": 50},
                 "dev": {"kind": "deployer", "block": 5, "tx": "c", "acquired": 200}}
        b = bundles.analyse_solana(parts, {"a": 100, "dev": 20, "c": 30}, 1000, 800, [("c", 30, 0.1)],
                                   {"c": {"via": "b"}})
        self.assertEqual(b["confidence"], "low")
        self.assertAlmostEqual(b["supply"]["concentrated_share"], 0.12)   # a 100 + dev 20 still in original hands
        self.assertAlmostEqual(b["supply"]["traceable_share"], 0.15)      # + c, a top holder that got it from b
        self.assertAlmostEqual(b["supply"]["left_share"], 0.23)
        self.assertEqual(b["coordination"]["same_tx"], [["a", "b"]])


class TestInScan(unittest.TestCase):

    def test_scan_has_bundle_and_traced_scenario(self):
        with fakes.patched():
            res = engine.scan(fakes.TOKEN)
        b = res["bundle"]
        self.assertIsNotNone(b)
        self.assertEqual(b["mode"], "traced")
        self.assertAlmostEqual(b["conservation"]["residual_share"], 0.0, places=9)
        self.assertGreater(b["supply"]["initial_share"], 0)
        self.assertIn("bundle_traced", res["sell_impact"]["scenarios"])


if __name__ == "__main__":
    unittest.main()
