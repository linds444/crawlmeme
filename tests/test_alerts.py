"""Alert rules (crawler/alerts.py): scan result snapshots and the diff between two scans. No network."""
import json, unittest

from crawler import alerts

T = "0x" + "11" * 20
A, B, C, D = ("0x" + c * 40 for c in "abcd")


def res(band="OK", score=70, rug=None, ops=None, holders=None, token=T, chain="robinhood", packs=None, bundle=None):
    return {"token": token, "chain": chain, "band": band, "score": score, "rug": rug, "header": {"ticker": "AAA"},
            "packs": packs or [], "bundle": bundle,
            "operators": ops if ops is not None else [{"wallets": [A, B], "share_supply": 0.10}],
            "holders": holders if holders is not None else [{"wallet": A, "share_supply": 0.06},
                                                            {"wallet": B, "share_supply": 0.04},
                                                            {"wallet": C, "share_supply": 0.02}]}


def snap(early_share=None, **kw):
    return alerts.snapshot(res(**kw), early_share, ts=100)


def kinds(changes):
    return [c["kind"] for c in changes]


class TestSnapshot(unittest.TestCase):

    def test_fields(self):
        s = alerts.snapshot(res(band="DANGER", score=12, rug={"drop": 0.7234567891}), 0.123456789, ts=5)
        self.assertEqual(s, {"token": T, "chain": "robinhood", "ticker": "AAA", "ts": 5, "band": "DANGER", "score": 12,
                             "rug": True, "rug_drop": 0.723457,
                             "operator": {"wallets": [A, B], "share_supply": 0.1},
                             "top": {A: 0.06, B: 0.04, C: 0.02}, "early_share": 0.123457,
                             "pack": 0.0, "pack_wallets": 0, "bundle_sold": None, "bundle_held": None,
                             "mcap_usd": None, "headline": None})

    def test_no_operators_and_too_established(self):
        s = alerts.snapshot({"token": T, "chain": "solana", "band": "TOO_ESTABLISHED", "score": None, "rug": None,
                             "operators": [], "holders": []})
        self.assertEqual((s["operator"], s["top"], s["score"], s["rug"], s["early_share"]), (None, {}, None, False, None))

    def test_no_verdict(self):
        for bad in (None, {}, {"token": T}, {"band": "OK"}, "error"):
            self.assertIsNone(alerts.snapshot(bad))


