# Phase 10: launch bundle detection and traced bundle supply

Status: implemented. 797 offline tests pass (14 new). Checked with live scans on Robinhood, BNB and Solana.

## What changed from V1

V1's "launch bundle" flag only looks at the **current top holders**. Phase 10 starts from the **launch transactions
themselves** and follows the supply those wallets took through every later transfer. So it still finds launch-originated
exposure after the original wallets have sold, split their supply, or dropped out of the top list.

## Launch participants (`crawler/bundles.py`)

**Window.** The adapter's block window (the launch block on EVM, 2 slots on Solana), extended to the first 3 seconds
when that is longer:
- a block lasts 2 s on Base;
- it lasts 0.45 s on BNB;
- on Robinhood it is a fraction of that.

The time window is used only when the scan has at least two block-time anchors, so a single known block time cannot
stretch it.

**Who counts.** Every address whose token balance grew in a launch-window transaction is a participant. Market and
infrastructure addresses don't count, and nor do pass-through routers, which forward everything in the same
transaction.

| kind | meaning |
|---|---|
| `buy` | the transaction took tokens out of the market (curve, pool, router) |
| `dev_transfer` | the deployer handed supply out in it |
| `transfer` | received from another address inside the window |
| `deployer` | what the deployer still holds when the window closes (its kept allocation) |
| `sniper` | a buy within 30 s of launch, on chains that count snipers |

**Coordination signals:**
- several participants funded by **one transaction**;
- 3+ launch-block buys of **near-equal size** (±30% of their median);
- **shared non-hub funders** (Phase 6 funding with the hub check);
- **transfers between launch wallets**.

## Tracing (EVM)

The trace replays the token's full transfer history in **pro-rata** mode:
- **Moving supply.** Every transfer moves bundle supply in proportion to the sender's bundle share of its balance.
- **Sinks.** Pools and routers count as "sold". Burn addresses count as "burned". Lockers and other infrastructure count as "infra". Tokens bought back out of a pool are new money, not bundle supply.
- **Hand-offs inside the window** count once: a participant's new bundle supply excludes anything that arrived already tainted from another participant.
- **Purity** is a wallet's bundle tokens divided by its balance. A wallet with purity of at least 50% holds **concentrated** bundle supply, meaning still in bundle hands. Supply below that is **diluted** and is reported as such.
- **Conservation is checked on every scan:** initial = held now + sold + burned + infra. On live tokens the residual was about 1e-15 of supply, which is floating-point rounding. Transfers that spend more than the replayed balance (history gaps) are counted and lower the confidence.

What a result carries (`result.bundle`):
- the initial allocation;
- the supply still traceable (concentrated and diluted), and how much of it sits **outside the analysed top holders**;
- how much was sold, burned or locked;
- how many wallets hold bundle supply now, how many of them are brand-new wallets, and the maximum number of hops;
- per participant: what it took, what it holds, what it sold and moved, and its status (holding, sold part, sold all, moved on, burned);
- the current holders of bundle supply, with purity, whether they are in the top list, and their hop count;
- a confidence label and its limits.

| confidence | when |
|---|---|
| high | full transfer history, no balance gaps, at most 25% of the traced supply diluted |
| medium | full history, but diluted or with gaps |
| low | partial history (windowed or still indexing), or Solana |

## Solana

The scan reads the launch transactions' balance changes from the curve's oldest signatures. `get_launch` has already
read those signatures, so this costs at most one batch, and only when 2+ seconds of scan time remain.

From the complete holder list it also reports exactly what the launch wallets still hold now.

Onward transfers are **not traced** on Solana: the scan doesn't have every holder's history. The supply that left the
launch wallets is attributed only where a current top holder received it from a participant, which gives a lower
bound. Confidence is always "low", and the page says why.

## Sell impact

A new scenario, **"launch supply, traced"**, prices the concentrated bundle supply wherever it sits now, using the
Phase 9 venues and taxes.

## Live checks

- **Robinhood (Pons sample):** 38 launch wallets (1 buy, 37 snipers) took 46% of supply. 43% has since been sold and 2.5% is still traceable. Conservation residual: 5e-16.
- **BNB (SHOPINX, PancakeSwap V3):** the deployer kept 25% and dispersed it into **217 new wallets**, one hop away, most outside the top list. All of it is still concentrated, and selling it would empty the pool (−100%).
- **BNB (Flap sample):** no wallet took supply in the launch window or as a sniper, and the page says so.
- **Solana (pump.fun sample):** the deployer bought 79.7% in the create transaction and holds none of it now. Confidence is low (onward transfers not traced).

## Cost

- **EVM:** no extra requests; the trace reuses the scan's transfer history. Replaying 30k transfers takes well under a second.
- **Solana:** at most one transaction batch, and usually none, because `get_launch` has already cached those transactions.

## Limits

- Pro-rata attribution is a model. When bundle supply mixes into a wallet that holds other supply, the split is proportional, not proven, which is why diluted supply is shown separately.
- Partial histories (Bankr and Flap windowed reads, tokens still being indexed) give low confidence.
- On Solana, supply is traced only into current top holders.
