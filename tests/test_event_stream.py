"""Phase 4: Server-Sent Events delivery of a scan's events (POST /api/scan streaming, GET /api/stream)."""
import http.client, json, os, tempfile, threading, time, unittest
from http.server import ThreadingHTTPServer
from unittest import mock

import fakes
from crawler import server


def parse(raw):
    """[(event, data, id)] from an SSE body; comments as ("comment", text, None)."""
    out = []
    for block in raw.decode().split("\n\n"):
        if not block.strip():
            continue
        ev, data, id_ = "message", None, None
        for line in block.split("\n"):
            if line.startswith(":"):
                out.append(("comment", line[1:].strip(), None))
            elif line.startswith("event:"):
                ev = line[6:].strip()
            elif line.startswith("data:"):
                data = json.loads(line[5:].strip())
            elif line.startswith("id:"):
                id_ = int(line[3:].strip())
        if data is not None or ev != "message":
            out.append((ev, data, id_))
    return out


class StreamBase(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.H)
        cls.port = cls.httpd.server_address[1]
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
        for _ in range(100):
            if server._ACTIVE[0] == 0:
                break
            time.sleep(0.05)
        with server._stores_lock:
            for st in list(server._recent_stores.values()) + list(server._alerts_stores.values()):
                st.close()
            server._recent_stores.clear(); server._alerts_stores.clear()
        self.stack.__exit__(None, None, None)
        self.tmp.cleanup()

    def call(self, method, path, body=None, headers=None, timeout=20):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=timeout)
        h = {"content-type": "application/json"} | (headers or {})
        c.request(method, path, body=json.dumps(body) if body is not None else None, headers=h)
        r = c.getresponse()
        data = r.read()
        c.close()
        return r.status, dict(r.getheaders()), data

    def stream_scan(self, token=fakes.TOKEN):
        return self.call("POST", "/api/scan", {"token": token}, {"accept": "text/event-stream"})


class TestStreamingScan(StreamBase):

    def test_post_streams_events_then_result(self):
        code, h, raw = self.stream_scan()
        self.assertEqual(code, 200)
        self.assertTrue(h["content-type"].startswith("text/event-stream"))
        self.assertEqual(h["cache-control"], "no-store")
        frames = parse(raw)
        self.assertEqual(frames[0][0], "job")
        self.assertEqual(frames[0][1]["chain"], "robinhood")
        scans = [f for f in frames if f[0] == "scan"]
        self.assertEqual([f[2] for f in scans], list(range(len(scans))))          # every event, in order, id = index
        self.assertEqual([f[1]["i"] for f in scans], [f[2] for f in scans])
        self.assertEqual(scans[-1][1]["type"], "done")
        self.assertEqual(frames[-1][0], "result")
        self.assertEqual(frames[-1][1]["result"]["token"], fakes.TOKEN)

    def test_json_clients_unchanged(self):
        code, h, raw = self.call("POST", "/api/scan", {"token": fakes.TOKEN})
        self.assertEqual(code, 200)
        self.assertTrue(h["content-type"].startswith("application/json"))
        self.assertIn("job", json.loads(raw))

    def test_failed_scan_streams_error_result(self):
        code, _, raw = self.stream_scan(fakes.OTHER)       # not a launchpad token: fails inside the job
        frames = parse(raw)
        self.assertEqual(code, 200)
        self.assertEqual([f[1]["type"] for f in frames if f[0] == "scan"][-1], "error")
        self.assertIn("error", frames[-1][1])

    def test_bad_address_is_plain_400(self):
        code, h, raw = self.call("POST", "/api/scan", {"token": "nope"}, {"accept": "text/event-stream"})
        self.assertEqual(code, 400)
        self.assertTrue(h["content-type"].startswith("application/json"))


class TestJoinStream(StreamBase):

    def test_replay_and_resume_after_last_event_id(self):
        code, _, raw = self.stream_scan()
        job = parse(raw)[0][1]["job"]
        n = len([f for f in parse(raw) if f[0] == "scan"])
        _, _, again = self.call("GET", f"/api/stream?job={job}")
        self.assertEqual(len([f for f in parse(again) if f[0] == "scan"]), n)
        _, _, rest = self.call("GET", f"/api/stream?job={job}", headers={"last-event-id": str(n - 3)})
        ids = [f[2] for f in parse(rest) if f[0] == "scan"]
        self.assertEqual(ids, [n - 2, n - 1])
        self.assertEqual(parse(rest)[-1][0], "result")

    def test_unknown_job_404(self):
        code, _, _ = self.call("GET", "/api/stream?job=nope")
        self.assertEqual(code, 404)

    def test_heartbeat_and_timeout_on_a_job_that_never_ends(self):
        with server._lock:
            server.JOBS["slow"] = {"token": fakes.TOKEN, "chain": "robinhood", "events": [], "done": False,
                                   "result": None, "error": None, "ts": time.time()}
        with mock.patch.object(server, "STREAM_HEARTBEAT_S", 0.1), mock.patch.object(server, "STREAM_MAX_S", 0.5):
            code, _, raw = self.call("GET", "/api/stream?job=slow")
        frames = parse(raw)
        self.assertIn(("comment", "ping", None), frames)
        self.assertEqual(frames[-1][0], "timeout")

    def test_live_event_reaches_open_stream(self):
        with server._lock:
            server.JOBS["live"] = {"token": fakes.TOKEN, "chain": "robinhood", "events": [], "done": False,
                                   "result": None, "error": None, "ts": time.time()}
        def produce():
            time.sleep(0.3)
            with server._cv:
                server.JOBS["live"]["events"].append({"i": 0, "type": "stage", "detail": "launch"})
                server._cv.notify_all()
            time.sleep(0.3)
            with server._cv:
                server.JOBS["live"]["done"] = True
                server.JOBS["live"]["result"] = {"token": fakes.TOKEN}
                server._cv.notify_all()
        threading.Thread(target=produce, daemon=True).start()
        t0 = time.time()
        _, _, raw = self.call("GET", "/api/stream?job=live")
        frames = parse(raw)
        self.assertEqual([f[0] for f in frames], ["job", "scan", "result"])
        self.assertLess(time.time() - t0, 3)                  # woken by the notify, not by the heartbeat

    def test_stream_cap_503(self):
        with mock.patch.object(server, "STREAMS_MAX", 0):
            code, _, _ = self.stream_scan()
        self.assertEqual(code, 503)
        self.assertEqual(server._STREAMS[0], 0)


class TestServerlessStream(StreamBase):

    def setUp(self):
        super().setUp()
        self.stack.enter_context(mock.patch.dict(os.environ, {"SERVERLESS": "1"}))

    def test_streams_while_scanning(self):
        code, _, raw = self.stream_scan()
        frames = parse(raw)
        self.assertEqual(code, 200)
        self.assertEqual(frames[0][0], "job")
        self.assertEqual(server._sjob_target(frames[0][1]["job"]), ("robinhood", fakes.TOKEN))
        self.assertEqual(frames[-1][0], "result")


if __name__ == "__main__":
    unittest.main()
