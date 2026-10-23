"""Priority of live scans over background work in the adapters' RPS limiters.

Background work is a thread whose name starts with BG (the alerts recheck thread and the wallet pool the engine starts
from the background) or code inside background() (/api/early without a live scan's history).
Before each network request such a thread waits (wait_turn) while users' live scans are running
(live() is a counter in server), and goes through its own separate BACKGROUND_RPS limit (the adapter's Throttle).
Live threads never wait: for them it is a single thread check.
Critical work (critical()) does not yield to live scans and does not
count as background: it goes through its own CRITICAL_RPS limit (a guaranteed share) and the adapter's shared limit.
Strict background (background(strict=True): building the Bankr index) waits for live scans to end without the YIELD_MAX cap:
while at least one live scan is running, it starts no requests at all.
RATE_LIMITED in adapters — how many times the node answered with a rate limit (used to pause rechecks).

Scan scope (Scope, bound to a thread with scope()): the wallet readers a scan starts work for that scan only. Once
the scope is cancelled or its deadline passes, rate_limit raises Cancelled instead of letting the thread start another
request, and a limiter slot that would only open after the deadline is never taken. So a scan's leftover readers stop
at the scan's deadline (no RPC after the verdict) and post-crawl stages do not queue behind them.
"""
import os, threading, time
from contextlib import contextmanager

BG = "recheck"
YIELD_MAX = 60.0          # seconds: a background thread never waits longer than this in one wait (then it checks again)
BACKGROUND_RPS = 2.0      # default for env BACKGROUND_RPS: requests/sec for all of an adapter's background work; 0 — no own limit
_cond = threading.Condition()
_live = [0]
_live_time = [0.0, None]  # seconds during which at least one live scan was running (total), and since when the current one runs
_tls = threading.local()
CRITICAL_RPS = 2.0        # default for env CRITICAL_RPS: requests/sec for an adapter's critical work; 0 — no own limit
YIELDS = [0]              # how many times background requests yielded to live scans


def is_critical():
    return getattr(_tls, "crit", False)


def is_strict():
    return getattr(_tls, "strict", False)


def is_background():
    if is_critical():
        return False
    return getattr(_tls, "bg", False) or threading.current_thread().name.startswith(BG)


@contextmanager
def critical():
    """Critical work in the current thread: does not wait for live scans, has its own CRITICAL_RPS limit."""
    prev = getattr(_tls, "crit", False)
    _tls.crit = True
    try:
        yield
    finally:
        _tls.crit = prev


@contextmanager
def background(strict=False):
    """Background work in the current thread (/api/early without scan history).
    BACKGROUND_RPS=0 is the off switch: such work is not background, as before (alerts rechecks are always background).
    strict — always background (even with BACKGROUND_RPS=0) and waits for live scans without the YIELD_MAX cap (Bankr index)."""
    prev, prev_strict = getattr(_tls, "bg", False), getattr(_tls, "strict", False)
    _tls.bg = prev or strict or background_rps() > 0
    _tls.strict = prev_strict or strict
    try:
        yield
    finally:
        _tls.bg, _tls.strict = prev, prev_strict


@contextmanager
def live():
    """A user's live scan (or /api/early from its history) is running: background requests wait."""
    with _cond:
        _live[0] += 1
        if _live[0] == 1:
            _live_time[1] = time.time()
    try:
        yield
    finally:
        with _cond:
            _live[0] -= 1
            if _live[0] == 0 and _live_time[1] is not None:
                _live_time[0] += time.time() - _live_time[1]
                _live_time[1] = None
            _cond.notify_all()


def live_seconds():
    """How many seconds in total at least one live scan was running (for the background ETA: its pauses)."""
    with _cond:
        return _live_time[0] + (time.time() - _live_time[1] if _live_time[1] is not None else 0.0)


def live_count():
    with _cond:
        return _live[0]


def wait_turn():
    """Called by an adapter before a network request. A live thread goes at once; a background one waits for live scans to end."""
    if not is_background():
        return
    with _cond:
        if _live[0] == 0:
            return
        YIELDS[0] += 1
        if is_strict():
            while _live[0]:
                _cond.wait(YIELD_MAX)
            return
        _cond.wait_for(lambda: _live[0] == 0, timeout=YIELD_MAX)


