# Phase 5: wallet forensics

Status: implemented. 732 offline tests pass, and it has been run against live mainnet tokens on Robinhood, BNB Chain
and Solana.

For every analysed holder (the top `SCAN_DEPTH`, 20 by default) a scan now also reports how they traded, what it
earned them, how long they have held, and who funded them. This is evidence shown next to the verdict. **It does not
change the score**: folding funding edges into the score is Phase 6 (relationship graph) and later scoring work.

## What each holder gets (`holders[].forensics`)

| field | meaning | source |
|---|---|---|
| `bought` / `sold` | tokens bought from and sold to the market (raw units, as strings) | the token's transfer history |
| `received` / `sent` | tokens received from and sent to other wallets (burns excluded) | same |
| `free_share` | share of the wallet's tokens that arrived as transfers, not buys | same |
| `cost_usd`, `proceeds_usd` | every buy and sell priced at the candle of its time | GeckoTerminal OHLCV (the chart's candles) |
| `holding_usd` | current balance at the current price | market data |
| `pnl_usd`, `multiple` | proceeds + holding − cost, and (proceeds + holding) / cost | — |
| `sell_multiple` | proceeds per unit of average buy cost of what was sold | — |
| `priced_share` | share of bought tokens whose buy could be priced | — |
| `hold_h`, `first_in_ts`, `exited` | time since first receipt; whether the wallet still holds | — |
| `funding` | first native-coin deposit before entry: `funder`, `block`, `tx`, `amount`, `shared_with` | see below |

The result also carries `forensics`: `priced`, `price_points`, `decimals`, `common_funders` (a funder of 2+ holders,
with those holders' combined share of float and of supply) and `funded_by_holder`.

## How it is computed, and why that way

- **No extra chain reads for P&L.** Trades come from the transfer history the scan has already read. Prices come from
  the same GeckoTerminal candles the chart uses, which are cached for 15 minutes.
- **Block times without extra calls.** A trade's block is converted to a time by interpolating between blocks whose
  times the scan already knows (launch and every entry). Block times on these chains are regular enough for 1-minute
  to 4-hour candles.
- **Estimates are never shown as exact.** USD values are candle closes, not fill prices. `pnl_usd` and `multiple`
  appear only when every buy and sell could be priced; otherwise `priced_share` says how much was. Tokens received
  for free carry no cost.
- **Funding on EVM chains** (`chains/evm/funding.py`): one Alchemy Transfers API call per wallet returns its first
  *external* native deposit before entry, which is how exchanges and wallets send gas money. A wallet funded only
  through a contract call (some bridges, smart-wallet factories) shows "no direct deposit". That is a fact, not a
  failure.
- **Funding on Solana:** Helius's parsed transaction history (one call returns 100 decoded transactions). The first
  SOL deposit is known only when the wallet's whole pre-entry history fits in that page; otherwise funding is
  "unread", never guessed.
- **Solana wallet histories now use the same parsed history**, one call per wallet where the RPC path needed about
  100. Measured on a pump.fun token: 9 of 20 wallets read, where before this change 0 of 20 were read on the Helius
  free tier.
- **Budget.** Funding lookups run alongside the wallet histories under the same scan scope (`FUNDING_WORKERS` = 4)
  and stop at the crawl deadline. Candles and decimals are read in a side thread. A failure anywhere in forensics
  leaves `forensics: null` and never costs the verdict.

## Live results (2026-10-22)

| token | finding |
|---|---|
| Robinhood, Uniswap launch (rugged: liquidity now $1.47) | 16 of the top 20 were funded by three addresses: one funded 6 holders (25.4% of float), another 7 (24.2%), a third 3 (12.3%). P&L: most lost 30–85% buying in, and a few exited above 1.2x |
| BNB Chain, Flap (on the bonding curve) | 20 of 20 funders read; one funder shared by 2 holders (14.7%); one holder funded by another holder. No USD P&L: GeckoTerminal has no candles for Flap curves |
| Solana, pump.fun | P&L priced on 93 candles (for example, one holder at 5.4x and another at 0.88x); 9 of 20 wallet histories read |

## Limits

- **Flap and four.meme tokens still on the bonding curve:** GeckoTerminal has no candles, so P&L shows amounts but no
  USD.
- **Solana funding:** read only for wallets with at most 100 transactions before entry. Active trading wallets stay
  "unread".
- **Exchanges are not labelled.** A "common funder" can be an exchange hot wallet. Labelling exchanges needs a
  maintained address list, which is planned with the Phase 11 memory brain.
- **Solana trades:** only the holder transactions that were sampled are seen, so Solana P&L can miss trades of very
  active holders.

## Security

- Every read is read-only. The Helius key travels only in the request URL and is never logged; error paths print
  exception types only.
- No new endpoint was added. Forensics ride on the existing result, `/api/result` and the stream's `result` frame.
