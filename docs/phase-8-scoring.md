# Phase 8: scoring models, shadow verdicts and data health

Status: implemented. 760 offline tests pass. Memory is live in production with the secret key set, and every scan
now stores the verdict of every scoring model.

## Why versioned models instead of just changing the score

Phases 5–7 added evidence V1 never had: who funded each holder, repeat funders, crews across tokens and serial
deployers. Weighting it into the score straight away would change verdicts on evidence that memory has only just
started to collect. There is no history yet to show which adjustments predict a rug and which only add noise (the
look-ahead risk named in the Phase 1 audit).

Instead, every scan computes **every** model on the same evidence:
- the **live** model gives the verdict;
- the other models are **shadows**, shown on the page and stored in memory.

Once Rug and Gem Replay (Phase 13) measure outcomes, a shadow can be promoted on evidence with one switch
(`SCORE_MODEL`), and old results can be rescored under it.

## Models (`crawler/scoring.py`)

| model | status | definition |
|---|---|---|
| `crawlmeme-1` | **live** (default) | V1's score unchanged: five parts weighted 35/25/20/10/10, bands, hard and soft gates, plus the Phase 3 coverage cap (a limited scan is never CLEAN). Funding links from Phase 6 merge operators inside it |
| `crawlmeme-2` | shadow | `crawlmeme-1` plus the capped, explained adjustments below |

`crawlmeme-2` adjustments:

| evidence (from `result.memory`) | effect |
|---|---|
| crew: top holders that top-held 2+ other tokens together | −8 each, at most −15; if a crew holds ≥ 10% of the float, band no better than RISKY |
| serial deployer: 2+ other tokens in memory | −4 |
| … of which 2+ were rated DANGER | −10, and band no better than RISKY |
| repeat funder: a non-hub funder of holders here and in other tokens | −5 |
| danger regulars: 2+ top holders that each sat in 2+ DANGER tokens | −5 |

Rules that hold for both models:
- A model never raises a DANGER verdict.
- A scan with no verdict (too early, too established, too active) stays without one.
- Funding links are not counted twice: they already shape the `operator` part of `crawlmeme-1`.

## What a result carries

- `model`: `{"id": live model, "scores": {model: {"score", "band", "gates", "adjustments": [{"code", "points",
  "why"}]}}}`.
- `data_health`: what the verdict stands on, with a **confidence** label.

| confidence | when |
|---|---|
| high | full coverage, ≥ 80% of the examined wallets read, and balances not contradicted by the chain |
| low | limited coverage, or fewer than half of the wallets read |
| medium | everything else |

  The block also reports: coverage reasons, wallets read, the holder method and its verification, the market
  source, price candles used for P&L, funding read, memory size, time, and RPC requests.
- Memory: `scans.scores` stores every model's verdict (migration `20261022140000_scan_scores.sql`), so the models
  can be compared on real outcomes later.

## API

`GET /api/rescore?job=ID[&model=M]` returns `{"live", "models": {...}}`. Every verdict is recomputed from the
stored `crawlmeme-1` verdict and the scan's memory findings, so a model change applies to old results too. An
unknown model returns 400; a scan that hasn't finished returns 409.

## Page

A new "how sure is this" panel shows:
- the confidence label and the live model;
- the shadow model's verdict, with each adjustment and its reason (or "agrees");
- the data-health grid.

## Switching models

`SCORE_MODEL=crawlmeme-2` in the Vercel environment makes the shadow live:
- the verdict, the headline reason and the rug projection follow it;
- `crawlmeme-1` becomes the shadow.

My recommendation: wait for Phase 13's outcome tracking to show that `crawlmeme-2` separates rugs from survivors
better before switching.
