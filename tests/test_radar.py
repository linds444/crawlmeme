"""Phase 12: launch radar — evaluation (downgrade / remove rules), filters, discovery, endpoints, rescans, and the
radar_feed SQL on a real Postgres (CRAWLER_PG)."""
import json, os, subprocess, time, unittest
from unittest import mock

import fakes
from crawler import memory_db, radar, server
from test_event_stream import StreamBase

NOW = 2_000_000_000.0


def iso(ts):
    return time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime(ts))


def row(**k):
    base = {"chain": "base", "token": "0xt", "ticker": "T", "launchpad": "clanker",
            "launched_at": iso(NOW - 20 * 60), "first_scan_at": iso(NOW - 15 * 60),
            "scan": {"scanned_at": iso(NOW - 10 * 60), "band": "CLEAN", "score": 85, "coverage": "full",
                     "largest_operator_share": 0.04, "rug": False,
                     "market": {"price_usd": 1.0, "liquidity_usd": 10000.0, "mcap_usd": 50000.0},
                     "scores": {"crawlmeme-1": {"band": "CLEAN"}}},
            "prev": None, "bundle": {"concentrated_share": 0.02, "sold_share": 0.01}, "prev_bundle": None,
            "market_now": None, "outcome": None}
    for key, v in k.items():
        if isinstance(v, dict) and isinstance(base.get(key), dict):
            base[key] = {**base[key], **v}
        else:
            base[key] = v
    return base


class TestEvaluate(unittest.TestCase):

    def ev(self, r, **k):
        return radar.evaluate(r, NOW, **k)

    def test_clean_fresh_is_active(self):
        e = self.ev(row())
        self.assertEqual((e["status"], e["reasons"]), ("active", []))
        self.assertEqual(e["scan_age_min"], 10.0)

    def test_verdict_deteriorates(self):
        e = self.ev(row(scan={"band": "RISKY"}, prev={"band": "CLEAN"}))
        self.assertEqual(e["status"], "removed")
        self.assertIn("verdict risky (was clean)", e["reasons"])
        e = self.ev(row(scan={"band": "OK"}, prev={"band": "CLEAN"}))
        self.assertEqual((e["status"], e["reasons"]), ("downgraded", ["verdict slipped from clean to ok"]))

    def test_operator_and_bundle_selling(self):
        e = self.ev(row(scan={"largest_operator_share": 0.03}, prev={"band": "CLEAN", "largest_operator_share": 0.12},
                        bundle={"sold_share": 0.09}, prev_bundle={"sold_share": 0.01}))
        self.assertEqual(e["status"], "downgraded")
        self.assertEqual(len(e["reasons"]), 2)
        self.assertIn("operator", e["reasons"][0])
        self.assertIn("launch bundle sold another 8.0%", e["reasons"][1])
        self.assertEqual(self.ev(row(bundle={"concentrated_share": 0.3}))["status"], "downgraded")

    def test_market_against_the_scan_and_collapse(self):
        later = iso(NOW - 60)
        e = self.ev(row(market_now={"observed_at": later, "price_usd": 0.45, "liquidity_usd": 9000.0}))
        self.assertEqual(e["status"], "downgraded")
        self.assertAlmostEqual(e["price_change"], -0.55)
        e = self.ev(row(market_now={"observed_at": later, "price_usd": 0.9, "liquidity_usd": 500.0}))
        self.assertEqual(e["status"], "removed")
        self.assertIn("collapsed", e["reasons"][0])
        old = iso(NOW - 3600)        # a snapshot older than the scan says nothing about after it
        self.assertEqual(self.ev(row(market_now={"observed_at": old, "price_usd": 0.01}))["status"], "active")

    def test_rug_outcome_and_staleness(self):
        self.assertEqual(self.ev(row(scan={"rug": True}))["status"], "removed")
        self.assertEqual(self.ev(row(outcome={"label": "rugged"}))["status"], "removed")
        e = self.ev(row(scan={"scanned_at": iso(NOW - 60 * 60)}))
        self.assertEqual(e["status"], "downgraded")
        self.assertIn("stale", e["reasons"][0])
        self.assertEqual(self.ev(row(scan={"scanned_at": iso(NOW - 4 * 3600)}))["status"], "removed")

    def test_bands(self):
        self.assertEqual(self.ev(row(scan={"band": "OK"}), bands=("CLEAN",))["status"], "removed")


