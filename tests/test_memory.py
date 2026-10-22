"""Memory brain (crawler/memory_db.py) and Phase 7 cabals (crawler/cabals.py).
Offline by default. With CRAWLER_PG=<socket dir>:<port> (a local Postgres with supabase/migrations applied and the
anon / authenticated / service_role roles), TestPostgres runs the real SQL behind a PostgREST stand-in."""
import json, os, subprocess, threading, unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock

import fakes
from crawler import cabals, engine, memory_db

SECRET = "sb_secret_TESTKEYxxxxxxxxxxxxxxxx_checksum"


class Recorder(BaseHTTPRequestHandler):
    """PostgREST stand-in: records requests; answers via the server's `answer(name, args)`."""

    def do_POST(self):
        n = int(self.headers.get("content-length") or 0)
        args = json.loads(self.rfile.read(n) or b"{}")
        self.server.seen.append((self.path, {k.lower(): v for k, v in self.headers.items()}, args))
        code, body = self.server.answer(self.path.rsplit("/", 1)[-1], args)
        b = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def log_message(self, *a):
        pass


class StubBase(unittest.TestCase):

    def start(self, answer):
        srv = ThreadingHTTPServer(("127.0.0.1", 0), Recorder)
        srv.seen, srv.answer = [], answer
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        env = mock.patch.dict(os.environ, {"SUPABASE_URL": f"http://127.0.0.1:{srv.server_address[1]}",
                                           "SUPABASE_SECRET_KEY": SECRET, "MEMORY_ENABLED": "on"})
        env.start()
        self.addCleanup(env.stop)
        return srv


class TestClient(StubBase):

    def test_enabled_needs_https_and_key(self):
        with mock.patch.dict(os.environ, {"SUPABASE_URL": "http://db.example.com", "SUPABASE_SECRET_KEY": "k",
                                          "MEMORY_ENABLED": "on"}):
            self.assertFalse(memory_db.enabled())          # key must never cross the network in clear
        with mock.patch.dict(os.environ, {"SUPABASE_URL": "https://x.supabase.co", "SUPABASE_SECRET_KEY": "k",
                                          "MEMORY_ENABLED": "on"}):
            self.assertTrue(memory_db.enabled())
        with mock.patch.dict(os.environ, {"SUPABASE_URL": "https://x.supabase.co", "SUPABASE_SECRET_KEY": "k",
                                          "MEMORY_ENABLED": "off"}):
            self.assertFalse(memory_db.enabled())

    def test_secret_key_only_in_apikey_header(self):
        srv = self.start(lambda name, args: (200, {"seen": {}}))
        out = memory_db.lookup("bsc", "0xt", ["0xa"], ["0xf", None, "0xf"], "0xd")
        self.assertEqual(out["seen"], {})
        path, headers, args = srv.seen[0]
        self.assertEqual(path, "/rest/v1/rpc/memory_lookup")
        self.assertEqual(headers.get("apikey"), SECRET)
        self.assertNotIn("authorization", headers)                     # sb_secret is not a JWT
        self.assertNotIn(SECRET, path)
        self.assertEqual(args, {"p_chain": "bsc", "p_token": "0xt", "p_wallets": ["0xa"], "p_funders": ["0xf"],
                                "p_deployer": "0xd"})

    def test_legacy_jwt_also_bearer(self):
        srv = self.start(lambda name, args: (200, {}))
        with mock.patch.dict(os.environ, {"SUPABASE_SECRET_KEY": "", "SUPABASE_SERVICE_ROLE_KEY": "eyJabc.def.ghi"}):
            memory_db.lookup("bsc", "t", [], [], None)
        self.assertEqual(srv.seen[0][1].get("authorization"), "Bearer eyJabc.def.ghi")

    def test_failure_is_none_and_never_logs_the_key(self):
        self.start(lambda name, args: (500, {"message": "boom"}))
        with mock.patch("builtins.print") as pr:
            self.assertIsNone(memory_db.lookup("bsc", "t", [], [], None))
        self.assertNotIn(SECRET, " ".join(str(c) for c in pr.call_args_list))

    def test_record_payload_from_a_scan(self):
        srv = self.start(lambda name, args: (200, 7))
        with fakes.patched():
            res = engine.scan(fakes.TOKEN)
        self.assertEqual(memory_db.record(res), 7)
        p = next(a for path, _, a in srv.seen if path.endswith("/memory_record"))["p"]
        self.assertEqual((p["chain"], p["token"], p["band"], p["model"]), ("robinhood", fakes.TOKEN, res["band"],
                                                                            memory_db.MODEL))
        self.assertEqual([h["rank"] for h in p["holders"]], list(range(1, len(res["holders"]) + 1)))
        self.assertEqual(p["deployer"], fakes.DEPLOYER)

    def test_no_record_without_a_verdict(self):
        srv = self.start(lambda name, args: (200, 1))
        self.assertIsNone(memory_db.record({"chain": "bsc", "token": "t", "score": None}))
        self.assertIsNone(memory_db.record({"chain": "bsc", "token": "t", "score": 50, "partial_scan": True}))
        self.assertEqual(srv.seen, [])

    def test_scan_reads_memory_and_reports_cabals(self):
        holders = []
        with fakes.patched():
            holders = [h["wallet"] for h in engine.scan(fakes.TOKEN)["holders"]]
        mem = {"seen": {holders[0]: {"tokens": 3, "danger": 2, "list": []}},
               "pairs": [{"a": holders[0], "b": holders[1], "together": 2, "tokens": ["x", "y"]}],
               "funders": {}, "funder_stats": {}, "deployer": {"tokens": 2, "list": []},
               "memory": {"tokens": 10, "scans": 12}}
        self.start(lambda name, args: (200, mem if name == "memory_lookup" else 1))
        with fakes.patched():
            r = engine.scan(fakes.TOKEN)
        self.assertEqual(r["memory"]["crews"][0]["wallets"][:2], sorted(holders[:2], key=lambda w: holders.index(w)))
        self.assertIn("crew", r["memory"]["flags"])
        self.assertIn("serial_deployer", r["memory"]["flags"])


