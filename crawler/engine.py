"""Scan conductor: walks the steps, calls the adapter and detectors, emit(event) at every step.
Hard budget of 25 seconds: wallets whose history was not read in time are marked unread,
the scan does not wait for them: we take only those who made it before the deadline, stragglers are not awaited.
Chain by address: 0x + 40 hex -> Robinhood (chains/robinhood.py), base58 -> Solana
(chains/solana.py, only with SOLANA_ENABLED=true). Both adapters return the same facts.
Running from code: engine.scan("0x...", emit=print) -> result (dict)."""
import math, os, re, threading, time
from concurrent.futures import ThreadPoolExecutor, as_completed, TimeoutError as _CFTimeout

from crawler.chains import bankr
from crawler.chains import registry
from crawler.chains.evm import networks as evm_networks
from crawler.chains.evm import token as evm_token
from crawler.chains import flap
from crawler.chains import priority
from crawler.chains import robinhood as ch
from crawler.chains import solana as sol
from crawler import detect as d
from crawler import coverage
from crawler import forensics
from crawler import graph
from crawler import cabals
from crawler import memory_db
from crawler import scoring
from crawler import impact
from crawler.chains.evm import funding as evm_funding
from crawler import market
BUDGET = 25.0          # seconds for the whole scan (README)
DETECT_RESERVE = 1.0   # seconds kept for detect and events after reading wallets
MARKET_WAIT = 3.0      # wait for the GeckoTerminal header before the first RPC at most this many seconds from scan start
MARKET_LATE = 20.0     # not received in time -- the GT request continues in the background (up to this many seconds for all attempts),
                       # a late response is checked before the verdict
RESERVE_MIN_SEEN = 0.25  # reserve in $ < 25% of the token side of GT liquidity (liquidity / 2) -- pools not found
ESTABLISHED_ENV = {"min_age_days": "ESTABLISHED_MIN_AGE_DAYS", "min_liquidity_usd": "ESTABLISHED_MIN_LIQUIDITY_USD",
                   "min_mcap_usd": "ESTABLISHED_MIN_MCAP_USD"}
WORKERS = 12           # threads reading wallet history; the RPS limiter in the adapter is shared
# how many wallets to read at once (by descending share): an adapter with heavy requests under the shared limiter
# (Robinhood: a batch of 10 getLogs = 10 RPS slots) sets HISTORY_PARALLEL -- when the budget is short the largest
# go first; without the attribute -- all WORKERS at once, as before
SPIDERS = 6            # spiders; wallets are dealt round-robin
HIST_CAP = d.SHORT_HISTORY_MAX + 1  # count coins before entry up to 4: what matters is 0 / 1..3 / more
USE_FUNDING = False    # V1 funding path (robinhood eth_inflows/outgoing_count) stays off; Phase 6 funding links come from
                       # Phase 5 forensics instead (FUNDING_LINKS below),
                       # the "common funder" link and the one_shot_funded signal are disabled

ADDR_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")
CHAINS = {"robinhood": ch, "solana": sol}
NATIVE = {"robinhood": ("ETH", 10 ** 18), "solana": ("SOL", 10 ** 9), "bsc": ("BNB", 10 ** 18),
          "base": ("ETH", 10 ** 18)}   # unit of eth_in
NOT_LAUNCHPAD = {"robinhood": "not a Pons V2 token", "solana": "not a pump.fun token"}
PAD_LABEL = {"clanker": "Clanker", "fourmeme": "four.meme", "flap": "Flap", "dex": "Uniswap/PancakeSwap pool"}
NOT_LAUNCHPAD_FLAP = "not a Pons V2 or Flap token"   # Robinhood with FLAP_ENABLED
TOO_LARGE = "token history too large"                  # Pons: more than ch.SCAN_MAX_LOGS transfers
GT_NETWORK = {"robinhood": "robinhood", "solana": "solana", "bsc": "bsc", "base": "base"}


class ScanError(Exception):
    """User-facing scan error (bad address, not Pons V2 / pump.fun, Solana disabled)."""


class AmbiguousChain(ScanError):
    """The 0x address has contract code on several enabled EVM chains: the caller must pick one (chains)."""

    def __init__(self, token, chains):
        names = ", ".join(evm_networks.NETWORKS[c].name for c in chains)
        super().__init__(f"this address exists on several chains ({names}): choose one")
        self.token, self.chains = token, chains


PARTIAL_BUILDING = "Partial scan: building the full holder history, check again in {eta}"
PARTIAL_TOO_LARGE = "Partial scan: the holder history is too large to read in full, top holders are approximate"
PARTIAL_QUEUED = "Partial scan: full holder history queued: {pos} in line, check again in {eta}"
PARTIAL_QUEUE_FULL = "Partial scan: full history is queued, check again later"
PARTIAL_LATER = "Partial scan: the full holder history is not available right now, check again later"


def bankr_partial(token, hist):
    """Partial Bankr scan -> {"state", "eta_s", "message"[, "position"]} (state -- bankr.index_status: building |
    queued | queue_full | too_large | unavailable | none | ready). ready -- the index finished building during the scan: the next
    scan will be full."""
    st = bankr.index_status(token)
    state, eta = st["state"], st["eta_s"]
    if state in ("building", "ready"):
        msg = PARTIAL_BUILDING.format(eta="about a minute" if state == "ready" else _about(eta))
    elif state == "queued":
        msg = PARTIAL_QUEUED.format(pos=_ordinal(st["position"]), eta=_about(eta))
        return {"state": state, "eta_s": eta, "position": st["position"], "message": msg}
    elif state == "queue_full":
        msg = PARTIAL_QUEUE_FULL
    elif state == "too_large":
        msg = PARTIAL_TOO_LARGE
    else:
        msg = PARTIAL_LATER
    return {"state": state, "eta_s": eta, "message": msg}