class TestFilters(unittest.TestCase):

    def run_(self, rows, **q):
        return radar.filter_rows(rows, radar.params({k: [str(v)] for k, v in q.items()}), NOW)

    def test_defaults_and_order(self):
        rows = [row(token="ok", scan={"band": "OK", "score": 70}), row(token="c1", scan={"score": 82}),
                row(token="c2", scan={"score": 90}), row(token="bad", scan={"band": "DANGER", "score": 20})]
        self.assertEqual([r["token"] for r in self.run_(rows)], ["c2", "c1", "ok"])   # CLEAN first, removed hidden
        self.assertEqual([r["token"] for r in self.run_(rows, show="removed")], ["bad"])
        self.assertEqual([r["token"] for r in self.run_(rows, bands="CLEAN")], ["c2", "c1"])

    def test_each_filter(self):
        r = [row()]
        cases = {"chain": ("base", "solana"), "launchpad": ("clanker", "pons"), "age_max": (30, 10),
                 "age_min": (10, 30), "mcap_min": (40000, 60000), "mcap_max": (60000, 40000),
                 "liq_min": (5000, 20000), "score_min": (80, 90), "bundle_max": (0.05, 0.01),
                 "operator_max": (0.05, 0.03), "complete": ("1", None)}
        for k, (keep, drop) in cases.items():
            self.assertEqual(len(self.run_(r, **{k: keep})), 1, k)
            if drop is not None:
                self.assertEqual(len(self.run_(r, **{k: drop})), 0, k)
        moved = [row(market_now={"observed_at": iso(NOW - 60), "price_usd": 1.3, "liquidity_usd": 12000.0})]
        self.assertEqual(len(self.run_(moved, change_min=0.2)), 1)
        self.assertEqual(len(self.run_(moved, change_min=0.5)), 0)
        self.assertEqual(self.run_(moved)[0]["liquidity_usd"], 12000.0)    # the newer market is shown
        self.assertEqual(len(self.run_([row(scan={"coverage": "partial"})], complete=1)), 0)

    def test_params_are_bounded(self):
        p = radar.params({"window": ["999999"], "bands": ["CLEAN,NOPE"], "score_min": ["abc"]})
        self.assertEqual((p["window"], p["bands"], p["score_min"]), (radar.WINDOW_MAX, ("CLEAN",), None))
        self.assertEqual(radar.params({})["window"], 60)