class TestDiff(unittest.TestCase):

    def test_same_or_missing(self):
        self.assertEqual(alerts.diff(snap(), snap()), [])
        self.assertEqual(alerts.diff(None, snap()), [])
        self.assertEqual(alerts.diff(snap(), None), [])

    def test_verdict(self):
        ch = alerts.diff(snap(band="OK", score=70), snap(band="DANGER", score=20))
        self.assertEqual(kinds(ch), ["verdict_worse"])
        self.assertEqual(ch[0]["text"], "Verdict worsened: OK → DANGER (score 70 → 20)")
        self.assertEqual(alerts.diff(snap(band="RISKY", score=45), snap(band="CLEAN", score=85)), [])   # not an alert
        self.assertEqual(alerts.diff(snap(band="OK", score=61), snap(band="OK", score=79)), [])   # score without a band change

    def test_border_noise_is_silent(self):
        self.assertEqual(alerts.diff(snap(band="CLEAN", score=80), snap(band="OK", score=78)), [])
        self.assertEqual(alerts.diff(snap(band="OK", score=78), snap(band="CLEAN", score=81)), [])
        self.assertEqual(alerts.diff(snap(band="OK", score=62), snap(band="RISKY", score=55)), [])      # 7 points
        self.assertEqual(kinds(alerts.diff(snap(band="OK", score=62), snap(band="RISKY", score=54))),
                         ["verdict_worse"])                                                           # 8 points
        self.assertEqual(kinds(alerts.diff(snap(band="CLEAN", score=85), snap(band="OK", score=77))),
                         ["verdict_worse"])

    def test_danger_always(self):
        ch = alerts.diff(snap(band="OK", score=61), snap(band="DANGER", score=59))   # hard rule, score almost the same
        self.assertEqual(ch[0]["text"], "Verdict worsened: OK → DANGER (score 61 → 59)")
        ch = alerts.diff(snap(band="OK", score=None), snap(band="RISKY", score=None))   # no score — report it
        self.assertEqual(kinds(ch), ["verdict_worse"])

    def test_unranked_bands_not_compared(self):
        for a, b in (("TOO_EARLY_OR_LATE", "DANGER"), ("OK", "TOO_ESTABLISHED"), ("TOO_ESTABLISHED", "CLEAN")):
            self.assertNotIn("verdict_worse", kinds(alerts.diff(snap(band=a, score=None), snap(band=b, score=None))))

    def test_rug(self):
        ch = alerts.diff(snap(band="DANGER", score=20), snap(band="DANGER", score=20, rug={"drop": 0.72}))
        self.assertEqual(ch, [{"kind": "rug_appeared", "drop": 0.72,
                               "text": "Probably rug: −72% if suspicious holders sell"}])
        ch = alerts.diff(snap(band="DANGER", score=20, rug={"drop": 0.72}), snap(band="DANGER", score=20))
        self.assertEqual(ch, [{"kind": "rug_gone", "text": "Probably rug is gone (was −72%)"}])
        self.assertEqual(alerts.diff(snap(rug={"drop": 0.5}), snap(rug={"drop": 0.6})), [])   # persisted, +10 points
        ch = alerts.diff(snap(rug={"drop": 0.5}), snap(rug={"drop": 0.9}))                       # +40 points
        self.assertEqual(ch, [{"kind": "rug_worse", "from": 0.5, "to": 0.9,
                               "text": "Probably rug got worse: −50% → −90% if suspicious holders sell"}])

    def test_operator_sold(self):
        # previous operator A+B (10%): now A 3%, B dropped out of the top → 3% = sold 70%
        new = snap(ops=[{"wallets": [C], "share_supply": 0.05}],
                   holders=[{"wallet": C, "share_supply": 0.05}, {"wallet": A, "share_supply": 0.03}])
        ch = alerts.diff(snap(), new)
        self.assertEqual(kinds(ch), ["operator_sold"])
        self.assertEqual(ch[0]["text"], "Biggest operator sold 70% of their holdings (10.0% → 3.0% of supply)")

    def test_operator_threshold(self):
        just = snap(holders=[{"wallet": A, "share_supply": 0.04}, {"wallet": B, "share_supply": 0.03}])   # −30%
        self.assertEqual(kinds(alerts.diff(snap(), just)), ["operator_sold"])
        less = snap(holders=[{"wallet": A, "share_supply": 0.05}, {"wallet": B, "share_supply": 0.03}])   # −20%
        self.assertEqual(alerts.diff(snap(), less), [])
        more = snap(holders=[{"wallet": A, "share_supply": 0.08}, {"wallet": B, "share_supply": 0.04}])   # bought more
        self.assertEqual(alerts.diff(snap(), more), [])

    def test_operator_tiny_or_no_top(self):
        tiny = snap(ops=[{"wallets": [A], "share_supply": 0.0005}], holders=[{"wallet": A, "share_supply": 0.0005}])
        self.assertEqual(alerts.diff(tiny, snap(holders=[{"wallet": B, "share_supply": 0.04}])), [])
        # new scan without holders (TOO_ESTABLISHED) — not "sold everything"
        est = snap(band="TOO_ESTABLISHED", score=None, ops=[], holders=[])
        self.assertNotIn("operator_sold", kinds(alerts.diff(snap(), est)))

    def test_early_dropped(self):
        ch = alerts.diff(snap(early_share=0.20), snap(early_share=0.12))
        self.assertEqual(ch, [{"kind": "early_dropped", "fell": 0.4, "from": 0.2, "to": 0.12,
                               "text": "Early buyers are exiting: their share fell 40% (20.0% → 12.0% of supply)"}])
        self.assertEqual(alerts.diff(snap(early_share=0.20), snap(early_share=0.15)), [])      # −25%
        self.assertEqual(alerts.diff(snap(early_share=0.20), snap(early_share=None)), [])      # unknown
        self.assertEqual(alerts.diff(snap(early_share=None), snap(early_share=0.01)), [])
        self.assertEqual(alerts.diff(snap(early_share=0.004), snap(early_share=0.0)), [])      # was < 0.5%

    def test_several_at_once(self):
        new = snap(band="DANGER", score=10, rug={"drop": 0.8}, early_share=0.05,
                   holders=[{"wallet": C, "share_supply": 0.02}])
        self.assertEqual(kinds(alerts.diff(snap(early_share=0.2), new)),
                         ["verdict_worse", "rug_appeared", "operator_sold", "early_dropped"])

    def test_json_roundtrip(self):
        old, new = snap(early_share=0.2), snap(band="DANGER", score=10, early_share=0.1)
        self.assertEqual(alerts.diff(json.loads(json.dumps(old)), json.loads(json.dumps(new))), alerts.diff(old, new))

    def test_coordinated_pack(self):
        hs = [{"wallet": A, "share": 0.04, "share_supply": 0.02}, {"wallet": B, "share": 0.03, "share_supply": 0.015},
              {"wallet": C, "share": 0.01, "share_supply": 0.005}]
        op = [{"wallets": [A, B], "share_supply": 0.035}]                                     # the operator holds
        old = snap(holders=hs, ops=op)
        new = snap(holders=hs, ops=op, packs=[{"block": 9, "wallets": [A, B, C]}])
        self.assertEqual(new["pack"], 0.08)
        ch = alerts.diff(old, new)
        self.assertEqual(kinds(ch), ["coordinated"])
        self.assertEqual(ch[0]["text"], "Coordinated wallets: a same-block pack of 3 wallets now holds 8.0% of float")
        small = snap(holders=hs, ops=op, packs=[{"block": 9, "wallets": [C]}])                      # 1% of float
        self.assertEqual(alerts.diff(old, small), [])
        self.assertEqual(alerts.diff(new, new), [])                                          # the same pack again

    def test_coordinated_bundle_selling(self):
        b = lambda sold: {"supply": {"sold_share": sold, "concentrated_share": 0.2}}
        ch = alerts.diff(snap(bundle=b(0.05)), snap(bundle=b(0.20)))
        self.assertEqual(ch, [{"kind": "coordinated", "signal": "bundle", "sold": 0.15,
                               "text": "Coordinated selling: the launch bundle sold another 15.0% of supply"}])
        self.assertEqual(alerts.diff(snap(bundle=b(0.05)), snap(bundle=b(0.12))), [])        # +7 points
        self.assertEqual(alerts.diff(snap(bundle=None), snap(bundle=b(0.5))), [])             # not measured before

    def test_json_roundtrip(self):
        old, new = snap(early_share=0.2), snap(band="DANGER", score=10, early_share=0.1)
        self.assertEqual(alerts.diff(json.loads(json.dumps(old)), json.loads(json.dumps(new))), alerts.diff(old, new))


if __name__ == "__main__":
    unittest.main()
