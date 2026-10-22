"""Phase 9: sell impact (crawler/impact.py) — venue models, aggregation, taxes, scenarios, the «probably rug» marker,
and the adapters' inputs (Solana Token-2022 transfer fee, pool venues)."""
import base64
import math
import unittest

from crawler import detect as d
from crawler import impact
from crawler.chains import solana as sol

E18 = 10 ** 18


def sig(**k):
    s = {"kind": "buy", "virgin": False, "launch_bundle": False, "sniper": False, "sold": False, "is_deployer": False}
    return s | k


class TestVenues(unittest.TestCase):

    def test_constant_product_matches_v1(self):
        v = impact.venue("constant_product", 1000 * E18)
        for q in (1, 10 * E18, 500 * E18, 5000 * E18):
            self.assertAlmostEqual(impact.market_drop([v], q), d.dump_impact(q, 1000 * E18))

    def test_two_cp_pools_equal_one_with_summed_reserves(self):
        # a router splits until both pools sit at one price: for x*y=k that is one pool with R1 + R2
        a, b = impact.venue("constant_product", 300 * E18), impact.venue("constant_product", 700 * E18)
        one = impact.venue("constant_product", 1000 * E18)
        for q in (5 * E18, 120 * E18, 900 * E18):
            self.assertAlmostEqual(impact.market_drop([a, b], q), impact.market_drop([one], q), places=9)

    def test_absorb_inverts_drop(self):
        curve = [(10 * E18, 0.02), (50 * E18, 0.09), (200 * E18, 0.31), (400 * E18, 0.52)]
        for v in (impact.venue("quote_curve", curve=curve), impact.venue("constant_product", 250 * E18)):
            for q in (3 * E18, 70 * E18, 390 * E18, 900 * E18):
                x = impact.venue_drop(v, q)
                self.assertAlmostEqual(impact.venue_absorb(v, x) / q, 1.0, places=6)

    def test_quote_curve_tail_is_continuous_and_falls(self):
        v = impact.venue("quote_curve", curve=[(10, 0.1), (100, 0.5)])
        self.assertAlmostEqual(impact.venue_drop(v, 100), 0.5)
        self.assertAlmostEqual(impact.venue_drop(v, 100.0001), 0.5, places=5)
        self.assertGreater(impact.venue_drop(v, 300), 0.5)
        self.assertLess(impact.venue_drop(v, 10 ** 9), 1.0)

    def test_dry_curve_is_total(self):
        v = impact.venue("quote_curve", curve=[(10, 0.2), (100, 1.0)])
        self.assertEqual(impact.venue_drop(v, 500), 1.0)

    def test_mixed_venues_never_worse_than_either_alone(self):
        cp = impact.venue("constant_product", 400 * E18)
        qc = impact.venue("quote_curve", curve=[(20 * E18, 0.05), (100 * E18, 0.2), (300 * E18, 0.45)])
        q = 150 * E18
        both = impact.market_drop([cp, qc], q)
        self.assertLess(both, impact.market_drop([cp], q))
        self.assertLess(both, impact.market_drop([qc], q))

    def test_deeper_than_quoted(self):
        flat = impact.venue("quote_curve", curve=[(10, 0.0), (100, 0.0)])     # no drop anywhere in the quoted range
        self.assertTrue(impact.beyond_quotes([flat], 0.02))
        self.assertEqual(impact.market_absorb([flat], 0.02), 100)              # a lower bound
        ok = impact.venue("quote_curve", curve=[(10, 0.01), (100, 0.2)])
        self.assertFalse(impact.beyond_quotes([ok], 0.1))
        self.assertFalse(impact.beyond_quotes([ok], 0.5))                      # the CP tail continues it

    def test_no_venue(self):
        self.assertIsNone(impact.market_drop([], 10))
        self.assertIsNone(impact.market_drop([impact.venue("constant_product", 0)], 10))

    def test_cp_slippage_closed_form(self):
        r, q = 1000.0, 250.0
        v = impact.venue("constant_product", r)
        self.assertAlmostEqual(impact.slippage([v], q), 1 - r / (r + q), places=4)
        self.assertLess(impact.slippage([v], q), impact.market_drop([v], q))   # average fill beats the last fill

    def test_thin_pool_proceeds_are_bounded(self):
        # a pool that is dry after 0.01% of the sale: proceeds can be no more than what the first slice fetched
        v = impact.venue("quote_curve", curve=[(100, 0.5), (200, 1.0), (10 ** 6, 1.0)])
        slip = impact.slippage([v], 10 ** 6)
        self.assertGreater(slip, 0.9998)
        self.assertAlmostEqual((1 - slip) * 10 ** 6, 100 * 0.75 + 100 * 0.25, delta=1)   # exact area of the curve

    def test_approximate_is_not_exact(self):
        self.assertFalse(impact.venue("approximate", 10)["exact"])
        self.assertTrue(impact.venue("bonding_curve", 10)["exact"])


