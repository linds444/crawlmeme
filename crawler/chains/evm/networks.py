"""EVM network configurations and their RPC providers.

Every contract address here was checked on-chain on 2026-10-21 (eth_getCode, plus a functional call where it matters:
see docs/phase-2-chains.md). Provider capabilities are the measured behaviour of each endpoint; the client refines them
at run time from the endpoints' own refusals.

Providers per chain, in order of preference for ordinary calls:
  1. {CHAIN}_RPC_URLS — your own endpoints (comma-separated; paid nodes, self-hosted), capabilities learned at run time
  2. Alchemy           — if ALCHEMY_API_KEY is set. Free tier: eth_getLogs over 10 blocks only (ALCHEMY_TIER=free,
                         the default), but alchemy_getAssetTransfers gives full token and wallet transfer history
  3. public endpoint   — no key; capabilities as measured (see PUBLIC below)
Log scans pick the provider with the widest span regardless of this order (client.iter_logs).
"""
import os
from dataclasses import dataclass, field

from crawler.chains.evm.client import EvmClient, Provider


@dataclass(frozen=True)
class Network:
    key: str                     # internal chain key (also the API/URL value): robinhood | bsc | base
    chain_id: int
    name: str
    native: str                  # gas token symbol
    wrapped_native: str          # WETH / WBNB
    gt_network: str              # GeckoTerminal network id
    ds_chain: str                # DexScreener chainId
    explorer: str
    alchemy_subdomain: str | None
    v4_pool_manager: str
    v4_quoter: str
    block_time_s: float          # typical, for windows and ETAs only
    multicall3: str = "0xca11bde05977b3631167028862be2a173976ca11"
    stables: frozenset = field(default_factory=frozenset)
    routers: frozenset = field(default_factory=frozenset)   # aggregator / DEX routers seen moving launchpad tokens
    burn: frozenset = frozenset({"0x000000000000000000000000000000000000dead", "0x" + "0" * 40})


NETWORKS = {
    "robinhood": Network(
        key="robinhood", chain_id=4663, name="Robinhood Chain", native="ETH",
        wrapped_native="0x0bd7d308f8e1639fab988df18a8011f41eacad73",
        gt_network="robinhood", ds_chain="robinhood", explorer="https://robinhoodchain.blockscout.com",
        alchemy_subdomain="robinhood-mainnet",
        v4_pool_manager="0x8366a39cc670b4001a1121b8f6a443a643e40951",
        v4_quoter="0x8dc178efb8111bb0973dd9d722ebeff267c98f94",
        block_time_s=0.1,
        routers=frozenset({"0xb92fe925dc43a0ecde6c8b1a2709c170ec4fff4f", "0x65050a9b7e5075a2ba5ced7b1b64ee66262c40dc",
                           "0x8876789976decbfcbbbe364623c63652db8c0904"}),
    ),
    "bsc": Network(
        key="bsc", chain_id=56, name="BNB Chain", native="BNB",
        wrapped_native="0xbb4cdb9cbd36b01bd1cbaebf2de08d9173bc095c",
        gt_network="bsc", ds_chain="bsc", explorer="https://bscscan.com",
        alchemy_subdomain="bnb-mainnet",
        v4_pool_manager="0x28e2ea090877bf75740558f6bfb36a5ffee9e9df",
        v4_quoter="0x9f75dd27d6664c475b90e105573e550ff69437b0",
        block_time_s=0.75,
        stables=frozenset({"0x55d398326f99059ff775485246999027b3197955",    # USDT
                           "0x8ac76a51cc950d9822d68b83fe1ad97b32cd580d",    # USDC
                           "0xe9e7cea3dedca5984780bafc599bd69add087d56"}),  # BUSD
        routers=frozenset({"0x10ed43c718714eb63d5aa57b78b54704e256024e",   # PancakeSwap V2 router
                           "0x13f4ea83d0bd40e75c8222255bc855a974568dd4",   # PancakeSwap V3 smart router
                           "0x1b81d678ffb9c0263b24a97847620c99d213eb14"}), # PancakeSwap V3 swap router
    ),
    "base": Network(
        key="base", chain_id=8453, name="Base", native="ETH",
        wrapped_native="0x4200000000000000000000000000000000000006",
        gt_network="base", ds_chain="base", explorer="https://basescan.org",
        alchemy_subdomain="base-mainnet",
        v4_pool_manager="0x498581ff718922c3f8e6a244956af099b2652b2b",
        v4_quoter="0x0d5e0f971ed27fbff6c2837bf31316121532048d",
        block_time_s=2.0,
        stables=frozenset({"0x833589fcd6edb6e08f4c7c32d4f71b54bda02913"}),  # USDC
        routers=frozenset({"0x6ff5693b99212da76ad316178a184ab56d299b43",   # Uniswap Universal Router (v4)
                           "0x3fc91a3afd70395cd496c647d5a6cc9d4b2b7fad",   # Uniswap Universal Router
                           "0x2626664c2603336e57b271c5c0b26f421741e481"}), # Uniswap V3 SwapRouter02
    ),
}

