"""Phase 13: historical proof — classification, metrics with the full denominator, the tracker, the endpoints, and on a
real Postgres (CRAWLER_PG): append-only records, the fingerprint, proof_rows' observation window and track_due."""
import http.client, json, os, subprocess, time, unittest
from unittest import mock

from crawler import memory_db, proof
from test_event_stream import StreamBase

NOW = 2_000_000_000.0
H = 3600


def iso(ts):
    return time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime(ts))


def row(band="DANGER", age_h=200, trough=None, peak=None, end=None, last_h=190, n=10, mcap=100_000.0, **k):
    """A proof_rows row for a scan age_h hours ago; trough / peak / end are multiples of the scan-time market cap."""
    t0 = NOW - age_h * H
    r = {"scan_id": k.pop("scan_id", 1), "chain": "base", "token": "0xt", "ticker": "T", "scanned_at": iso(t0),
         "band": band, "score": 30, "scores": {"crawlmeme-1": {"band": band, "score": 30}},
         "market": {"mcap_usd": mcap, "price_usd": 1.0}, "record_hash": "h", "n": n,
         "trough_mcap": None if trough is None else trough * mcap, "peak_mcap": None if peak is None else peak * mcap,
         "trough_price": trough, "peak_price": peak, "last_obs": iso(t0 + last_h * H) if n else None,
         "last": {"mcap_usd": None if end is None else end * mcap, "observed_at": iso(t0 + last_h * H)}}
    r.update(k)
    return r


P = proof.params({})


class TestClassify(unittest.TestCase):

    def c(self, r, **q):
        p = proof.params({k: [str(v)] for k, v in q.items()}) if q else P
        return proof.classify(r, p, NOW)

    def test_rug_cases(self):
        self.assertEqual(self.c(row(trough=0.05, peak=1.2, end=0.06))["rug"], "TP")
        self.assertEqual(self.c(row(trough=0.5, peak=1.2, end=0.6))["rug"], "FP")
        self.assertEqual(self.c(row(band="CLEAN", trough=0.08, peak=1.0, end=0.1))["rug"], "FN")
        self.assertEqual(self.c(row(band="CLEAN", trough=0.7, peak=1.4, end=0.9))["rug"], "TN")

    def test_exactly_ninety_percent_counts(self):
        self.assertEqual(self.c(row(trough=0.1, peak=1.0, end=0.1))["rug"], "TP")

    def test_resolution(self):
        # an event is resolved as soon as it is seen, even inside the window
        self.assertEqual(self.c(row(age_h=10, trough=0.02, peak=1.0, end=0.02, last_h=9))["rug"], "TP")
        # no event yet and the window is still open: unresolved, not a negative
        self.assertEqual(self.c(row(age_h=10, trough=0.6, peak=1.0, end=0.6, last_h=9))["rug"], "UNRESOLVED")
        # window over but no snapshot in its last quarter: unresolved
        self.assertEqual(self.c(row(trough=0.6, peak=1.0, end=0.6, last_h=40))["rug"], "UNRESOLVED")
        # no snapshots at all
        self.assertEqual(self.c(row(n=0, last_h=0))["rug"], "UNRESOLVED")

    def test_measure_at_window_end(self):
        r = row(trough=0.05, peak=1.0, end=0.5)     # dumped then recovered
        self.assertEqual(self.c(r)["rug"], "TP")
        self.assertEqual(self.c(r, measure="end")["rug"], "FP")

    def test_gem_and_criteria(self):
        r = row(band="CLEAN", trough=0.9, peak=3.5, end=2.0)
        self.assertEqual(self.c(r)["gem"], "TP")
        self.assertEqual(self.c(r, mult=4)["gem"], "FP")         # flagged CLEAN, rose 3.5x: short of a 4x gem
        self.assertEqual(self.c(row(band="OK", trough=0.9, peak=5, end=5))["gem"], "FN")
        self.assertEqual(self.c(row(trough=0.5, peak=1.2), drop=0.5)["rug"], "TP")

    def test_shadow_model_verdict(self):
        r = row(band="RISKY", trough=0.02, peak=1, end=0.02,
                scores={"crawlmeme-1": {"band": "RISKY"}, "crawlmeme-3": {"band": "DANGER", "score": 20}})
        self.assertEqual(self.c(r)["rug"], "FN")
        self.assertEqual(self.c(r, model="crawlmeme-3")["rug"], "TP")

    def test_price_basis_when_no_market_cap(self):
        r = row(trough=0.05, peak=1.0, end=0.05)
        r.update(market={"price_usd": 2.0}, trough_mcap=None, peak_mcap=None, trough_price=0.08, peak_price=2.2)
        c = self.c(r)
        self.assertEqual((c["basis"], c["rug"]), ("price", "TP"))
        self.assertAlmostEqual(c["trough"], 0.04)