class TestTax(unittest.TestCase):

    def test_token_side_and_cap(self):
        t = impact.tax(0.1, "token", max_fee=5)
        self.assertEqual(impact.net_into_pool(20, t), 18)
        self.assertEqual(impact.net_into_pool(1000, t), 995)
        self.assertEqual(impact.net_into_pool(1000, impact.tax(0.1, "quote")), 1000)
        self.assertEqual(impact.net_into_pool(1000, None), 1000)

    def sell(self, tx, seller, amount, rate, receiver="0xtax"):
        fee = int(amount * rate)
        rows = [{"tx": tx, "frm": seller, "to": "0xpair", "amount": amount - fee}]
        if fee:
            rows.append({"tx": tx, "frm": seller, "to": receiver, "amount": fee})
        return rows

    def test_observed_tax(self):
        tr = []
        for i in range(6):
            tr += self.sell(f"t{i}", f"0xs{i}", 1000, 0.05)
        tr += [{"tx": "t0", "frm": "0xtax", "to": "0xpair", "amount": 300}]   # contract swapping its tax: not a sample
        tr += [{"tx": "b1", "frm": "0xpair", "to": "0xs9", "amount": 500}]    # a buy
        t = impact.observed_tax(tr, {"0xpair"})
        self.assertAlmostEqual(t["sell"], 0.05, places=3)
        self.assertEqual((t["side"], t["source"], t["exact"], t["receiver"], t["samples"]),
                         ("token", "observed", False, "0xtax", 6))

    def test_no_tax_seen(self):
        tr = []
        for i in range(6):
            tr += self.sell(f"t{i}", f"0xs{i}", 1000, 0.0)
        self.assertIsNone(impact.observed_tax(tr, {"0xpair"}))

    def test_too_few_or_scattered(self):
        self.assertIsNone(impact.observed_tax(self.sell("t0", "0xa", 1000, 0.1) + self.sell("t1", "0xb", 1000, 0.1),
                                              {"0xpair"}))
        tr = []
        for i in range(6):                                   # every sell pays someone else: transfers, not a tax
            tr += self.sell(f"t{i}", f"0xs{i}", 1000, 0.05, receiver=f"0xr{i}")
        self.assertIsNone(impact.observed_tax(tr, {"0xpair"}))


