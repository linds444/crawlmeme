"""Phase 15: private Telegram bot and personal alerts — bot core, alert queue and delivery, endpoints, SQL. No network:
Telegram and the database are fakes, except TestTelegramSQL (local Postgres via CRAWLER_PG)."""
import json, os, subprocess, time, unittest
from unittest import mock

from bot import core, text as T
from bot.tg import TelegramError
from crawler import alerts, tg_alerts
from test_event_stream import StreamBase

OWNER, STRANGER = 111, 999
EVM = "0x" + "ab" * 20
SOL = "So1aNaToKenMint1111111111111111111111111111"[:44]
A, B, C = ("0x" + c * 40 for c in "abc")
DEP = "0x" + "de" * 20


class FakeTG:
    def __init__(self):
        self.calls, self.fail, self.mid = [], {}, 100

    def redact(self, s):
        return str(s)

    def call(self, method, **params):
        self.calls.append((method, params))
        if method in self.fail:
            raise self.fail[method]
        if method in ("sendMessage", "editMessageText"):
            self.mid += 1
            return {"message_id": params.get("message_id") or self.mid}
        return True

    def of(self, method):
        return [p for m, p in self.calls if m == method]

    def texts(self):
        return [p["text"] for m, p in self.calls if m in ("sendMessage", "editMessageText")]


class FakeStore:
    """In-memory stand-in for crawler/tg_store with the queue semantics the SQL enforces (dedupe key per chat,
    same-kind cooldown, priority order, hourly cap)."""

    def __init__(self):
        self.on = True
        self.chats, self.watches, self.snaps, self.alerts, self.seen, self.state = {}, {}, {}, [], set(), {}
        self.profile = {"address": A, "held": [], "deployed": []}
        self.labels = {}

    def enabled(self):
        return self.on

    def update_seen(self, uid):
        new = uid not in self.seen
        self.seen.add(uid)
        return new

    def chat_touch(self, chat_id, user_id, username):
        self.chats.setdefault(chat_id, {"user_id": user_id, "global": True})

    def watch(self, chat_id, chain, token, ticker, limit):
        mine = [k for k in self.watches if k[0] == chat_id]
        if (chat_id, chain, token) in self.watches:
            return {"status": "exists", "count": len(mine), "limit": limit}
        if len(mine) >= limit:
            return {"status": "limit", "count": len(mine), "limit": limit}
        self.watches[(chat_id, chain, token)] = {"ticker": ticker, "radar_active": False, "checked": None}
        return {"status": "added", "count": len(mine) + 1, "limit": limit}

    def unwatch(self, chat_id, token, chain=None):
        keys = [k for k in self.watches if k[0] == chat_id and k[2] == token and (chain is None or k[1] == chain)]
        for k in keys:
            del self.watches[k]
        return len(keys)

    def watchlist(self, chat_id):
        return [{"chain": k[1], "token": k[2], "ticker": v["ticker"], "radar_active": v["radar_active"],
                 **({"band": self.snaps[k[1:]]["band"], "score": self.snaps[k[1:]]["score"]} if k[1:] in self.snaps else {})}
                for k, v in self.watches.items() if k[0] == chat_id]

    def watched(self):
        return [{"chat_id": k[0], "chain": k[1], "token": k[2], "ticker": v["ticker"], "radar_active": v["radar_active"]}
                for k, v in self.watches.items()]

    def radar_set(self, rows):
        for r in rows:
            self.watches[(r["chat_id"], r["chain"], r["token"])]["radar_active"] = r["active"]
        return len(rows)

    def watch_due(self, min_age, limit):
        toks = sorted({(k[1], k[2]) for k, v in self.watches.items() if v["checked"] is None})
        return [{"chain": c, "token": t} for c, t in toks][:limit]

    def watch_checked(self, chain, token):
        for k, v in self.watches.items():
            if k[1:] == (chain, token):
                v["checked"] = time.time()

    def snapshot_swap(self, chain, token, snap):
        chats = sorted({k[0] for k in self.watches if k[1:] == (chain, token)})
        if not chats:
            return {"watched": False, "prev": None, "chats": []}
        prev = self.snaps.get((chain, token))
        self.snaps[(chain, token)] = snap
        return {"watched": True, "prev": prev, "chats": chats}

    def snapshot_early(self, chain, token, share):
        return True

    def global_chats(self):
        return [c for c, v in self.chats.items() if v["global"]]

    def token_labels(self, chain, tokens):
        return {t: self.labels[t] for t in tokens if t in self.labels}

    def enqueue(self, rows):
        n = 0
        for r in rows:
            if r["chat_id"] not in self.chats:
                continue
            if any(a["chat_id"] == r["chat_id"] and a["dedupe_key"] == r["dedupe_key"] for a in self.alerts):
                continue
            if any(a["chat_id"] == r["chat_id"] and a["kind"] == r["kind"] and a["token"] == r["token"]
                   and a["status"] in ("queued", "sending", "sent") and r["cooldown_s"] > 0 for a in self.alerts):
                continue
            self.alerts.append(dict(r, id=len(self.alerts) + 1, status="queued", attempts=0))
            n += 1
        return n

    def claim(self, limit, cap, max_age_h=24):
        q = sorted((a for a in self.alerts if a["status"] == "queued"), key=lambda a: (a["priority"], a["id"]))
        sent = {}
        for a in self.alerts:
            if a["status"] in ("sent", "sending"):
                sent[a["chat_id"]] = sent.get(a["chat_id"], 0) + 1
        out = []
        for a in q:                                    # the cap counts alerts claimed in this batch too
            if sent.get(a["chat_id"], 0) >= cap or len(out) >= limit:
                continue
            sent[a["chat_id"]] = sent.get(a["chat_id"], 0) + 1
            a["status"], a["attempts"] = "sending", a["attempts"] + 1
            out.append(dict(a))
        return out

    def done(self, rows):
        for r in rows:
            for a in self.alerts:
                if a["id"] == r["id"] and a["status"] == "sending":
                    a["status"], a["retry_s"] = r["status"], r.get("retry_s")
        return len(rows)

    def chat_gone(self, chat_id):
        self.chats.pop(chat_id, None)
        self.alerts = [a for a in self.alerts if a["chat_id"] != chat_id]
        self.watches = {k: v for k, v in self.watches.items() if k[0] != chat_id}

    def status(self, chat_id):
        return {"watches": sum(1 for k in self.watches if k[0] == chat_id), "sent_24h": 0, "queued": 0, "cron": None}

    def wallet_profile(self, address, chain=None, limit=8):
        return dict(self.profile, address=address)

    def housekeeping(self):
        return {}

    def state_set(self, key, value):
        self.state[key] = value