def _about(eta_s):
    mins = math.ceil((eta_s or 60) / 60)
    return "about a minute" if mins <= 1 else f"about {mins} minutes"


def _ordinal(n):
    return f"{n}{'th' if 10 <= n % 100 <= 20 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"


def not_launchpad(chain):
    """Error text "not from a launchpad" based on the chain's enabled launchpads (FLAP_ENABLED, BANKR_ENABLED,
    CLANKER_ENABLED, FOURMEME_ENABLED)."""
    evm = [PAD_LABEL[n] for n in registry.launchpad_names(chain)]
    if chain not in ("robinhood", "solana"):
        if not evm:
            return f"no launchpad is enabled for {evm_networks.NETWORKS[chain].name}"
        return f"not a {evm[0]} token" if len(evm) == 1 else f"not a {', '.join(evm[:-1])} or {evm[-1]} token"
    if chain != "robinhood":
        return NOT_LAUNCHPAD[chain]
    pads = ["Pons V2"] + (["Flap"] if flap.enabled() else []) + (["Bankr"] if bankr.enabled() else []) + evm
    return f"not a {pads[0]} token" if len(pads) == 1 else \
        f"not a {', '.join(pads[:-1])} or {pads[-1]} token"


def solana_enabled():
    return os.environ.get("SOLANA_ENABLED", "false").strip().lower() in ("1", "true", "yes")


def chain_of(token, chain=None):
    """CA -> (chain, normalized address) or ScanError.
    0x + 40 hex (lowercase): the given chain if it is an enabled EVM chain; with only Robinhood enabled -> robinhood
    without any request (V1 behaviour); otherwise the enabled EVM chains where the address has code
    (registry.locate): one -> it, several -> AmbiguousChain (caller picks), none -> ScanError.
    base58 32 bytes -> solana (case preserved); Solana with SOLANA_ENABLED off -> "Solana support is coming soon"."""
    token = (token or "").strip() if isinstance(token, str) or token is None else ""
    chain = (chain or "").strip().lower() or None if isinstance(chain, str) or chain is None else None
    if ADDR_RE.match(token):
        token = token.lower()
        enabled = evm_networks.enabled_chains()
        if chain is not None:
            if chain not in enabled:
                raise ScanError(f"chain {chain} is not enabled")
            return chain, token
        if enabled == ["robinhood"]:
            return "robinhood", token
        found, unknown = registry.locate(token, enabled)
        if len(found) == 1 and not unknown:
            return found[0], token
        if len(found) > 1 or (found and unknown):
            raise AmbiguousChain(token, found + unknown)
        if unknown:
            raise ScanError("could not check every chain for this address (RPC unavailable): choose the chain")
        raise ScanError("no contract at this address on " + ", ".join(evm_networks.NETWORKS[c].name for c in enabled))
    if sol.is_address(token):
        if not solana_enabled():
            raise ScanError("Solana support is coming soon")
        return "solana", token
    raise ScanError("not a token address")


def established_limits():
    """Thresholds for "too established" from env (ESTABLISHED_*); invalid or empty value -> default detect.ESTABLISHED."""
    out = {}
    for k, name in ESTABLISHED_ENV.items():
        try:
            v = float(os.environ.get(name) or "nan")
            out[k] = v if v == v and v >= 0 else d.ESTABLISHED[k]
        except ValueError:
            out[k] = d.ESTABLISHED[k]
    return out


def reserve_seen(reserve, supply, gt):
    """The reserve must agree with GT liquidity: R in $ (R / supply x FDV) is at least RESERVE_MIN_SEEN of the token
    side (liquidity / 2). Less -- not all pools were found (another DEX, outside top-20): the reserve is unreliable.
    No GT numbers -- nothing to check against, True."""
    fdv, liq = gt.get("fdv_usd"), gt.get("liquidity_usd")
    if not reserve or not supply or not fdv or not liq:
        return True
    return reserve / supply * fdv >= liq / 2 * RESERVE_MIN_SEEN


def _header(gt, meta=None, age_h=None):
    meta = meta or {}
    return {"name": gt.get("name") or meta.get("name"), "ticker": gt.get("ticker") or meta.get("symbol"),
            "price_usd": gt.get("price_usd"), "mcap_usd": gt.get("mcap_usd"), "fdv_usd": gt.get("fdv_usd"),
            "liquidity_usd": gt.get("liquidity_usd"), "vol24h_usd": gt.get("vol24h_usd"), "age_h": age_h}


def established_result(token, chain, gt, limits, ev, t0, rpc_requests=0):
    """Result without a full scan: the token is old and large (detect.too_established). No score.
    rpc_requests > 0 -- verdict from a late GT response, the scan was already running. market_source -- where market data came from
    ("gt" | "dexscreener", market.fetch_market), market_pool -- pool for the chart widget."""
    ev("done", d.ESTABLISHED_TEXT, score=None, band=d.TOO_ESTABLISHED, headline=d.ESTABLISHED_HEADLINE, rug=None)
    age = gt.get("age_days")
    return {"token": token, "chain": chain, "header": _header(gt, age_h=None if age is None else round(age * 24, 1)),
            "score": None, "band": d.TOO_ESTABLISHED, "headline": d.ESTABLISHED_HEADLINE,
            "reason": d.ESTABLISHED_TEXT, "parts": {}, "gates": [], "metrics": {}, "rug": None,
            "holders": [], "holders_total": None, "operators": [], "links": [], "packs": [], "unread": [],
            "established": {"liquidity_usd": gt.get("liquidity_usd"), "mcap_usd": gt.get("mcap_usd"),
                            "age_days": age, "limits": limits},
            "market_source": gt.get("source"), "market_pool": gt.get("pool"),
            "elapsed_s": round(time.time() - t0, 1), "rpc_requests": rpc_requests}


