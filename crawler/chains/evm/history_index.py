"""Resumable per-token transfer history index for the generic EVM adapter (SQLite, standard library).

Why: re-reading a token's whole transfer history on every scan wastes most of the RPC budget, and a large history
may not fit in one scan's time budget at all. The index keeps, per (chain, token):
  from_block  — where the history starts (the launch block)
  head        — every block ≤ head has been read (a checkpoint taken only at the end of a complete block window)
  complete    — the history reached the chain head at the time of the read (False: indexing was cut by the budget)
  verified    — the balances rebuilt from the transfers sum exactly to totalSupply (conservation check)
  transfers   — zlib-compressed rows [frm, to, amount (hex), tx, block, log_index]
A repeat scan reads only blocks after head − REORG_LAG (the last blocks are re-read: a node near the head may have
lagged or a short reorg may have replaced them); an interrupted read resumes from its checkpoint.
Size: bounded by INDEX_MAX_MB (least recently updated tokens are evicted). Lives next to the other local stores
(LOCAL_DB_PATH's folder; /tmp on Vercel, where it is per-instance and ephemeral until Postgres in Phase 11).
"""
import json, os, sqlite3, threading, time, zlib

from crawler import db

REORG_LAG = 64            # blocks re-read on every update
SCHEMA = """
CREATE TABLE IF NOT EXISTS token_history (
  chain TEXT NOT NULL, token TEXT NOT NULL, from_block INTEGER NOT NULL, head INTEGER NOT NULL,
  complete INTEGER NOT NULL, verified INTEGER NOT NULL, n INTEGER NOT NULL, updated REAL NOT NULL,
  bytes INTEGER NOT NULL, blob BLOB NOT NULL, PRIMARY KEY (chain, token));
CREATE INDEX IF NOT EXISTS token_history_updated ON token_history(updated);
"""


def max_bytes():
    try:
        return max(1, int(os.environ.get("INDEX_MAX_MB", "").strip() or 512)) * 1024 * 1024
    except ValueError:
        return 512 * 1024 * 1024


def default_path():
    return os.path.join(os.path.dirname(os.path.abspath(db.local_path())), "history.db")


def encode(transfers):
    rows = [[t["frm"], t["to"], format(t["amount"], "x"), t["tx"], t["block"], t["log_index"]] for t in transfers]
    return zlib.compress(json.dumps(rows, separators=(",", ":")).encode(), 6)


def decode(blob):
    same = {}   # one string object per address / tx hash: long histories repeat them thousands of times
    out = []
    for frm, to, amt, tx, block, li in json.loads(zlib.decompress(blob)):
        out.append({"frm": same.setdefault(frm, frm), "to": same.setdefault(to, to), "amount": int(amt, 16),
                    "tx": same.setdefault(tx, tx), "block": block, "log_index": li})
    return out


class HistoryIndex:
    def __init__(self, path=None):
        self.path = path or default_path()
        os.makedirs(os.path.dirname(os.path.abspath(self.path)), exist_ok=True)
        self.db = db.connect(self.path)
        self._lock = threading.Lock()
        with self._lock:
            self.db.executescript(SCHEMA)
            self.db.commit()

    def load(self, chain, token):
        """{"from_block", "head", "complete", "verified", "transfers"} or None."""
        with self._lock:
            row = self.db.execute("SELECT from_block, head, complete, verified, blob FROM token_history "
                                  "WHERE chain = ? AND token = ?", (chain, token)).fetchone()
        if row is None:
            return None
        try:
            transfers = decode(row["blob"])
        except (zlib.error, ValueError):
            self.drop(chain, token)
            return None
        return {"from_block": row["from_block"], "head": row["head"], "complete": bool(row["complete"]),
                "verified": bool(row["verified"]), "transfers": transfers}

    def save(self, chain, token, from_block, head, transfers, complete, verified):
        blob = encode(transfers)
        with self._lock:
            self.db.execute("INSERT OR REPLACE INTO token_history VALUES (?,?,?,?,?,?,?,?,?,?)",
                            (chain, token, from_block, head, int(complete), int(verified), len(transfers),
                             time.time(), len(blob), sqlite3.Binary(blob)))
            total = self.db.execute("SELECT COALESCE(SUM(bytes), 0) FROM token_history").fetchone()[0]
            cap = max_bytes()
            while total > cap:   # evict the least recently updated tokens (never the one just written)
                old = self.db.execute("SELECT chain, token, bytes FROM token_history WHERE NOT (chain = ? AND "
                                      "token = ?) ORDER BY updated LIMIT 1", (chain, token)).fetchone()
                if old is None:
                    break
                self.db.execute("DELETE FROM token_history WHERE chain = ? AND token = ?", (old[0], old[1]))
                total -= old[2]
            self.db.commit()

    def drop(self, chain, token):
        with self._lock:
            self.db.execute("DELETE FROM token_history WHERE chain = ? AND token = ?", (chain, token))
            self.db.commit()

    def close(self):
        with self._lock:
            self.db.close()


_INDEXES, _ilock = {}, threading.Lock()


def get():
    """Process-wide index at the current default path; None if disabled (HISTORY_INDEX=off) or it cannot be opened:
    scans then read the history in full every time."""
    if (os.environ.get("HISTORY_INDEX") or "").strip().lower() in ("off", "0", "false"):
        return None
    path = default_path()
    with _ilock:
        if path not in _INDEXES:
            try:
                _INDEXES[path] = HistoryIndex(path)
            except (OSError, sqlite3.Error) as e:
                print(f"history index: unavailable at {path}: {type(e).__name__}", flush=True)
                _INDEXES[path] = None
        return _INDEXES[path]
