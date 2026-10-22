"""Memory brain client: Supabase Postgres through PostgREST RPC (standard library only; schema in
supabase/migrations/20261022120000_memory.sql).

Two calls per scan:
  lookup(...)  memory_lookup — what memory knows about the scan's top holders, their funders and the deployer, from
               OTHER tokens (Phase 7 reads it before the verdict)
  record(res)  memory_record — the finished scan, normalised (tokens, scans, wallets, holdings, funding, funder
               stats, graph edges), in one transaction

Configuration (server-side only, never sent to the browser):
  SUPABASE_URL         https://<project>.supabase.co
  SUPABASE_SECRET_KEY  a secret key (sb_secret_…), or SUPABASE_SERVICE_ROLE_KEY (legacy service_role JWT)
  MEMORY_ENABLED       on by default when both are set; "off" disables it
A secret key bypasses row level security: it must stay in the server's environment. It goes only in the apikey
header (a legacy JWT also as Bearer, as Supabase requires), never in a URL, and is never logged.
Memory is evidence, never a dependency: every failure is logged by type and the scan goes on without it.
"""
import json, os, time, urllib.error, urllib.parse, urllib.request

LOOKUP_TIMEOUT = 3.0     # seconds: the scan waits no longer for memory
RECORD_TIMEOUT = 4.0
STATS = {"lookups": 0, "records": 0, "errors": 0}


def _url():
    return (os.environ.get("SUPABASE_URL") or "").strip().rstrip("/")


def _key():
    return (os.environ.get("SUPABASE_SECRET_KEY") or os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or "").strip()


def enabled():
    if (os.environ.get("MEMORY_ENABLED") or "on").strip().lower() in ("0", "off", "false", "no"):
        return False
    u = urllib.parse.urlparse(_url())
    # https only: the key must never cross the network in clear (http allowed for a local test server only)
    secure = u.scheme == "https" or (u.scheme == "http" and u.hostname in ("127.0.0.1", "localhost"))
    return bool(secure and u.hostname and _key())


class MemoryUnavailable(Exception):
    pass


def _rpc(name, args, timeout):
    key = _key()
    headers = {"content-type": "application/json", "accept": "application/json", "apikey": key,
               "user-agent": "crawlmeme-server"}
    if key.startswith("eyJ"):    # legacy service_role JWT: Supabase also wants it as the bearer token
        headers["authorization"] = "Bearer " + key
    req = urllib.request.Request(f"{_url()}/rest/v1/rpc/{name}", data=json.dumps(args).encode(), headers=headers,
                                 method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read() or b"null")
    except urllib.error.HTTPError as e:
        STATS["errors"] += 1
        raise MemoryUnavailable(f"{name}: HTTP {e.code}") from None
    except (urllib.error.URLError, TimeoutError, ConnectionError, ValueError) as e:
        STATS["errors"] += 1
        raise MemoryUnavailable(f"{name}: {type(e).__name__}") from None


def lookup(chain, token, wallets, funders, deployer, timeout=LOOKUP_TIMEOUT):
    """memory_lookup result (dict) or None (memory off or unavailable)."""
    if not enabled():
        return None
    t0 = time.time()
    try:
        out = _rpc("memory_lookup", {"p_chain": chain, "p_token": token, "p_wallets": list(wallets),
                                     "p_funders": sorted(set(f for f in funders if f)),
                                     "p_deployer": deployer}, timeout)
    except MemoryUnavailable as e:
        print(f"memory: lookup failed ({e})", flush=True)
        return None
    STATS["lookups"] += 1
    if isinstance(out, dict):
        out["elapsed_ms"] = int((time.time() - t0) * 1000)
        return out
    return None


def payload(res):
    """memory_record argument from a finished scan result (engine.scan)."""
    g = res.get("graph") or {}
    nodes = {n["id"]: n for n in g.get("nodes") or []}
    holders = []
    for i, h in enumerate(res.get("holders") or [], 1):
        f = (h.get("forensics") or {}).get("funding") or {}
        node = nodes.get(h["wallet"]) or {}
        holders.append({"wallet": h["wallet"], "rank": i, "share": h.get("share"),
                        "entry": (h.get("signals") or {}).get("kind"), "flags": node.get("flags") or [],
                        "operator": node.get("operator"), "funder": f.get("funder") if f.get("found") else None})
    funding = [{"wallet": h["wallet"], "funder": f["funder"], "block": f.get("block"), "tx": f.get("tx"),
                "amount": f.get("amount")}
               for h in res.get("holders") or []
               for f in [((h.get("forensics") or {}).get("funding") or {})] if f.get("found") and f.get("funder")]
    funders = [{"address": n["id"], "outgoing": n.get("outgoing"), "hub": n.get("hub")}
               for n in nodes.values() if n.get("outgoing") is not None]
    edges = [{k: e.get(k) for k in ("a", "b", "kind", "level", "confidence", "tx", "block")}
             for e in g.get("edges") or []]
    header, launch = res.get("header") or {}, res.get("launch") or {}
    merged = [o["share"] for o in res.get("operators") or [] if len(o.get("wallets") or []) > 1]
    return {"chain": res["chain"], "token": res["token"], "ticker": header.get("ticker"), "name": header.get("name"),
            "launchpad": res.get("launchpad") or ("pons" if res["chain"] == "robinhood" else None),
            "deployer": launch.get("deployer"), "band": res.get("band"), "score": res.get("score"),
            "coverage": (res.get("coverage") or {}).get("level"), "holders_total": res.get("holders_total"),
            "largest_operator_share": max(merged) if merged else None,
            "model": (res.get("model") or {}).get("id") or "crawlmeme-1", "rug": res.get("rug"),
            "scores": {k: {"score": v.get("score"), "band": v.get("band"),
                           "adjustments": [a["code"] for a in v.get("adjustments") or []]}
                       for k, v in ((res.get("model") or {}).get("scores") or {}).items()},
            "holders": holders, "funding": funding, "funders": funders, "edges": edges}


def record(res, timeout=RECORD_TIMEOUT):
    """Write a finished scan; the new scan id or None. Partial / too-established results are not memory."""
    if not enabled() or not res or res.get("score") is None or res.get("partial_scan"):
        return None
    try:
        sid = _rpc("memory_record", {"p": payload(res)}, timeout)
    except MemoryUnavailable as e:
        print(f"memory: record failed ({e})", flush=True)
        return None
    STATS["records"] += 1
    return sid