class TestCabals(unittest.TestCase):

    H = [("w1", 50, 0.5), ("w2", 30, 0.3), ("w3", 20, 0.2)]

    def test_none_without_memory(self):
        self.assertIsNone(cabals.analyse(None, self.H, 1.0, {}))

    def test_crew_needs_two_shared_tokens(self):
        mem = {"pairs": [{"a": "w1", "b": "w2", "together": 1, "tokens": ["x"]}]}
        self.assertEqual(cabals.analyse(mem, self.H, 1.0, {})["crews"], [])
        mem = {"pairs": [{"a": "w1", "b": "w2", "together": 2, "tokens": ["x", "y"]},
                         {"a": "w2", "b": "w3", "together": 3, "tokens": ["y", "z", "q"]}]}
        crews = cabals.analyse(mem, self.H, 0.5, {})["crews"]
        self.assertEqual(len(crews), 1)
        self.assertEqual((crews[0]["wallets"], crews[0]["tokens"], crews[0]["together"]),
                         (["w1", "w2", "w3"], ["q", "x", "y", "z"], 3))
        self.assertAlmostEqual(crews[0]["share_supply"], 0.5)

    def test_hub_funder_is_not_a_repeat_funder_flag(self):
        fu = {"w1": {"funder": "cex", "found": True}, "w2": {"funder": "dev", "found": True}}
        mem = {"funders": {"cex": {"tokens": 9, "wallets": 40}, "dev": {"tokens": 2, "wallets": 3}},
               "funder_stats": {"cex": {"outgoing": 100, "hub": True}}}
        out = cabals.analyse(mem, self.H, 1.0, fu, {"dev": 4})
        self.assertEqual([(r["funder"], r["hub"]) for r in out["repeat_funders"]], [("dev", False), ("cex", True)])
        self.assertIn("repeat_funder", out["flags"])
        out = cabals.analyse({"funders": {"cex": {"tokens": 9}}, "funder_stats": {"cex": {"outgoing": 500}}},
                             self.H, 1.0, fu)
        self.assertNotIn("repeat_funder", out["flags"])

    def test_seen_wallets_in_rank_order(self):
        mem = {"seen": {"w3": {"tokens": 1, "danger": 0}, "w1": {"tokens": 4, "danger": 3}}}
        out = cabals.analyse(mem, self.H, 1.0, {})
        self.assertEqual([(s["wallet"], s["rank"]) for s in out["seen_wallets"]], [("w1", 1), ("w3", 3)])


