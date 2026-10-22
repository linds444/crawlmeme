# Phase 9: sell impact and «probably rug»

Status: implemented. 782 offline tests pass (22 new). Checked with live scans on Solana, Base, BNB and Robinhood.

## What it answers

What the price would do if a given set of holders sold everything they can sell now into today's liquidity.

Every number is a **conditional scenario, not a prediction**. Both the page and the chart say so.

## Pricing: one model per venue (`crawler/impact.py`)

The constant-product formula `drop = 1 − (R / (R + q))²` is used only where it is the pool's actual pricing model.

| model | used for | how |
|---|---|---|
| `constant_product` | Uniswap / PancakeSwap V2 pairs, Flap pairs, PumpSwap, Raydium AMM v4 / CPMM, Meteora DAMM | the formula on the pool's token reserve |
| `bonding_curve` | pump.fun, Flap and Pons curves | the formula on the curve's **virtual** token reserve (what it prices off) |
| `quote_curve` | Uniswap V3 / V4, Clanker v4, Bankr, the four.meme curve | on-chain quoter calls at the current block (QuoterV2, V4Quoter, four.meme `trySell`) up to 50% of float, linear between quotes |
| `approximate` | Pons tokens after migration (V4 pool manager balance); Raydium CLMM, Orca Whirlpool, Meteora DLMM / DBC, Raydium LaunchLab, Meteora DAMM v2 | the formula on the pool's token balance, flagged `exact: false` and labelled "approximate" on the page |

**Past the last quote:**
- If the quoted curve slopes, a constant-product tail is fitted through the last point.
- If the pool is deeper than anything quoted, the result is a lower bound: "≥ −x%", or "> y%" for depth.

**Aggregate liquidity:**
- A router splits a large sale until every pool sits at the same price. So the aggregate drop `d` solves `Σ absorb_i(d) = q`, where `absorb_i` inverts each pool's own model.
- For constant-product pools this equals one pool holding the summed reserves (tested).
- DEX-launched EVM tokens price up to 3 pools, the deepest first. Solana prices each DEX pool among the top token accounts.
- With a single pool, the result equals V1's number exactly. Verified live: Flap BNB −12.4%, Uniswap V2 on Robinhood −31.6%, Flap pair −29.9%.

## Sell taxes

| source | chains | side |
|---|---|---|
| launchpad state | Flap (Robinhood, BNB) | after graduation, in tokens (less reaches the pair); on the curve, in the quote coin (reduces proceeds only) |
| Token-2022 transfer fee | Solana | in tokens, capped by the mint's maximum fee |
| observed | other EVM tokens | from the scan's own sell transfers (see below) |

How the observed tax is estimated:
- **Method.** Each sell where the seller's tokens split between the market and one fixed receiver gives a rate of `T / (A + T)`. The tax is the median rate.
- **Rules.**
  - It needs at least 3 sells.
  - The same receiver must appear in at least 60% of them.
  - The median must be at least 0.1%.
  - The token contract selling its collected tax is not counted as a sale.
- **How it is shown.** The page marks it as an estimate.
- **Verified.** On BNBCAT (BNB) it read 4.5% from 1,344 sells, and GoPlus independently reports a 4.5% sell tax.
- **Limit.** "None found" is not proof that there is no tax.

## Scenarios (`result.sell_impact.scenarios`)

| scenario | wallets |
|---|---|
| `largest_wallet` | the biggest analysed holder |
| `largest_operator` | the biggest group of 2+ linked wallets (proven links or a pack), unweighted, with its level |
| `launch_bundle` | holders that entered in the launch bundle window, plus snipers on chains that count them |
| `suspicious_combined` | linked, received by transfer, fresh, bundle / snipers not sold, deployer, and Phase 7 crew members |

How the scenarios avoid double counting:
- Within a scenario, each wallet counts once.
- In `suspicious_combined`, a wallet goes to its first matching reason, so the parts add up exactly to the total.
- Scenarios overlap with each other on purpose, because each answers its own question.
- Vested tokens that are not yet unlocked count toward a wallet's share, but not toward what it can sell now.

Each scenario reports:
- share of float and of supply, and the sellable share;
- price impact and average slippage (the execution shortfall against the spot price, integrated along the pricing curve);
- value at spot, estimated proceeds after slippage and tax, and the price level after the sale.

The block also reports:
- **liquidity:** every venue with its model and exactness, and whether pools were combined;
- **tax:** the sell tax and its source;
- **depth:** the share of float needed to move the price −2%, −10%, −25% and −50%, adjusted for token-side tax;
- **slippage:** the result of selling 0.1%, 0.5%, 1% and 5% of float.

## «Probably rug» marker

The behavioural criteria are V1's, unchanged:
- the verdict is DANGER from a hard rule, not a soft one;
- liquidity is reliably measured;
- holder signals are not limited;
- the drop is at least 40%.

What changed:
- The drop now comes from `suspicious_combined` across all priced pools, with tax applied.
- Deployer and crew wallets now count as suspicious supply.
- `result.rug` carries `conditional: true`, `basis` and `aggregated`.
- The red dashed arrow and the explanation under the chart now say "a conditional scenario, not a prediction".
- If the new block fails, the V1 projection is kept.

The `crawlmeme-1` score is unchanged: it still prices the biggest operator on the deepest pool, as V1 did. Including
the extra pools in the score would be a model change, and would go into a future shadow model.

## Page

A new "sell impact" panel sits above "how sure is this". It shows:
- a scenario table (holds, price impact, average slippage, proceeds);
- the venues it was priced with;
- the sell tax and its source;
- depth;
- a slippage row.

## Cost

No extra RPC calls on Pons, Flap, Clanker, four.meme, Bankr or Solana: the block reuses what the scan already read.
DEX-launched EVM tokens with more than one pool cost 2–3 eth_calls per extra pool, for at most 2 extra pools.

## Limits

- A live multi-pool token was not scanned end to end: the candidates found were old tokens that were still being indexed. The pool aggregation is covered by unit tests.
- Approximate venues (concentrated pools without a quoter) can over- or under-state depth, and are labelled as approximate.
- Observed taxes are an estimate. A tax that applies only to some wallets (for example, whitelists) or changes over time shows up as its median.