class TestMetrics(unittest.TestCase):

    def test_full_denominator(self):
        m = proof.metrics(["TP", "TP", "FP", "FN", "TN", "TN", "TN", "UNRESOLVED"])
        self.assertEqual((m["eligible"], m["resolved"], m["UNRESOLVED"]), (8, 7, 1))
        self.assertAlmostEqual(m["precision"], 2 / 3)
        self.assertAlmostEqual(m["recall"], 2 / 3)
        self.assertAlmostEqual(m["coverage"], 7 / 8)
        self.assertAlmostEqual(m["base_rate"], 3 / 7)
        lo, hi = m["precision_ci"]
        self.assertTrue(0 < lo < 2 / 3 < hi < 1)

    def test_empty_and_wilson(self):
        m = proof.metrics([])
        self.assertIsNone(m["precision"])
        self.assertIsNone(m["coverage"])
        self.assertIsNone(proof.wilson(0, 0))
        lo, hi = proof.wilson(50, 100)
        self.assertAlmostEqual(lo, 0.4038, places=3)
        self.assertAlmostEqual(hi, 0.5962, places=3)

    def test_report_views(self):
        rows = [row(scan_id=1, trough=0.05, peak=1, end=0.05), row(scan_id=2, trough=0.6, peak=1, end=0.7),
                row(scan_id=3, band="CLEAN", trough=0.9, peak=4, end=3), row(scan_id=4, band="CLEAN", mcap=500.0,
                                                                              trough=0.9, peak=4, end=3)]
        out = proof.report(rows, proof.params({"view": ["rug"]}), NOW)
        self.assertEqual(([i["scan_id"] for i in out["items"]], out["eligible"]), ([1], 4))   # hits shown, all counted
        self.assertEqual(out["metrics"]["rug"]["FP"], 1)
        out = proof.report(rows, proof.params({"view": ["gem"]}), NOW)
        self.assertEqual([i["scan_id"] for i in out["items"]], [3, 4])
        out = proof.report(rows, proof.params({"view": ["rug"], "cls": ["fp"]}), NOW)
        self.assertEqual([i["scan_id"] for i in out["items"]], [2])
        out = proof.report(rows, proof.params({"min_mcap": ["1000"]}), NOW)
        self.assertEqual(out["eligible"], 3)
        item = proof.report(rows, P, NOW)["items"][0]
        self.assertEqual(item["links"]["record"], "/api/proof/scan?id=1")
        self.assertTrue(item["links"]["explorer"].startswith("https://basescan.org/token/"))

    def test_params_bounded(self):
        p = proof.params({"window_h": ["99999"], "drop": ["2"], "model": ["evil"], "view": ["x"], "cls": ["zz"]})
        self.assertEqual((p["window_h"], p["drop"], p["model"], p["view"], p["cls"]),
                         (720, 0.999, "crawlmeme-1", "all", None))