def active_result(token, chain, gt, launch, info, est_logs, ev, t0, rpc_requests):
    """Result without a verdict: the Bankr token history does not fit in a regular scan and the background index is disabled
    (bankr.TooActive). Only what is certain without the full history: dev vesting, price, liquidity, market cap (GT), chart."""
    ev("done", d.ACTIVE_TEXT, score=None, band=d.TOO_ACTIVE, headline=d.ACTIVE_HEADLINE, rug=None)
    meta = {} if gt.get("name") else ch.token_meta(token)
    age_h = round((time.time() - launch["ts"]) / 3600, 1) if launch.get("ts") else None
    return {"token": token, "chain": chain, "launchpad": "bankr", "bankr": info, "header": _header(gt, meta, age_h),
            "launch": launch, "score": None, "band": d.TOO_ACTIVE, "headline": d.ACTIVE_HEADLINE,
            "reason": d.ACTIVE_TEXT, "parts": {}, "gates": [], "metrics": {}, "rug": None,
            "holders": [], "holders_total": None, "operators": [], "links": [], "packs": [], "unread": [],
            "too_active": {"est_logs": est_logs}, "partial_scan": None, "limited": False,
            "market_source": gt.get("source"), "market_pool": gt.get("pool"),
            "elapsed_s": round(time.time() - t0, 1), "rpc_requests": rpc_requests}


def validate(token):
    """CA -> normalized address or ScanError (see chain_of)."""
    return chain_of(token)[1]


class _RankGate:
    """Wallet reading window: the task of rank i starts when i < released + width (at most width at once,
    in rank order -- largest first). A slot is released when the task finishes or uses up its share
    of time slot_s (then it keeps reading in the background but does not hold the queue: a slow wallet does not block the next ones).
    close() -- budget exhausted: waiting tasks exit immediately without reading."""

    def __init__(self, width, slot_s=None):
        self.width, self.slot_s, self.released, self.closed = width, slot_s, 0, False
        self.cv = threading.Condition()

    def _release(self, done):
        with self.cv:
            if not done[0]:
                done[0] = True
                self.released += 1
                self.cv.notify_all()

    def run(self, rank, fn, *args):
        with self.cv:
            self.cv.wait_for(lambda: self.closed or rank < self.released + self.width)
            if self.closed:
                return None
        done = [False]
        timer = None
        if self.slot_s is not None:
            timer = threading.Timer(max(0.0, self.slot_s), self._release, (done,))
            timer.daemon = True
            timer.start()
        try:
            return fn(*args)
        finally:
            if timer:
                timer.cancel()
            self._release(done)

    def close(self):
        with self.cv:
            self.closed = True
            self.cv.notify_all()


_HIST, _HIST_LOCK = {}, threading.Lock()  # (wallet, before_block) -> coins before entry

FUNDING_WORKERS = 4     # funding-source lookups in flight (one Transfers API / parsed-history call each)


def funding_links():
    """FUNDING_LINKS (default on): holders whose first deposit came from one non-hub funder are linked
    (detect.find_links kind "funder"), and a holder funded by another holder is linked to it (kind "funded")."""
    return (os.environ.get("FUNDING_LINKS") or "on").strip().lower() not in ("0", "off", "false", "no")


def _hub_counter(chain):
    """(address, cap) → outgoing native transfers up to cap, for the funder hub check; None when unavailable."""
    if chain == "solana":
        return sol.outgoing_count
    try:
        client = evm_networks.client(chain)
    except Exception:
        return None
    return lambda addr, cap: evm_funding.outgoing_count(client, addr, cap)


def _funding_reader(chain):
    """(wallet, entry block) → first funder, for wallet forensics; None when the chain has no reader."""
    if chain == "solana":
        return sol.first_funder
    try:
        client = evm_networks.client(chain)
    except Exception:
        return None
    return lambda w, b: evm_funding.first_funder(client, w, b)


def _history(a, wallet, before_block, token):
    key = (wallet, before_block)
    with _HIST_LOCK:
        if key in _HIST:
            return _HIST[key]
    n = a.wallet_distinct_tokens(wallet, before_block, token, window=None, cap=HIST_CAP)
    if n is not None:  # None -- the adapter did not fit in the read limit: read again next time
        with _HIST_LOCK:
            _HIST[key] = n
    return n


def _flags(s):
    return [f for f, on in (("unread", s["unread"]), ("virgin", s["virgin"]),
                            ("short_history", s["short_history"]), ("one_shot_funded", s["one_shot_funded"]),
                            ("sniper", s["sniper"]), ("deployer", s["is_deployer"]), ("sold", s["sold"])) if on]


def scan_depth():
    """Top holders examined per scan: SCAN_DEPTH (default detect.TOP_N = 20, the V1 methodology; 5..100).
    Solana reads its largest 20 accounts, so deeper scans apply to EVM chains (coverage says so)."""
    try:
        n = int(os.environ.get("SCAN_DEPTH", "").strip() or d.TOP_N)
    except ValueError:
        return d.TOP_N
    return max(5, min(100, n))


def sell_venues(facts, chain):
    """impact.py venues from the adapter's facts["venues"]; adapters without them (Bankr) — the V1 inputs: a quote
    curve when there is one, else the reserve as a constant-product pool."""
    if facts.get("venues") is not None:
        return [impact.venue(v["model"], v.get("reserve"), v.get("curve"), v.get("label"), v.get("address"))
                for v in facts["venues"]]
    if facts.get("impact_curve"):
        return [impact.venue("quote_curve", curve=facts["impact_curve"], label=f"{facts.get('launchpad') or chain} "
                             "pool (quoted)")]
    if facts.get("reserve"):
        return [impact.venue("constant_product", facts["reserve"], label="pool reserve")]
    return []


