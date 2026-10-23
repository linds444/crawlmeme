"""Scan budget and reader cancellation (audit findings B1, B2: docs/phase-1-audit.md).

B1: wallet readers kept issuing RPC requests after the verdict (and kept the process alive).
B2: the limiter reserved future slots for waiting readers, so stages after the crawl queued behind them and the
    25 s budget was overrun (39 s observed on mainnet).
The tests drive the real Robinhood adapter limiter (chains.priority.Throttle) with batch-weighted requests, as
wallet_distinct_tokens does, and a short budget. No network: fakes.patched forbids RPC.
"""
import threading, time, unittest
from unittest import mock

import fakes
from fakes import ch, engine
from crawler.chains import priority


class TestThrottleScope(unittest.TestCase):

    def test_no_scope_unchanged(self):
        th = priority.Throttle(20)
        t0 = time.time()
        for _ in range(4):
            th.wait()
        self.assertGreaterEqual(time.time() - t0, 0.14)

    def test_ended_scope_raises_without_taking_slot(self):
        th = priority.Throttle(10)
        sc = priority.Scope(time.time() - 1)
        before = th.next
        with self.assertRaises(priority.Cancelled):
            th.wait(1, sc)
        self.assertEqual(th.next, before)

    def test_slot_after_deadline_is_not_taken(self):
        th = priority.Throttle(1)            # 1 s per slot
        th.wait()                            # next slot opens in ~1 s
        sc = priority.Scope(time.time() + 0.3)
        t0 = time.time()
        with self.assertRaises(priority.Cancelled):
            th.wait(1, sc)
        self.assertLess(time.time() - t0, 0.1)   # refused at once, no sleeping until the deadline
        th.wait()                            # the slot is still free for others

    def test_cancel_wakes_waiter(self):
        th = priority.Throttle(0.5)          # 2 s per slot
        th.wait()
        sc, out = priority.Scope(), []

        def waiter():
            try:
                th.wait(1, sc)
                out.append("granted")
            except priority.Cancelled:
                out.append("cancelled")

        t = threading.Thread(target=waiter)
        t.start()
        time.sleep(0.1)
        t0 = time.time()
        sc.cancel()
        t.join(1)
        self.assertEqual(out, ["cancelled"])
        self.assertLess(time.time() - t0, 0.3)

    def test_cancelled_waiter_holds_no_future_slot(self):
        """B2: a cancelled reader must not leave a reservation that delays the next request."""
        th = priority.Throttle(10)           # 100 ms per unit
        sc = priority.Scope()
        th.wait()
        ts = [threading.Thread(target=lambda: self._swallow(th, sc)) for _ in range(4)]
        for t in ts:
            t.start()
        time.sleep(0.02)
        sc.cancel()
        for t in ts:
            t.join(1)
        t0 = time.time()
        th.wait()
        self.assertLess(time.time() - t0, 0.25)   # old reserving limiter: 4 × 10 × 100 ms = 4 s

    @staticmethod
    def _swallow(th, sc):
        try:
            th.wait(10, sc)
        except priority.Cancelled:
            pass

    def test_cancelled_is_not_swallowed_by_except_exception(self):
        with priority.scope(priority.Scope(time.time() - 1)):
            with self.assertRaises(priority.Cancelled):
                try:
                    priority.rate_limit(priority.Throttle(100), priority.Throttle(0))
                except Exception:      # the adapters' retry loops catch Exception
                    self.fail("Cancelled caught as Exception")

    def test_rate_limit_without_scope_unchanged(self):
        self.assertIsNone(priority.current_scope())
        priority.rate_limit(priority.Throttle(1000), priority.Throttle(0))


class TestScanBudget(unittest.TestCase):
    BUDGET = 2.0

    def setUp(self):
        self.grants = []                       # time of every RPC slot granted to a wallet reader
        self.lock = threading.Lock()

    def history(self, wallet_, before_block, token, window=None, cap=4):
        """A wallet history that needs far more RPC than the budget allows: 50 batches of 10."""
        for _ in range(50):
            ch._rate_limit(10)                 # real adapter limiter path (priority.rate_limit)
            with self.lock:
                self.grants.append(time.time())
            time.sleep(0.01)                   # the "HTTP request"
        return cap

    def scan(self):
        meta_wait = []

        def token_meta(token):
            t0 = time.time()
            ch._rate_limit()                   # a post-crawl request from the scan thread itself
            meta_wait.append(time.time() - t0)
            return {"name": "Synthetic", "symbol": "SYN"}

        with fakes.patched(history_fn=self.history), \
                mock.patch.object(engine, "BUDGET", self.BUDGET), \
                mock.patch.object(ch, "_LIMIT", priority.Throttle(20)), \
                mock.patch.object(ch, "token_meta", side_effect=token_meta):
            t0 = time.time()
            res = engine.scan(fakes.TOKEN)
            elapsed = time.time() - t0
            returned = time.time()
            time.sleep(0.6)                    # leftover readers would keep going here
        return res, elapsed, returned, meta_wait

    def test_budget_is_hard_and_readers_stop(self):
        res, elapsed, returned, meta_wait = self.scan()
        # B2: the verdict arrives within the budget (+ scheduling slack), not after the queued reservations
        self.assertLess(elapsed, self.BUDGET + 0.5)
        # a post-crawl request waits at most for the one batch granted before the deadline (10 units at 20 RPS),
        # not for every reader's queued reservation
        self.assertLess(meta_wait[0], 10 / 20 + 0.15)
        # B1: no RPC slot granted to a reader after the verdict
        late = [t for t in self.grants if t > returned]
        self.assertEqual(late, [])
        # the heavy wallets did not finish: they are reported unread, never as clean
        self.assertTrue(res["unread"])
        for h in res["holders"]:
            if h["wallet"] in res["unread"]:
                self.assertTrue(h["signals"]["unread"])
                self.assertFalse(h["signals"]["virgin"])

    def test_reader_threads_exit(self):
        """B1: the pool threads end shortly after the scan (the CLI scan no longer hangs at exit)."""
        before = {t.ident for t in threading.enumerate()}
        self.scan()
        deadline = time.time() + 2
        while time.time() < deadline:
            left = [t for t in threading.enumerate()
                    if t.ident not in before and t.name.startswith("ThreadPoolExecutor")]
            if not left:
                break
            time.sleep(0.05)
        self.assertEqual(left, [])


if __name__ == "__main__":
    unittest.main()