class TestQueryAndTracker(unittest.TestCase):

    def test_query(self):
        proof._cache.clear()
        with mock.patch.object(memory_db, "enabled", return_value=False):
            self.assertFalse(proof.query({})["available"])
        with mock.patch.object(memory_db, "enabled", return_value=True), \
                mock.patch.object(memory_db, "call", return_value=[row(trough=0.05, peak=1, end=0.05)]) as c:
            a = proof.query({"view": ["rug"]})
            proof.query({"view": ["rug"]})
        self.assertEqual(c.call_count, 1)
        self.assertEqual(c.call_args[0][0], "proof_rows")
        self.assertEqual(c.call_args[0][1]["p_window_h"], 168)
        self.assertTrue(a["available"])

    def test_fetch_batch(self):
        pairs = [{"chainId": "base", "baseToken": {"address": "0xAbC"}, "priceUsd": "2", "marketCap": 2000,
                  "fdv": 2100, "liquidity": {"usd": 500}},
                 {"chainId": "base", "baseToken": {"address": "0xabc"}, "priceUsd": "1.9", "marketCap": 1900,
                  "liquidity": {"usd": 100}},
                 {"chainId": "solana", "baseToken": {"address": "0xabc"}, "priceUsd": "9"}]
        with mock.patch.object(proof.market, "_ds", return_value=pairs):
            got = proof.fetch_batch("base", ["0xabc", "0xmissing"])
        self.assertEqual(got, {"0xabc": {"price_usd": 2.0, "mcap_usd": 2000.0, "fdv_usd": 2100.0,
                                         "liquidity_usd": 600.0, "source": "dexscreener"}})

    def test_track(self):
        due = [{"chain": "base", "token": f"0x{i}"} for i in range(35)] + [{"chain": "solana", "token": "S"}]
        calls = []

        def call(name, args):
            if name == "track_due":
                return due
            calls.append(args["p"]["rows"])
            return len(args["p"]["rows"])
        fetch = lambda chain, toks: {t: {"price_usd": 1.0} for t in toks[:-1]}   # one per batch not listed
        with mock.patch.object(memory_db, "call", side_effect=call), mock.patch.object(proof, "TRACK_PAUSE_S", 0):
            out = proof.track(fetch=fetch)
        self.assertEqual((out["due"], out["requests"], out["missing"], out["sampled"]), (36, 3, 3, 33))

    def test_track_reports_memory_failure(self):
        with mock.patch.object(memory_db, "call", return_value=None):
            with self.assertRaises(memory_db.MemoryUnavailable):
                proof.track()


class TestEndpoints(StreamBase):

    def test_pages_and_redirects(self):
        code, _, raw = self.call("GET", "/proof")
        self.assertEqual(code, 200)
        self.assertIn(b"proof of work", raw)
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        conn.request("GET", "/gems")
        r = conn.getresponse()
        self.assertEqual((r.status, r.getheader("location")), (302, "/proof?view=gem"))
        conn.close()

    def test_api(self):
        with mock.patch.object(proof, "query", return_value={"available": True, "items": []}):
            self.assertEqual(json.loads(self.call("GET", "/api/proof?view=rug")[2]), {"available": True, "items": []})
        self.assertEqual(self.call("GET", "/api/proof/scan?id=abc")[0], 400)
        with mock.patch.object(memory_db, "enabled", return_value=True), \
                mock.patch.object(memory_db, "call", return_value=None):
            self.assertEqual(self.call("GET", "/api/proof/scan?id=7")[0], 404)

    def test_tracker_auth(self):
        with mock.patch.dict(os.environ, {"CRON_SECRET": "s"}), mock.patch.object(proof, "track") as t:
            self.assertEqual(self.call("GET", "/api/cron/track")[0], 401)
            t.assert_not_called()
            t.return_value = {"due": 0}
            code, _, raw = self.call("GET", "/api/cron/track", headers={"authorization": "Bearer s"})
        self.assertEqual((code, json.loads(raw)), (200, {"due": 0}))


PG = os.environ.get("CRAWLER_PG")


