"""Funding source of an EVM wallet: its first native-coin deposit before a block (Phase 5 wallet forensics).

Read from Alchemy's Transfers API (category "external": plain value transfers, the way exchanges and wallets send
gas money). One call per wallet. A wallet funded only through a contract call (internal transfer — some bridges,
smart-wallet factories) or by a sponsored first transaction has no external deposit: found False, which is a fact,
not a failure. None means the history could not be read (no provider with the Transfers API, or an error).
"""
from crawler.chains.evm.client import NoProvider, RpcError


def available(client):
    return any(p.asset_transfers and not p.disabled for p in client.providers)


def first_funder(client, wallet, before_block):
    """{"funder", "found", "block", "tx", "amount"} — amount in native coin units (float); None — unreadable."""
    if not available(client) or before_block is None:
        return None
    try:
        rows, _ = client.asset_transfers(max_count=1, toAddress=wallet, fromBlock=0, toBlock=max(0, before_block - 1),
                                         category=["external"], order="asc", excludeZeroValue=True)
    except (NoProvider, RpcError):
        return None
    if not rows:
        return {"funder": None, "found": False, "block": None, "tx": None, "amount": None}
    r = rows[0]
    return {"funder": (r.get("from") or "").lower() or None, "found": bool(r.get("from")),
            "block": int(r["blockNum"], 16), "tx": r.get("hash"),
            "amount": float(r["value"]) if r.get("value") is not None else None}


def outgoing_count(client, addr, cap):
    """How many native-coin transfers addr has sent (external), counted up to cap: the hub check for a funder
    (detect.HUB_MIN_OUT: an exchange hot wallet or a bot sends thousands and must never link the wallets it funded).
    None — unreadable (the caller then treats the address as a hub: no data, no link)."""
    if not available(client):
        return None
    try:
        rows, more = client.asset_transfers(max_count=cap, fromAddress=addr, fromBlock=0, category=["external"],
                                            excludeZeroValue=True)
    except (NoProvider, RpcError):
        return None
    return cap if more else len(rows)