class TestDiscovery(unittest.TestCase):

    def pools(self):
        return [{"token": "a", "created": NOW - 600, "liquidity_usd": 9000.0},
                {"token": "a", "created": NOW - 500, "liquidity_usd": 20000.0},     # same token, deeper pool
                {"token": "old", "created": NOW - 7200, "liquidity_usd": 50000.0},
                {"token": "thin", "created": NOW - 60, "liquidity_usd": 100.0},
                {"token": "seen", "created": NOW - 60, "liquidity_usd": 40000.0},
                {"token": "b", "created": NOW - 120, "liquidity_usd": 15000.0}]

    def test_candidates(self):
        c = radar.candidates(self.pools(), {"seen"}, NOW)
        self.assertEqual([(x["token"], x["liquidity_usd"]) for x in c], [("a", 20000.0), ("b", 15000.0)])

    def test_discover_scans_records_refreshes(self):
        calls = []

        def call(name, args):
            calls.append(name)
            if name == "radar_recent":
                return ["seen"]
            if name == "radar_feed":
                return [row(token="a", scan={"scanned_at": iso(NOW - 3600)}),
                        row(token="z", chain="solana")]
            if name == "market_snapshot_record":
                return len(args["p"]["rows"])
        scanned = []
        with mock.patch.object(memory_db, "call", side_effect=call), \
                mock.patch.object(radar.market, "fetch_market", return_value={"price_usd": 2.0, "liquidity_usd": 1.0}):
            out = radar.discover("base", pools=self.pools(), now=NOW,
                                 scan=lambda tok: scanned.append(tok) or {"band": "CLEAN", "score": 80})
        self.assertEqual(out["candidates"], 2)
        self.assertEqual([s["token"] for s in out["scanned"]], ["a", "b"])
        self.assertEqual(out["refreshed"], 1)                      # only this chain's radar tokens
        self.assertEqual([s["token"] for s in out["rescanned"]], ["a"])   # stale (1 h old) and still qualifying
        self.assertEqual(scanned, ["a", "b", "a"])

    def test_one_failure_does_not_stop_the_run(self):
        def scan(tok):
            if tok == "a":
                raise RuntimeError("rpc")
            return {"band": "OK", "score": 70}
        with mock.patch.object(memory_db, "call", return_value=[]):
            out = radar.discover("base", pools=self.pools(), now=NOW, scan=scan)
        self.assertEqual(out["failed"], [{"token": "a", "error": "RuntimeError"}])
        self.assertEqual([s["token"] for s in out["scanned"]], ["seen", "b"])   # nothing recent in memory here

    def test_dexpaprika_fallback_picks_the_new_side(self):
        weth = "0x4200000000000000000000000000000000000006"
        rows = {"results": [
            {"id": "p1", "created_at": "2033-05-18T03:23:20Z", "liquidity_usd": 5000, "dex_id": "uniswap_v4",
             "tokens": [{"id": radar.NATIVE}, {"id": "0xNEW1"}]},
            {"id": "p2", "created_at": "2033-05-18T03:22:20Z", "liquidity_usd": 900, "dex_id": "uniswap_v3",
             "tokens": [{"id": "0xNEW2"}, {"id": weth}]},
            {"id": "p3", "created_at": "2033-05-18T03:21:20Z", "liquidity_usd": 1, "dex_id": "x",
             "tokens": [{"id": "0xa"}, {"id": "0xb"}]}]}                       # neither side is a quote: skipped

        def get(url, budget):
            if "geckoterminal" in url:
                raise OSError("429")
            return rows
        with mock.patch.object(radar.market, "_get", side_effect=get):
            p = radar.new_pools("base")
        self.assertEqual([(x["token"], x["source"]) for x in p], [("0xnew1", "dp"), ("0xnew2", "dp")])

    def test_new_pools_parsing(self):
        data = {"data": [{"attributes": {"address": "0xp", "pool_created_at": "2033-05-18T03:23:20Z",
                                         "reserve_in_usd": "1234.5"},
                          "relationships": {"base_token": {"data": {"id": "base_0xtok"}},
                                            "dex": {"data": {"id": "uniswap-v4-base"}}}}]}
        with mock.patch.object(radar.market, "_get", return_value=data):
            p = radar.new_pools("base")
        self.assertEqual(p, [{"token": "0xtok", "pool": "0xp", "created": radar._ts("2033-05-18T03:23:20Z"),
                              "liquidity_usd": 1234.5, "dex": "uniswap-v4-base", "source": "gt"}])


class TestQuery(unittest.TestCase):

    def test_unavailable_without_memory(self):
        with mock.patch.object(memory_db, "enabled", return_value=False):
            self.assertFalse(radar.query({})["available"])

    def test_counts_and_cache(self):
        radar._cache.clear()
        with mock.patch.object(memory_db, "enabled", return_value=True), \
                mock.patch.object(memory_db, "call", return_value=[row(scan={"scanned_at": iso(time.time() - 60)})]) as c:
            a = radar.query({"window": ["90"]})
            b = radar.query({"window": ["90"]})
        self.assertEqual((a["counts"]["active"], a["tokens_in_window"]), (1, 1))
        self.assertIs(a, b)
        self.assertEqual(c.call_count, 1)                           # second poll within CACHE_S: no database call
        self.assertEqual(c.call_args[0][0], "radar_feed")


