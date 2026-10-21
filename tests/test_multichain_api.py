"""Multi-chain behaviour of the V1 HTTP API and the engine's chain resolution (no network: registry.locate mocked)."""
import http.client, json, os, tempfile, threading, time, unittest, urllib.error, urllib.request
from http.server import ThreadingHTTPServer
from unittest import mock

import fakes
from crawler import engine, server
from crawler.chains import registry

ADDR = "0x" + "ab" * 20


class TestChainOf(unittest.TestCase):

    def test_robinhood_only_needs_no_request(self):
        with mock.patch.object(registry, "locate", side_effect=AssertionError("no lookup expected")):
            self.assertEqual(engine.chain_of(ADDR.upper().replace("0X", "0x")), ("robinhood", ADDR))

    def test_found_on_one_chain(self):
        with mock.patch.dict(os.environ, {"BSC_ENABLED": "true"}), \
                mock.patch.object(registry, "locate", return_value=(["bsc"], [])):
            self.assertEqual(engine.chain_of(ADDR), ("bsc", ADDR))

    def test_found_on_several_chains_is_ambiguous(self):
        with mock.patch.dict(os.environ, {"BSC_ENABLED": "true", "BASE_ENABLED": "true"}), \
                mock.patch.object(registry, "locate", return_value=(["bsc", "base"], [])):
            with self.assertRaises(engine.AmbiguousChain) as cm:
                engine.chain_of(ADDR)
        self.assertEqual(cm.exception.chains, ["bsc", "base"])
        self.assertIsInstance(cm.exception, engine.ScanError)

    def test_unknown_chain_never_dropped_silently(self):
        """A chain whose RPC failed may hold the token: found elsewhere + unknown → ambiguous, not a silent pick."""
        with mock.patch.dict(os.environ, {"BSC_ENABLED": "true", "BASE_ENABLED": "true"}), \
                mock.patch.object(registry, "locate", return_value=(["bsc"], ["base"])):
            with self.assertRaises(engine.AmbiguousChain):
                engine.chain_of(ADDR)
        with mock.patch.dict(os.environ, {"BSC_ENABLED": "true"}), \
                mock.patch.object(registry, "locate", return_value=([], ["bsc"])):
            with self.assertRaisesRegex(engine.ScanError, "choose the chain"):
                engine.chain_of(ADDR)

    def test_explicit_chain(self):
        with mock.patch.dict(os.environ, {"BASE_ENABLED": "true"}):
            self.assertEqual(engine.chain_of(ADDR, "base"), ("base", ADDR))
        with self.assertRaisesRegex(engine.ScanError, "not enabled"):
            engine.chain_of(ADDR, "base")

    def test_not_launchpad_names_enabled_pads(self):
        with mock.patch.dict(os.environ, {"BSC_ENABLED": "true", "FOURMEME_ENABLED": "true", "CLANKER_ENABLED": "true"}):
            self.assertEqual(engine.not_launchpad("bsc"), "not a four.meme or Clanker token")
            self.assertEqual(engine.not_launchpad("robinhood"), "not a Pons V2 or Clanker token")
        with mock.patch.dict(os.environ, {"BASE_ENABLED": "true"}):
            self.assertEqual(engine.not_launchpad("base"), "no launchpad is enabled for Base")


class TestApi(unittest.TestCase):
    """Real server on a free port, engine on the fake adapter (tests/fakes.py)."""

    @classmethod
    def setUpClass(cls):
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.H)
        cls.base = f"http://127.0.0.1:{cls.httpd.server_address[1]}"
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()

    def setUp(self):
        with server._lock:
            server.JOBS.clear(); server.BY_TOKEN.clear()
        self.tmp = tempfile.TemporaryDirectory()
        self.stack = fakes.patched()
        self.stack.enter_context(mock.patch.dict(os.environ, {"LOCAL_DB_PATH": os.path.join(self.tmp.name, "t.db")}))
        self.stack.__enter__()

    def tearDown(self):
        for _ in range(100):                     # scans started by a test finish before the patches are undone
            if server._ACTIVE[0] == 0:
                break
            time.sleep(0.05)
        with server._stores_lock:
            for st in list(server._recent_stores.values()) + list(server._alerts_stores.values()):
                st.close()
            server._recent_stores.clear(); server._alerts_stores.clear()
        self.stack.__exit__(None, None, None)
        self.tmp.cleanup()

    def request(self, path, body=None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, method="POST" if data is not None else "GET",
                                     headers={"content-type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, json.loads(r.read()), dict(r.headers)
        except urllib.error.HTTPError as e:
            with e:
                return e.code, json.loads(e.read()), dict(e.headers)

    def wait_done(self, job, timeout=10):
        t0 = time.time()
        while time.time() - t0 < timeout:
            code, d, _ = self.request(f"/api/events?job={job}&after=0")
            if d.get("done"):
                return d["events"]
            time.sleep(0.05)
        self.fail("scan did not finish")

    def test_ambiguous_chain_409(self):
        with mock.patch.object(server.engine, "chain_of", side_effect=engine.AmbiguousChain(ADDR, ["bsc", "base"])):
            code, d, _ = self.request("/api/scan", {"token": ADDR})
        self.assertEqual(code, 409)
        self.assertEqual(d["error"], "ambiguous_chain")
        self.assertEqual(d["chains"], ["bsc", "base"])

    def test_explicit_chain_reaches_engine(self):
        seen = []
        real = engine.chain_of
        with mock.patch.object(server.engine, "chain_of", side_effect=lambda t, c=None: seen.append(c) or real(t, c)):
            code, d, _ = self.request("/api/scan", {"token": fakes.TOKEN, "chain": "robinhood"})
            self.assertEqual(code, 200)
            self.wait_done(d["job"])
        self.assertIn("robinhood", seen)

    def test_body_too_large_413_without_reading(self):
        port = self.httpd.server_address[1]
        c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        c.putrequest("POST", "/api/scan")
        c.putheader("content-type", "application/json")
        c.putheader("content-length", str(server.MAX_BODY + 1))
        c.endheaders()                                   # headers only: the server must answer without the body
        r = c.getresponse()
        self.assertEqual(r.status, 413)
        c.close()

    def test_config_lists_chains(self):
        code, d, _ = self.request("/api/config")
        self.assertEqual(code, 200)
        rh = next(c for c in d["chains"] if c["chain"] == "robinhood")
        self.assertEqual(rh["chain_id"], 4663)
        self.assertIn("pons", rh["launchpads"])

    def test_cache_key_per_chain(self):
        self.assertEqual(server._key("robinhood", ADDR), ADDR)          # V1 keys unchanged
        self.assertEqual(server._key("bsc", ADDR), "bsc:" + ADDR)        # same address on two chains: two entries


class TestImportWithoutRobinhood(unittest.TestCase):

    def test_robinhood_adapter_imports_without_rpc(self):
        """A BNB/Base-only deployment has no CRAWLER_RPC: importing the engine must not fail (requests fail instead)."""
        import subprocess, sys
        env = {k: v for k, v in os.environ.items() if k != "CRAWLER_RPC"}
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        r = subprocess.run([sys.executable, "-c", "from crawler import engine; print('ok')"], cwd=root, env=env,
                           capture_output=True, text=True, timeout=60)
        self.assertEqual(r.stdout.strip(), "ok", r.stderr)


if __name__ == "__main__":
    unittest.main()
