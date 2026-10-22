"""Phase 5 wallet forensics (crawler/forensics.py, chains/evm/funding.py, Solana parsed history): no network."""
import unittest
from types import SimpleNamespace
from unittest import mock

import fakes
from crawler import engine, forensics
from crawler.chains import solana
from crawler.chains.evm import funding
from crawler.chains.evm.client import Provider

M, W1, W2, W3, X = "pool", "w1", "w2", "w3", "x"


class TestClockAndPrice(unittest.TestCase):

    def test_clock_interpolates_and_extrapolates(self):
        c = forensics.make_clock([(100, 1000), (200, 1200)])
        self.assertEqual((c(150), c(300), c(50)), (1100, 1400, 900))
        self.assertEqual(forensics.make_clock([(5, 77)])(9), 77)
        self.assertIsNone(forensics.make_clock([]))

    def test_price_by_candle(self):
        p = forensics.price_lookup([[1000, 1, 1, 1, 2.0, 0], [1060, 1, 1, 1, 3.0, 0]])
        self.assertEqual((p(999), p(1000), p(1059), p(1060), p(9999)), (None, 2.0, 2.0, 3.0, 3.0))


class TestFlowsAndPnl(unittest.TestCase):

    def transfers(self):
        T = lambda frm, to, amt, block: {"frm": frm, "to": to, "amount": amt, "block": block}
        return [T(M, W1, 100, 10), T(M, W1, 100, 20), T(W1, M, 50, 30), T(X, W2, 40, 15), T(W2, X, 10, 25),
                T(W2, "burn", 5, 26)]

    def test_flows(self):
        f = forensics.flows(self.transfers(), [W1, W2], {M}, burn={"burn"})
        self.assertEqual((f[W1]["bought"], f[W1]["sold"], f[W1]["first_in"], f[W1]["last_out"]), (200, 50, 10, 30))
        self.assertEqual((f[W2]["received"], f[W2]["sent"], f[W2]["bought"]), (40, 10, 0))   # burn is not «sent»

    def test_pnl_fully_priced(self):
        f = forensics.flows(self.transfers(), [W1], {M})[W1]
        clock = lambda b: b * 10                      # block 10 → ts 100 ...
        price = forensics.price_lookup([[0, 0, 0, 0, 1.0, 0], [250, 0, 0, 0, 4.0, 0]])
        r = forensics.wallet_pnl(f, 150, clock, price, 2.0, 0, now=3700)
        self.assertEqual((r["cost_usd"], r["proceeds_usd"], r["holding_usd"]), (200.0, 200.0, 300.0))
        self.assertEqual((r["pnl_usd"], r["multiple"], r["sell_multiple"]), (300.0, 2.5, 4.0))
        self.assertEqual((r["priced_share"], r["hold_h"], r["exited"]), (1.0, 1.0, False))

    def test_unpriced_buy_never_gives_a_pnl(self):
        f = forensics.flows(self.transfers(), [W1], {M})[W1]
        price = forensics.price_lookup([[150, 0, 0, 0, 1.0, 0]])     # first buy (ts 100) before the candles
        r = forensics.wallet_pnl(f, 150, lambda b: b * 10, price, 1.0, 0, now=1000)
        self.assertEqual((r["priced_share"], r["pnl_usd"], r["multiple"]), (0.5, None, None))

    def test_no_candles(self):
        f = forensics.flows(self.transfers(), [W1], {M})[W1]
        r = forensics.wallet_pnl(f, 150, None, forensics.price_lookup([]), None, 18, now=0)
        self.assertEqual((r["cost_usd"], r["holding_usd"], r["hold_h"]), (0.0, None, None))


class TestFundingGroups(unittest.TestCase):

    def test_common_and_holder_funded(self):
        fu = {W1: {"funder": "F", "found": True}, W2: {"funder": "F", "found": True},
              W3: {"funder": W1, "found": True}, X: None}
        common, by_holder = forensics.funding_groups(fu, {W1: 0.3, W2: 0.1, W3: 0.05}, 0.5, [W1, W2, W3, X])
        self.assertEqual(len(common), 1)
        self.assertEqual((common[0]["wallets"], round(common[0]["share"], 6), round(common[0]["share_supply"], 6)),
                         ([W1, W2], 0.4, 0.2))
        self.assertEqual(by_holder, [{"wallet": W3, "funder": W1}])


