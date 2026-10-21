"""Multi-provider JSON-RPC client for one EVM chain (standard library only).

Why: free RPC tiers differ wildly in what they serve, and a single endpoint is a single point of failure. Measured
2026-10-21: Alchemy Free allows eth_getLogs over 10 blocks only; public Base/BNB nodes refuse address-less log
queries and keep only a few thousand recent blocks; the public Robinhood node serves full history in 10M-block
windows. So each request goes to a provider that can serve it, with failover:

  Provider capabilities (declared in config, refined at run time from the provider's own error messages):
    max_span       — widest eth_getLogs block range (None: no known limit)
    addressless    — accepts eth_getLogs without an address filter
    archive_depth  — how many blocks back logs, receipts and blocks are served (None: full history)
    state_depth    — how many blocks back historical state (eth_call / eth_getCode at a block) is served
                     (None: full archive; 0: latest only). Separate from archive_depth: the public Robinhood
                     node serves every log since genesis but no historical state
    asset_transfers— serves alchemy_getAssetTransfers
  Routing: providers in configured order, skipping those that cannot serve the request or are cooling down after
  failures. A provider-side failure (transport, HTTP 5xx, rate limit after retries, capability refusal) moves on to
  the next provider; a deterministic error (execution reverted, invalid params) is raised at once — another node
  would answer the same.

Every request goes through chains.priority.rate_limit with the provider's own throttle, so live scans keep priority
over background work and a cancelled scan scope stops issuing requests (priority.Cancelled).
Secrets: provider URLs may embed API keys (Alchemy, QuickNode). URLs are never logged or put into error messages;
providers are identified by name only.
"""
import json, random, re, threading, time, urllib.error, urllib.request

from crawler.chains import priority

LOG_CAP = 10_000          # many nodes silently truncate eth_getLogs at this many logs: a full page is split and re-read
COOLDOWN_S = 30.0         # a provider that failed (transport / 5xx / rate limit) is skipped for this long
DENY_S = 300.0            # a method refused with HTTP 401/403 is not asked of that provider for this long: some
                          # endpoints (publicnode) answer 403 both for methods they never serve and when throttling
TRIES = 3                 # attempts per provider for rate limits and transport errors
TIMEOUT_S = 15.0          # per HTTP attempt (connect included): a stalled endpoint fails over instead of holding a scan
HEAD_TTL_S = 1.0


class RpcError(RuntimeError):
    """Deterministic JSON-RPC error (another provider would answer the same): code, message."""

    def __init__(self, code, message, provider=None):
        super().__init__(f"{message} (code {code})")
        self.code, self.message, self.provider = code, message, provider


class NoProvider(RuntimeError):
    """No configured provider can serve this request (capabilities) or all of them failed."""


# --- error classification (provider error texts are not standardised; patterns seen on real nodes) ---
# not "capacity" / "exceeded" alone: they also appear in span refusals, which must split the window, not retry it
_RATE = re.compile(r"rate.?limit|too many requests|per second|request limit reached", re.I)
_SPAN = re.compile(r"block range|range.*(too|exceed|allowed|limit|over)|spans? .*blocks|only \d+ are allowed|"
                   r"ranges over \d+|max results|response size|too large|more than \d+ (logs|results)|"
                   r"query returned more than|log response size exceeded|up to a \d+ block range", re.I)
_ARCHIVE = re.compile(r"archive|missing trie node|header not found|state.*not available|pruned|historical state", re.I)
_ADDRESSLESS = re.compile(r"specify an address|address.*required|requires an address", re.I)
_SPAN_NUM = re.compile(r"only (\d+) are allowed|ranges over (\d+)|up to a (\d+) block range|(\d+) block range", re.I)
_RETRY_RANGE = re.compile(r"retry with the range (\d+)-(\d+)", re.I)


def classify(code, message):
    """Error kind: rate | span | archive | addressless | unsupported | fatal."""
    m = message or ""
    if code == -32601 or re.search(r"method not found|method .{0,60}(not supported|not available|does not exist)|unsupported method", m, re.I):
        return "unsupported"
    if _RATE.search(m) or (code == 429 and not _SPAN.search(m)):
        return "rate"
    if _ADDRESSLESS.search(m):
        return "addressless"
    if _SPAN.search(m):
        return "span"
    if _ARCHIVE.search(m):
        return "archive"
    return "fatal"