class Ambiguous(Exception):
    def __init__(self, chains):
        super().__init__("several chains")
        self.chains = chains


class ScanErr(Exception):
    pass


def result(token=EVM, chain="robinhood", band="DANGER", score=20, ticker="TKN", rug=None, launch_age_h=1, **kw):
    r = {"token": token, "chain": chain, "band": band, "score": score, "header": {"ticker": ticker, "mcap_usd": 9000},
         "gates": ["biggest operator could move price −62% if sold (≥ 40%; 31.5% of float)"],
         "operators": [{"wallets": [A, B], "share": 0.315, "share_supply": 0.2}],
         "metrics": {"impact": 0.62}, "rug": rug,
         "holders": [{"wallet": A, "share": 0.2, "share_supply": 0.12}, {"wallet": B, "share": 0.115, "share_supply": 0.08}],
         "holders_total": 80, "launch": {"deployer": DEP, "ts": time.time() - launch_age_h * 3600}}
    r.update(kw)
    return r


def private(text, uid=OWNER, chat=None):
    return {"update_id": 1, "message": {"message_id": 5, "text": text, "chat": {"id": chat or uid, "type": "private"},
                                        "from": {"id": uid, "username": "u"}}}


def callback(data, uid=OWNER):
    return {"update_id": 2, "callback_query": {"id": "cq", "data": data, "from": {"id": uid},
                                               "message": {"message_id": 7, "chat": {"id": uid, "type": "private"}}}}


def make_bot(scan=None, resolve=None, store=None, alerts_on=True, radar=None):
    tg, st = FakeTG(), store or FakeStore()
    scans = []

    def default_scan(chain, token):
        scans.append((chain, token))
        return result(token=token, chain=chain)

    bot = core.Bot(tg, st, scan or default_scan, resolve or (lambda tok, hint: (hint or "robinhood", tok)),
                   radar or (lambda q: {"available": True, "items": []}), lambda: ["robinhood", "base", "bsc", "solana"],
                   lambda: alerts_on, lambda c, t: f"https://site.test/?ca={t}&chain={c}", lambda: "https://site.test",
                   allowed={OWNER}, log=lambda m: None, ambiguous=(Ambiguous,), scan_errors=(ScanErr,))
    return bot, tg, st, scans