class TestEvmFunding(unittest.TestCase):

    def client(self, rows, transfers_api=True):
        c = SimpleNamespace(providers=[Provider("p", "u", asset_transfers=transfers_api)])
        c.asset_transfers = mock.Mock(return_value=(rows, False))
        return c

    def test_first_external_deposit(self):
        c = self.client([{"from": "0xABC", "blockNum": "0x10", "hash": "0xh", "value": 0.5}])
        self.assertEqual(funding.first_funder(c, "0xw", 100),
                         {"funder": "0xabc", "found": True, "block": 16, "tx": "0xh", "amount": 0.5})
        kw = c.asset_transfers.call_args.kwargs
        self.assertEqual((kw["toBlock"], kw["category"], kw["order"]), (99, ["external"], "asc"))

    def test_none_found_vs_unreadable(self):
        self.assertFalse(funding.first_funder(self.client([]), "0xw", 100)["found"])
        self.assertIsNone(funding.first_funder(self.client([], transfers_api=False), "0xw", 100))


class TestSolanaParsedHistory(unittest.TestCase):

    def page(self, txs, complete=True):
        return mock.patch.object(solana, "enhanced_before", return_value=(txs, complete))

    def test_distinct_tokens_from_swaps(self):
        txs = [{"type": "SWAP", "tokenTransfers": [{"mint": f"m{i}", "toUserAccount": "W"}]} for i in range(2)]
        txs.append({"type": "TRANSFER", "tokenTransfers": [{"mint": "gift", "toUserAccount": "W"}]})
        with self.page(txs):
            self.assertEqual(solana._distinct_enhanced("W", "sig", "T", 4), 2)
        with self.page(txs, complete=False):
            self.assertIsNone(solana._distinct_enhanced("W", "sig", "T", 4))      # longer history, under cap

    def test_first_funder_oldest_deposit(self):
        txs = [{"slot": 9, "signature": "s9", "nativeTransfers": [{"fromUserAccount": "B", "toUserAccount": "W",
                                                                   "amount": 10 ** 9}]},
               {"slot": 5, "signature": "s5", "nativeTransfers": [{"fromUserAccount": "A", "toUserAccount": "W",
                                                                   "amount": 5 * 10 ** 8}]}]
        with self.page(txs), mock.patch.dict(solana._ENTRY_SIG, {("W", 100): "entry"}):
            self.assertEqual(solana.first_funder("W", 100),
                             {"funder": "A", "found": True, "block": 5, "tx": "s5", "amount": 0.5})
        with self.page(txs, complete=False), mock.patch.dict(solana._ENTRY_SIG, {("W", 100): "entry"}):
            self.assertIsNone(solana.first_funder("W", 100))

    def test_no_key_no_call(self):
        with mock.patch.object(solana, "HELIUS_KEY", ""), mock.patch("urllib.request.urlopen") as up:
            self.assertIsNone(solana.enhanced_before("W", "sig"))
        up.assert_not_called()


class TestEngineForensics(unittest.TestCase):

    def test_scan_carries_forensics_and_funding(self):
        fund = lambda w, b: {"funder": "hub", "found": True, "block": 1, "tx": "t", "amount": 1.0}
        with fakes.patched(), mock.patch.object(engine, "_funding_reader", return_value=fund):
            r = engine.scan(fakes.TOKEN)
        self.assertEqual(r["forensics"]["version"], forensics.VERSION)
        self.assertEqual(r["forensics"]["funding_read"], len([h for h in r["holders"]
                                                              if h["signals"]["kind"] is not None]))
        self.assertTrue(r["forensics"]["common_funders"])
        self.assertIn("hold_h", r["holders"][0]["forensics"])

    def test_forensics_failure_keeps_the_verdict(self):
        with fakes.patched(), mock.patch.object(forensics, "build", side_effect=RuntimeError("boom")):
            r = engine.scan(fakes.TOKEN)
        self.assertIsNone(r["forensics"])
        self.assertIsNotNone(r["band"])


if __name__ == "__main__":
    unittest.main()