def span_hint(message, lo=None, hi=None):
    """Largest block span the provider says it accepts, or None."""
    m = _RETRY_RANGE.search(message or "")
    if m:
        return max(1, int(m.group(2)) - int(m.group(1)) + 1)
    m = _SPAN_NUM.search(message or "")
    if m:
        return int(next(g for g in m.groups() if g))
    return None


def _urllib_transport(url, body, timeout):
    req = urllib.request.Request(url, data=body, headers={"content-type": "application/json",
                                                          "user-agent": "crawler/1 (read-only)"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        with e:
            return e.code, e.read()


class Provider:
    """One RPC endpoint and what it can serve. url may contain a secret: never log it."""

    def __init__(self, name, url, rps=5.0, max_span=None, addressless=True, archive_depth=None,
                 asset_transfers=False, batch_max=50, max_span_addressless=None, throttle=None, state_depth=None,
                 deny=None):
        self.name, self.url = name, url
        self.rps = rps
        self.max_span, self.addressless, self.archive_depth = max_span, addressless, archive_depth
        self.max_span_addressless = max_span_addressless   # address-less queries often have a tighter limit
        self.state_depth = state_depth
        # methods this endpoint refuses → until when: declared ones (measured, e.g. receipts on publicnode,
        # eth_getLogs on Binance's dataseed nodes) forever, ones learned from HTTP 401/403 for DENY_S
        self._denied = {m: float("inf") for m in (deny or ())}
        self.asset_transfers, self.batch_max = asset_transfers, batch_max
        # throttle may be shared with another client of the same endpoint (Robinhood: the Pons adapter's limiter),
        # so both together stay under the provider's rate limit
        self.throttle = throttle or priority.Throttle(rps)
        self.lock = threading.Lock()
        self.cool_until = 0.0
        self.stats = {"requests": 0, "errors": 0, "rate_limited": 0, "failovers": 0, "latency_ms": 0.0}

    def __repr__(self):   # never the URL
        return f"Provider({self.name})"

    @property
    def denied(self):
        now = time.time()
        return {m for m, until in self._denied.items() if until > now}

    @property
    def denied_forever(self):
        return {m for m, until in self._denied.items() if until == float("inf")}

    def deny(self, method, seconds=DENY_S):
        with self.lock:
            if self._denied.get(method, 0) != float("inf"):
                self._denied[method] = time.time() + seconds

    def serves(self, need, head, lenient=False):
        if need.get("method") in (self.denied_forever if lenient else self.denied):
            return False
        if need.get("asset_transfers") and not self.asset_transfers:
            return False
        if need.get("addressless") and not self.addressless:
            return False
        lo = need.get("from_block")
        if lo is not None and self.archive_depth is not None and head is not None and lo < head - self.archive_depth:
            return False
        sb = need.get("state_block")
        if sb is not None and self.state_depth is not None and head is not None and sb < head - self.state_depth:
            return False
        return True

    def span(self, addressless=False):
        """Known getLogs span limit for this kind of query (None: none known)."""
        if addressless and self.max_span_addressless:
            return min(self.max_span_addressless, self.max_span or self.max_span_addressless)
        return self.max_span

    def learn(self, kind, message, lo=None, hi=None, head=None, addressless=False, method=None, state_block=None):
        """Refine capabilities from a refusal (kept for the process lifetime)."""
        with self.lock:
            if kind == "addressless":
                self.addressless = False
            elif kind == "unsupported" and method == "alchemy_getAssetTransfers":
                self.asset_transfers = False
            elif kind == "span":
                attr = "max_span_addressless" if addressless else "max_span"
                hint = span_hint(message, lo, hi)
                cur = getattr(self, attr) or (hi - lo + 1 if lo is not None and hi is not None else None)
                new = hint if hint else (max(1, cur // 2) if cur else None)
                if new and (getattr(self, attr) is None or new < getattr(self, attr)):
                    setattr(self, attr, new)
            elif kind == "archive" and state_block is not None and head is not None:
                depth = max(0, head - state_block - 1)
                if self.state_depth is None or depth < self.state_depth:
                    self.state_depth = depth
            elif kind == "archive" and lo is not None and head is not None:
                depth = max(0, head - lo - 1)
                if self.archive_depth is None or depth < self.archive_depth:
                    self.archive_depth = depth

    def cool(self):
        with self.lock:
            self.cool_until = time.time() + COOLDOWN_S

    def cooling(self, now):
        return now < self.cool_until

    def snapshot(self):
        with self.lock:
            n = self.stats["requests"]
            return {"name": self.name, "max_span": self.max_span, "addressless": self.addressless,
                    "archive_depth": self.archive_depth, "state_depth": self.state_depth,
                    "denied": sorted(self.denied),
                    "asset_transfers": self.asset_transfers,
                    "cooling": self.cooling(time.time()),
                    **{k: v for k, v in self.stats.items() if k != "latency_ms"},
                    "avg_latency_ms": round(self.stats["latency_ms"] / n, 1) if n else None}


class EvmClient:
    """JSON-RPC over a provider list for one chain. REQUESTS / RATE_LIMITED: counters the engine reads like
    an adapter's (requests including retries)."""

    def __init__(self, chain, providers, transport=None, bg_rps=None, crit_rps=None):
        if not providers:
            raise ValueError(f"{chain}: no RPC providers configured")
        self.chain = chain
        self.providers = list(providers)
        self.transport = transport or _urllib_transport
        self.REQUESTS, self.RATE_LIMITED = [0], [0]
        self._bg = priority.Throttle(priority.background_rps() if bg_rps is None else bg_rps)
        self._crit = priority.Throttle(priority.critical_rps() if crit_rps is None else crit_rps)
        self._head = (0.0, None)
        self._gen = [0]        # +1 whenever a provider learns a limit or cools down: callers may re-plan

    # ------------------------------------------------------------------ transport
    def _send(self, p, payload, weight, timeout=None, tries=None):
        """One HTTP exchange with provider p (rate-limited, retried on rate limits / transport errors).
        Returns the decoded JSON body; raises _ProviderDown when p should be skipped."""
        body = json.dumps(payload).encode()
        last = "no attempt"
        tries = tries or TRIES
        for a in range(tries):
            priority.rate_limit(p.throttle, self._bg, weight, crit=self._crit)
            self.REQUESTS[0] += 1
            t0 = time.time()
            try:
                status, raw = self.transport(p.url, body, timeout or TIMEOUT_S)
            except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as e:
                last = type(e).__name__
                self._count(p, t0, error=True)
                if a < tries - 1:
                    self._backoff(a)
                continue
            self._count(p, t0, error=status >= 400)
            if status in (401, 403):                       # this endpoint refuses the method: no retry, no cooldown
                raise _ProviderDown(f"{p.name}: HTTP {status}", denied=True)
            if status == 429:
                self._rate_limited(p)
                last = "HTTP 429"
                self._backoff(a)
                continue
            try:
                data = json.loads(raw) if raw else None
            except ValueError:
                data = None
            if status >= 500 or data is None:
                last = f"HTTP {status}"
                self._backoff(a)
                continue
            if self._is_rate_limited(data):
                self._rate_limited(p)
                last = "rate limited"
                self._backoff(a)
                continue
            return data
        raise _ProviderDown(f"{p.name}: {last}")

    def _count(self, p, t0, error=False):
        with p.lock:
            p.stats["requests"] += 1
            p.stats["latency_ms"] += (time.time() - t0) * 1000
            if error:
                p.stats["errors"] += 1

    def _rate_limited(self, p):
        self.RATE_LIMITED[0] += 1
        with p.lock:
            p.stats["rate_limited"] += 1

    @staticmethod
    def _is_rate_limited(data):
        errs = [data.get("error")] if isinstance(data, dict) else [x.get("error") for x in data if isinstance(x, dict)]
        return any(isinstance(e, dict) and classify(e.get("code"), str(e.get("message", ""))) == "rate" for e in errs)

    @staticmethod
    def _backoff(a):
        priority.check_scope()
        time.sleep(min(2.0, 0.25 * (2 ** a)) + random.random() * 0.2)

    def _candidates(self, need):
        head = self._head[1] if need.get("from_block") is not None or need.get("state_block") is not None else None
        now = time.time()
        ok = [p for p in self.providers if p.serves(need, head)]
        if not ok:   # only temporarily denied providers left (a 403 may have been throttling): try them again
            ok = [p for p in self.providers if p.serves(need, head, lenient=True)]
        if not ok:
            raise NoProvider(f"{self.chain}: no provider serves {sorted(k for k, v in need.items() if v)}")
        fresh = [p for p in ok if not p.cooling(now)]
        return fresh or ok    # all cooling: try anyway rather than fail

    # ------------------------------------------------------------------ public API
    def call(self, method, params, need=None, prefer=None, timeout=None, tries=None, max_providers=None):
        """Single JSON-RPC call → result. need: capability requirements (see Provider.serves);
        prefer: sort key over providers (stable: configured order breaks ties).
        timeout / tries / max_providers: tighter bounds for latency-critical probes (chain detection), where an
        unanswered call is reported as unknown by the caller instead of being retried for minutes."""
        need = dict(need or {}, method=method)
        errors = []
        cands = self._candidates(need)
        cands = sorted(cands, key=prefer) if prefer else cands
        for p in cands[:max_providers] if max_providers else cands:
            try:
                data = self._send(p, {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}, 1,
                                  timeout=timeout, tries=tries)
            except _ProviderDown as e:
                self._down(p, method if e.denied else None); errors.append(str(e))
                continue
            err = data.get("error") if isinstance(data, dict) else None
            if not err:
                return data.get("result")
            code, msg = err.get("code"), str(err.get("message", ""))
            kind = classify(code, msg)
            if kind == "fatal":
                raise RpcError(code, msg, p.name)
            p.learn(kind, msg, need.get("from_block"), need.get("to_block"), self._head[1],
                    addressless=bool(need.get("addressless")), method=method, state_block=need.get("state_block"))
            self._gen[0] += 1
            self._failover(p)
            errors.append(f"{p.name}: {kind}")
        raise NoProvider(f"{self.chain} {method}: " + "; ".join(errors))

    def batch(self, calls, need=None):
        """Many calls → results in order (None where that item errored). Split into the provider's batch size."""
        if not calls:
            return []
        methods = {m for m, _ in calls}
        need = dict(need or {}, method=next(iter(methods)) if len(methods) == 1 else None)
        errors = []
        for p in self._candidates(need):
            if methods & p.denied:
                continue
            out, ok = [], True
            for i in range(0, len(calls), p.batch_max):
                part = calls[i:i + p.batch_max]
                payload = [{"jsonrpc": "2.0", "id": k, "method": m, "params": a} for k, (m, a) in enumerate(part)]
                try:
                    data = self._send(p, payload, len(part))
                except _ProviderDown as e:
                    self._down(p, need["method"] if e.denied else None); errors.append(str(e)); ok = False
                    break
                if isinstance(data, dict):     # whole batch refused (batching unsupported or capability)
                    err = data.get("error") or {}
                    kind = classify(err.get("code"), str(err.get("message", "")))
                    if kind == "fatal" and len(part) == 1:
                        raise RpcError(err.get("code"), str(err.get("message", "")), p.name)
                    p.learn(kind, str(err.get("message", "")), need.get("from_block"), need.get("to_block"),
                            self._head[1])
                    self._gen[0] += 1
                    self._failover(p); errors.append(f"{p.name}: batch {kind}"); ok = False
                    break
                by_id = {r.get("id"): r for r in data if isinstance(r, dict)}
                for k in range(len(part)):
                    r = by_id.get(k) or {}
                    out.append(r.get("result") if "error" not in r else None)
            if ok:
                return out
        raise NoProvider(f"{self.chain} batch: " + "; ".join(errors))

    def _failover(self, p):
        with p.lock:
            p.stats["failovers"] += 1

    def _down(self, p, denied_method=None):
        if denied_method:
            p.deny(denied_method)
        else:
            p.cool()
        self._gen[0] += 1
        self._failover(p)

    def head(self):
        """Latest block number (cached HEAD_TTL_S)."""
        ts, h = self._head
        if h is not None and time.time() - ts < HEAD_TTL_S:
            return h
        h = int(self.call("eth_blockNumber", []), 16)
        self._head = (time.time(), h)
        return h

    def eth_call(self, to, data, block="latest"):
        need = {} if block == "latest" else {"state_block": int(block, 16) if isinstance(block, str) else block}
        return self.call("eth_call", [{"to": to, "data": data}, block if isinstance(block, str) else hex(block)], need)

    def iter_logs(self, lo, hi, address=None, topics=None):
        """eth_getLogs pages over [lo, hi] in block order. Windows are sized for the provider that will be asked
        first (its known span limit); a refusal teaches that provider its limit and the window is re-planned.
        Full pages (LOG_CAP, possibly truncated) are split and re-read. Memory: one page at a time."""
        self.head()
        stack = [(lo, hi)]
        replans = 0
        while stack:
            a, b = stack.pop()
            need = {"from_block": a, "to_block": b, "addressless": address is None, "method": "eth_getLogs"}
            # widest provider first: one 10-block (Alchemy Free) provider must not dictate the windows
            widest = lambda p: -(p.span(address is None) or float("inf"))
            span = sorted(self._candidates(need), key=widest)[0].span(address is None)
            if span and b - a + 1 > span:
                parts, x = [], a
                while x <= b:
                    parts.append((x, min(b, x + span - 1))); x += span
                stack.extend(reversed(parts))
                continue
            flt = {"fromBlock": hex(a), "toBlock": hex(b)}
            if address:
                flt["address"] = address
            if topics:
                flt["topics"] = topics
            gen = self._gen[0]
            try:
                logs = self.call("eth_getLogs", [flt], need, prefer=widest)
            except NoProvider:
                # a provider learned a smaller span or cooled down meanwhile: plan this window again (bounded)
                if self._gen[0] != gen and replans < 8 * len(self.providers):
                    replans += 1
                    stack.append((a, b))
                    continue
                raise
            if len(logs) >= LOG_CAP and b > a:
                mid = (a + b) // 2
                stack.append((mid + 1, b)); stack.append((a, mid))
                continue
            yield logs

    def get_logs(self, lo, hi, address=None, topics=None):
        return [lg for page in self.iter_logs(lo, hi, address, topics) for lg in page]

    def asset_transfers(self, max_count=None, **flt):
        """alchemy_getAssetTransfers pages (ascending block order): all transfers matching flt
        (fromBlock/toBlock as ints or hex, contractAddresses, fromAddress, toAddress, category, order).
        max_count — stop after this many rows. Returns (rows, truncated)."""
        p = {"category": ["erc20"], "withMetadata": False, "excludeZeroValue": False} | {
            k: (hex(v) if k in ("fromBlock", "toBlock") and isinstance(v, int) else v) for k, v in flt.items()}
        rows, key = [], None
        while True:
            q = dict(p, maxCount=hex(min(1000, max_count - len(rows)) if max_count else 1000))
            if key:
                q["pageKey"] = key
            res = self.call("alchemy_getAssetTransfers", [q], {"asset_transfers": True})
            rows += res.get("transfers") or []
            key = res.get("pageKey")
            if not key or (max_count and len(rows) >= max_count):
                return rows, bool(key)

    def health(self):
        return {"chain": self.chain, "providers": [p.snapshot() for p in self.providers]}


class _ProviderDown(Exception):
    """Provider unusable for this request (transport / 5xx / persistent rate limit): fail over.
    denied — the endpoint refuses this method (HTTP 401/403): remember it, do not cool the provider down."""

    def __init__(self, msg, denied=False):
        super().__init__(msg)
        self.denied = denied
