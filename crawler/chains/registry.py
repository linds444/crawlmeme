"""Chain and launchpad registry: which adapter scans a token, and on which chain an EVM address lives.

Robinhood Pons, Flap and Bankr keep their dedicated adapters (chains/robinhood.py, flap.py, bankr.py) and the
engine's original dispatch. Launchpads added in Phase 2 run on the generic EVM adapter (chains/evm/token.py) with a
plugin per launchpad, registered here per chain in detection order (cheapest and most specific first).

Switches (all default off, so V1 behaviour is unchanged until enabled):
  BSC_ENABLED, BASE_ENABLED — scan BNB Chain / Base tokens (chains/evm/networks.py)
  CLANKER_ENABLED           — Clanker v4 on every enabled chain (one eth_call per Robinhood scan to rule it out)
  FOURMEME_ENABLED          — four.meme on BNB Chain
  FLAP_ENABLED              — Flap on Robinhood (dedicated adapter) and on BNB Chain (plugin)
A launchpad is listed as supported only once its mainnet integration test passes (tests/test_live_launchpads.py).
"""
import os, threading
from concurrent.futures import ThreadPoolExecutor

from crawler.chains.evm import networks


def _on(var):
    return os.environ.get(var, "false").strip().lower() in ("1", "true", "yes")


def plugins(chain):
    """Enabled launchpad plugins for a chain, in detection order."""
    from crawler.chains.evm.launchpads import clanker, flap, fourmeme
    table = {   # Robinhood Flap / Bankr / Pons: dedicated adapters, dispatched by the engine before these
        "robinhood": [(clanker, "CLANKER_ENABLED")],
        "bsc": [(flap, "FLAP_ENABLED"), (fourmeme, "FOURMEME_ENABLED"), (clanker, "CLANKER_ENABLED")],
        "base": [(clanker, "CLANKER_ENABLED")],
    }
    return [p for p, var in table.get(chain, []) if _on(var)]


def launchpad_names(chain):
    return [p.NAME for p in plugins(chain)]


_ADAPTERS, _lock = {}, threading.Lock()


def adapter(chain, plugin):
    """Process-wide EvmTokenAdapter for (chain, launchpad plugin)."""
    from crawler.chains.evm.token import EvmTokenAdapter
    key = (chain, plugin.NAME)
    with _lock:
        if key not in _ADAPTERS:
            _ADAPTERS[key] = EvmTokenAdapter(networks.NETWORKS[chain], networks.client(chain), plugin)
        return _ADAPTERS[key]


def detect(chain, token):
    """(adapter, state) of the first enabled launchpad plugin on chain that recognises token, else None."""
    for p in plugins(chain):
        a = adapter(chain, p)
        st = a.detect(token)
        if st is not None:
            return a, st
    return None


LOCATE_TIMEOUT_S = 4.0     # per provider, one attempt, at most two providers per chain


def locate(token, chains=None):
    """EVM chains (of those enabled) where the address has contract code: one eth_getCode per chain, in parallel.
    A chain whose providers all fail is reported in the second list (unknown), never silently dropped."""
    chains = chains or networks.enabled_chains()
    found, unknown = [], []

    def probe(c):
        try:   # bounded: a slow public node must not hold the scan for minutes; unanswered → unknown
            code = networks.client(c).call("eth_getCode", [token.lower(), "latest"], timeout=LOCATE_TIMEOUT_S,
                                           tries=1, max_providers=2)
            return c, code not in (None, "0x", "0x0", "")
        except Exception:
            return c, None

    with ThreadPoolExecutor(max_workers=len(chains)) as ex:
        for c, has in ex.map(probe, chains):
            if has:
                found.append(c)
            elif has is None:
                unknown.append(c)
    return found, unknown


def clients():
    """EVM clients built so far (health and rate-limit counters)."""
    return dict(networks._CLIENTS)
