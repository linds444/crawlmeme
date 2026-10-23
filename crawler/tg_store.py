"""Phase 15: Telegram bot and alert storage in Supabase (schema: supabase/migrations/20261023120000_telegram.sql).

Thin wrappers over the SQL functions through memory_db.call (PostgREST RPC with the service-role key, server side
only). Every call returns None when the database is unavailable; callers decide whether that is an error for the
user ("try again") or a skipped step (the cron logs it and moves on). Bot tests replace this module with a fake that
has the same functions.
"""
from crawler import memory_db

TIMEOUT = 6.0


def enabled():
    return memory_db.enabled()


def _call(name, args, timeout=TIMEOUT):
    return memory_db.call(name, args, timeout=timeout)


def update_seen(update_id):
    """True the first time a webhook update id is seen; None: database down (the caller handles the update anyway)."""
    return _call("tg_update_seen", {"p_update_id": int(update_id)})


def chat_touch(chat_id, user_id, username):
    return _call("tg_chat_touch", {"p_chat_id": int(chat_id), "p_user_id": int(user_id), "p_username": username})


def watch(chat_id, chain, token, ticker, limit):
    """{"status": added | exists | limit, "count", "limit"} or None."""
    return _call("tg_watch", {"p_chat_id": int(chat_id), "p_chain": chain, "p_token": token, "p_ticker": ticker,
                              "p_limit": int(limit)})


def unwatch(chat_id, token, chain=None):
    """Number of watches removed (all chains of a 0x address unless chain) or None."""
    return _call("tg_unwatch", {"p_chat_id": int(chat_id), "p_token": token, "p_chain": chain})


def watchlist(chat_id):
    return _call("tg_watchlist", {"p_chat_id": int(chat_id)})


def watch_due(min_age_min, limit):
    return _call("tg_watch_due", {"p_min_age_min": int(min_age_min), "p_limit": int(limit)})


def watch_checked(chain, token):
    return _call("tg_watch_checked", {"p_chain": chain, "p_token": token})


def watched():
    return _call("tg_watched", {})


def radar_set(rows):
    return _call("tg_radar_set", {"p": rows}) if rows else 0


def snapshot_swap(chain, token, snapshot):
    """{"watched": bool, "prev": snapshot | None, "chats": [chat_id]} or None."""
    return _call("tg_snapshot_swap", {"p_chain": chain, "p_token": token, "p_snapshot": snapshot})


def snapshot_early(chain, token, share):
    return _call("tg_snapshot_early", {"p_chain": chain, "p_token": token, "p_share": share})


def global_chats():
    return _call("tg_global_chats", {})


def token_labels(chain, tokens):
    return _call("tg_token_labels", {"p_chain": chain, "p_tokens": list(tokens)}) if tokens else {}


def enqueue(rows):
    """Number of alerts queued (dedupe and cooldown applied by the database) or None."""
    return _call("tg_alert_enqueue", {"p": rows}) if rows else 0


def claim(limit, hourly_cap, max_age_h=24):
    return _call("tg_alert_claim", {"p_limit": int(limit), "p_hourly_cap": int(hourly_cap),
                                    "p_max_age_h": int(max_age_h)})


def done(rows):
    return _call("tg_alert_done", {"p": rows}) if rows else 0


def chat_gone(chat_id):
    return _call("tg_chat_gone", {"p_chat_id": int(chat_id)})


def state_set(key, value):
    return _call("tg_state_set", {"p_key": key, "p_value": value})


def status(chat_id):
    return _call("tg_status", {"p_chat_id": int(chat_id)})


def housekeeping():
    return _call("tg_housekeeping", {})


def wallet_profile(address, chain=None, limit=8):
    return _call("wallet_profile", {"p_address": address, "p_chain": chain, "p_limit": int(limit)})