# Public endpoints and their measured capabilities (2026-10-21):
#  robinhood: full archive; getLogs with address up to 10M blocks, without address up to 30k blocks.
#  bsc/base (publicnode): getLogs needs an address; ~8k blocks of recent state/logs; >20k results refused.
PUBLIC = {
    "robinhood": [dict(name="robinhood-public", url="https://rpc.mainnet.chain.robinhood.com", rps=8,
                       max_span=10_000_000, max_span_addressless=30_000)],
    "bsc": [dict(name="bsc-publicnode", url="https://bsc-rpc.publicnode.com", rps=5,
                 addressless=False, archive_depth=8_000, max_span=5_000)],
    "base": [dict(name="base-publicnode", url="https://base-rpc.publicnode.com", rps=5,
                  addressless=False, archive_depth=8_000, max_span=1_000)],
}


def enabled_chains():
    """EVM chains switched on: Robinhood always (V1 behaviour); BNB and Base with BSC_ENABLED / BASE_ENABLED."""
    on = ["robinhood"]
    for key, var in (("bsc", "BSC_ENABLED"), ("base", "BASE_ENABLED")):
        if os.environ.get(var, "false").strip().lower() in ("1", "true", "yes"):
            on.append(key)
    return on


def providers(key, env=os.environ, shared_throttle=None):
    """Provider list for a chain from the environment (see module doc). shared_throttle: the throttle of an existing
    adapter on the same first endpoint (Robinhood CRAWLER_RPC), so both stay under one rate limit."""
    net = NETWORKS[key]
    out = []
    own = [u.strip() for u in (env.get(f"{key.upper()}_RPC_URLS") or "").split(",") if u.strip()]
    if key == "robinhood" and (env.get("CRAWLER_RPC") or "").strip():
        own.insert(0, env["CRAWLER_RPC"].strip())
    rps = float(env.get(f"{key.upper()}_RPS") or 8)
    for i, url in enumerate(dict.fromkeys(own)):
        out.append(Provider(f"{key}-own-{i + 1}", url, rps=rps,
                            throttle=shared_throttle if i == 0 and shared_throttle is not None else None))
    alchemy = (env.get("ALCHEMY_API_KEY") or "").strip()
    if alchemy and net.alchemy_subdomain and not any("alchemy.com" in u for u in own):
        free = (env.get("ALCHEMY_TIER") or "free").strip().lower() == "free"
        out.append(Provider(f"{key}-alchemy", f"https://{net.alchemy_subdomain}.g.alchemy.com/v2/{alchemy}",
                            rps=float(env.get("ALCHEMY_RPS") or 10), max_span=10 if free else 10_000,
                            asset_transfers=True))
    for cfg in PUBLIC.get(key, []):
        if not any(cfg["url"] == u for u in own):
            out.append(Provider(**cfg))
    return out


_CLIENTS = {}


def client(key):
    """Process-wide EvmClient for a chain (lazily built from the environment)."""
    if key not in _CLIENTS:
        shared = None
        if key == "robinhood" and (os.environ.get("CRAWLER_RPC") or "").strip():
            from crawler.chains import robinhood   # same endpoint as the Pons adapter: share its rate limiter
            shared = robinhood._LIMIT
        _CLIENTS[key] = EvmClient(key, providers(key, shared_throttle=shared))
    return _CLIENTS[key]
