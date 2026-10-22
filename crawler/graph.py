"""Relationship graph of a scan (Phase 6): who is tied to whom among the analysed holders, and by what evidence.
Pure function over what the scan already computed: links (detect.find_links + funding), packs, operators, funding.

Nodes
  holder       — an analysed holder: rank, share of float, operator index, entry kind, flags
  funder       — an address that made a holder's first native deposit: how many holders it funded, its outgoing
                 count, hub (≥ detect.HUB_MIN_OUT outgoing, or unknown — then it links nobody), contract
  distributor  — a regular wallet that sent the token to 2+ holders
Edges (every edge carries its evidence: tx / block / time where known, and a confidence)
  same_tx      holder–holder  first entry in one transaction                       proven   1.0
  direct       holder–holder  a token transfer between them                       proven   1.0
  funded       holder–holder  one holder's first deposit came from the other      proven   0.95
  distributor  holder–holder  both received the token from one regular wallet     proven   0.9
  funder       holder–holder  both first funded by one non-hub address            proven   0.85
  pack         holder–holder  virgin wallets buying in one block with similar size pack     0.5
  funding      funder→holder  the deposit itself (also for hubs, at 0.2: shown, never linking)
  sent_token   distributor→holder
Confidence is the prior strength of each evidence class, not a fitted probability: it orders edges for display
and for the memory brain (Phase 11); operator merging still follows detect.operators (any proven/pack link).
"""
CONFIDENCE = {"same_tx": 1.0, "direct": 1.0, "funded": 0.95, "distributor": 0.9, "funder": 0.85, "pack": 0.5}
HUB_FUNDING_CONFIDENCE = 0.2
FUNDING_CONFIDENCE = 0.85
VERSION = 1


def build(chain, holders, sig, links, packs, ops, funders, funder_out, contracts, hub_min, clock=None):
    """{"version", "chain", "nodes", "edges", "stats"}. holders — [(wallet, balance, share)] analysed, largest first;
    sig — detect.wallet_signals; ops — detect.operators; funders — {wallet: forensics funding | None};
    funder_out — {funder: outgoing count} (absent: unknown); contracts — addresses with code."""
    when = (lambda b: None if b is None or clock is None else int(clock(b)))
    op_of = {w: i for i, o in enumerate(ops) for w in o["wallets"] if len(o["wallets"]) > 1}
    nodes, edges, seen = {}, [], set()
    for rank, (w, _, share) in enumerate(holders, 1):
        s = sig.get(w) or {}
        nodes[w] = {"id": w, "type": "holder", "rank": rank, "share": share, "operator": op_of.get(w),
                    "entry": s.get("kind"),
                    "flags": [k for k in ("is_deployer", "virgin", "sniper", "launch_bundle", "sold") if s.get(k)]}

    def edge(e):
        key = (min(e["a"], e["b"]), max(e["a"], e["b"]), e["kind"])
        if e["a"] != e["b"] and key not in seen:
            seen.add(key)
            edges.append(e)

    for l in links:
        e = {"a": l["a"], "b": l["b"], "kind": l["kind"], "level": l["level"], "via": l.get("via"),
             "confidence": CONFIDENCE.get(l["kind"], 0.5)}
        if l["kind"] == "same_tx":
            e["tx"] = l.get("via")
            e["block"] = (sig.get(l["a"]) or {}).get("block")
        elif l["kind"] in ("direct", "funded"):
            e["tx"] = l.get("via")
        elif l["kind"] == "pack":
            e["block"] = next((p["block"] for p in packs if l["a"] in p["wallets"]), None)
        elif l["kind"] == "distributor":
            d = l.get("via")
            if d:
                nodes.setdefault(d, {"id": d, "type": "distributor", "holders": 0})
                for h in (l["a"], l["b"]):
                    if ("sent_token", d, h) not in seen:
                        seen.add(("sent_token", d, h))
                        nodes[d]["holders"] += 1
                        edges.append({"a": d, "b": h, "kind": "sent_token", "level": "evidence", "via": None,
                                      "confidence": CONFIDENCE["distributor"]})
        e["ts"] = when(e.get("block"))
        edge(e)

    funded_by = {}
    for w, f in funders.items():
        if f and f.get("found") and f.get("funder"):
            funded_by.setdefault(f["funder"], []).append((w, f))
    for funder, ws in funded_by.items():
        n = funder_out.get(funder)
        hub = n is None or n >= hub_min
        if len(ws) < 2 and funder not in nodes:     # a lone, unrelated funder: shown on its holder only (forensics)
            continue
        if funder in nodes:
            nodes[funder].update({"funds": len(ws), "outgoing": n, "hub": hub, "contract": funder in contracts})
        else:
            nodes[funder] = {"id": funder, "type": "funder", "funds": len(ws), "outgoing": n, "hub": hub,
                             "contract": funder in contracts}
        for w, f in ws:
            edges.append({"a": funder, "b": w, "kind": "funding", "level": "evidence", "via": None,
                          "tx": f.get("tx"), "block": f.get("block"), "ts": when(f.get("block")),
                          "amount": f.get("amount"),
                          "confidence": HUB_FUNDING_CONFIDENCE if hub or funder in contracts else FUNDING_CONFIDENCE})
    merged = [o for o in ops if len(o["wallets"]) > 1]
    stats = {"holders": len(holders), "links": sum(1 for e in edges if e["level"] in ("proven", "pack")),
             "operators_merged": len(merged), "largest_operator_share": max((o["share"] for o in merged), default=0.0),
             "funders": sum(1 for v in nodes.values() if v["type"] == "funder" or "funds" in v),
             "hubs": sum(1 for v in nodes.values() if v.get("hub"))}
    return {"version": VERSION, "chain": chain, "nodes": list(nodes.values()), "edges": edges, "stats": stats}
