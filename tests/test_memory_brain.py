"""Phase 11: outcome labels and worker, the cron endpoint's auth, crawlmeme-3 (evidence-backed, leakage-safe),
observations with evidence, wallet events, and the result reaching the browser before the memory write."""
import http.client, json, os, threading, time, unittest
from unittest import mock

import fakes
from crawler import cabals, memory_db, outcomes, scoring, server
from test_event_stream import StreamBase, parse


class TestLabels(unittest.TestCase):

    def test_rules(self):
        self.assertEqual(outcomes.label(1.0, 1000.0, 0.05, 50.0)[0], "rugged")      # pool drained, price gone
        self.assertEqual(outcomes.label(1.0, 1000.0, None, 10.0)[0], "rugged")      # pool pulled, no price
        self.assertEqual(outcomes.label(1.0, 1000.0, 0.05, 900.0)[0], "dead")       # price gone, pool still there
        self.assertEqual(outcomes.label(1.0, 1000.0, 2.5, 3000.0)[0], "up")
        self.assertEqual(outcomes.label(1.0, 1000.0, 0.8, 900.0)[0], "survived")
        self.assertEqual(outcomes.label(1.0, 1000.0, None, None), (None, None, None))
        lab, pr, lr = outcomes.label(2.0, 100.0, 1.0, 50.0)
        self.assertEqual((lab, pr, lr), ("survived", 0.5, 0.5))


class TestWorker(unittest.TestCase):

    def due(self, **k):
        return {"scan_id": 1, "horizon_h": 24, "chain": "base", "token": "0xt", "due_at": "2026-10-22T10:00:00+00:00",
                "price_usd": 1.0, "liquidity_usd": 1000.0} | k

    def test_measures_once_per_token_and_records(self):
        rows = [self.due(), self.due(scan_id=2, horizon_h=168), self.due(scan_id=3, token="0xu")]
        calls = []
        fetch = lambda tok, net: calls.append((tok, net)) or {"price_usd": 0.01, "liquidity_usd": 5.0, "source": "gt"}
        with mock.patch.object(memory_db, "outcomes_due", return_value=rows), \
                mock.patch.object(memory_db, "outcome_record", side_effect=lambda r: len(r)) as rec:
            out = outcomes.run(fetch=fetch)
        self.assertEqual(sorted(calls), [("0xt", "base"), ("0xu", "base")])           # one read per token
        self.assertEqual((out["due"], out["measured"], out["tokens"]), (3, 3, 2))
        sent = rec.call_args[0][0]
        self.assertEqual({r["label"] for r in sent}, {"rugged"})
        self.assertEqual(sent[0]["basis"]["scan_price_usd"], 1.0)
        self.assertEqual(sent[0]["basis"]["rules"]["rug_liq"], outcomes.RUG_LIQ)

    def test_no_market_data_waits_then_unpriced(self):
        fresh = self.due(due_at=time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime(time.time() - 3600)))
        stale = self.due(scan_id=9, due_at="2020-01-01T00:00:00+00:00")
        with mock.patch.object(memory_db, "outcomes_due", return_value=[fresh, stale]), \
                mock.patch.object(memory_db, "outcome_record", side_effect=lambda r: len(r)) as rec:
            out = outcomes.run(fetch=lambda tok, net: {})
        self.assertEqual((out["pending"], out["unpriced"]), (1, 1))
        self.assertEqual([(r["scan_id"], r["label"]) for r in rec.call_args[0][0]], [(9, "unpriced")])


class TestModelV3(unittest.TestCase):

    V1 = {"score": 72, "band": "OK", "gates": []}

    def test_outcome_backed_adjustments_with_evidence(self):
        mem = {"deployer": {"rugged": 2, "labelled": 3, "group": ["dev0"],
                            "list": [{"token": "a", "outcome": "rugged", "outcome_at": "t1"},
                                     {"token": "b", "outcome": "rugged", "outcome_at": "t2"},
                                     {"token": "c", "outcome": "survived", "outcome_at": "t3"}]},
               "seen_wallets": [{"wallet": "w1", "rugged": 2, "list": []}, {"wallet": "w2", "rugged": 3, "list": []}],
               "clusters": [{"token": "z", "level": "proven", "wallets": ["w1", "w2"], "at": "t0"}]}
        v3 = scoring.evaluate(self.V1, mem)["crawlmeme-3"]
        codes = [a["code"] for a in v3["adjustments"]]
        self.assertEqual(codes, ["rug_history", "rugged_regulars", "recurring_cluster"])
        self.assertEqual([e["token"] for e in v3["adjustments"][0]["evidence"]], ["a", "b"])
        self.assertIn("dev group", v3["adjustments"][0]["why"])
        self.assertLessEqual(v3["score"], scoring.SOFT_CAP_SCORE)
        self.assertIn(v3["band"], ("RISKY", "DANGER"))

    def test_ignores_verdict_labels(self):
        # crawlmeme-2 reacts to our own DANGER verdicts; crawlmeme-3 does not (circular labels)
        mem = {"deployer": {"tokens": 3, "danger": 3, "rugged": 0}, "flags": ["danger_regulars"], "seen_wallets": []}
        res = scoring.evaluate(self.V1, mem)
        self.assertTrue(res["crawlmeme-2"]["adjustments"])
        self.assertEqual(res["crawlmeme-3"], {"score": 72, "band": "OK", "gates": [], "adjustments": []})

    def test_never_raises_danger(self):
        v1 = {"score": 30, "band": "DANGER", "gates": ["x"]}
        self.assertEqual(scoring.evaluate(v1, {"deployer": {"rugged": 5}})["crawlmeme-3"]["band"], "DANGER")


