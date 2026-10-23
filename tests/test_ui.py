"""Phase 14: terminal UI — localization layer, scan history API and SQL, share/export and graph controls in the build."""
import json, os, re, shutil, subprocess, unittest
from unittest import mock

from crawler import memory_db
from test_event_stream import StreamBase

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WEB = os.path.join(ROOT, "crawler", "web_v1")
I18N = os.path.join(WEB, "static", "i18n.js")
LANGS = ["en", "zh", "es", "fr", "de", "id", "fil", "vi"]
NODE = shutil.which("node")
TOKEN = "0x" + "ab" * 20


def read(name):
    with open(os.path.join(WEB, name), encoding="utf-8") as f:
        return f.read()

# Load i18n.js in Node without a browser: the DOM entry points are stubbed, the tables and tr() are exported.
HARNESS = r"""
const fs = require('fs');
let src = fs.readFileSync(process.argv[1], 'utf8');
src = src.replace(/if \(document.readyState[^\n]*/, 'window.__P = P; window.__X = X; window.__tr = tr; window.__col = function (c) { col = c; };');
global.window = {}; global.document = {readyState: 'complete'}; global.MutationObserver = function () {};
global.location = {search: ''}; global.navigator = {language: 'en'};
global.localStorage = {getItem: function () { return null; }, setItem: function () {}};
eval(src);
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const out = {phrases: window.__P, patterns: window.__X.map(x => [x[0].source, x[1]]), tr: {}};
for (let c = 0; c < 8; c++) { window.__col(c); out.tr[c] = input.map(s => window.__tr(s)); }
process.stdout.write(JSON.stringify(out));
"""

# Real engine output (verdict reasons, gates, rug scenario, radar reasons, coverage notes, result counters): every one
# must come out translated in every language.
ENGINE = [
    "20 wallets → 20 operators, biggest holds 10.2% of float (4.0% of supply), liquidity not measured",
    "20 wallets → 7 operators, biggest holds 31.5% of float (12.0% of supply), could move price −45% if sold",
    "biggest operator could move price −62% if sold (≥ 40%; 31.5% of float)",
    "biggest operator holds 22.1% of float (liquidity not measured)",
    "soft: 73% virgin wallets in top",
    "73% virgin wallets in top (≥ 60%)",
    "38.2% of float received by transfer (≥ 30%)",
    "one holder could move price −30% (thin liquidity)",
    "pack of 5 wallets, 12.4% of float",
    "proven operator of 3 wallets, 8.0% of float",
    "liquidity too thin ($1,240)",
    "snipers hold 14.2% of float",
    "top 10 hold 61.9% of float",
    "no clear signs of a single operator",
    "virgin share unknown: most of the top couldn't be read",
    "fewer than 10 holders (6) — too early or too late",
    "58 RISKY · top 10 hold 61.9% of float",
    "probably rug −92%",
    "probably rug −40% if suspicious holders sell",
    "suspicious supply · 31.5% of float",
    "A conditional scenario, not a prediction: if these wallets sell into the current liquidity, the price could "
    "fall about 92%.",
    "verdict slipped from CLEAN to RISKY",
    "largest operator's share fell 4.5 points (selling or moving)",
    "launch bundle still holds 18% of supply",
    "latest scan is 7.5 h old",
    "3 of 20 examined wallets unread",
    "holder count is a lower bound (largest accounts only)",
    "8 criteria · 3 flagged",
    "20 of 20 holders · 73.5% of float",
    "funded 6 of the top 20 · 25.4% of float · 100+ outgoing transfers: exchange-like, not a link",
    "0 links · 0 operators merged · 3 hub funders (not linking)",
    "5m ago",
]


