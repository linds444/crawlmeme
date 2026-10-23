# Phase 6: relationship graph

Status: implemented. 738 offline tests pass, and it has been run against live mainnet tokens on Robinhood and BNB
Chain.

## What changed

### 1. Funding links: V1's "common funder" evidence, switched on with a hub check
V1 already defined the `funder` link: holders whose first deposit came from one regular wallet. It also defined the
guard: a funder with `HUB_MIN_OUT` (100) or more outgoing transfers is a hub (an exchange or a bot) and never links
anyone. But V1 had no funding data, so the path was off (`USE_FUNDING = False`). Phase 5 now reads every analysed
holder's first deposit, so Phase 6 turns the path on:

- **Hub check.** As soon as a funder has funded a second holder, or is itself a holder, the scan counts its outgoing
  native transfers up to 100. This is one Alchemy Transfers API call on EVM chains, or one Helius parsed-history page
  on Solana. A full page counts as a hub. The check runs alongside the wallet histories, within the crawl deadline.
- **Unknown means hub.** An unread or unknown count is treated as a hub, as V1 specified: no data, no link.
- **Contracts never link.** Funders are added to the scan's existing contract check, and a contract never links.
- **Holder funded by holder.** A new `funded` link joins a holder to the holder that made its first deposit, unless
  that holder is a hub or a contract.
- **Operator merging is unchanged.** These links feed `detect.operators` like every other proven link. A scan where
  holders share a real (non-hub) funder now shows them as one operator, which can lower the score. That is the point
  of the evidence. Turn it off with `FUNDING_LINKS=off`.

**Measured on live tokens:** every shared funder on the two test tokens was a hub. Each had sent 1,000+ transfers to
700+ different wallets, which is exchange or on-ramp behaviour. They were correctly *not* linked, and the verdicts
did not change (DANGER 50 and CLEAN 90 with the switch on or off). Phase 5's page had shown these as red "common
funder" warnings. They are now grey "hub funder · exchange-like, not a link" notes, and red is kept for real shared
funders.

### 2. The graph (`result.graph`, `crawler/graph.py`)

Nodes:
- **holder:** rank, share, merged operator, entry kind and flags.
- **funder:** how many holders it funded, its outgoing count, whether it is a hub or a contract.
- **distributor:** a wallet that sent the token to 2+ holders.

Every edge carries its evidence (`tx`, `block`, `ts` where known) and a confidence:

| edge | between | confidence |
|---|---|---|
| `same_tx`: first entry in one transaction | holder–holder | 1.0 |
| `direct`: a token transfer between them | holder–holder | 1.0 |
| `funded`: one funded the other | holder–holder | 0.95 |
| `distributor`: same token sender | holder–holder | 0.9 |
| `funder`: same non-hub funder | holder–holder | 0.85 |
| `pack`: virgin wallets buying in one block with similar size | holder–holder | 0.5 |
| `funding`: the deposit itself | funder→holder | 0.85, hub 0.2 |
| `sent_token` | distributor→holder | 0.9 |

Confidence is the prior strength of each evidence class, used to order and draw edges and kept for the Phase 11
memory brain. Merging still follows the V1 rule (any proven or pack link).

### 3. Page
A "relationship graph" panel (an SVG force layout, no library):
- **Holders:** sized by share and coloured by merged operator.
- **Funders:** diamonds, red when they link and grey when they are hubs.
- **Distributors:** triangles.
- **Edges:** solid for proven links, dashed for packs and funding.
- **Interaction:** hovering shows the evidence (tx, block, confidence); clicking opens the explorer.

## Limits
- **Within one scan.** The graph covers the analysed holders of one token. Cross-token persistence (the same funder
  behind several launches) is Phase 7 (operators and cabals) on the Phase 11 memory brain.
- **Native coin only.** The hub check counts native transfers. A funder that spreads stablecoins instead is not
  checked. This is rare for gas funding.
- **Solana.** Funding is known only for wallets with at most 100 transactions before entry (Phase 5), so Solana
  funding links are rarer.