def sell_tax(facts, chain):
    """Sell tax for impact.py: the adapter's (launchpad state, Token-2022 fee), else the V1 q_factor, else — EVM only —
    what the scan's own sell transfers show (an estimate)."""
    t = facts.get("tax")
    if t:
        return impact.tax(t["sell"], t.get("side", "token"), t.get("source"), t.get("exact", True), t.get("max_fee"))
    if facts.get("q_factor", 1) < 1:
        return impact.tax(1 - facts["q_factor"], "token", "launchpad")
    if chain == "solana":
        return None
    return impact.observed_tax(facts.get("transfers") or [], set(facts.get("market") or ()),
                               set(facts.get("excluded") or ()) - set(facts.get("market") or ()))


def _reason(sc, base, n=d.TOP_N):
    """Main reason for the verdict in one line."""
    if sc["band"] == d.TOO_EARLY:
        return f"fewer than {d.MIN_HOLDERS} holders ({base['holders_total']}) — too early or too late"
    hard = [g for g in sc["gates"] if not g.startswith("soft:")]
    if hard:
        return hard[0]
    soft = [g for g in sc["gates"] if g.startswith("soft:")]
    if soft:
        return soft[0][len("soft: "):]
    m = sc["metrics"]
    loss = {k: d.WEIGHTS[k] - v for k, v in sc["parts"].items()}
    k = max(loss, key=loss.get)
    if loss[k] < 5:
        return "no clear signs of a single operator"
    return {"operator": (f"biggest operator could move price −{m['impact'] * 100:.0f}% if sold "
                         f"({m['operator'] * 100:.1f}% of float)" if m["impact"] is not None else
                         f"biggest operator holds {m['operator'] * 100:.1f}% of float (liquidity not measured)"),
            "virgin": (f"{m['virgin'] * 100:.0f}% virgin wallets in top" if m["virgin"] is not None
                       else "virgin share unknown: most of the top couldn't be read"),
            "transfer": f"{m['transfer'] * 100:.1f}% of float received by transfer",
            "sniper": f"snipers hold {m['sniper'] * 100:.1f}% of float",
            "concentration": f"top {n} hold {m['concentration'] * 100:.1f}% of float"}[k]