def background_rps():
    """BACKGROUND_RPS from env (empty or invalid — default; 0 or less — background has no own limit)."""
    try:
        v = float(os.environ.get("BACKGROUND_RPS", "").strip() or BACKGROUND_RPS)
    except ValueError:
        return BACKGROUND_RPS
    return v if v > 0 else 0.0


def critical_rps():
    """CRITICAL_RPS from env (empty or invalid — default; 0 or less — no own limit, only the shared one)."""
    try:
        v = float(os.environ.get("CRITICAL_RPS", "").strip() or CRITICAL_RPS)
    except ValueError:
        return CRITICAL_RPS
    return v if v > 0 else 0.0


def background_scale(rps):
    """How many times slower background is than a live scan under the adapter's rps limit: background budgets are stretched by the same factor,
    so a background scan manages to read as much as a live one. No own limit, or it is not below rps — 1."""
    bg = background_rps()
    return rps / bg if 0 < bg < rps else 1.0


POLL_S = 0.05             # seconds: longest a limiter waiter sleeps before rechecking its slot and its scope


class Cancelled(BaseException):
    """The scan this thread reads for has ended (scope cancelled or past its deadline): no new requests.
    BaseException, like asyncio.CancelledError: the adapters' broad `except Exception` retry loops must not swallow it."""


class Scope:
    """Lifetime of one scan's background readers: cancel() or deadline (unix time, None — none) ends it."""

    def __init__(self, deadline=None):
        self.deadline = deadline
        self._ev = threading.Event()

    def cancel(self):
        self._ev.set()

    def done(self, now=None):
        return self._ev.is_set() or (self.deadline is not None and (now or time.time()) >= self.deadline)


def current_scope():
    return getattr(_tls, "scope", None)


@contextmanager
def scope(sc):
    """Bind scan scope sc to the current thread (engine: around each wallet reader in its pool)."""
    prev = getattr(_tls, "scope", None)
    _tls.scope = sc
    try:
        yield sc
    finally:
        _tls.scope = prev


def run_scoped(sc, fn, *args):
    """fn(*args) with scan scope sc bound: for ThreadPoolExecutor.submit (pool threads do not inherit thread-locals)."""
    with scope(sc):
        return fn(*args)


def check_scope():
    """Cancelled if the current thread's scan scope has ended; no scope — no-op."""
    sc = current_scope()
    if sc is not None and sc.done():
        raise Cancelled


class Throttle:
    """Rate limit over time: at most one request start per gap × n seconds.
    The slot is granted at the moment the request starts, never reserved ahead: a waiter whose scan ended
    (Cancelled) leaves without holding any future slot, so it cannot delay later requests.
    Waiting is on a condition over self.lock, which is released while sleeping (a waiter does not block others).
    n — request weight (providers count each batch item). rps <= 0 — no limit."""

    def __init__(self, rps):
        self.gap = 1.0 / rps if rps and rps > 0 else 0.0
        self.lock = threading.Lock()
        self._cv = threading.Condition(self.lock)
        self.next = 0.0

    def wait(self, n=1, sc=None):
        """Block until a slot is free and take it. sc — scan scope: ended, or no slot before its deadline — Cancelled."""
        if not self.gap:
            if sc is not None and sc.done():
                raise Cancelled
            return
        with self._cv:
            while True:
                now = time.time()
                if sc is not None and (sc.done(now) or (sc.deadline is not None and self.next >= sc.deadline)):
                    raise Cancelled
                if now >= self.next:
                    self.next = now + self.gap * n
                    return
                self._cv.wait(min(self.next - now, POLL_S))


def rate_limit(main, bg, n=1, crit=None):
    """Shared path of the adapters' limiters: critical work goes through its own crit limit and the shared main one, without waiting for live scans;
    background yields to live scans and goes through its own bg limit, then the shared main one.
    A thread bound to a scan scope (scope()) gets Cancelled once that scan has ended, before or while waiting."""
    sc = current_scope()
    if is_critical():
        if crit is not None:
            crit.wait(n, sc)
        main.wait(n, sc)
        return
    wait_turn()
    if is_background() and bg.gap:
        bg.wait(n, sc)
        wait_turn()      # a live scan may have started while we waited for our slot
    main.wait(n, sc)