class TestEndpoints(StreamBase):

    def test_page_and_api(self):
        code, h, raw = self.call("GET", "/radar")
        self.assertEqual(code, 200)
        self.assertIn(b"launch radar", raw)
        with mock.patch.object(radar, "query", return_value={"available": True, "items": []}):
            code, _, raw = self.call("GET", "/api/radar?window=60")
        self.assertEqual((code, json.loads(raw)), (200, {"available": True, "items": []}))

    def test_cron_radar_auth_and_chain(self):
        with mock.patch.dict(os.environ, {"CRON_SECRET": ""}):
            self.assertEqual(self.call("GET", "/api/cron/radar?chain=base")[0], 503)
        auth = {"authorization": "Bearer k3y"}
        with mock.patch.dict(os.environ, {"CRON_SECRET": "k3y"}), \
                mock.patch.object(radar, "discover", return_value={"chain": "robinhood"}) as disc, \
                mock.patch.object(memory_db, "enabled", return_value=True):
            self.assertEqual(self.call("GET", "/api/cron/radar?chain=robinhood")[0], 401)
            self.assertEqual(self.call("GET", "/api/cron/radar?chain=mars", headers=auth)[0], 400)
            code, _, raw = self.call("GET", "/api/cron/radar?chain=robinhood", headers=auth)
        self.assertEqual((code, json.loads(raw)), (200, {"chain": "robinhood"}))
        self.assertEqual(disc.call_args[0][0], "robinhood")

    def test_fresh_rescan_skips_an_older_cached_result(self):
        first = self.call("POST", "/api/scan", {"token": fakes.TOKEN})
        jid = json.loads(first[2])["job"]
        for _ in range(100):
            if server.JOBS[jid]["done"]:
                break
            time.sleep(0.05)
        self.assertEqual(json.loads(self.call("POST", "/api/scan", {"token": fakes.TOKEN})[2])["job"], jid)  # cached
        with server._lock:
            server.JOBS[jid]["ts"] -= server.RESCAN_MIN_S + 1          # the cached result is now over a minute old
        again = json.loads(self.call("POST", "/api/scan", {"token": fakes.TOKEN, "fresh": True})[2])["job"]
        self.assertTrue(again != jid or server.serverless())


PG = os.environ.get("CRAWLER_PG")


@unittest.skipUnless(PG, "set CRAWLER_PG to run the radar SQL against a local Postgres")
class TestRadarSQL(unittest.TestCase):

    def psql(self, q):
        host, port = PG.split(":")
        out = subprocess.run(["psql", "-h", host, "-p", port, "-U", "postgres", "-At", "-v", "ON_ERROR_STOP=1",
                              "-c", "set role service_role; " + q], capture_output=True, text=True, timeout=30)
        self.assertEqual(out.returncode, 0, out.stderr)
        return out.stdout.strip().splitlines()[-1]

    def rec(self, chain, token, band, op, price):
        p = {"chain": chain, "token": token, "band": band, "score": 80, "largest_operator_share": op,
             "market": {"price_usd": price, "liquidity_usd": 1000}, "launch": {"block": 1, "ts": time.time()},
             "bundle": {"mode": "traced", "concentrated_share": 0.01, "sold_share": 0.02, "list": []}}
        return self.psql("select public.memory_record('" + json.dumps(p).replace("'", "''") + "'::jsonb)")

    def test_feed_latest_and_previous_scan(self):
        chain = "test-" + os.urandom(4).hex()
        self.rec(chain, "t1", "CLEAN", 0.12, 1.0)
        self.rec(chain, "t1", "OK", 0.03, 1.0)
        self.psql(f"select public.market_snapshot_record('{{\"rows\": [{{\"chain\": \"{chain}\", \"token\": \"t1\", "
                  f"\"price_usd\": 0.4, \"liquidity_usd\": 900}}]}}'::jsonb)")
        feed = [r for r in json.loads(self.psql("select public.radar_feed(now() - interval '1 hour', 300)"))
                if r["chain"] == chain]
        self.assertEqual(len(feed), 1)
        f = feed[0]
        self.assertEqual((f["scan"]["band"], f["prev"]["band"]), ("OK", "CLEAN"))
        self.assertEqual(f["market_now"]["price_usd"], 0.4)
        self.assertEqual(f["bundle"]["sold_share"], 0.02)
        recent = json.loads(self.psql(f"select public.radar_recent('{chain}', array['t1','t9'], 30)"))
        self.assertEqual(recent, ["t1"])
        e = radar.evaluate(f, time.time() + 1)
        self.assertEqual(e["status"], "downgraded")             # verdict slipped, operator sold, market down


if __name__ == "__main__":
    unittest.main()
