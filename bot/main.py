"""CrawlMeme Telegram bot, polling transport for a self-hosted machine:

  python bot/main.py

The production bot runs as a webhook on the site (POST /api/telegram, registered by GET /api/cron/telegram); use this
runner only where no public URL exists, for example local development. It handles updates with the same bot core
(bot/core.py) and the same in-process scanner, memory and alert hook as the webhook.

env (from .env via crawler/env.py; never printed): TG_BOT_TOKEN (required), TG_ALLOWED_USERS (numeric user ids; empty
serves nobody), the scanner's own RPC and memory settings. Telegram allows one consumer: while a webhook is registered
getUpdates answers 409 and this runner stops with a message instead of taking the webhook over.
"""
import os, sys, threading, time, traceback
from concurrent.futures import ThreadPoolExecutor

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from crawler import env                     # noqa: E402
from bot.tg import TelegramError            # noqa: E402

WORKERS = 3            # updates handled at once (a scan takes a worker for up to a minute)
LONG_POLL = 30         # getUpdates timeout in seconds

_log_lock = threading.Lock()


def log(msg):
    with _log_lock:
        print(time.strftime("%Y-%m-%d %H:%M:%S"), msg, flush=True)


def poll(bot, tg, sleep=time.sleep, once=False):
    """getUpdates loop; each update goes to the worker pool. 409 (a webhook or another runner) stops the loop."""
    offset, backoff = None, 1
    with ThreadPoolExecutor(WORKERS) as pool:
        while True:
            try:
                updates = tg.call("getUpdates", http_timeout=LONG_POLL + 10, offset=offset, timeout=LONG_POLL,
                                  allowed_updates=["message", "callback_query"]) or []
                backoff = 1
            except TelegramError as e:
                if e.code == 409:
                    log("getUpdates: 409 Conflict — a webhook is registered or another runner is polling; stopping")
                    return False
                log(f"getUpdates: {e}")
                sleep(backoff)
                backoff = min(30, backoff * 2)
                continue
            for u in updates:
                offset = max(offset or 0, u.get("update_id", 0) + 1)
                pool.submit(bot.handle_update, u)
            if once:
                return True


def main():
    env.load_dotenv()
    if not os.environ.get("TG_BOT_TOKEN", "").strip():
        print("TG_BOT_TOKEN is not set", file=sys.stderr)
        sys.exit(1)
    from crawler import server             # the scanner, stores and alert hook the webhook uses
    from bot.core import COMMANDS
    bot = server.telegram_bot()
    if not bot.allowed:
        log("TG_ALLOWED_USERS is empty: the bot will answer every user with their id and serve nobody")
    me = bot.call("getMe") or {}
    bot.username = me.get("username") or ""
    bot.call("setMyCommands", commands=COMMANDS)
    log(f"@{bot.username} polling, {len(bot.allowed)} allowed user(s)")
    try:
        poll(bot, bot.tg)
    except KeyboardInterrupt:
        pass
    except Exception:
        log("poll loop crashed:\n" + bot.tg.redact(traceback.format_exc()))
        sys.exit(1)


if __name__ == "__main__":
    main()