class TestCabalsOutcomes(unittest.TestCase):

    def test_flags(self):
        mem = {"seen": {"w1": {"tokens": 3, "danger": 0, "rugged": 2}, "w2": {"tokens": 2, "danger": 0, "rugged": 2}},
               "deployer": {"tokens": 1, "rugged": 2, "labelled": 2, "group": ["dev0"], "list": []},
               "clusters": [{"token": "z", "level": "proven", "wallets": ["w1", "w2"]}, {"wallets": ["w1"]}]}
        out = cabals.analyse(mem, [("w1", 1, 0.5), ("w2", 1, 0.5)], 1.0, {})
        self.assertTrue({"rug_history", "rugged_regulars", "dev_group", "recurring_cluster"} <= set(out["flags"]))
        self.assertEqual(len(out["clusters"]), 1)
        self.assertEqual(out["deployer"]["group"], ["dev0"])


class TestPayload(unittest.TestCase):

    def test_observations_need_evidence(self):
        res = {"graph": {"edges": [
            {"a": "w1", "b": "w2", "kind": "same_tx", "tx": "0xab", "confidence": 1.0},
            {"a": "w1", "b": "w3", "kind": "pack", "confidence": 0.5},
            {"a": "cex", "b": "w4", "kind": "funding", "confidence": 0.2},            # hub: no claim
            {"a": "f", "b": "w5", "kind": "funding", "confidence": 0.85, "tx": "0xf"},
            {"a": "d", "b": "w6", "kind": "sent_token", "confidence": 0.9}]},
            "bundle": {"participants": [{"wallet": "w7", "kind": "sniper", "tx": "0x7", "acquired_share": 0.01}]},
            "memory": {"crews": [{"wallets": ["w1", "w2"], "tokens": ["x", "y"], "together": 2}]}}
        obs = memory_db.observations(res)
        got = {(o["subject"], o["kind"], o["status"]) for o in obs}
        self.assertEqual(got, {("w1", "shared_transaction", "confirmed"), ("w1", "pack", "unconfirmed"),
                               ("f", "common_funder", "unconfirmed"), ("w7", "launch_sniper", "confirmed"),
                               ("w1", "crew", "unconfirmed"), ("w2", "crew", "unconfirmed")})
        self.assertEqual(next(o for o in obs if o["kind"] == "shared_transaction")["evidence"]["tx"], "0xab")

    def test_scan_payload_carries_phase11(self):
        memo = {}
        with fakes.patched():
            from crawler import engine
            res = engine.scan(fakes.TOKEN, memory_out=memo)
        p = memory_db.payload(res, memo)
        self.assertTrue(p["events"])
        self.assertTrue({"wallet", "kind", "block", "tx", "amount"} <= set(p["events"][0]))
        self.assertEqual(p["launch"]["block"], res["launch"]["block"])
        self.assertEqual(p["bundle"]["mode"], res["bundle"]["mode"])
        self.assertEqual(p["confidence"], res["data_health"]["confidence"])
        self.assertIn("crawlmeme-3", p["scores"])
        self.assertNotIn("events", res)                       # events reach memory only, never the browser


class TestCronEndpoint(StreamBase):

    def test_fails_closed_and_checks_the_secret(self):
        with mock.patch.dict(os.environ, {"CRON_SECRET": ""}):
            self.assertEqual(self.call("GET", "/api/cron/outcomes")[0], 503)
        with mock.patch.dict(os.environ, {"CRON_SECRET": "s3cret-value"}), \
                mock.patch.object(outcomes, "run", return_value={"due": 0}) as run:
            self.assertEqual(self.call("GET", "/api/cron/outcomes")[0], 401)
            self.assertEqual(self.call("GET", "/api/cron/outcomes", headers={"authorization": "Bearer nope"})[0], 401)
            run.assert_not_called()
            code, _, raw = self.call("GET", "/api/cron/outcomes", headers={"authorization": "Bearer s3cret-value"})
            self.assertEqual((code, json.loads(raw)), (200, {"due": 0}))


class TestResultBeforeMemoryWrite(StreamBase):

    def test_result_streams_while_memory_write_runs(self):
        gate, stamps = threading.Event(), {}

        def slow_record(res, extra=None):
            stamps["record_start"] = time.time()
            gate.wait(5)
            stamps["record_end"] = time.time()

        with mock.patch.object(server.memory_db, "record", side_effect=slow_record):
            conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=20)
            conn.request("POST", "/api/scan", json.dumps({"token": fakes.TOKEN}),
                         {"content-type": "application/json", "accept": "text/event-stream"})
            r = conn.getresponse()
            buf = b""
            while b"event: result" not in buf:
                buf += r.read1(65536)
            stamps["result"] = time.time()
            threading.Timer(0.3, gate.set).start()
            buf += r.read()           # the response ends only after the memory write
            stamps["closed"] = time.time()
            conn.close()
        self.assertLess(stamps["result"], stamps["record_end"])          # the browser had the result first
        self.assertGreaterEqual(stamps["closed"], stamps["record_end"])   # the request outlived the write
        self.assertEqual(parse(buf)[-1][0], "result")


if __name__ == "__main__":
    unittest.main()