class TestBotAccess(unittest.TestCase):

    def test_allowlist_parsing(self):
        self.assertEqual(core.allowed_users("111, 222 x 333"), {111, 222, 333})
        self.assertEqual(core.allowed_users(""), set())

    def test_stranger_gets_only_their_id(self):
        bot, tg, st, scans = make_bot()
        bot.handle_update(private(EVM, uid=STRANGER))
        self.assertEqual(scans, [])
        self.assertEqual(st.chats, {})                                  # nothing stored about them
        self.assertEqual(tg.texts(), [T.PRIVATE.format(uid=STRANGER)])
        bot.handle_update(callback(f"watch:robinhood:{EVM}", uid=STRANGER))
        self.assertEqual(st.watches, {})

    def test_empty_allowlist_serves_nobody(self):
        bot, tg, _, scans = make_bot()
        bot.allowed = set()
        bot.handle_update(private("/status"))
        self.assertIn("This bot is private", tg.texts()[0])

    def test_groups_are_ignored(self):
        bot, tg, _, scans = make_bot()
        u = private(f"/scan {EVM}")
        u["message"]["chat"] = {"id": -5, "type": "supergroup"}
        bot.handle_update(u)
        self.assertEqual((tg.calls, scans), ([], []))


class TestBotCommands(unittest.TestCase):

    def test_pasted_address_scans_and_edits_one_message(self):
        bot, tg, st, scans = make_bot()
        bot.handle_update(private(f"look at base {EVM} pls"))
        self.assertEqual(scans, [("base", EVM)])
        sent, edited = tg.of("sendMessage"), tg.of("editMessageText")
        self.assertIn("crawling", sent[0]["text"])
        self.assertEqual(edited[0]["message_id"], 101)
        card = edited[0]["text"]
        for part in ("<b>$TKN</b> · Robinhood" if False else "<b>$TKN</b> · Base", "Score 20/100 · DANGER",
                     "biggest operator could move price −62% if sold", "Largest operator: 2 wallets · 31.5% of float",
                     "basescan.org/address/" + A, "deployer", EVM):
            self.assertIn(part, card)
        buttons = edited[0]["reply_markup"]["inline_keyboard"][0]
        self.assertEqual(buttons[0]["url"], f"https://site.test/?ca={EVM}&chain=base")
        self.assertEqual(buttons[1]["callback_data"], f"watch:base:{EVM}")

    def test_ambiguous_address_asks_the_chain_then_scans_it(self):
        def resolve(tok, hint):
            if not hint:
                raise Ambiguous(["base", "bsc"])
            return hint, tok
        bot, tg, st, scans = make_bot(resolve=resolve)
        bot.handle_update(private(f"/scan {EVM}"))
        ask = tg.of("editMessageText")[-1]
        self.assertIn("exists on Base, BNB Chain", ask["text"])
        datas = [b["callback_data"] for b in ask["reply_markup"]["inline_keyboard"][0]]
        self.assertEqual(datas, [f"scan:base:{EVM}", f"scan:bsc:{EVM}"])
        self.assertTrue(all(len(d.encode()) <= 64 for d in datas))       # Telegram's callback_data limit
        bot.handle_update(callback(f"scan:bsc:{EVM}"))
        self.assertEqual(scans, [("bsc", EVM)])

    def test_unchecked_chains_ask_without_claiming_the_token_exists(self):
        def resolve(tok, hint):
            if hint:
                return hint, tok
            e = Ambiguous(["base"])
            e.unchecked = True
            raise e
        bot, tg, st, scans = make_bot(resolve=resolve)
        bot.handle_update(private(EVM))
        ask = tg.of("editMessageText")[-1]
        self.assertIn("Couldn't check every chain", ask["text"])
        self.assertEqual(ask["reply_markup"]["inline_keyboard"][0][0]["callback_data"], f"scan:base:{EVM}")

    def test_scan_errors_are_plain(self):
        def scan(chain, token):
            raise ScanErr("no contract at this address")
        bot, tg, *_ = make_bot(scan=scan)
        bot.handle_update(private(EVM))
        self.assertIn("no contract at this address", tg.texts()[-1])
        bot, tg, *_ = make_bot(scan=lambda c, t: 1 / 0)
        bot.handle_update(private(EVM))
        self.assertEqual(tg.texts()[-1], T.FAILED)                     # internal errors are not shown

    def test_markup_from_scan_data_is_escaped(self):
        bot, tg, *_ = make_bot(scan=lambda c, t: result(ticker="<b>x</b>", gates=["a <script>"]))
        bot.handle_update(private(EVM))
        card = tg.texts()[-1]
        self.assertIn("$&lt;b&gt;x&lt;/b&gt;", card)
        self.assertNotIn("<script>", card)

    def test_watch_watchlist_unwatch(self):
        bot, tg, st, _ = make_bot()
        bot.handle_update(private(f"/watch {EVM}"))
        self.assertIn("Watching", tg.texts()[-1])
        bot.handle_update(callback(f"watch:robinhood:{EVM}"))
        self.assertIn("Already watching", tg.texts()[-1])
        bot.handle_update(private("/watchlist"))
        listing = tg.of("sendMessage")[-1]
        self.assertIn("Your watchlist", listing["text"])
        self.assertEqual(listing["reply_markup"]["inline_keyboard"][0][0]["callback_data"], f"unwatch:robinhood:{EVM}")
        bot.handle_update(callback(f"unwatch:robinhood:{EVM}"))
        self.assertIn("Stopped watching", tg.texts()[-1])
        self.assertEqual(st.watches, {})

    def test_watch_limit(self):
        bot, tg, st, _ = make_bot()
        with mock.patch.dict(os.environ, {"TG_WATCH_LIMIT": "1"}):
            bot.handle_update(private(f"/watch {EVM}"))
            bot.handle_update(private(f"/watch {A}"))
        self.assertIn("You already watch 1 tokens", tg.texts()[-1])

    def test_watch_needs_alerts(self):
        bot, tg, st, _ = make_bot(alerts_on=False)
        bot.handle_update(private(f"/watch {EVM}"))
        self.assertEqual(tg.texts()[-1], T.ALERTS_OFF)
        bot.handle_update(private(EVM))
        self.assertEqual(len(tg.of("editMessageText")[-1]["reply_markup"]["inline_keyboard"][0]), 1)   # no Watch

    def test_wallet_radar_status_help(self):
        st = FakeStore()
        st.profile = {"held": [{"chain": "base", "token": EVM, "ticker": "TKN", "rank": 2, "share": 0.05,
                                "band": "DANGER", "outcomes": ["rugged"]}], "held_total": 1,
                      "deployed": [], "funded_by": [{"chain": "base", "funder": B, "hub": True}], "funded": 0,
                      "operators": 2, "bundles": 1, "rugged": 1, "up": 0}
        radar = lambda q: {"available": True, "items": [{"status": "active", "chain": "solana", "token": SOL,
                                                         "ticker": "SOLX", "band": "CLEAN", "score": 82,
                                                         "mcap_usd": 50000, "age_min": 90}]}
        bot, tg, *_ = make_bot(store=st, radar=radar)
        bot.handle_update(private(f"/wallet {A}"))
        w = tg.texts()[-1]
        for part in ("top holder in 1 scanned token", "in 2 linked operator groups", "1 rugged", "exchange-like hub",
                     "#2 5.0%", "→ rugged", "Base explorer"):
            self.assertIn(part, w)
        bot.handle_update(private("/radar"))
        self.assertIn("$SOLX</b> · Solana · score 82 · mcap $50.0K · 1.5h old", tg.texts()[-1])
        bot.handle_update(private("/status"))
        self.assertIn("alerts: on", tg.texts()[-1])
        bot.handle_update(private("/help"))
        self.assertIn("/wallet", tg.texts()[-1])
        bot.handle_update(private("hello"))
        self.assertEqual(tg.texts()[-1], T.HINT)

    def test_database_down(self):
        st = FakeStore()
        st.watchlist = lambda chat: None
        bot, tg, *_ = make_bot(store=st)
        bot.handle_update(private("/watchlist"))
        self.assertEqual(tg.texts()[-1], T.DB_DOWN)


