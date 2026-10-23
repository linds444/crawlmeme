"""CrawlMeme Telegram bot: command handling, independent of how updates arrive.

Two transports share this class:
  webhook   /api/telegram on the site (crawler/server.py): Telegram POSTs each update; the server checks the secret
            header and the update id (handled once), then calls handle_update in the same request — a scan finishes
            inside the function's time limit
  polling   bot/main.py for a self-hosted machine: getUpdates in a loop, the same handle_update per update
Private by default: only Telegram user ids in TG_ALLOWED_USERS are served (an empty list serves nobody). Anyone else
gets one line with their own user id, so the owner can add it, and nothing else — no scan, no database row.
Only private chats are handled; the bot stays silent in groups.
"""
import os, time, traceback

from bot import text as T
from bot.tg import TelegramError

COMMANDS = [
    {"command": "scan", "description": "Scan a token: /scan <address>"},
    {"command": "watch", "description": "Alerts for a token: /watch <address>"},
    {"command": "watchlist", "description": "Tokens you watch"},
    {"command": "unwatch", "description": "Stop alerts: /unwatch <address>"},
    {"command": "wallet", "description": "What memory knows about a wallet"},
    {"command": "radar", "description": "Fresh tokens on the launch radar"},
    {"command": "status", "description": "Scanner and alert status"},
    {"command": "help", "description": "Commands and how to read a verdict"},
]
WATCH_LIMIT = 20          # default TG_WATCH_LIMIT


def allowed_users(raw=None):
    """TG_ALLOWED_USERS: comma / space separated numeric Telegram user ids → set of ints (bad entries ignored)."""
    raw = os.environ.get("TG_ALLOWED_USERS", "") if raw is None else raw
    out = set()
    for p in raw.replace(",", " ").split():
        try:
            out.add(int(p))
        except ValueError:
            pass
    return out


def watch_limit():
    try:
        v = int(os.environ.get("TG_WATCH_LIMIT", "").strip())
        return v if v > 0 else WATCH_LIMIT
    except ValueError:
        return WATCH_LIMIT


def parse_command(text):
    """"/scan@bot 0x..." → ("scan", "bot", "0x..."); not a command → (None, None, text)."""
    if not text.startswith("/"):
        return None, None, text
    head, _, arg = text.partition(" ")
    cmd, _, target = head[1:].partition("@")
    return cmd.lower(), target or None, arg.strip()


