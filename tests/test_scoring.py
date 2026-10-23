"""Phase 8: scoring models (crawler/scoring.py), the live/shadow split, /api/rescore and data health. No network."""
import json, os, threading, unittest, urllib.request
from http.server import ThreadingHTTPServer
from unittest import mock

import fakes
from crawler import engine, scoring, server

V1 = {"score": 72, "band": "OK", "gates": []}


class TestModels(unittest.TestCase):

    def test_v1_untouched_and_v2_without_memory_agrees(self):
        out = scoring.evaluate(V1, None)
        self.assertEqual((out["crawlmeme-1"]["score"], out["crawlmeme-1"]["band"]), (72, "OK"))
        self.assertEqual((out["crawlmeme-2"]["score"], out["crawlmeme-2"]["adjustments"]), (72, []))

    def test_v2_adjustments_and_caps(self):
        mem = {"crews": [{"share": 0.05}, {"share": 0.02}], "deployer": {"tokens": 3, "danger": 2},
               "repeat_funders": [{"hub": False, "other_tokens": 2}], "flags": ["danger_regulars"]}
        v2 = scoring.evaluate({"score": 95, "band": "CLEAN", "gates": []}, mem)["crawlmeme-2"]
        codes = [a["code"] for a in v2["adjustments"]]
        self.assertEqual(codes, ["crew", "serial_deployer_danger", "repeat_funder", "danger_regulars"])
        self.assertEqual(sum(a["points"] for a in v2["adjustments"]), -(15 + 10 + 5 + 5))   # crews capped at 15
        self.assertEqual((v2["score"], v2["band"]), (59, "RISKY"))                         # soft cap: ≤ RISKY
        self.assertTrue(any(g.startswith("soft:") for g in v2["gates"]))

    def test_hub_funder_and_small_deployer(self):
        mem = {"repeat_funders": [{"hub": True, "other_tokens": 9}], "deployer": {"tokens": 2, "danger": 0}}
        v2 = scoring.evaluate(V1, mem)["crawlmeme-2"]
        self.assertEqual([a["code"] for a in v2["adjustments"]], ["serial_deployer"])
        self.assertEqual((v2["score"], v2["band"]), (68, "OK"))

    def test_never_raises_and_no_verdict_stays_none(self):
        v2 = scoring.evaluate({"score": 30, "band": "DANGER", "gates": ["x"]}, {"deployer": {"tokens": 2}})
        self.assertEqual(v2["crawlmeme-2"]["band"], "DANGER")
        self.assertLessEqual(v2["crawlmeme-2"]["score"], 30)
        none = scoring.evaluate({"score": None, "band": "TOO_EARLY_OR_LATE", "gates": []}, {"crews": [{"share": 1}]})
        self.assertEqual((none["crawlmeme-2"]["score"], none["crawlmeme-2"]["band"]), (None, "TOO_EARLY_OR_LATE"))

    def test_current_from_env(self):
        with mock.patch.dict(os.environ, {"SCORE_MODEL": "crawlmeme-2"}):
            self.assertEqual(scoring.current(), "crawlmeme-2")
        with mock.patch.dict(os.environ, {"SCORE_MODEL": "nope"}):
            self.assertEqual(scoring.current(), "crawlmeme-1")

    def test_rescore_recomputes_from_stored_v1(self):
        res = {"model": {"id": "crawlmeme-1", "scores": {"crawlmeme-1": V1}},
               "memory": {"deployer": {"tokens": 4, "danger": 3}}}
        out = scoring.rescore(res)
        self.assertEqual(out["crawlmeme-2"]["band"], "RISKY")
        self.assertEqual(list(scoring.rescore(res, "crawlmeme-1")), ["crawlmeme-1"])
        with self.assertRaises(KeyError):
            scoring.rescore(res, "crawlmeme-9")


class TestDataHealth(unittest.TestCase):

    def cov(self, level, read, examined, verified=True):
        return {"level": level, "reasons": [], "wallets": {"read": read},
                "holders": {"examined": examined, "method": "full_history", "history": {"verified": verified}}}

    def test_confidence(self):
        h = lambda c: scoring.data_health(c, None, None, False, "gt", 10, 5)["confidence"]
        self.assertEqual(h(self.cov("full", 20, 20)), "high")
        self.assertEqual(h(self.cov("partial", 15, 20)), "medium")
        self.assertEqual(h(self.cov("full", 20, 20, verified=False)), "medium")
        self.assertEqual(h(self.cov("limited", 20, 20)), "low")
        self.assertEqual(h(self.cov("partial", 5, 20)), "low")
        self.assertIsNone(scoring.data_health(self.cov("full", 1, 1), None, None, False, None, 1, 1)["memory"])


class TestEngineAndApi(unittest.TestCase):

    def test_scan_carries_models_and_health(self):
        with fakes.patched():
            r = engine.scan(fakes.TOKEN)
        self.assertEqual(r["model"]["id"], "crawlmeme-1")
        self.assertEqual(set(r["model"]["scores"]), set(scoring.MODELS))
        self.assertEqual(r["model"]["scores"]["crawlmeme-1"]["score"], r["score"])
        self.assertIn(r["data_health"]["confidence"], ("high", "medium", "low"))

    def test_live_v2_changes_the_verdict(self):
        mem = {"deployer": {"tokens": 3, "danger": 3}, "crews": [], "repeat_funders": [], "flags": []}
        with fakes.patched(), mock.patch.object(engine.cabals, "analyse", return_value=mem), \
                mock.patch.dict(os.environ, {"SCORE_MODEL": "crawlmeme-2"}):
            r = engine.scan(fakes.TOKEN)
        v1 = r["model"]["scores"]["crawlmeme-1"]
        self.assertEqual(r["model"]["id"], "crawlmeme-2")
        self.assertEqual(r["score"], r["model"]["scores"]["crawlmeme-2"]["score"])
        if v1["score"] is not None and v1["score"] > scoring.SOFT_CAP_SCORE:
            self.assertLessEqual(r["score"], scoring.SOFT_CAP_SCORE)

    def test_rescore_endpoint(self):
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.H)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        self.addCleanup(httpd.server_close)
        self.addCleanup(httpd.shutdown)
        base = f"http://127.0.0.1:{httpd.server_address[1]}"
        with fakes.patched():
            res = engine.scan(fakes.TOKEN)
        with server._lock:
            server.JOBS["rs1"] = {"token": fakes.TOKEN, "chain": "robinhood", "events": [], "done": True,
                                  "result": res, "error": None, "ts": 0}
        try:
            with urllib.request.urlopen(base + "/api/rescore?job=rs1") as r:
                body = json.loads(r.read())
            self.assertEqual(set(body["models"]), set(scoring.MODELS))
            with self.assertRaises(urllib.error.HTTPError) as cm:
                urllib.request.urlopen(base + "/api/rescore?job=rs1&model=bogus")
            self.assertEqual(cm.exception.code, 400)
        finally:
            with server._lock:
                server.JOBS.pop("rs1", None)


if __name__ == "__main__":
    unittest.main()