class AlertsBase(unittest.TestCase):

    def setUp(self):
        self.st = FakeStore()
        self.st.chat_touch(OWNER, OWNER, "u")
        self.sent = []
        self.env = mock.patch.dict(os.environ, {"TG_BOT_TOKEN": "t", "SITE_URL": "https://site.test"})
        self.env.__enter__()
        self.p = [mock.patch.object(tg_alerts, "store", self.st), mock.patch.object(tg_alerts.time, "sleep")]
        for p in self.p:
            p.__enter__()

    def tearDown(self):
        for p in self.p:
            p.__exit__(None, None, None)
        self.env.__exit__(None, None, None)

    def send(self, chat_id, text, markup):
        self.sent.append((chat_id, text, markup))


class TestDetection(AlertsBase):

    def test_watched_token_diff_is_queued_and_urgent_ones_sent(self):
        self.st.watch(OWNER, "robinhood", EVM, "TKN", 20)
        self.assertEqual(tg_alerts.on_scan(result(band="OK", score=70), send=self.send), 0)   # baseline only
        n = tg_alerts.on_scan(result(band="DANGER", score=20, rug={"drop": 0.8}), send=self.send)
        self.assertEqual(n, 2)
        kinds = sorted(a["kind"] for a in self.st.alerts)
        self.assertEqual(kinds, ["rug_appeared", "verdict_worse"])
        self.assertTrue(all(a["priority"] == 0 for a in self.st.alerts))      # into DANGER and rug: urgent
        self.assertEqual(len(self.sent), 1)                                     # one message for both
        text = self.sent[0][1]
        self.assertIn("Verdict worsened: OK → DANGER", text)
        self.assertIn("Probably rug: −80%", text)
        self.assertIn("Now: 🔴 <b>DANGER</b> · score 20/100", text)
        self.assertEqual(self.sent[0][2]["inline_keyboard"][0][1]["callback_data"], f"unwatch:robinhood:{EVM}")

    def test_unwatched_tokens_store_nothing(self):
        tg_alerts.on_scan(result(), send=self.send)
        self.assertEqual((self.st.snaps, self.st.alerts), ({}, []))

    def test_partial_scans_are_not_diffed(self):
        self.st.watch(OWNER, "robinhood", EVM, None, 20)
        tg_alerts.on_scan(result(partial_scan={"state": "building"}))
        self.assertEqual(self.st.snaps, {})

    def test_suspicious_deployer_on_fresh_tokens_only(self):
        mem = {"deployer": {"tokens": 4, "rugged": 2, "danger": 1}}
        tg_alerts.on_scan(result(memory=mem, launch_age_h=3), send=self.send)
        self.assertEqual([a["kind"] for a in self.st.alerts], ["deployer_repeat"])
        self.assertIn("Known suspicious deployer launched again: 4 earlier tokens in memory (2 rugged, 1 rated DANGER)",
                      self.st.alerts[0]["body"]["line"])
        tg_alerts.on_scan(result(memory=mem, launch_age_h=3), send=self.send)   # the same token again: deduped
        self.assertEqual(len(self.st.alerts), 1)
        tg_alerts.on_scan(result(token=A, memory=mem, launch_age_h=72), send=self.send)   # old launch: not "again"
        self.assertEqual(len(self.st.alerts), 1)

    def test_smart_crew_needs_tokens_that_went_up(self):
        crew = {"crews": [{"wallets": [A, B], "tokens": [C, DEP, "t3"], "share_supply": 0.07}]}
        self.st.labels = {C: ["up"], DEP: ["rugged"]}
        tg_alerts.on_scan(result(memory=crew), send=self.send)
        self.assertEqual(self.st.alerts, [])                                    # one up of three: not smart
        self.st.labels["t3"] = ["survived", "up"]
        tg_alerts.on_scan(result(token=A, memory=crew), send=self.send)
        self.assertEqual([a["kind"] for a in self.st.alerts], ["smart_crew"])
        self.assertIn("2 wallets that held 2 earlier tokens that went up together now hold 7.0% of supply",
                      self.st.alerts[0]["body"]["line"])

    def test_alert_hook_never_raises(self):
        self.st.snapshot_swap = lambda *a: 1 / 0
        self.st.watch(OWNER, "robinhood", EVM, None, 20)
        self.assertEqual(tg_alerts.on_scan(result()), 0)

    def test_radar_pass(self):
        self.st.watch(OWNER, "base", EVM, "TKN", 20)
        feed = lambda q: {"available": True, "items": [{"status": "active", "chain": "base", "token": EVM,
                                                        "band": "CLEAN", "score": 81, "mcap_usd": 12000}]}
        out = tg_alerts.radar_pass(feed)
        self.assertEqual((out["changed"], out["queued"]), (1, 1))
        self.assertIn("Now on the launch radar: CLEAN · score 81 · mcap $12,000", self.st.alerts[0]["body"]["line"])
        self.assertEqual(tg_alerts.radar_pass(feed)["queued"], 0)               # still on the radar: no new alert
        tg_alerts.radar_pass(lambda q: {"available": True, "items": []})
        self.assertFalse(self.st.watches[(OWNER, "base", EVM)]["radar_active"])