class TestScenarios(unittest.TestCase):

    def setUp(self):
        circ = 1000 * E18
        self.base = {"supply": 2 * circ, "circulating": circ}
        self.holders = [("a", 200 * E18, 0.2), ("b", 100 * E18, 0.1), ("c", 80 * E18, 0.08), ("dev", 50 * E18, 0.05),
                        ("e", 40 * E18, 0.04), ("f", 30 * E18, 0.03)]
        self.sig = {"a": sig(), "b": sig(virgin=True), "c": sig(virgin=True, launch_bundle=True),
                    "dev": sig(is_deployer=True), "e": sig(kind="transfer"), "f": sig(sniper=True, sold=True)}
        self.ops = [{"wallets": ["a"], "share": 0.2, "level": None},
                    {"wallets": ["b", "c"], "share": 0.18, "level": "proven"}]
        self.venues = [impact.venue("constant_product", 500 * E18)]

    def run_(self, **k):
        args = dict(holders=self.holders, signals=self.sig, ops=self.ops, base=self.base, venues=self.venues,
                    venues_ok=True)
        return impact.analyse(**(args | k))

    def test_scenarios(self):
        si = self.run_(price_usd=2.0, memory={"crews": [{"wallets": ["a", "zz"]}]})
        sc = si["scenarios"]
        self.assertEqual(sc["largest_wallet"]["wallets"], ["a"])
        self.assertEqual(sc["largest_operator"]["wallets"], ["b", "c"])
        self.assertEqual(sc["largest_operator"]["level"], "proven")
        self.assertEqual(sc["launch_bundle"]["wallets"], ["c", "f"])           # snipers count, sold or not
        combo = sc["suspicious_combined"]
        self.assertEqual(combo["wallets"], ["a", "b", "c", "dev", "e"])          # each once, top order
        self.assertEqual([p["kind"] for p in combo["parts"]], ["linked", "transfer", "deployer", "crew"])
        self.assertAlmostEqual(sum(p["share"] for p in combo["parts"]), combo["share"])
        self.assertAlmostEqual(combo["share"], 0.47)
        self.assertAlmostEqual(combo["share_supply"], 0.235)
        self.assertAlmostEqual(combo["drop"], d.dump_impact(470 * E18, 500 * E18))
        self.assertAlmostEqual(combo["value_usd"], 470 * 2.0)
        self.assertAlmostEqual(combo["level_usd"], 2.0 * (1 - combo["drop"]))
        self.assertLess(combo["proceeds_usd"], combo["value_usd"])
        self.assertTrue(si["liquidity"]["exact"])
        self.assertFalse(si["liquidity"]["aggregated"])

    def test_no_snipers_on_chain(self):
        sc = self.run_(snipers=False)["scenarios"]
        self.assertEqual(sc["launch_bundle"]["wallets"], ["c"])

    def test_locked_and_tax(self):
        plain = self.run_()["scenarios"]["largest_wallet"]
        locked = self.run_(locked={"a": 100 * E18})["scenarios"]["largest_wallet"]
        self.assertAlmostEqual(locked["sellable_share"], 0.1)
        self.assertAlmostEqual(locked["drop"], d.dump_impact(100 * E18, 500 * E18))
        taxed = self.run_(tax_info=impact.tax(0.1, "token"))["scenarios"]["largest_wallet"]
        self.assertAlmostEqual(taxed["drop"], d.dump_impact(180 * E18, 500 * E18))
        self.assertLess(taxed["drop"], plain["drop"])

    def test_depth_and_slippage(self):
        si = self.run_(price_usd=1.0)
        two = next(x for x in si["depth"] if x["drop"] == 0.02)
        self.assertAlmostEqual(two["share"], 500 * (1 / math.sqrt(0.98) - 1) / 1000, places=9)
        taxed = next(x for x in self.run_(tax_info=impact.tax(0.2, "token"))["depth"] if x["drop"] == 0.02)
        self.assertAlmostEqual(taxed["share"], two["share"] / 0.8, places=9)   # sell more to land the same amount
        self.assertEqual([s["share"] for s in si["slippage"]], list(impact.SLIPPAGE_SIZES))

    def test_unmeasured_liquidity(self):
        si = self.run_(venues_ok=False)
        self.assertIsNone(si["scenarios"]["suspicious_combined"]["drop"])
        self.assertEqual(si["depth"], [])
        self.assertFalse(si["liquidity"]["measured"])

    def test_marker(self):
        si = self.run_(price_usd=2.0)
        hard = ["biggest operator could move price −60% if sold"]
        m = impact.marker(si, "DANGER", hard, True, False)
        self.assertTrue(m["conditional"])
        self.assertEqual(m["basis"], "suspicious_combined")
        self.assertAlmostEqual(m["level_factor"], 1 - m["drop"])
        self.assertIsNone(impact.marker(si, "RISKY", hard, True, False))
        self.assertIsNone(impact.marker(si, "DANGER", ["soft: thin"], True, False))
        self.assertIsNone(impact.marker(si, "DANGER", hard, False, False))
        self.assertIsNone(impact.marker(si, "DANGER", hard, True, True))
        deep = self.run_(venues=[impact.venue("constant_product", 10 ** 6 * E18)])
        self.assertIsNone(impact.marker(deep, "DANGER", hard, True, False))   # below RUG_MIN_DROP


class TestSolanaInputs(unittest.TestCase):

    def mint(self, bps, max_fee, older_bps=0):
        ext = bytes(32) + bytes(32) + (0).to_bytes(8, "little")
        ext += (0).to_bytes(8, "little") + (10 ** 6).to_bytes(8, "little") + older_bps.to_bytes(2, "little")
        ext += (5).to_bytes(8, "little") + max_fee.to_bytes(8, "little") + bps.to_bytes(2, "little")
        b = bytes(165) + b"\x01" + (1).to_bytes(2, "little") + len(ext).to_bytes(2, "little") + ext
        return base64.b64encode(b).decode()

    def test_transfer_fee(self):
        self.assertEqual(sol.transfer_fee(self.mint(250, 7 * 10 ** 9, older_bps=100)),
                         {"bps": 250, "max_fee": 7 * 10 ** 9})
        self.assertIsNone(sol.transfer_fee(base64.b64encode(bytes(82)).decode()))
        self.assertIsNone(sol.transfer_fee("not base64!"))

    def test_pool_venues(self):
        cpmm = "GpMZbSM2GgvTKHJirzeGfMFoaZ8UR2X7F4v8vHTvxFbL"     # Raydium CPMM vault authority
        sol._OWNER_PROG["pdaClmm"] = "CAMMCzo5YL8w4VFF8KVHrK22GGUsp5VTaW7grrKgrWqK"
        try:
            rows = [{"owner": cpmm, "amount": 300}, {"owner": "pdaClmm", "amount": 500},
                    {"owner": "wallet", "amount": 900}, {"owner": "curve", "amount": 1}]
            out = sol.pool_venues(rows, "curve")
        finally:
            sol._OWNER_PROG.pop("pdaClmm", None)
        self.assertEqual([(v["model"], v["reserve"]) for v in out], [("approximate", 500), ("constant_product", 300)])
        self.assertEqual(out[1]["label"], "Raydium CPMM pool")


if __name__ == "__main__":
    unittest.main()