PG = os.environ.get("CRAWLER_PG")      # "<socket dir>:<port>" of a local Postgres with the migration applied


@unittest.skipUnless(PG, "set CRAWLER_PG to run memory SQL against a local Postgres")
class TestPostgres(StubBase):
    """The real migration SQL, called through a PostgREST stand-in (psql as service_role)."""

    def sql(self, name, args):
        host, port = PG.split(":")
        if name == "memory_record":
            q = f"select public.memory_record({self.lit(json.dumps(args['p']))}::jsonb)"
        else:
            arr = lambda xs: "array[" + ",".join(self.lit(x) for x in xs) + "]::text[]"
            dep = self.lit(args["p_deployer"]) if args["p_deployer"] else "null"
            q = (f"select public.memory_lookup({self.lit(args['p_chain'])}, {self.lit(args['p_token'])}, "
                 f"{arr(args['p_wallets'])}, {arr(args['p_funders'])}, {dep})")
        out = subprocess.run(["psql", "-h", host, "-p", port, "-U", "postgres", "-At", "-v", "ON_ERROR_STOP=1",
                              "-c", "set role service_role; " + q], capture_output=True, text=True, timeout=30)
        if out.returncode:
            return 500, {"message": out.stderr}
        return 200, json.loads(out.stdout.strip().splitlines()[-1])

    @staticmethod
    def lit(s):
        return "'" + str(s).replace("'", "''") + "'"

    def test_record_then_lookup_from_another_token(self):
        self.start(self.sql)
        chain = "test-" + os.urandom(4).hex()          # isolates reruns against the same database
        res = lambda token, ws, band: {
            "chain": chain, "token": token, "band": band, "score": 20, "header": {"ticker": token.upper()},
            "launch": {"deployer": "dev"}, "holders": [{"wallet": w, "share": 0.1, "signals": {"kind": "buy"},
                                                        "forensics": {"funding": {"funder": "f1", "found": True}}}
                                                       for w in ws],
            "graph": {"nodes": [{"id": "f1", "type": "funder", "outgoing": 3, "hub": False}], "edges": []},
            "operators": [], "coverage": {"level": "full"}, "holders_total": 50}
        self.assertIsInstance(memory_db.record(res("a", ["w1", "w2"], "DANGER")), int)
        self.assertIsInstance(memory_db.record(res("b", ["w1", "w2", "w3"], "RISKY")), int)
        mem = memory_db.lookup(chain, "c", ["w1", "w2", "w9"], ["f1"], "dev")
        self.assertEqual(set(mem["seen"]), {"w1", "w2"})
        self.assertEqual(mem["seen"]["w1"]["tokens"], 2)
        self.assertEqual(mem["pairs"][0]["together"], 2)
        self.assertEqual(mem["deployer"]["tokens"], 2)
        self.assertEqual(mem["funders"]["f1"]["tokens"], 2)
        out = cabals.analyse(mem, [("w1", 1, 0.4), ("w2", 1, 0.3), ("w9", 1, 0.3)], 1.0,
                             {"w1": {"funder": "f1", "found": True}}, {"f1": 3})
        self.assertEqual(out["crews"][0]["wallets"], ["w1", "w2"])
        self.assertEqual(set(out["flags"]), {"crew", "serial_deployer", "repeat_funder"})
        own = memory_db.lookup(chain, "a", ["w1"], [], "dev")          # its own scans are not evidence
        self.assertEqual([t["token"] for t in own["seen"]["w1"]["list"]], ["b"])


if __name__ == "__main__":
    unittest.main()