class TestDelivery(AlertsBase):

    def queue(self, kind, token=EVM, chat=OWNER, key=None):
        self.st.chat_touch(chat, chat, None)
        self.st.enqueue([tg_alerts._row(chat, "robinhood", token, kind, key or f"{kind}:{token}", f"{kind} line")])

    def test_priority_order_and_grouping(self):
        self.queue("radar_eligible", token=A)
        self.queue("operator_sold")
        self.queue("early_dropped")
        out = tg_alerts.dispatch(send=self.send)
        self.assertEqual((out["sent"], out["messages"]), (3, 2))
        self.assertIn("operator_sold line", self.sent[0][1])                  # most urgent token first
        self.assertIn("early_dropped line", self.sent[0][1])                  # same token: same message
        self.assertIn("radar_eligible line", self.sent[1][1])

    def test_cooldown_and_dedupe(self):
        self.queue("operator_sold", key="k1")
        self.queue("operator_sold", key="k1")
        self.queue("operator_sold", key="k2")                                 # same kind, inside the cooldown
        self.assertEqual(len(self.st.alerts), 1)

    def test_hourly_cap(self):
        with mock.patch.dict(os.environ, {"TG_ALERT_HOURLY_CAP": "1"}):
            self.queue("operator_sold")
            self.queue("early_dropped", token=A)
            out = tg_alerts.dispatch(send=self.send)
        self.assertEqual(out["sent"], 1)
        self.assertEqual([a["status"] for a in self.st.alerts], ["sent", "queued"])

    def test_blocked_bot_drops_the_chat(self):
        self.queue("operator_sold")

        def send(*a):
            raise TelegramError(403, "Forbidden: bot was blocked by the user")
        out = tg_alerts.dispatch(send=send)
        self.assertEqual(out["failed"], 1)
        self.assertNotIn(OWNER, self.st.chats)

    def test_rate_limited_alert_is_retried(self):
        self.queue("operator_sold")

        def send(*a):
            raise TelegramError(429, "Too Many Requests", retry_after=7)
        out = tg_alerts.dispatch(send=send)
        self.assertEqual(out["retry"], 1)
        self.assertEqual((self.st.alerts[0]["status"], self.st.alerts[0]["retry_s"]), ("queued", 7))

    def test_bad_request_fails_without_retry(self):
        self.queue("operator_sold")

        def send(*a):
            raise TelegramError(400, "Bad Request: chat not found")
        self.assertEqual(tg_alerts.dispatch(send=send)["failed"], 1)
        self.assertEqual(self.st.alerts[0]["status"], "failed")

    def test_compose_global_alert_offers_watch(self):
        row = tg_alerts._row(OWNER, "solana", SOL, "deployer_repeat", "d", "Known suspicious deployer", ticker="X")
        text, markup = tg_alerts.compose([dict(row, id=1)])
        self.assertTrue(text.startswith("🚩 <b>$X</b> · Solana"))
        self.assertEqual(markup["inline_keyboard"][0][0]["url"], f"https://site.test/?ca={SOL}")
        self.assertEqual(markup["inline_keyboard"][0][1]["callback_data"], f"watch:solana:{SOL}")

    def test_report_url_falls_back_to_vercel_domain(self):
        with mock.patch.dict(os.environ, {"SITE_URL": "", "VERCEL_PROJECT_PRODUCTION_URL": "crawlmeme.vercel.app"}):
            self.assertEqual(tg_alerts.report_url("base", EVM), f"https://crawlmeme.vercel.app/?ca={EVM}&chain=base")
        with mock.patch.dict(os.environ, {"SITE_URL": "", "VERCEL_PROJECT_PRODUCTION_URL": ""}):
            self.assertIsNone(tg_alerts.report_url("base", EVM))


