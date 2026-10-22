"""Which reader a wallet history / Solana batch takes, from the providers' measured capabilities (no network)."""
import importlib, os, unittest
from types import SimpleNamespace
from unittest import mock

import fakes  # noqa: F401  (dummy RPC env)
from crawler.chains.evm.client import Provider
from crawler.chains.evm.networks import NETWORKS
from crawler.chains.evm.token import EvmTokenAdapter


def adapter(*providers):
    return EvmTokenAdapter(NETWORKS["bsc"], SimpleNamespace(providers=list(providers), REQUESTS=[0], RATE_LIMITED=[0]),
                           plugin=type("P", (), {"NAME": "test"}))


class TestWalletHistoryReader(unittest.TestCase):

    def test_narrow_alchemy_spans_use_transfers_api(self):
        """BNB as measured: Alchemy free (10-block spans + Transfers API), dataseed (no eth_getLogs), publicnode."""
        a = adapter(Provider("alchemy", "u", max_span=10, asset_transfers=True),
                    Provider("dataseed", "u", deny=["eth_getLogs"]),
                    Provider("publicnode", "u", addressless=False, archive_depth=8000))
        self.assertFalse(a._wide_wallet_logs())
        self.assertTrue(a._has_asset_transfers())

    def test_full_archive_log_node_scans_by_topic(self):
        """Robinhood-style public node: address-less logs over the whole history, no span limit."""
        self.assertTrue(adapter(Provider("node", "u"))._wide_wallet_logs())

    def test_disabled_provider_ignored(self):
        p = Provider("node", "u")
        p.disabled = True
        self.assertFalse(adapter(p)._wide_wallet_logs())


class TestSolanaBatchWeight(unittest.TestCase):

    def load(self, env):
        from crawler.chains import solana
        with mock.patch.dict(os.environ, env, clear=False):
            for k in ("SOLANA_RPC", "SOLANA_BATCH_WEIGHT", "SOLANA_TX_BATCH", "SOLANA_RPS", "HELIUS_API_KEY"):
                if k not in env:
                    os.environ.pop(k, None)
            return importlib.reload(solana)

    def tearDown(self):
        from crawler.chains import solana
        importlib.reload(solana)

    def test_helius_counts_batch_items(self):
        s = self.load({"HELIUS_API_KEY": "k"})
        self.assertEqual((s.BATCH_WEIGHT, s.TX_BATCH, s.RPS), ("item", 10, 6))

    def test_other_endpoint_counts_requests(self):
        s = self.load({"SOLANA_RPC": "https://solana-mainnet.example/v2/k"})
        self.assertEqual((s.BATCH_WEIGHT, s.TX_BATCH, s.RPS), ("request", 50, 8))

    def test_overrides(self):
        s = self.load({"HELIUS_API_KEY": "k", "SOLANA_BATCH_WEIGHT": "request", "SOLANA_TX_BATCH": "25"})
        self.assertEqual((s.BATCH_WEIGHT, s.TX_BATCH), ("request", 25))

    def test_weight_reaches_limiter(self):
        s = self.load({"HELIUS_API_KEY": "k"})
        with mock.patch.object(s.priority, "rate_limit") as rl:
            s._rate_limit(10)
        self.assertEqual(rl.call_args.kwargs["n"], 10)


if __name__ == "__main__":
    unittest.main()


class TestSolanaTxBudget(unittest.TestCase):

    def test_budget_samples_oldest_and_newest(self):
        from crawler.chains import solana
        sigs = {f"acc{i}": [{"signature": f"s{i}-{k}", "slot": k} for k in range(30)] for i in range(4)}
        rows = [{"account": a, "owner": f"o{a}", "pda": False} for a in sigs]
        with mock.patch.object(solana, "top_accounts", return_value=rows), \
                mock.patch.object(solana, "_account_history", return_value={a: (v, False) for a, v in sigs.items()}), \
                mock.patch.object(solana, "get_transactions", side_effect=lambda ss: {s: None for s in ss}) as gt:
            sampled = []
            solana.get_token_transfers("T", 0, tx_budget=20, sampled=sampled)
            asked = gt.call_args.args[0]
            self.assertEqual(sampled, [True])
            self.assertEqual(len(asked), 20)                        # 4 accounts × 5
            self.assertIn("s0-0", asked); self.assertIn("s0-29", asked)   # entry and latest kept
            sampled = []
            solana.get_token_transfers("T", 0, tx_budget=None, sampled=sampled)
            self.assertEqual((sampled, len(gt.call_args.args[0])), ([], 120))

    def test_no_deadline_no_cap(self):
        from crawler.chains import solana
        self.assertIsNone(solana._tx_budget(None))