@unittest.skipUnless(NODE, "node is needed to run the localization layer")
class TestLocalization(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        out = subprocess.run([NODE, "-e", HARNESS, I18N], input=json.dumps(ENGINE), capture_output=True, text=True,
                             timeout=30)
        if out.returncode:
            raise AssertionError(out.stderr)
        cls.d = json.loads(out.stdout)

    def test_every_phrase_has_every_language(self):
        seen = set()
        for row in self.d["phrases"]:
            self.assertEqual(len(row), len(LANGS), row[0])
            self.assertTrue(all(isinstance(c, str) and c for c in row), row[0])
            self.assertNotIn(row[0], seen, "duplicate phrase")
            seen.add(row[0])
        for src, tpl in self.d["patterns"]:
            self.assertEqual(len(tpl), len(LANGS) - 1, src)
            groups = re.compile(src).groups
            for t in tpl:   # every $n / %n names a group the pattern captures
                self.assertTrue(all(int(n) <= groups for n in re.findall(r"[$%](\d)", t)), (src, t))

    def test_engine_explanations_are_translated(self):
        en = self.d["tr"]["0"]
        self.assertEqual(en, ENGINE)                       # English is the source, never rewritten
        for c in range(1, len(LANGS)):
            for src, out in zip(ENGINE, self.d["tr"][str(c)]):
                self.assertNotEqual(out, src, f"{LANGS[c]}: {src}")
                for num in re.findall(r"\d+(?:\.\d+)?", src):
                    self.assertIn(num, out, f"{LANGS[c]}: numbers survive: {src}")   # figures are never altered

    def test_reason_inside_a_verdict_line_is_translated_too(self):
        zh = self.d["tr"]["1"][ENGINE.index("58 RISKY · top 10 hold 61.9% of float")]
        self.assertEqual(zh, "58 RISKY · 前 10 名持有流通量的 61.9%")


class TestEndpoints(StreamBase):

    def test_i18n_script_is_served(self):
        code, headers, raw = self.call("GET", "/i18n.js")
        self.assertEqual(code, 200)
        self.assertTrue(headers["content-type"].startswith("text/javascript"))
        self.assertIn("max-age=300", headers["cache-control"])
        self.assertIn(b"CrawlMemeI18n", raw)

    def test_history_validates_input_without_rpc(self):
        self.assertEqual(self.call("GET", "/api/history?chain=nope&token=" + TOKEN)[0], 400)
        self.assertEqual(self.call("GET", "/api/history?chain=robinhood&token=0x12")[0], 400)
        self.assertEqual(self.call("GET", "/api/history?chain=robinhood&token=" + TOKEN.upper().replace("0X", "0x"))[0],
                         200)

    def test_history_reads_memory(self):
        body = {"scans": [{"scan_id": 4, "band": "DANGER", "score": 20}], "latest": None}
        with mock.patch.object(memory_db, "enabled", return_value=True), \
                mock.patch.object(memory_db, "call", return_value=body) as call:
            code, _, raw = self.call("GET", "/api/history?chain=robinhood&token=" + TOKEN.upper().replace("0X", "0x"))
        self.assertEqual(code, 200)
        self.assertEqual(json.loads(raw), {"available": True, "chain": "robinhood", "token": TOKEN, **body})
        self.assertEqual(call.call_args[0], ("token_history", {"p_chain": "robinhood", "p_token": TOKEN, "p_limit": 20}))
        with mock.patch.object(memory_db, "enabled", return_value=True), \
                mock.patch.object(memory_db, "call", return_value=None):
            self.assertFalse(json.loads(self.call("GET", "/api/history?chain=robinhood&token=" + TOKEN)[2])["available"])
        with mock.patch.object(memory_db, "enabled", return_value=False):
            d = json.loads(self.call("GET", "/api/history?chain=robinhood&token=" + TOKEN)[2])
        self.assertEqual((d["available"], d["scans"]), (False, []))


class TestBuild(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.index = read("index.html")

    def test_every_page_loads_the_localization_layer_with_a_picker(self):
        for page in ("index.html", "radar.html", "proof.html"):
            html = read(page)
            self.assertIn('<script src="/i18n.js"></script>', html, page)   # in <head>, before the page rewrites its URL
            self.assertIn("data-lang-slot", html, page)

    def test_result_panels_and_controls(self):
        for marker in ("data-i18n-whole", "paintShare", "paintMine", "data-share", "data-mine", "/api/history?chain=",
                       "copy report link", "download report (JSON)", "download share card", "cm_my_scans",
                       "setPointerCapture", "same-block packs", "qs.get('chain')"):
            self.assertIn(marker, self.index, marker)

    def test_english_unless_chosen(self):
        with open(I18N, encoding="utf-8") as f:
            src = f.read()
        self.assertNotIn("navigator.language", src)        # the browser language never switches the site
        self.assertIn("set(e.target.value, true)", src)     # only a picker choice is remembered

    def test_copy_share_card_only_where_the_browser_can(self):
        self.assertIn("if(navigator.clipboard&&navigator.clipboard.write&&window.ClipboardItem)", self.index)


PG = os.environ.get("CRAWLER_PG")


@unittest.skipUnless(PG, "set CRAWLER_PG to run the history SQL against a local Postgres")
class TestHistorySQL(unittest.TestCase):

    def psql(self, q):
        host, port = PG.split(":")
        out = subprocess.run(["psql", "-h", host, "-p", port, "-U", "postgres", "-At", "-v", "ON_ERROR_STOP=1",
                              "-c", "set role service_role; " + q], capture_output=True, text=True, timeout=30)
        self.assertEqual(out.returncode, 0, out.stderr)
        return out.stdout.strip().splitlines()[-1]

    def test_token_history_newest_first_with_latest_market(self):
        chain = "test-" + os.urandom(4).hex()
        for band, score, mcap in (("CLEAN", 80, 1000), ("RISKY", 55, 3000)):
            p = {"chain": chain, "token": "t1", "band": band, "score": score,
                 "market": {"price_usd": mcap / 1000, "mcap_usd": mcap, "liquidity_usd": 500}}
            self.psql("select public.memory_record('" + json.dumps(p) + "'::jsonb)")
        snap = [{"chain": chain, "token": "t1", "price_usd": 9.0, "mcap_usd": 9000}]
        self.psql("select public.market_snapshot_record('" + json.dumps({"rows": snap}) + "'::jsonb)")
        d = json.loads(self.psql(f"select public.token_history('{chain}', 't1', 20)"))
        self.assertEqual([s["band"] for s in d["scans"]], ["RISKY", "CLEAN"])
        self.assertEqual([s["mcap_usd"] for s in d["scans"]], [3000, 1000])
        self.assertTrue(all(len(s["record_hash"]) == 64 for s in d["scans"]))
        self.assertEqual(d["latest"]["mcap_usd"], 9000)
        empty = json.loads(self.psql(f"select public.token_history('{chain}', 'none', 20)"))
        self.assertEqual((empty["scans"], empty["latest"]), ([], None))


if __name__ == "__main__":
    unittest.main()