class TestCron(AlertsBase):

    def test_rechecks_due_tokens_within_budget_and_delivers(self):
        self.st.watch(OWNER, "robinhood", EVM, "TKN", 20)
        self.st.watch(OWNER, "robinhood", A, None, 20)
        self.st.snaps[("robinhood", EVM)] = alerts.snapshot(result(band="OK", score=70), 0.2)
        scanned = []

        def scan(chain, token):
            scanned.append(token)
            return result(token=token, band="OK", score=70)
        t = [0.0]
        clock = lambda: t[0]

        def early_get(token):
            t[0] += 30                                   # the first recheck uses most of the budget
            return {"available": True, "summary": {"held_share_supply": 0.05}}
        out = tg_alerts.cron(scan, budget=48.0, clock=clock, send=self.send,
                             query=lambda q: {"available": True, "items": []}, early_get=early_get)
        self.assertEqual(scanned, [A])                   # sorted due list: one recheck fits, the next waits
        self.assertEqual(out["due"], 2)
        self.assertEqual(self.st.state["alerts_cron"]["rechecked"], 1)

    def test_early_buyers_exit_is_found_by_the_recheck(self):
        self.st.watch(OWNER, "robinhood", EVM, "TKN", 20)
        self.st.snaps[("robinhood", EVM)] = alerts.snapshot(result(band="OK", score=70), 0.20)
        out = tg_alerts.cron(lambda c, t: result(token=t, band="OK", score=70), send=self.send,
                             query=lambda q: {"available": True, "items": []},
                             early_get=lambda t: {"available": True, "summary": {"held_share_supply": 0.05}})
        self.assertEqual(out["rechecked"][0]["queued"], 1)
        self.assertIn("Early buyers are exiting: their share fell 75%", self.sent[0][1])