def scan(token, emit=lambda e: None, chain=None, holders_out=None):
    """Full token scan. emit(event) -- events in README format. Returns result (dict).
    ScanError -- user-facing error (address, not Pons V2 / pump.fun, Solana disabled); other exceptions -- failure.
    The chain adapter returns token facts in a common shape (token_facts): transfers, supply,
    excluded addresses, market, liquidity token reserve (reserve) and, if the chain knows
    balances directly (Solana), base.
    holders_out — a list to receive every holder [(wallet, balance)] by balance, largest first (the result itself
    carries only the analysed top: a token can have 100,000 holders)."""
    t0 = time.time()
    chain, token = chain_of(token, chain)
    a = CHAINS.get(chain) or evm_networks.client(chain)   # BNB / Base: the adapter is picked per launchpad below
    # background scan (alerts recheck) runs under its own BACKGROUND_RPS limit: the budget grows by the same factor
    # to read as many wallets as a live scan
    deadline = t0 + BUDGET * (priority.background_scale(a.RPS) if priority.is_background() else 1.0)
    r0 = a.REQUESTS[0]
    seq = [0]

    def ev(type_, detail="", wallet=None, spider=None, **extra):
        e = {"i": seq[0], "t": int((time.time() - t0) * 1000), "type": type_,
             "spider": spider, "wallet": wallet, "detail": detail, "chain": chain} | extra
        seq[0] += 1
        emit(e)

    # Market (GeckoTerminal; no answer -> DexScreener: market.fetch_market) -- before the first RPC: header and the
    # "too established" check. A cached too established verdict (market.ESTABLISHED_TTL) is returned right away, no requests.
    # No answer within MARKET_WAIT -- the scan proceeds, the request continues in the background; a late answer is checked between stages
    # and before the verdict. Market unavailable -- regular scan (old token -> limited).
    network = GT_NETWORK[chain]
    limits = established_limits()
    cached = market.established_get(token, network)
    if cached is not None and d.too_established(cached, limits):
        return established_result(token, chain, cached, limits, ev, t0)
    mkt_box = {}
    mkt_thread = threading.Thread(target=lambda: mkt_box.update(market.fetch_market(token, network,
                                                                                    budget=MARKET_LATE)),
                                  daemon=True)
    mkt_thread.start()
    mkt_thread.join(timeout=max(0.0, t0 + MARKET_WAIT - time.time()))
    gt = dict(mkt_box)
    late = mkt_thread.is_alive()
    if late:
        print(f"gt: no answer in {MARKET_WAIT:g}s for {token}, scanning; late check before verdict", flush=True)
    if d.too_established(gt, limits):
        market.established_put(token, network, gt)
        return established_result(token, chain, gt, limits, ev, t0)

    def late_established():
        """A late GT answer has arrived and the token is too established -> result (scan stops here), otherwise None."""
        if not late or mkt_thread.is_alive() or not d.too_established(mkt_box, limits):
            return None
        print(f"gt: late answer for {token} at {time.time() - t0:.1f}s: too established", flush=True)
        market.established_put(token, network, mkt_box)
        return established_result(token, chain, dict(mkt_box), limits, ev, t0, a.REQUESTS[0] - r0)

    ev("stage", "launch")
    # Robinhood launchpad: Flap -- only with FLAP_ENABLED, suffix 8888/7777 (no RPC) and Portal confirmation
    # (one HTTP); Bankr -- only with BANKR_ENABLED, suffix ba3 (no RPC) and one Airlock eth_call;
    # the rest -- Pons the old way, no extra requests
    flap_state = flap.detect(token) if chain == "robinhood" else None
    bankr_state = bankr.detect(token) if chain == "robinhood" and flap_state is None else None
    # launchpads on the generic EVM adapter (Clanker, four.meme: chains/registry.py), before the Pons fallback
    evm_hit = registry.detect(chain, token) if chain != "solana" and flap_state is None and bankr_state is None \
        else None
    eth_box, eth_thread = {}, None
    if evm_hit is not None:
        a = evm_hit[0]
        if a.LAUNCHPAD == "flap" and evm_hit[1].get("status") != 4:   # BNB Flap on the curve: price = Portal × BNB/USD
            eth_thread = threading.Thread(target=lambda: eth_box.update(usd=market.native_usd(network)), daemon=True)
            eth_thread.start()
        try:
            launch = a.get_launch(token)
        except evm_token.HistoryUnavailable as e:
            raise ScanError(str(e)) from None
    elif chain not in CHAINS:
        launch = None    # BNB / Base without a launchpad match: the DEX fallback below
    elif bankr_state is not None:
        a = bankr
        launch = bankr.get_launch(token, bankr_state)
    elif flap_state is not None:
        a = flap
        if flap_state["status"] != flap.STATUS_DEX:   # curve: GT does not know it, price = Portal x ETH/USD (in parallel)
            eth_thread = threading.Thread(target=lambda: eth_box.update(usd=market.native_usd(network)), daemon=True)
            eth_thread.start()
        launch = flap.get_launch(token, flap_state)
    else:
        launch = a.get_launch(token)
    if launch is None and chain != "solana" and (evm_hit := registry.detect(chain, token, fallback=True)):
        a = evm_hit[0]   # no launchpad: a token launched straight into a DEX pool
        try:
            launch = a.get_launch(token)
        except evm_token.HistoryUnavailable as e:
            raise ScanError(str(e)) from None
    if launch is None:
        raise ScanError(not_launchpad(chain))
    if est := late_established():
        return est

    ev("stage", "transfers")
    # Flap: a large token history is read in windows within the scan budget (deadline counted from scan start)
    try:
        facts = a.token_facts(token, launch, deadline) if a in (flap, bankr) or getattr(a, "FACTS_DEADLINE", False) \
            else a.token_facts(token, launch)
    except (ch.HistoryTooLarge, evm_token.HistoryTooLarge) as e:
        print(f"scan: {token} history too large ({e} logs, cap {ch.SCAN_MAX_LOGS})", flush=True)
        raise ScanError(TOO_LARGE) from None
    except evm_token.HistoryUnavailable as e:   # older than every configured provider serves: no partial verdict
        raise ScanError(str(e)) from None
    except evm_token.HistoryIndexing as e:      # large history: progress checkpointed, the next scan resumes
        raise ScanError(f"Indexing this token's history: {e.progress:.0%} read so far ({e.transfers:,} transfers). "
                        f"Scan again to continue — progress is kept.") from None
    except bankr.TooActive as e:   # Bankr, index disabled: history larger than a regular scan -- no verdict
        if est := late_established():
            return est
        bankr.mark_active(token)
        info = bankr.active_info(token, launch)
        if late:   # market needed for the header: wait for the late GT answer within the budget
            mkt_thread.join(timeout=max(0.0, deadline - time.time()))
            gt = dict(mkt_box)
        ts0 = a.block_timestamps([launch["block"]]).get(launch["block"])
        return active_result(token, chain, gt, launch | {"ts": ts0}, info, e.est_logs, ev, t0, a.REQUESTS[0] - r0)
    transfers, supply, excluded, mkt = facts["transfers"], facts["supply"], facts["excluded"], facts["market"]
    base = facts["base"] or d.supply_base(transfers, supply, excluded)
    holders = d.top_holders(base, scan_depth())
    hs = [w for w, _, _ in holders]
    if holders_out is not None:
        holders_out.extend(sorted(base["balances"].items(), key=lambda x: (-x[1], x[0])))
    ratio = base["circulating"] / supply if supply else 0.0

    if est := late_established():
        return est

    ev("stage", "entries")
    entries = a.classify_entries(token, transfers, hs)
    ts = a.block_timestamps([launch["block"]] + [e["block"] for e in entries.values() if e["block"] is not None])
    sold = d.sellers(transfers, mkt)
    # entry not found (Solana: kind None) -- no entry time and no history read: unread, not virgin
    data = {w: entries[w] | {"entry_ts": ts.get(entries[w]["block"]), "inflows": [], "sold": w in sold,
                             "distinct_tokens": None} for w in hs}
    unit, scale = NATIVE[chain]

    if est := late_established():
        return est

    # holder type (one batch): contracts are labelled, never excluded — a team's multisig is a contract too
    try:
        holder_type = {w: "contract" if c else "wallet" for w, c in a.is_contract(hs).items()} if hs else {}
    except Exception:   # labelling only: a failure leaves the type unknown, the scan goes on
        holder_type = {}

    ev("stage", "wallets")
    spider = {w: i % SPIDERS for i, w in enumerate(hs)}
    share = {a: s for a, _, s in holders}
    for w in hs:
        ev("spider_move", "to wallet", wallet=w, spider=spider[w])
    # alerts recheck pool -- also background (chains.priority: yields to live scans in the RPS limiter)
    ex = ThreadPoolExecutor(max_workers=WORKERS,
                            thread_name_prefix=f"{priority.BG}-pool" if priority.is_background() else "")
    # readers' scope: past the crawl deadline (or once cancelled below) they start no more RPC requests, so they neither
    # outlive the scan nor hold limiter slots that the links stage would have to queue behind
    crawl_deadline = deadline - DETECT_RESERVE
    sc = priority.Scope(crawl_deadline)
    order = [w for w in hs if entries[w]["kind"] is not None]   # hs -- by descending share: largest first
    width = getattr(a, "HISTORY_PARALLEL", WORKERS)
    left = max(0.05, deadline - DETECT_RESERVE - time.time())
    # time share per wallet: if each fits into it, all make it; whoever takes longer yields the slot to the next
    gate = _RankGate(width, left * width / len(order) if width < len(order) else None)
    futs = {ex.submit(priority.run_scoped, sc, gate.run, i, _history, a, w, entries[w]["block"], token): w
            for i, w in enumerate(order)}
    # wallet forensics, alongside the histories and under the same scope: funding sources, price candles, decimals
    extras = {}

    def read_extras():
        try:
            extras["chart"] = market.fetch_chart(token, network)
        except Exception:
            extras["chart"] = {}
        # Pons / Flap / Bankr tokens are 18 decimals by construction (no request); a token launched straight into a
        # pool can use any (generic EVM adapter); Solana's comes cached with the supply
        extras["decimals"] = 6 if chain == "solana" else 18
        if chain == "solana" or isinstance(a, evm_token.EvmTokenAdapter):
            try:
                extras["decimals"] = a.token_decimals(token)
            except Exception:
                pass
    xt = threading.Thread(target=read_extras, daemon=True)
    xt.start()
    fund = _funding_reader(chain)
    fex = ThreadPoolExecutor(max_workers=FUNDING_WORKERS,
                             thread_name_prefix=f"{priority.BG}-fund" if priority.is_background() else "fund")
    ffuts = {fex.submit(priority.run_scoped, sc, fund, w, entries[w]["block"]): w for w in order} if fund else {}
    # hub check as soon as a funder matters for links: it funded a second holder, or it is a holder itself
    hub_count = _hub_counter(chain) if funding_links() else None
    fund_lock, by_funder, hub_futs, hset0 = threading.Lock(), {}, {}, set(hs)

    def on_funded(fut, w):
        try:
            f = fut.result()
        except BaseException:
            return
        if hub_count is None or not f or not f.get("found") or not f.get("funder"):
            return
        with fund_lock:
            ws = by_funder.setdefault(f["funder"], set())
            ws.add(w)
            if (len(ws) >= 2 or f["funder"] in hset0) and f["funder"] not in hub_futs:
                try:
                    hub_futs[f["funder"]] = fex.submit(priority.run_scoped, sc, hub_count, f["funder"], d.HUB_MIN_OUT)
                except RuntimeError:   # pool already shut down: no hub data, no link
                    pass
    for fu, w in ffuts.items():
        fu.add_done_callback(lambda x, w=w: on_funded(x, w))

    def flag(w):
        s = d.wallet_signals({w: data[w]}, ts[launch["block"]], launch["deployer"],
                             launch["block"], a.BUNDLE_WINDOW)[w]
        eth = "" if s["eth_in"] is None else f" {s['eth_in'] / scale:.4f} {unit}"
        fl = _flags(s)
        when = "entry not found" if data[w]["entry_ts"] is None else f"+{data[w]['entry_ts'] - ts[launch['block']]}s"
        ev("wallet_flag", f"{s['kind'] or 'unknown'}{eth}, {when}"
           + (", " + ", ".join(fl) if fl else ""), wallet=w, spider=spider[w],
           flags=fl, kind=s["kind"], share=share[w], share_supply=share[w] * ratio)

    done = set()
    try:
        for f in as_completed(futs, timeout=max(0.05, crawl_deadline - time.time())):
            w = futs[f]
            try:
                data[w]["distinct_tokens"] = f.result()
            except priority.Cancelled:   # reached the deadline mid-read: unread, flagged below with the stragglers
                continue
            except Exception:
                pass
            done.add(w)
            flag(w)
    except _CFTimeout:
        pass
    funders = {}
    try:
        for f in as_completed(ffuts, timeout=max(0.05, crawl_deadline - time.time())):
            try:
                funders[ffuts[f]] = f.result()
            except (priority.Cancelled, Exception):   # unread: funding unknown for this wallet
                pass
    except _CFTimeout:
        pass
    with fund_lock:
        hubs_pending = dict(hub_futs)
    try:
        for _ in as_completed(list(hubs_pending.values()), timeout=max(0.05, crawl_deadline - time.time())):
            pass
    except _CFTimeout:
        pass
    funder_out = {}   # funder → outgoing native transfers (absent: unknown, treated as a hub)
    for funder, fu in hubs_pending.items():
        if fu.done() and not fu.cancelled() and fu.exception() is None and fu.result() is not None:
            funder_out[funder] = fu.result()
    sc.cancel()
    gate.close()
    ex.shutdown(wait=False, cancel_futures=True)
    fex.shutdown(wait=False, cancel_futures=True)
    # memory brain (Phase 7): the same holders, funders and deployer in other tokens — read while links are computed
    mem_box = {}
    mem_funders = [f["funder"] for f in funders.values() if f and f.get("found") and f.get("funder")]
    mt = threading.Thread(target=lambda: mem_box.update(
        mem=memory_db.lookup(chain, token, hs, mem_funders, launch.get("deployer"))), daemon=True)
    if memory_db.enabled():
        mt.start()
    unread = [w for w in hs if w not in done]
    for w in unread:
        flag(w)

    ev("stage", "links")
    sig = d.wallet_signals(data, ts[launch["block"]], launch["deployer"], launch["block"], a.BUNDLE_WINDOW)
    packs = d.find_packs(sig, window=a.PACK_WINDOW)
    # token distributors shared by >= 2 holders: only check whether it is a contract
    hset, by_src = set(hs), {}
    for t in transfers:
        if t["to"] in hset and t["frm"] not in hset and t["frm"] not in excluded:
            by_src.setdefault(t["frm"], set()).add(t["to"])
    cand = {a for a, ws in by_src.items() if len(ws) > 1}
    fcand = set(funder_out)                       # funders that may link (hub-checked)
    contracts = {x for x, c in a.is_contract(cand | fcand).items() if c} if cand | fcand else set()
    # without funding we do not know outgoing ETH: a distributor wallet (not a contract) is not considered a hub
    outgoing = {a: 0 for a in cand - contracts}
    outgoing.update(funder_out)                  # funders: their measured outgoing count decides hub or not
    inflows = ({w: [{"from": f["funder"], "tx": f.get("tx")}] for w, f in funders.items()
                if f and f.get("found") and f.get("funder")} if funding_links() else {})
    links = d.find_links(hs, transfers, sig, inflows, outgoing, contracts, excluded, packs)
    # a holder whose first deposit came from another holder (not a hub, not a contract): one operator
    for w, srcs in inflows.items():
        f = srcs[0]["from"]
        if f in hset and f != w and f not in contracts and outgoing.get(f, d.HUB_MIN_OUT) < d.HUB_MIN_OUT:
            links.append({"a": f, "b": w, "kind": "funded", "level": "proven", "via": srcs[0]["tx"]})
    ops = d.operators(holders, links, packs, ratio)
    for l in links:
        ev("link", l["kind"], wallet=l["a"], b=l["b"], level=l["level"], kind=l["kind"], via=l["via"])
    for o in ops:
        if len(o["wallets"]) > 1:
            ev("cluster", f"{len(o['wallets'])} wallets, {o['share'] * 100:.1f}% of float",
               wallet=o["wallets"][0], wallets=o["wallets"], level=o["level"],
               share=o["share"], share_supply=o["share_supply"])

    # late GT answer (not received within MARKET_WAIT): if it arrived -- use it; too established -> that is the verdict
    if est := late_established():
        return est
    if late:
        if not mkt_thread.is_alive():
            gt = dict(mkt_box)
        print(f"gt: late answer for {token} at {time.time() - t0:.1f}s: "
              + ("ok" if gt else "still none" if mkt_thread.is_alive() else "failed"), flush=True)
    # fallback without GT: market unknown, but the token is older on-chain than the too established threshold --
    # holder signals are limited: no hard rules on impact and transfer, and no probably rug
    limited = not gt and time.time() - ts[launch["block"]] > limits["min_age_days"] * 86400
    # Flap: a large history was read in windows and the top is not guaranteed accurate -- holder signals are limited too
    hist = (facts.get("flap") or facts.get("bankr") or {}).get("history") or {}
    partial = hist.get("mode") == "windowed" and not hist.get("top_exact")
    limited_note = d.PARTIAL_NOTE if partial and not limited else d.LIMITED_NOTE
    limited = limited or partial
    # Bankr: large history, the full holder index is still building (or unavailable) -- partial scan:
    # verdict no better than RISKY, on the site and in the bot -- "Partial scan: ..." (Pons and Flap -- old path)
    partial_scan = bankr_partial(token, hist) if "bankr" in facts and partial else None
    # reserve is reliable: the adapter found a pool (on the curve -- always) and it agrees with GT liquidity
    reserve_ok = facts.get("reserve_ok", True) and reserve_seen(facts["reserve"], supply, gt)
    q_factor = facts.get("q_factor", 1)   # Flap after graduation: sell tax in tokens (1 - sellTax)
    # Bankr: impact from V4Quoter quotes (impact_curve), not from the reserve; non-redeemable dev vesting (locked) is
    # in the dev share but not in q (cannot be sold now); dev -- dev share from contracts (rule applies even when limited).
    # Pons and Flap: None -- old path
    curve, locked = facts.get("impact_curve"), facts.get("locked")
    # memory (Phase 7) before the verdict: the live scoring model may use it (scoring.py)
    if mt.is_alive() or mt.ident is not None:
        mt.join(timeout=max(0.0, min(memory_db.LOOKUP_TIMEOUT, deadline + 3.0 - time.time())))
    try:
        mem = cabals.analyse(mem_box.get("mem"), holders, ratio, funders, funder_out, d.HUB_MIN_OUT)
    except Exception as e:
        print(f"cabals: {token}: {type(e).__name__}: {e}", flush=True)
        mem = None
    sc = d.score(holders, sig, ops, base, facts["reserve"], gt.get("liquidity_usd"), reserve_ok=reserve_ok,
                 limited=limited, q_factor=q_factor, limited_note=limited_note, impact_curve=curve, locked=locked,
                 dev=facts.get("dev"), partial=partial_scan is not None)
    # coverage: what could and could not be read; a limited scan is never CLEAN (explicit cap, V1 scoring unchanged)
    client = getattr(a, "client", None)
    cov = coverage.assess(facts, holders, entries, data, unread, facts.get("history_info"), gt.get("source"),
                          [{k: p.get(k) for k in ("name", "requests", "errors", "rate_limited", "cooling")}
                           for p in client.health()["providers"]] if client is not None else [])
    sc = coverage.apply(sc, cov)
    # every scoring model on the same evidence; the live one (SCORE_MODEL) gives the verdict, the rest are shadows
    models = scoring.evaluate(sc, mem)
    live = scoring.current()
    if live != scoring.BASE:
        sc = dict(sc, score=models[live]["score"], band=models[live]["band"], gates=models[live]["gates"])
    reason = _reason(sc, base, len(holders))
    # probably rug: only computation on already collected data, no network requests
    # and only if DANGER was triggered by a behavioral hard rule (not a single whale in a thin pool)
    behavioral = any(not g.startswith("soft:") for g in sc["gates"])
    rug = None if limited else d.rug_projection(holders, sig, ops, base, facts["reserve"], sc["band"],
                                                snipers=a.RUG_SNIPERS, reserve_ok=reserve_ok, behavioral=behavioral,
                                                q_factor=q_factor, impact_curve=curve, locked=locked)
    # Flap on the curve: GeckoTerminal does not know it -- name and ticker from the contract, price = Portal x ETH/USD,
    # market cap = price x full supply, liquidity = ETH in the curve x ETH/USD
    fl = facts.get("flap") or {}
    curve = fl.get("phase") == "bonding_curve"
    if curve:
        gt = {k: v for k, v in gt.items()
              if k not in ("name", "ticker", "price_usd", "mcap_usd", "liquidity_usd", "vol24h_usd")}
        if eth_thread is not None:
            eth_thread.join(timeout=max(0.0, min(2.0, deadline - time.time())))
        usd = eth_box.get("usd")
        if usd and fl.get("price_eth"):
            gt.update(price_usd=fl["price_eth"] * usd, mcap_usd=fl["price_eth"] * usd * supply / 1e18)
        if usd and fl.get("reserve_eth") is not None:
            gt["liquidity_usd"] = fl["reserve_eth"] * usd
    if rug:
        rug["level_usd"] = gt["price_usd"] * rug["level_factor"] if gt.get("price_usd") else None
    rug_v1 = rug
    meta = {}
    if not gt.get("name"):
        meta = a.token_meta(token)
    header = _header(gt, meta, round((time.time() - ts[launch["block"]]) / 3600, 1))
    # wallet forensics (estimates, never part of the score): trades, P&L at candle prices, hold time, funding
    xt.join(timeout=max(0.0, min(2.0, deadline + 2.0 - time.time())))
    chart = extras.get("chart") or {}
    clock = forensics.make_clock(list(ts.items()))   # launch + entry times the scan already read: no extra request
    try:
        fz = forensics.build(transfers, hs, mkt, excluded, base["balances"], share, ratio,
                             clock, chart.get("candles"),
                             chart.get("price_usd") or gt.get("price_usd"),
                             extras.get("decimals", 6 if chain == "solana" else 18), time.time(), funders, entries,
                             funder_out, d.HUB_MIN_OUT)
    except Exception as e:   # evidence only: a failure here never costs the verdict
        print(f"forensics: {token}: {type(e).__name__}: {e}", flush=True)
        fz = None
    try:   # relationship graph (Phase 6): the evidence behind every link, for the page and the memory brain
        rel = graph.build(chain, holders, sig, links, packs, ops, funders, funder_out, contracts, d.HUB_MIN_OUT, clock)
    except Exception as e:
        print(f"graph: {token}: {type(e).__name__}: {e}", flush=True)
        rel = None

    # sell impact (Phase 9): every scenario on every venue, with taxes; the «probably rug» marker comes from it
    try:
        si = impact.analyse(holders, sig, ops, base, sell_venues(facts, chain), reserve_ok, sell_tax(facts, chain),
                            locked, mem, a.RUG_SNIPERS, gt.get("price_usd"),
                            extras.get("decimals", 6 if chain == "solana" else 18))
        rug = None if limited else impact.marker(si, sc["band"], sc["gates"], reserve_ok, limited)
    except Exception as e:   # the V1 projection stays if the new block fails
        print(f"impact: {token}: {type(e).__name__}: {e}", flush=True)
        si, rug = None, rug_v1

    elapsed = round(time.time() - t0, 1)
    ev("done", reason, score=sc["score"], band=sc["band"], headline=sc["headline"], rug=rug)

    extra = ({"launchpad": "flap", "flap": facts["flap"]} if "flap" in facts else
             {"launchpad": "bankr", "bankr": facts["bankr"]} if "bankr" in facts else
             {"launchpad": facts["launchpad"], facts["launchpad"]: facts[facts["launchpad"]]} if "launchpad" in facts
             else {})
    return extra | {
        "token": token, "chain": chain, "header": header,
        "launch": launch | {"ts": ts[launch["block"]]},
        "supply": supply, "circulating": base["circulating"], "holders_total": base["holders_total"],
        "holders_complete": cov["holders"]["method"] in ("full_history", "all_accounts"),
        "coverage": cov,
        "holders": [{"wallet": a, "share": s, "share_supply": s * ratio, "spider": spider[a],
                     "holder_type": holder_type.get(a, "unknown"),
                     "forensics": (fz or {}).get("wallets", {}).get(a),
                     "signals": sig[a]} for a, _, s in holders],
        "forensics": None if fz is None else {k: v for k, v in fz.items() if k != "wallets"},
        "links": links, "packs": packs, "operators": ops, "graph": rel, "memory": mem,
        "score": sc["score"], "band": sc["band"], "parts": sc["parts"], "gates": sc["gates"],
        "metrics": sc["metrics"], "headline": sc["headline"], "reason": reason, "notes": sc.get("notes", []),
        "reserve": facts["reserve"],
        "reserve_ok": reserve_ok, "limited": limited, "market_source": gt.get("source"),
        "market_pool": gt.get("pool"),
        "rug": rug, "sell_impact": si, "partial_scan": partial_scan,
        "unread": unread, "use_funding": bool(funding_links() and funders),
        "elapsed_s": elapsed, "rpc_requests": a.REQUESTS[0] - r0,
        "model": {"id": live, "scores": models},
        "data_health": scoring.data_health(cov, fz, mem, memory_db.enabled(), gt.get("source"), elapsed,
                                           a.REQUESTS[0] - r0),
    }
