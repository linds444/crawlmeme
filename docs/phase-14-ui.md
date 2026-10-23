# Phase 14 — terminal UI

The scanner keeps its terminal look: near-black and charcoal, neon green, cyan secondary text, monospace logs. Phase 14
adds what a terminal UI needs to be shared and read in any language, without fabricating activity: every control does
something real, and the feed, history and radar only show real scans.

## What changed

| Area | What it does | Where |
|---|---|---|
| Localization | 8 languages (English, 简体中文, Español, Français, Deutsch, Bahasa Indonesia, Filipino, Tiếng Việt), picker in every page header | `crawler/web_v1/static/i18n.js`, served at `/i18n.js` |
| Translated risk explanations | verdict reasons, hard/soft gates, headline, rug scenario, radar reasons, coverage notes, result counters, proof definitions | regex patterns in `i18n.js` |
| Share & export | copy report link (`/?ca=…&chain=…`), report as JSON, report as text (in the page language), 1200×630 share card PNG (download; copy where the browser supports image clipboard) | `paintShare` in `scripts/build_frontend.py` |
| Scan history (server) | every immutable scan record of the token, newest first, with fingerprint, market at scan, measured outcomes and the newest market snapshot ("then vs now"); each row opens its supporting data | `GET /api/history?chain=&token=`, SQL `token_history` |
| Your recent scans | the last 20 tokens this browser scanned, on the landing; stored in `localStorage` only, never sent anywhere; "clear" removes them | `paintMine` |
| Interactive graph | drag nodes (links follow), hover/tap highlights a node's links, toggles for hub funders and same-block packs | `paintGraph` |
| Chain in links | `?chain=` picks the EVM chain, so a copied link reopens on Base/BNB rather than asking again; proof "scan" links carry it too | `fromUrl`, `proof.link` |

## Localization layer: design

Pages stay written in English. `i18n.js` translates text nodes and `placeholder` / `title` / `aria-label`
attributes in place, and keeps translating as the page changes (a `MutationObserver` on the whole document). Why this
shape:

- **No rendering code changes.** The scanner page is a bundled template, and radar/proof are hand-written. A layer that
  works on the DOM covers all three, plus every future panel, with one script.
- **Maintainable tables.** `PHRASES` holds exact UI strings, one row per string with one column per language.
  `PATTERNS` holds the engine's dynamic explanations as anchored regexes. `$n` inserts a captured group as is
  (numbers, addresses, tickers are never translated). `%n` translates it recursively, for example a reason inside a
  verdict line.
- **To add a language**, add its code to `LANGS`, then a column to every row. A missing translation falls back to
  English, never to an empty string. `tests/test_ui.py` enforces the column count, no duplicate keys and valid
  placeholders.
- **English is the default for everyone.** Another language appears only when chosen: `?lang=` for one page view, or the picker (remembered). The browser language is never used to switch automatically. The script loads
  synchronously in `<head>` so it reads `?lang=` before the scanner rewrites its URL.

Things the bundled page forced, and how the layer handles them:

- **The whole `<html>` content is swapped after load.** The observer watches `document`, not the first `<body>`.
- **The template engine clones nodes that are already translated.**
  - A reverse index (translation → English) restores the source text.
  - The language picker is recognised by an expando that `cloneNode` never copies. A cloned picker has lost its
    listener, so it is replaced instead of being left dead.
- **The hero heading is one element per word.** Word order differs across languages, so it is translated as one
  sentence (`data-i18n-whole`), and the chain names keep their colours.

## Security review

- **XSS:** translations are written only through `nodeValue`, `textContent` and `setAttribute`, never `innerHTML`.
  The dictionaries are static code; untrusted text (tickers, token names) is never treated as markup. The share card
  is drawn on a canvas.
- **Data exposure:**
  - The JSON and text reports contain only the scan result already shown on the page.
  - `localStorage` holds token address, chain, ticker, band and score: no keys and nothing personal.
- **`/api/history`:**
  - Chain and address are checked by format only, with no RPC, so an arbitrary address costs one indexed query and
    nothing more.
  - The SQL is `security invoker`, granted to `service_role` only and revoked from `anon` and `authenticated`.
  - The service-role key never leaves the server, and results are capped at 20 rows.
- **No secrets** were added; nothing new reads the environment.

## Scalability notes

- `/api/history` makes one Postgres call per result view.
  - It is covered by `scans_token (chain, token, scanned_at desc)`, `outcomes` primary key `(scan_id, horizon_h)` and
    `market_snapshots_token`.
  - It is not cached on purpose: a rescan must show up at once. If result views grow large, a 30–60 s per-token cache
    in `server.py` (like `/api/proof`) is the next step.
- The translation observer costs one dictionary lookup plus up to ~90 regex tests per changed text node, only when a
  non-English language is selected. The live log adds a few nodes per second, which is negligible.
- The share card renders client-side, so the server does no image work.

## Verified

- **Tests:** 858 pass, including the new `tests/test_ui.py` and real SQL on local Postgres 16.
  - 32 real engine explanations translate in all 7 non-English languages, with every number preserved.
  - Server routes, history SQL ordering and fingerprints are covered.
- **Browser (Playwright, Chromium):**
  - Landing and result in zh, es, fil and vi, and radar and proof in fr, id and zh.
  - Switching back to English restores every string.
  - JSON, text and PNG downloads work; the copied link is correct; graph drag and toggles work.
  - "Your recent scans" persists.
  - No horizontal overflow at 820 px (tablet) or 390 px (phone).
  - No JavaScript errors, and no dead links on landing, radar or proof.

## Limitations and suggested improvements

- **Translation quality:** written by the developer, not by native speakers. A native review per language is
  recommended before marketing in that language, and Filipino and Vietnamese most of all.
- **New engine wording** falls back to English until a pattern is added; the test lists the strings it guarantees.
- **The text report** is translated; the JSON report stays raw data on purpose.
- **"Your recent scans"** lives in one browser only. Cross-device history would need accounts, which are out of scope
  for a read-only scanner.
- **Share links** carry no per-scan snapshot: opening one runs a fresh scan, or serves it from cache within 10 minutes.
  The immutable record for a given moment is linked from the scan history rows.