class TestEndpoints(StreamBase):

    def test_webhook_auth_and_dedupe(self):
        upd = {"update_id": 42, "message": {"text": "/help", "chat": {"id": 1, "type": "private"}, "from": {"id": 1}}}
        with mock.patch.dict(os.environ, {"TG_BOT_TOKEN": "", "TG_WEBHOOK_SECRET": "s"}):
            self.assertEqual(self.call("POST", "/api/telegram", upd)[0], 404)          # bot off
        with mock.patch.dict(os.environ, {"TG_BOT_TOKEN": "t", "TG_WEBHOOK_SECRET": "s"}):
            self.assertEqual(self.call("POST", "/api/telegram", upd)[0], 401)
            bad = {"X-Telegram-Bot-Api-Secret-Token": "x"}
            self.assertEqual(self.call("POST", "/api/telegram", upd, bad)[0], 401)
            ok = {"X-Telegram-Bot-Api-Secret-Token": "s"}
            self.assertEqual(self.call("POST", "/api/telegram", {"nope": 1}, ok)[0], 400)
            bot = mock.Mock()
            with mock.patch("crawler.server.telegram_bot", return_value=bot), \
                    mock.patch("crawler.tg_store.enabled", return_value=True), \
                    mock.patch("crawler.tg_store.update_seen", side_effect=[True, False]):
                self.assertEqual(json.loads(self.call("POST", "/api/telegram", upd, ok)[2]), {"ok": True})
                self.assertEqual(json.loads(self.call("POST", "/api/telegram", upd, ok)[2])["duplicate"], True)
            bot.handle_update.assert_called_once_with(upd)                           # a retry is not handled twice

    def test_cron_alerts_auth(self):
        with mock.patch.dict(os.environ, {"CRON_SECRET": "c", "TG_BOT_TOKEN": ""}):
            self.assertEqual(self.call("GET", "/api/cron/alerts")[0], 401)
            auth = {"authorization": "Bearer c"}
            self.assertEqual(self.call("GET", "/api/cron/alerts", headers=auth)[0], 503)
            with mock.patch("crawler.tg_alerts.enabled", return_value=True), \
                    mock.patch("crawler.tg_alerts.cron", return_value={"due": 0}) as cron:
                code, _, raw = self.call("GET", "/api/cron/alerts", headers=auth)
        self.assertEqual((code, json.loads(raw)), (200, {"due": 0}))
        cron.assert_called_once()

    def test_telegram_setup_never_returns_the_bypass(self):
        auth = {"authorization": "Bearer c"}
        with mock.patch.dict(os.environ, {"CRON_SECRET": "c", "TG_BOT_TOKEN": "", "TG_WEBHOOK_SECRET": "",
                                          "SITE_URL": ""}):
            code, _, raw = self.call("GET", "/api/cron/telegram", headers=auth)
        self.assertEqual((code, set(json.loads(raw)["missing"])), (503, {"TG_BOT_TOKEN", "TG_WEBHOOK_SECRET", "SITE_URL"}))
        calls = []

        def call(self_, method, **p):
            calls.append((method, p))
            return {"username": "crawlmemebot"} if method == "getMe" else \
                {"pending_update_count": 0, "last_error_message": "bad webhook ?x-vercel-protection-bypass=BYP"} \
                if method == "getWebhookInfo" else True
        env = {"CRON_SECRET": "c", "TG_BOT_TOKEN": "t", "TG_WEBHOOK_SECRET": "w", "SITE_URL": "https://site.test",
               "VERCEL_AUTOMATION_BYPASS_SECRET": "BYP", "TG_ALLOWED_USERS": "1,2"}
        with mock.patch.dict(os.environ, env), mock.patch("bot.tg.Telegram.call", call):
            code, _, raw = self.call("GET", "/api/cron/telegram", headers=auth)
        self.assertEqual(code, 200)
        self.assertNotIn(b"BYP", raw)
        body = json.loads(raw)
        self.assertEqual((body["bot"], body["bypass"], body["allowed_users"]), ("crawlmemebot", True, 2))
        hook = dict(calls)["setWebhook"]
        self.assertEqual(hook["url"], "https://site.test/api/telegram?x-vercel-protection-bypass=BYP")
        self.assertEqual((hook["secret_token"], hook["allowed_updates"]), ("w", ["message", "callback_query"]))
        self.assertEqual(dict(calls)["setMyCommands"]["commands"], core.COMMANDS)


PG = os.environ.get("CRAWLER_PG")


