"""Phase 6: funding links (with the hub check) and the relationship graph (crawler/graph.py). No network."""
import os, unittest
from unittest import mock

import fakes
from crawler import detect as d
from crawler import engine, graph


def fund_from(mapping):
    """funding reader: wallet → funder address (None: no deposit found)."""
    return lambda w, b: ({"funder": mapping[w], "found": True, "block": 5, "tx": "0xf" + w[-4:], "amount": 0.1}
                         if mapping.get(w) else None)


class TestFundingLinks(unittest.TestCase):

    def scan(self, mapping, outgoing, env=None):
        with fakes.patched(), mock.patch.object(engine, "_funding_reader", return_value=fund_from(mapping)), \
                mock.patch.object(engine, "_hub_counter", return_value=lambda a, cap: outgoing.get(a)), \
                mock.patch.dict(os.environ, env or {}):
            return engine.scan(fakes.TOKEN)

    def ws(self):
        with fakes.patched():
            r = engine.scan(fakes.TOKEN)
        return [h["wallet"] for h in r["holders"]]

    def test_shared_non_hub_funder_merges_holders(self):
        w = self.ws()
        r = self.scan({w[3]: "0xdev", w[5]: "0xdev"}, {"0xdev": 3})
        kinds = {(l["kind"], frozenset((l["a"], l["b"]))) for l in r["links"]}
        self.assertIn(("funder", frozenset((w[3], w[5]))), kinds)
        self.assertTrue(any({w[3], w[5]} <= set(o["wallets"]) for o in r["operators"]))
        g = r["graph"]
        self.assertEqual(next(n for n in g["nodes"] if n["id"] == "0xdev")["hub"], False)
        self.assertTrue(any(e["kind"] == "funder" and e["confidence"] == graph.CONFIDENCE["funder"] for e in g["edges"]))

    def test_hub_or_unknown_funder_never_links(self):
        w = self.ws()
        for out in ({"0xcex": d.HUB_MIN_OUT}, {}):          # measured hub, then unknown outgoing count
            r = self.scan({w[3]: "0xcex", w[5]: "0xcex"}, out)
            self.assertFalse([l for l in r["links"] if l["kind"] == "funder"])
            node = next(n for n in r["graph"]["nodes"] if n["id"] == "0xcex")
            self.assertTrue(node["hub"])
            self.assertTrue(all(e["confidence"] == graph.HUB_FUNDING_CONFIDENCE
                                for e in r["graph"]["edges"] if e["kind"] == "funding"))

    def test_holder_funded_by_holder(self):
        w = self.ws()
        r = self.scan({w[6]: w[2]}, {w[2]: 4})
        self.assertIn(("funded", w[2], w[6]), {(l["kind"], l["a"], l["b"]) for l in r["links"]})

    def test_switch_off(self):
        w = self.ws()
        r = self.scan({w[3]: "0xdev", w[5]: "0xdev"}, {"0xdev": 3}, env={"FUNDING_LINKS": "off"})
        self.assertFalse([l for l in r["links"] if l["kind"] in ("funder", "funded")])
        self.assertFalse(r["use_funding"])


class TestGraphBuild(unittest.TestCase):

    def test_nodes_edges_and_provenance(self):
        holders = [("h1", 50, 0.5), ("h2", 30, 0.3), ("h3", 20, 0.2)]
        sig = {"h1": {"kind": "buy", "block": 10, "sniper": True}, "h2": {"kind": "buy", "block": 10},
               "h3": {"kind": "transfer", "block": 12}}
        links = [{"a": "h1", "b": "h2", "kind": "same_tx", "level": "proven", "via": "0xtx"},
                 {"a": "h2", "b": "h3", "kind": "distributor", "level": "proven", "via": "dist"}]
        ops = d.operators(holders, links)
        funders = {"h1": {"funder": "cex", "found": True, "block": 3, "tx": "0xa", "amount": 1.0},
                   "h3": {"funder": "cex", "found": True, "block": 4, "tx": "0xb", "amount": 1.0}, "h2": None}
        g = graph.build("bsc", holders, sig, links, [], ops, funders, {"cex": 5000}, set(), d.HUB_MIN_OUT,
                        clock=lambda b: 1000 + b)
        types = {n["id"]: n["type"] for n in g["nodes"]}
        self.assertEqual(types, {"h1": "holder", "h2": "holder", "h3": "holder", "dist": "distributor",
                                 "cex": "funder"})
        same = next(e for e in g["edges"] if e["kind"] == "same_tx")
        self.assertEqual((same["tx"], same["block"], same["ts"], same["confidence"]), ("0xtx", 10, 1010, 1.0))
        self.assertEqual(sum(1 for e in g["edges"] if e["kind"] == "sent_token"), 2)
        self.assertEqual({e["confidence"] for e in g["edges"] if e["kind"] == "funding"},
                         {graph.HUB_FUNDING_CONFIDENCE})
        self.assertEqual((g["stats"]["operators_merged"], g["stats"]["hubs"]), (1, 1))
        self.assertEqual(next(n for n in g["nodes"] if n["id"] == "h1")["flags"], ["sniper"])

    def test_lone_unrelated_funder_left_out(self):
        holders = [("h1", 1, 1.0)]
        g = graph.build("base", holders, {"h1": {}}, [], [], d.operators(holders, []),
                        {"h1": {"funder": "x", "found": True}}, {}, set(), d.HUB_MIN_OUT)
        self.assertEqual([n["id"] for n in g["nodes"]], ["h1"])


if __name__ == "__main__":
    unittest.main()