class Bot:
    def __init__(self, tg, store, scan, resolve, radar_query, chains, alerts_on, report_url, site_url=None,
                 allowed=None, username="", log=None, ambiguous=(), scan_errors=()):
        """scan(chain, token) -> engine result (recorded in the site's stores and alerts);
        resolve(token, chain_hint) -> (chain, token), raising one of ambiguous (with .chains) or scan_errors;
        radar_query(q) -> /api/radar body; chains() -> enabled chains; alerts_on() -> bool;
        report_url(chain, token) -> full report link or None; site_url() -> base link or None."""
        self.tg, self.store, self.scan, self.resolve = tg, store, scan, resolve
        self.radar_query, self.chains, self.alerts_on = radar_query, chains, alerts_on
        self.report_url, self.site_url = report_url, site_url or (lambda: None)
        self.allowed = allowed_users() if allowed is None else set(allowed)
        self.username = username
        self.ambiguous, self.scan_errors = tuple(ambiguous), tuple(scan_errors)
        self.log = log or (lambda m: print(m, flush=True))

    # --- Telegram ------------------------------------------------------------------------------------
    def call(self, method, **params):
        try:
            return self.tg.call(method, **params)
        except TelegramError as e:
            if "message is not modified" not in e.description:
                self.log(f"telegram {method}: {e}")
        except Exception as e:
            self.log(f"telegram {method}: {type(e).__name__}: {self.tg.redact(e)}")
        return None

    def send(self, chat_id, html, markup=None):
        msg = self.call("sendMessage", chat_id=chat_id, text=html, parse_mode="HTML", reply_markup=markup,
                        link_preview_options={"is_disabled": True})
        return msg.get("message_id") if isinstance(msg, dict) else None

    def edit(self, chat_id, message_id, html, markup=None):
        if message_id is None:
            return self.send(chat_id, html, markup)
        self.call("editMessageText", chat_id=chat_id, message_id=message_id, text=html, parse_mode="HTML",
                  reply_markup=markup, link_preview_options={"is_disabled": True})
        return message_id

    # --- updates -------------------------------------------------------------------------------------
    def handle_update(self, u):
        try:
            if "message" in u:
                self.handle_message(u["message"])
            elif "callback_query" in u:
                self.handle_callback(u["callback_query"])
        except Exception:
            self.log("update failed:\n" + self.tg.redact(traceback.format_exc()))

    def _gate(self, chat, user):
        """→ True when the user may use the bot in this chat. Private chats only; non-allowlisted users get their id."""
        if chat.get("type") != "private" or user.get("is_bot"):
            return False
        uid = user.get("id")
        if uid not in self.allowed:
            self.log(f"bot: refused user {uid}")
            self.send(chat.get("id"), T.PRIVATE.format(uid=uid))
            return False
        if self.store.enabled():
            self.store.chat_touch(chat["id"], uid, user.get("username"))
        return True

    def handle_message(self, m):
        chat, user = m.get("chat") or {}, m.get("from") or {}
        txt = (m.get("text") or "").strip()
        if not txt:
            return
        cmd, target, arg = parse_command(txt)
        if target and self.username and target.lower() != self.username.lower():
            return
        if not self._gate(chat, user):
            return
        cid = chat["id"]
        if cmd in ("start",):
            return self.send(cid, T.START)
        if cmd == "help":
            return self.send(cid, T.HELP)
        if cmd == "scan":
            found = T.find_address(arg)
            return self.do_scan(cid, *found) if found else self.send(cid, T.SCAN_USAGE)
        if cmd == "watch":
            found = T.find_address(arg)
            return self.do_watch(cid, *found) if found else self.send(cid, T.WATCH_USAGE)
        if cmd == "unwatch":
            found = T.find_address(arg)
            return self.do_unwatch(cid, found[1], found[0]) if found else self.send(cid, T.UNWATCH_USAGE)
        if cmd == "watchlist":
            return self.do_watchlist(cid)
        if cmd == "wallet":
            found = T.find_address(arg)
            return self.do_wallet(cid, *found) if found else self.send(cid, T.WALLET_USAGE)
        if cmd == "radar":
            return self.send(cid, T.radar_list(self.radar_query({"window": ["1440"]}), self.site_url()))
        if cmd == "status":
            return self.do_status(cid)
        if cmd is None:
            found = T.find_address(txt)
            if found:
                return self.do_scan(cid, *found)
        return self.send(cid, T.HINT)

    def handle_callback(self, cq):
        self.call("answerCallbackQuery", callback_query_id=cq.get("id"))
        msg = cq.get("message") or {}
        chat, user = msg.get("chat") or {}, cq.get("from") or {}
        if not self._gate(chat, user):
            return
        action, _, rest = (cq.get("data") or "").partition(":")
        chain, _, token = rest.partition(":")
        if not token or T.kind_of(token) is None or chain not in T.CHAIN_NAME:
            return
        cid, mid = chat["id"], msg.get("message_id")
        if action == "scan":
            return self.do_scan(cid, chain, token, edit=mid)
        if action == "watch":
            return self.do_watch(cid, chain, token)
        if action == "unwatch":
            return self.do_unwatch(cid, token, chain)

    # --- commands ------------------------------------------------------------------------------------
    def _resolve(self, cid, hint, token, action, mid=None):
        """→ (chain, token) or None after telling the user (several chains: buttons to pick; bad address: why)."""
        try:
            return self.resolve(token, hint)
        except self.ambiguous as e:
            text, buttons = T.choose_chain(token, list(e.chains), action, getattr(e, "unchecked", False))
            self.edit(cid, mid, text, buttons)
        except self.scan_errors as e:
            self.edit(cid, mid, f"⚠️ {T.e(str(e))}")
        return None

    def do_scan(self, cid, hint, token, edit=None):
        mid = self.edit(cid, edit, T.crawling(token)) if edit else self.send(cid, T.crawling(token))
        r = self._resolve(cid, hint, token, "scan", mid)
        if not r:
            return
        chain, token = r
        t0 = time.time()
        try:
            res = self.scan(chain, token)
        except self.scan_errors as e:
            return self.edit(cid, mid, T.scan_error(token, str(e)))
        except Exception as e:
            self.log(f"bot: scan {chain}:{token} failed: {type(e).__name__}")
            return self.edit(cid, mid, T.FAILED)
        html, markup = T.scan_card(res, self.report_url(chain, token), watch_button=self.alerts_on())
        self.log(f"bot: scan {chain}:{token} {res.get('band')} in {time.time() - t0:.1f}s")
        return self.edit(cid, mid, html, markup)

    def do_watch(self, cid, hint, token):
        if not self.alerts_on():
            return self.send(cid, T.ALERTS_OFF)
        r = self._resolve(cid, hint, token, "watch")
        if not r:
            return
        chain, token = r
        res = self.store.watch(cid, chain, token, None, watch_limit())
        self.log(f"bot: watch {chain}:{token} chat {cid}: {(res or {}).get('status')}")
        return self.send(cid, T.watch_result(res, chain, token))

    def do_unwatch(self, cid, token, chain=None):
        n = self.store.unwatch(cid, token, chain)
        if n is None:
            return self.send(cid, T.DB_DOWN)
        return self.send(cid, T.unwatched(n, token))

    def do_watchlist(self, cid):
        items = self.store.watchlist(cid)
        if items is None:
            return self.send(cid, T.DB_DOWN)
        return self.send(cid, *T.watchlist(items))

    def do_wallet(self, cid, hint, address):
        p = self.store.wallet_profile(address, hint)
        if p is None:
            return self.send(cid, T.DB_DOWN)
        return self.send(cid, T.wallet_card(p, hint))

    def do_status(self, cid):
        s = self.store.status(cid) if self.store.enabled() else None
        return self.send(cid, T.status_card(s, self.chains(), self.alerts_on(), s is not None))
