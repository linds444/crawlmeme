"""Market data fallback chain (GeckoTerminal → DexScreener → DexPaprika) and multi-chain address handling. No network."""
import unittest
from unittest import mock

import fakes  # noqa: F401
from crawler import market

TOK = "0x" + "ab" * 20
DP = {"name": "Claude", "symbol": "Claude",
      "summary": {"price_usd": 0.0001, "fdv": 10_000_000.0, "liquidity_usd": 9_000_000.0, "24h": {"volume_usd": 1e6}}}


class TestFallback(unittest.TestCase):

    def setUp(self):
        market.clear_cache()

    def test_dexpaprika_when_gt_and_dexscreener_fail(self):
        def get(url, timeout):
            self.assertIn("/networks/base/tokens/" + TOK, url)
            self.assertLessEqual(timeout, market.DP_TIMEOUT)          # one bounded attempt
            return DP
        with mock.patch.object(market, "_gt", side_effect=TimeoutError("gt down")), \
                mock.patch.object(market, "_ds", side_effect=TimeoutError("dexscreener down")), \
                mock.patch.object(market, "_get", side_effect=get):
            out = market.fetch_market(TOK, "base", budget=3.0)
        self.assertEqual(out["source"], "dexpaprika")
        self.assertEqual(out["price_usd"], 0.0001)
        self.assertIsNone(out["age_days"])                       # no age: never "too established" from DexPaprika

    def test_dexpaprika_without_price_is_no_data(self):
        with mock.patch.object(market, "_get", return_value={"summary": {"price_usd": None}}):
            with self.assertRaises(LookupError):
                market._dp_market(TOK, "bsc")

    def test_checksummed_dexscreener_address_matches(self):
        """DexScreener returns checksummed addresses; EVM tokens must match case-insensitively on every chain."""
        pair = {"chainId": "base", "baseToken": {"address": TOK.upper().replace("0X", "0x"), "name": "T", "symbol": "T"},
                "quoteToken": {"symbol": "WETH"}, "priceUsd": "0.5", "marketCap": 100, "liquidity": {"usd": 10},
                "pairAddress": "0xpool"}
        with mock.patch.object(market, "_ds", return_value=[pair]):
            out = market._ds_market(TOK, "base")
        self.assertEqual(out["price_usd"], 0.5)
        self.assertEqual(out["mcap_usd"], 100.0)

    def test_native_wrapped_for_every_evm_chain(self):
        for net in ("robinhood", "bsc", "base"):
            self.assertTrue(market.NATIVE_WRAPPED[net].startswith("0x"))


if __name__ == "__main__":
    unittest.main()