@unittest.skipUnless(PG, "set CRAWLER_PG to run the Telegram SQL against a local Postgres")
class TestTelegramSQL(unittest.TestCase):

    def q(self, sql):
        host, port = PG.split(":")
        out = subprocess.run(["psql", "-h", host, "-p", port, "-U", "postgres", "-At", "-v", "ON_ERROR_STOP=1",
                              "-c", "set role service_role; " + sql], capture_output=True, text=True, timeout=30)
        self.assertEqual(out.returncode, 0, out.stderr)
        return out.stdout.strip().splitlines()[-1] if out.stdout.strip() else ""

    def j(self, sql):
        return json.loads(self.q(sql))

    def setUp(self):
        self.chat = int.from_bytes(os.urandom(4), "big")
        self.q(f"select public.tg_chat_touch({self.chat}, {self.chat}, 'u')")

    def test_update_ids_are_handled_once(self):
        uid = int.from_bytes(os.urandom(5), "big")
        self.assertEqual(self.q(f"select public.tg_update_seen({uid})"), "t")
        self.assertEqual(self.q(f"select public.tg_update_seen({uid})"), "f")

    def test_watch_limit_snapshot_swap_and_cascade(self):
        c, tok = self.chat, "0x" + os.urandom(20).hex()
        self.assertEqual(self.j(f"select public.tg_watch({c}, 'base', '{tok}', 'T', 1)")["status"], "added")
        self.assertEqual(self.j(f"select public.tg_watch({c}, 'base', '{tok}', null, 1)")["status"], "exists")
        self.assertEqual(self.j(f"select public.tg_watch({c}, 'bsc', '{tok}', null, 1)")["status"], "limit")
        other = "0x" + os.urandom(20).hex()
        self.assertFalse(self.j(f"select public.tg_snapshot_swap('base', '{other}', '{{}}'::jsonb)")["watched"])
        first = self.j(f"select public.tg_snapshot_swap('base', '{tok}', '{{\"band\": \"OK\"}}'::jsonb)")
        self.assertEqual((first["watched"], first["prev"], first["chats"]), (True, None, [c]))
        second = self.j(f"select public.tg_snapshot_swap('base', '{tok}', '{{\"band\": \"DANGER\"}}'::jsonb)")
        self.assertEqual(second["prev"], {"band": "OK"})
        wl = self.j(f"select public.tg_watchlist({c})")
        self.assertEqual((wl[0]["ticker"], wl[0]["band"]), ("T", "DANGER"))
        self.q(f"select public.tg_chat_gone({c})")
        self.assertEqual(self.j(f"select public.tg_watchlist({c})"), [])

    def test_queue_dedupe_cooldown_priority_and_cap(self):
        c, tok = self.chat, "t" + os.urandom(6).hex()
        rows = lambda *rs: "'" + json.dumps([dict(chat_id=c, chain="base", token=tok, body={"line": "x"}, **r)
                                             for r in rs]) + "'::jsonb"
        n = self.q("select public.tg_alert_enqueue(" + rows(
            dict(kind="radar_eligible", priority=2, dedupe_key="a", cooldown_s=3600),
            dict(kind="operator_sold", priority=0, dedupe_key="b", cooldown_s=3600),
            dict(kind="operator_sold", priority=0, dedupe_key="b", cooldown_s=3600)) + ")")
        self.assertEqual(n, "2")
        again = self.q("select public.tg_alert_enqueue(" + rows(
            dict(kind="operator_sold", priority=0, dedupe_key="c", cooldown_s=3600),        # inside the cooldown
            dict(kind="early_dropped", priority=1, dedupe_key="d", cooldown_s=0)) + ")")
        self.assertEqual(again, "1")
        mine = lambda xs: [x for x in xs if x["chat_id"] == c]
        got = mine(self.j("select public.tg_alert_claim(500, 1)"))
        self.assertEqual([g["kind"] for g in got], ["operator_sold"])                     # most urgent, cap 1
        self.assertEqual(mine(self.j("select public.tg_alert_claim(500, 1)")), [])          # a send in flight counts
        self.q("select public.tg_alert_done('" + json.dumps([{"id": got[0]["id"], "status": "sent"}]) + "'::jsonb)")
        self.assertEqual(mine(self.j("select public.tg_alert_claim(500, 1)")), [])          # hourly cap reached
        rest = mine(self.j("select public.tg_alert_claim(500, 5)"))
        self.assertEqual([g["kind"] for g in rest], ["early_dropped", "radar_eligible"])
        retry = [{"id": rest[0]["id"], "status": "queued", "retry_s": 3600}, {"id": rest[1]["id"], "status": "failed"}]
        self.assertEqual(self.q("select public.tg_alert_done('" + json.dumps(retry) + "'::jsonb)"), "2")
        self.assertEqual(mine(self.j("select public.tg_alert_claim(500, 5)")), [])          # retry waits an hour

    def test_wallet_profile_of_an_unknown_wallet(self):
        p = self.j("select public.wallet_profile('0x" + os.urandom(20).hex() + "')")
        self.assertEqual((p["held"], p["deployed"], p["held_total"], p["funded"]), ([], [], 0, 0))


if __name__ == "__main__":
    unittest.main()
