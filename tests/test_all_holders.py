"""Every holder of a token: engine holders_out, /api/holders paging, Solana's DAS list (no network)."""
import os, unittest
from unittest import mock

import fakes
from crawler import engine, server
from crawler.chains import solana


class TestEngineHoldersOut(unittest.TestCase):

    def test_all_balances_largest_first(self):
        out = []
        with fakes.patched():
            res = engine.scan(fakes.TOKEN, holders_out=out)
        self.assertEqual(len(out), res["holders_total"])
        self.assertEqual([b for _, b in out], sorted((b for _, b in out), reverse=True))
        self.assertTrue({h["wallet"] for h in res["holders"]} <= {w for w, _ in out})
        self.assertTrue(res["holders_complete"])          # EVM: rebuilt from the whole transfer history


class TestHoldersPage(unittest.TestCase):

    def job(self):
        rows = [(f"w{i}", 1000 - i) for i in range(250)]
        res = {"circulating": sum(b for _, b in rows), "supply": 10 ** 6, "holders_complete": True,
               "holders": [{"wallet": "w0"}, {"wallet": "w1"}]}
        return {"done": True, "error": None, "result": res, "holders_all": rows}

    def test_paging(self):
        j = self.job()
        p = server.holders_page(j, 0, 100)
        self.assertEqual((p["total"], len(p["items"]), p["complete"]), (250, 100, True))
        self.assertEqual(p["items"][0] | {"share": None, "share_supply": None},
                         {"rank": 1, "wallet": "w0", "balance": "1000", "share": None, "share_supply": None,
                          "analysed": True})
        last = server.holders_page(j, 200, 100)
        self.assertEqual((len(last["items"]), last["items"][-1]["rank"]), (50, 250))
        self.assertFalse(last["items"][0]["analysed"])

    def test_not_done(self):
        p = server.holders_page({"done": False, "error": None, "result": None}, 0, 10)
        self.assertEqual((p["done"], p["items"], p["total"]), (False, [], None))


class TestSolanaAllAccounts(unittest.TestCase):

    def pages(self, n_accounts):
        accs = [{"owner": f"o{i % (n_accounts - 3)}", "amount": 10 + i} for i in range(n_accounts)]   # owners with 2 accounts
        def rpc(method, params):
            self.assertEqual(method, "getTokenAccounts")
            k = (params["page"] - 1) * solana.DAS_PAGE
            return {"token_accounts": accs[k:k + solana.DAS_PAGE]}
        return rpc

    def test_pages_until_short_page_and_sums_owners(self):
        with mock.patch.object(solana, "_HELIUS", True), mock.patch.object(solana, "rpc", side_effect=self.pages(2500)):
            owners, complete = solana.all_accounts("M")
        self.assertTrue(complete)
        self.assertEqual(len(owners), 2497)
        self.assertEqual(owners["o0"], 10 + (10 + 2497))

    def test_cap_marks_incomplete(self):
        with mock.patch.object(solana, "_HELIUS", True), mock.patch.object(solana, "HOLDERS_MAX", 1000), \
                mock.patch.object(solana, "rpc", side_effect=self.pages(5000)):
            owners, complete = solana.all_accounts("M")
        self.assertFalse(complete)

    def test_not_helius_none(self):
        with mock.patch.object(solana, "_HELIUS", False):
            self.assertIsNone(solana.all_accounts("M"))

    def test_supply_base_excludes_infra_found_in_top(self):
        rows = [{"account": "a_curve", "owner": "CURVE", "amount": 500, "pda": True},
                {"account": "a1", "owner": "w1", "amount": 300, "pda": False}]
        with mock.patch.object(solana, "token_supply", return_value=1000), \
                mock.patch.object(solana, "top_accounts", return_value=rows), \
                mock.patch.object(solana, "excluded_addresses", return_value=set()), \
                mock.patch.object(solana, "all_accounts", return_value=({"CURVE": 500, "w1": 300, "w2": 200}, True)):
            base = solana.supply_base("M", {"curve": "CURVE"})
        self.assertEqual(base, {"supply": 1000, "circulating": 500, "holders_total": 2,
                                "balances": {"w1": 300, "w2": 200}, "complete": True})


if __name__ == "__main__":
    unittest.main()
