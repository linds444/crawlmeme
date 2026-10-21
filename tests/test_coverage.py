"""Scan coverage (crawler/coverage.py) and its engine wiring: depth, holder types, the limited-scan cap."""
import os, unittest
from unittest import mock

import fakes
from fakes import ch, engine
from crawler import coverage
from crawler import detect as d

W = ["0x" + f"{i:02x}" * 20 for i in range(1, 11)]
HOLDERS = [(w, 100 - i, 0.1) for i, w in enumerate(W)]
ENTRIES = {w: {"kind": "buy"} for w in W}


def data(read):
    return {w: {"distinct_tokens": 4 if i < read else None} for i, w in enumerate(W)}


class TestAssess(unittest.TestCase):

    def test_full(self):
        cov = coverage.assess({"base": None}, HOLDERS, ENTRIES, data(10), [])
        self.assertEqual(cov["level"], "full")
        self.assertEqual(cov["wallets"]["read"], 10)

    def test_partial_when_few_unread(self):
        cov = coverage.assess({"base": None}, HOLDERS, ENTRIES, data(7), W[7:])
        self.assertEqual(cov["level"], "partial")
        self.assertEqual(cov["wallets"]["unread_budget"], W[7:])

    def test_limited_when_most_unread_and_reasons_named(self):
        cov = coverage.assess({"base": None}, HOLDERS, ENTRIES, data(2), [])
        self.assertEqual(cov["level"], "limited")
        self.assertEqual(len(cov["wallets"]["unread_unavailable"]), 8)   # no provider could read them
        self.assertIn("8 of 10 examined wallets unread", cov["reasons"])

    def test_solana_top_accounts_is_partial(self):
        cov = coverage.assess({"base": {"balances": {}}}, HOLDERS, ENTRIES, data(10), [])
        self.assertEqual(cov["level"], "partial")
        self.assertTrue(cov["holders"]["holders_total_lower_bound"])

    def test_failed_verification_is_limited(self):
        cov = coverage.assess({"base": None}, HOLDERS, ENTRIES, data(10), [], {"verified": False, "source": "logs"})
        self.assertEqual(cov["level"], "limited")

    def test_windowed_history_is_limited(self):
        facts = {"base": None, "flap": {"history": {"mode": "windowed", "top_exact": False}}}
        self.assertEqual(coverage.assess(facts, HOLDERS, ENTRIES, data(10), [])["level"], "limited")

    def test_cap(self):
        sc = {"score": 91, "band": "CLEAN", "notes": []}
        lim = {"level": "limited", "reasons": ["8 of 10 examined wallets unread"]}
        out = coverage.apply(sc, lim)
        self.assertEqual((out["band"], out["score"]), ("OK", 79))
        self.assertTrue(out["coverage_capped"])
        self.assertIn("not rated CLEAN", out["notes"][-1])
        self.assertIs(coverage.apply(sc, {"level": "partial", "reasons": []}), sc)      # partial: no cap
        danger = {"score": 20, "band": "DANGER"}
        self.assertIs(coverage.apply(danger, lim), danger)                              # never raises a verdict


class TestEngine(unittest.TestCase):

    def scan(self, history_fn=fakes.history, env=None):
        with fakes.patched(history_fn=history_fn), mock.patch.dict(os.environ, env or {}):
            return engine.scan(fakes.TOKEN)

    def test_result_has_coverage_and_holder_types(self):
        res = self.scan()
        self.assertEqual(res["coverage"]["level"], "full")
        self.assertEqual({h["holder_type"] for h in res["holders"]}, {"wallet"})

    def test_unreadable_histories_never_clean(self):
        clean = lambda *a, **k: 4                         # every wallet with history: a clean distribution
        unread = lambda *a, **k: None                     # no provider could read any history
        r1 = self.scan(clean)
        r2 = self.scan(unread)
        self.assertEqual(r2["coverage"]["level"], "limited")
        self.assertNotEqual(r2["band"], "CLEAN")
        if r1["band"] == "CLEAN":
            self.assertTrue(r2.get("score") <= coverage.LIMITED_MAX_SCORE)

    def test_scan_depth(self):
        res = self.scan(env={"SCAN_DEPTH": "5"})
        self.assertEqual(len(res["holders"]), 5)
        self.assertEqual(engine.scan_depth(), d.TOP_N)
        with mock.patch.dict(os.environ, {"SCAN_DEPTH": "500"}):
            self.assertEqual(engine.scan_depth(), 100)
        with mock.patch.dict(os.environ, {"SCAN_DEPTH": "x"}):
            self.assertEqual(engine.scan_depth(), d.TOP_N)

    def test_contract_holders_labelled_not_excluded(self):
        with fakes.patched(), mock.patch.object(ch, "is_contract",
                                                side_effect=lambda addrs: {a: a == fakes.WALLETS[0] for a in addrs}):
            res = engine.scan(fakes.TOKEN)
        types = {h["wallet"]: h["holder_type"] for h in res["holders"]}
        self.assertEqual(types[fakes.WALLETS[0]], "contract")
        self.assertIn(fakes.WALLETS[0], types)             # still a holder


if __name__ == "__main__":
    unittest.main()