@unittest.skipUnless(PG, "set CRAWLER_PG to run the proof SQL against a local Postgres")
class TestProofSQL(unittest.TestCase):

    def psql(self, q, role=True, ok=True):
        host, port = PG.split(":")
        out = subprocess.run(["psql", "-h", host, "-p", port, "-U", "postgres", "-At", "-v", "ON_ERROR_STOP=1",
                              "-c", ("set role service_role; " if role else "") + q],
                             capture_output=True, text=True, timeout=30)
        if ok:
            self.assertEqual(out.returncode, 0, out.stderr)
        return out

    def test_records_are_immutable_and_fingerprinted(self):
        chain = "test-" + os.urandom(4).hex()
        p = {"chain": chain, "token": "t1", "band": "DANGER", "score": 20,
             "market": {"price_usd": 1.0, "mcap_usd": 1000, "liquidity_usd": 500}}
        sid = self.psql("select public.memory_record('" + json.dumps(p) + "'::jsonb)").stdout.strip().splitlines()[-1]
        rec = json.loads(self.psql(f"select public.proof_scan({sid})").stdout.strip().splitlines()[-1])
        self.assertTrue(rec["fingerprint_ok"])
        self.assertEqual(len(rec["scan"]["record_hash"]), 64)
        for q in (f"update public.scans set band = 'CLEAN' where id = {sid}",
                  f"delete from public.scans where id = {sid}",
                  f"update public.market_snapshots set price_usd = 9 where scan_id = {sid}"):
            out = self.psql(q, role=False, ok=False)     # even the database owner: the trigger, not a grant, stops it
            self.assertNotEqual(out.returncode, 0, q)
            self.assertIn("append-only", out.stderr)
        # an outcome can be measured once, then never rewritten
        self.psql(f"update public.outcomes set due_at = now() - interval '1 h' where scan_id = {sid}", role=False)
        rows = [{"scan_id": int(sid), "horizon_h": 24, "label": "rugged", "chain": chain, "token": "t1",
                 "basis": {"price_usd": 0.01}}]
        self.psql("select public.outcome_record('" + json.dumps({"rows": rows}) + "'::jsonb)")
        out = self.psql(f"update public.outcomes set label = 'up' where scan_id = {sid} and horizon_h = 24",
                        role=False, ok=False)
        self.assertIn("immutable", out.stderr)

    def test_proof_rows_window_and_track_due(self):
        chain = "test-" + os.urandom(4).hex()
        p = {"chain": chain, "token": "t2", "band": "DANGER", "score": 20,
             "market": {"price_usd": 1.0, "mcap_usd": 1000, "liquidity_usd": 500}}
        self.psql("select public.memory_record('" + json.dumps(p) + "'::jsonb)")
        snaps = [{"chain": chain, "token": "t2", "price_usd": 0.05, "mcap_usd": 50},
                 {"chain": chain, "token": "t2", "price_usd": 0.4, "mcap_usd": 400}]
        self.psql("select public.market_snapshot_record('" + json.dumps({"rows": snaps}) + "'::jsonb)")
        rows = json.loads(self.psql(f"select public.proof_rows(now() - interval '1 h', now() + interval '1 h', "
                                    f"168, '{chain}', 100)").stdout.strip().splitlines()[-1])
        self.assertEqual(len(rows), 1)
        r = rows[0]
        self.assertEqual((r["n"], r["trough_mcap"], r["peak_mcap"]), (2, 50, 400))   # the scan's own snapshot excluded
        c = proof.classify(r, proof.params({}), time.time())
        self.assertEqual(c["rug"], "TP")                                              # -95% already seen
        due = json.loads(self.psql("select public.track_due(168, 1, 1000)").stdout.strip().splitlines()[-1])
        self.assertNotIn({"chain": chain, "token": "t2"}, due)                        # sampled moments ago
        due = json.loads(self.psql("select public.track_due(168, 0, 1000)").stdout.strip().splitlines()[-1])
        self.assertIsInstance(due, list)


if __name__ == "__main__":
    unittest.main()
