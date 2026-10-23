"""CrawlMeme bot texts (Telegram HTML parse mode). Pure functions, no network.

Every value that comes from a scan, memory or a user (tickers, names, reasons, addresses) goes through e() before it
is placed in HTML: Telegram renders the markup, and a ticker like "<b>" must stay text.
"""
import html, re, time

ADDR_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")
B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
CHAIN_NAME = {"robinhood": "Robinhood", "solana": "Solana", "base": "Base", "bsc": "BNB Chain"}
CHAIN_ALIAS = {"robinhood": "robinhood", "rh": "robinhood", "solana": "solana", "sol": "solana", "base": "base",
               "bsc": "bsc", "bnb": "bsc"}
EXPLORER = {"robinhood": "https://robinhoodchain.blockscout.com/address/", "base": "https://basescan.org/address/",
            "bsc": "https://bscscan.com/address/", "solana": "https://solscan.io/account/"}
BAND_ICON = {"CLEAN": "🟢", "OK": "🟡", "RISKY": "🟠", "DANGER": "🔴"}
TOO_EARLY, TOO_ESTABLISHED, TOO_ACTIVE = "TOO_EARLY_OR_LATE", "TOO_ESTABLISHED", "TOO_ACTIVE"


def e(s):
    return html.escape(str(s), quote=False)


def short(addr):
    return f"{addr[:6]}…{addr[-4:]}" if addr and len(addr) > 12 else (addr or "")


def _b58_len(s):
    """Number of bytes in a base58 string; None if not base58."""
    n = 0
    for c in s:
        k = B58.find(c)
        if k < 0:
            return None
        n = n * 58 + k
    zeros = len(s) - len(s.lstrip("1"))
    return zeros + (n.bit_length() + 7) // 8


def kind_of(s):
    """"evm" (0x + 40 hex: Robinhood, Base or BNB), "solana" (base58 of 32 bytes) or None."""
    if ADDR_RE.match(s):
        return "evm"
    if 32 <= len(s) <= 44 and _b58_len(s) == 32:
        return "solana"
    return None


def find_address(text):
    """First address in the text (whole, inside a link, or after a chain word) → (chain hint or None, address) or
    None. A chain word anywhere ("base 0x…", "0x… bnb") pins an EVM address to that chain."""
    text = (text or "").strip()
    pieces = [text] + re.split(r"[^0-9A-Za-z]+", text)
    hint = next((CHAIN_ALIAS[p.lower()] for p in pieces if p.lower() in CHAIN_ALIAS), None)
    for p in pieces:
        k = kind_of(p)
        if k == "solana":
            return "solana", p
        if k == "evm":
            return (hint if hint and hint != "solana" else None), p.lower()
    return None


def explorer(chain, addr):
    base = EXPLORER.get(chain)
    return f"{base}{addr}" if base else None


def wallet_link(chain, addr):
    url = explorer(chain, addr)
    return f'<a href="{e(url)}">{e(short(addr))}</a>' if url else f"<code>{e(short(addr))}</code>"


def usd(v):
    if v is None:
        return "—"
    if v >= 1e9:
        return f"${v / 1e9:.1f}B"
    if v >= 1e6:
        return f"${v / 1e6:.1f}M"
    if v >= 1e3:
        return f"${v / 1e3:.1f}K"
    if v >= 1:
        return f"${v:.2f}"
    return f"${v:.3g}" if v else "$0"


def pct(x, digits=1):
    return "—" if x is None else f"{x * 100:.{digits}f}%"


def ago(ts, now=None):
    if not ts:
        return "never"
    s = max(0, int((now or time.time()) - ts))
    return "just now" if s < 60 else f"{s // 60}m ago" if s < 3600 else f"{s // 3600}h ago" if s < 86400 \
        else f"{s // 86400}d ago"


def name_of(ticker, token, chain):
    who = f"<b>${e(ticker)}</b>" if ticker else f"<b>{e(short(token))}</b>"
    return f"{who} · {e(CHAIN_NAME.get(chain, chain or ''))}"


# ---------- fixed texts ----------
START = (
    "🕷 <b>CrawlMeme</b>, your private memecoin holder scanner.\n\n"
    "Paste a token address (Robinhood, Base, BNB Chain or Solana) and the crawlers check who really holds it: "
    "linked wallets, fresh wallets, snipers, launch bundles and what memory knows about the deployer.\n\n"
    "/help lists every command."
)
HELP = (
    "<b>Commands</b>\n"
    "/scan &lt;token&gt; · or just paste an address: risk score, main warning, largest operator, wallet links\n"
    "/watch &lt;token&gt; · alerts when something important changes\n"
    "/watchlist · tokens you watch\n"
    "/unwatch &lt;token&gt; · stop alerts\n"
    "/wallet &lt;address&gt; · what memory knows about a wallet\n"
    "/radar · fresh tokens that pass the launch radar\n"
    "/status · scanner and alert status\n\n"
    "Add <code>base</code>, <code>bnb</code> or <code>robinhood</code> before a 0x address when it exists on "
    "several chains.\n\n"
    "<b>Alerts</b>: verdict getting worse, probably-rug changes, largest operator selling, early buyers exiting, "
    "coordinated wallets, a watched token reaching the radar; for fresh launches, a known suspicious deployer "
    "launching again or a smart crew entering.\n\n"
    "<b>Reading a verdict</b>: score 0–100 (higher is cleaner). CLEAN and OK pass the radar; RISKY and DANGER "
    "explain why. «Probably rug» is a conditional scenario (if suspicious holders sell into today's liquidity), "
    "not a prediction. Read-only: the bot never asks for a wallet or a key."
)
PRIVATE = ("🔒 This bot is private.\nYour Telegram user ID is <code>{uid}</code>. "
           "If you run this CrawlMeme, add it to TG_ALLOWED_USERS.")
HINT = "Paste a token address, or /help for the commands."
SCAN_USAGE = "Usage: /scan &lt;token address&gt; (add base, bnb or robinhood for a 0x address on several chains)"
WATCH_USAGE = "Usage: /watch &lt;token address&gt;"
UNWATCH_USAGE = "Usage: /unwatch &lt;token address&gt;"
WALLET_USAGE = "Usage: /wallet &lt;wallet address&gt;"
DB_DOWN = "⚠️ Memory is not reachable right now. Try again in a minute."
ALERTS_OFF = "🔕 Alerts need the memory database; it is not configured on this CrawlMeme."
FAILED = "⚠️ The scan failed. Try again in a minute."
NO_SITE = ""


def crawling(addr):
    return f"🕷 crawling {e(short(addr))}…"


def choose_chain(addr, chains, action="scan", unsure=False):
    """The 0x address exists on several chains (or some chains could not be checked: unsure) → (text, buttons)."""
    names = ", ".join(CHAIN_NAME.get(c, c) for c in chains)
    rows = [[{"text": CHAIN_NAME.get(c, c), "callback_data": f"{action}:{c}:{addr}"} for c in chains]]
    if unsure:
        return (f"🔀 Couldn't check every chain for {e(short(addr))} right now. Which chain is it on?",
                {"inline_keyboard": rows})
    return f"🔀 {e(short(addr))} exists on {e(names)}. Which one?", {"inline_keyboard": rows}


def scan_error(addr, err):
    if not err or err.startswith("scan failed"):
        return FAILED
    return f"⚠️ {e(short(addr))}: {e(err)}"


# ---------- /scan ----------
def _tax(x):
    return f"{x * 100:.2f}".rstrip("0").rstrip(".") + "%"


def flap_line(res):
    """Flap token: "Flap · bonding curve 26% · tax 3%/3%" or "Flap · Uniswap V2"; not Flap → None."""
    f = res.get("flap") if res.get("launchpad") == "flap" else None
    if not isinstance(f, dict):
        return None
    parts = ["Flap", "Uniswap V2" if f.get("phase") == "dex" else e(f.get("phase_text") or "bonding curve")]
    tax = f.get("tax") or {}
    if tax.get("buy") or tax.get("sell"):
        parts.append(f"tax {_tax(tax.get('buy') or 0)}/{_tax(tax.get('sell') or 0)}")
    return " · ".join(parts)


def bankr_line(res):
    """Bankr token: "Bankr · ETH pair · dev vesting 15%" (unlocked part in parentheses, if any); not Bankr → None."""
    b = res.get("bankr") if res.get("launchpad") == "bankr" else None
    if not isinstance(b, dict):
        return None
    pair = b.get("pair") or {}
    parts = ["Bankr", f"{e(pair.get('symbol') or ('ETH' if pair.get('kind') == 'eth' else 'token'))} pair"]
    v = b.get("vesting") or {}
    if v.get("total_share_supply"):
        unl = v.get("unlocked_share_supply") or 0
        parts.append(f"dev vesting {_tax(v['total_share_supply'])}" + (f" ({_tax(unl)} unlocked)" if unl >= 0.0005 else ""))
    return " · ".join(parts)


def primary_warning(res):
    """The one line that matters most: the first hard gate, else the first soft gate, else the reason."""
    gates = res.get("gates") or []
    hard = [g for g in gates if not g.startswith("soft:")]
    if hard:
        return hard[0]
    if gates:
        return gates[0][len("soft:"):].strip()
    return res.get("reason") or res.get("headline")


def scan_card(res, report_url=None, watch_button=True):
    """Compact verdict → (html, reply_markup)."""
    chain, token = res.get("chain"), res.get("token") or ""
    h = res.get("header") or {}
    title = name_of(h.get("ticker") or h.get("name"), token, chain)
    lines = [title]
    lp = flap_line(res) or bankr_line(res)
    if lp:
        lines.append(lp)
    band = res.get("band") or ""
    if band == TOO_ESTABLISHED:
        lines += ["", f"🏛 <b>{e(res.get('headline') or 'Too established for a holder scan')}</b>"]
    elif band == TOO_ACTIVE:
        lines += ["", f"🌊 <b>{e(res.get('headline') or 'Too many trades for a full scan right now')}</b>"]
    elif band == TOO_EARLY:
        lines += ["", f"⏳ <b>Too early or too late</b>: only {res.get('holders_total', 0)} holders"]
    else:
        lines.append(f"{BAND_ICON.get(band, '⚪️')} <b>Score {res.get('score')}/100 · {e(band)}</b>")
        if (ps := res.get("partial_scan")) and ps.get("message"):   # Bankr: full holder index still being built
            lines.append(f"⏳ {e(ps['message'])}")
        warn = primary_warning(res)
        if warn:
            lines.append(f"⚠️ {e(warn)}")
        ops = res.get("operators") or []
        m = res.get("metrics") or {}
        if ops:
            big = ops[0]
            ws = big.get("wallets") or []
            imp = m.get("impact")
            move = "" if imp is None else (" · could move price <1% if sold" if imp < 0.01
                                           else f" · could move price −{imp * 100:.0f}% if sold")
            lines.append(f"👤 Largest operator: {len(ws)} wallet{'s' if len(ws) != 1 else ''} · "
                         f"{pct(big.get('share'))} of float{move}")
            lines.append("    " + " · ".join(wallet_link(chain, w) for w in ws[:3]) + (" …" if len(ws) > 3 else ""))
        rug = res.get("rug")
        if rug and rug.get("drop"):
            lines.append(f"🔻 Probably rug −{rug['drop'] * 100:.0f}% if suspicious holders sell (a scenario, "
                         f"not a forecast)")
    links = []
    holders = res.get("holders") or []
    if holders:
        links.append(f"top holder {wallet_link(chain, holders[0]['wallet'])} {pct(holders[0].get('share'))}")
    dep = res.get("launch", {}).get("deployer") if isinstance(res.get("launch"), dict) else None
    if dep:
        mem = (res.get("memory") or {}).get("deployer") or {}
        extra = f" ({mem['tokens']} earlier, {mem.get('rugged', 0)} rugged)" if mem.get("tokens") else ""
        links.append(f"deployer {wallet_link(chain, dep)}{e(extra)}")
    if links:
        lines += ["", " · ".join(links)]
    mk = [f"mcap {usd(h.get('mcap_usd'))}", f"liq {usd(h.get('liquidity_usd'))}"]
    if res.get("holders_total") is not None:
        mk.append(f"{res['holders_total']} holders")
    lines.append(" · ".join(mk))
    lines.append(f"<code>{e(token)}</code>")
    row = []
    if report_url:
        row.append({"text": "Full report", "url": report_url})
    if watch_button and band not in (TOO_ESTABLISHED, TOO_ACTIVE):
        row.append({"text": "🔔 Watch", "callback_data": f"watch:{chain}:{token}"})
    return "\n".join(lines), ({"inline_keyboard": [row]} if row else None)


# ---------- watchlist ----------
def watch_result(r, chain, token):
    st = (r or {}).get("status")
    who = f"{e(short(token))} · {e(CHAIN_NAME.get(chain, chain))}"
    if st == "added":
        return (f"🔔 Watching {who}. Alerts start after its next recheck "
                f"({r['count']}/{r['limit']} watched).")
    if st == "exists":
        return f"🔔 Already watching {who}."
    if st == "limit":
        return f"You already watch {r['limit']} tokens. /unwatch one first."
    return DB_DOWN


def watchlist(items, now=None):
    """→ (html, buttons with one Unwatch per token)."""
    if not items:
        return "You're not watching any tokens. /watch &lt;token&gt; or the 🔔 Watch button under a scan.", None
    lines, rows = ["🔔 <b>Your watchlist</b>", ""], []
    for w in items:
        band = w.get("band")
        state = (f"{BAND_ICON.get(band, '⚪️')} {e(band)} {w['score']}" if band and w.get("score") is not None
                 else e(band.replace("_", " ")) if band else "waiting for the first recheck")
        extra = (" · 🔻 rug" if w.get("rug") else "") + (" · 🛰 radar" if w.get("radar_active") else "")
        lines.append(f"{name_of(w.get('ticker'), w['token'], w['chain'])} — {state}{extra}")
        rows.append([{"text": f"🔕 Unwatch {('$' + w['ticker']) if w.get('ticker') else short(w['token'])}"[:60],
                      "callback_data": f"unwatch:{w['chain']}:{w['token']}"}])
    return "\n".join(lines), {"inline_keyboard": rows}


def unwatched(n, token):
    return f"🔕 Stopped watching {e(short(token))}." if n else f"You weren't watching {e(short(token))}."


# ---------- /wallet ----------
def _labels(x):
    out = x.get("outcomes") or []
    return f" → {e(', '.join(out))}" if out else ""


def wallet_card(p, chain_hint=None):
    addr = p.get("address") or ""
    lines = [f"👛 <b>{e(short(addr))}</b>", f"<code>{e(addr)}</code>", ""]
    held, dep = p.get("held") or [], p.get("deployed") or []
    if not held and not dep and not p.get("funded_by") and not p.get("funded"):
        lines.append("Memory has never seen this wallet among the top holders, deployers or funders of a scanned token.")
        return "\n".join(lines)
    facts = [f"top holder in {p.get('held_total', 0)} scanned token{'s' if p.get('held_total') != 1 else ''}"]
    if p.get("deployed_total"):
        facts.append(f"deployed {p['deployed_total']}")
    if p.get("operators"):
        facts.append(f"in {p['operators']} linked operator group{'s' if p['operators'] != 1 else ''}")
    if p.get("bundles"):
        facts.append(f"in {p['bundles']} launch bundle{'s' if p['bundles'] != 1 else ''}")
    if p.get("funded"):
        facts.append(f"funded {p['funded']} holder{'s' if p['funded'] != 1 else ''}" + (" (hub)" if p.get("hub") else ""))
    lines.append(" · ".join(facts))
    if p.get("rugged") or p.get("up"):
        lines.append(f"market outcomes of its tokens: {p.get('rugged', 0)} rugged · {p.get('up', 0)} went up")
    for f in p.get("funded_by") or []:
        lines.append(f"funded by {wallet_link(f['chain'], f['funder'])}" + (" (exchange-like hub)" if f.get("hub") else ""))
    if dep:
        lines += ["", "<b>Deployed</b>"]
        lines += [f"• {name_of(x.get('ticker'), x['token'], x['chain'])} — {e(x.get('band') or '?')}{_labels(x)}"
                  for x in dep]
    if held:
        lines += ["", "<b>Held (latest scan)</b>"]
        lines += [f"• {name_of(x.get('ticker'), x['token'], x['chain'])} — #{x.get('rank')} {pct(x.get('share'))} · "
                  f"{e(x.get('band') or '?')}{_labels(x)}" for x in held]
    chains = sorted({x["chain"] for x in held + dep})
    links = [f'<a href="{e(explorer(c, addr))}">{e(CHAIN_NAME.get(c, c))} explorer</a>'
             for c in chains or ([chain_hint] if chain_hint else []) if explorer(c, addr)]
    if links:
        lines += ["", " · ".join(links)]
    return "\n".join(lines)


# ---------- /radar ----------
def radar_list(feed, site=None, limit=8):
    if not feed.get("available"):
        return f"🛰 Radar unavailable: {e(feed.get('reason') or 'memory is off')}"
    items = [i for i in feed.get("items") or [] if i.get("status") == "active"][:limit]
    if not items:
        return "🛰 Nothing passes the radar in the last 24 hours."
    lines = ["🛰 <b>Launch radar</b> · last 24 h", ""]
    for i in items:
        age = f"{i['age_min'] / 60:.1f}h" if i.get("age_min") is not None else "?"
        lines.append(f"{BAND_ICON.get(i.get('band'), '⚪️')} {name_of(i.get('ticker'), i['token'], i['chain'])} · "
                     f"score {i.get('score')} · mcap {usd(i.get('mcap_usd'))} · {age} old")
        lines.append(f"    <code>{e(i['token'])}</code>")
    if site:
        lines += ["", f'<a href="{e(site)}/radar">Open the full radar</a>']
    return "\n".join(lines)


# ---------- /status ----------
def status_card(s, chains, alerts_on, db, cron=None, now=None):
    lines = ["⚙️ <b>CrawlMeme status</b>", "",
             f"chains: {e(', '.join(CHAIN_NAME.get(c, c) for c in chains))}",
             f"memory: {'connected' if db else 'not reachable'}",
             f"alerts: {'on' if alerts_on else 'off'}"]
    if s:
        lines.append(f"you watch {s.get('watches', 0)} token{'s' if s.get('watches') != 1 else ''} · "
                     f"{s.get('sent_24h', 0)} alert{'s' if s.get('sent_24h') != 1 else ''} in 24 h · "
                     f"{s.get('queued', 0)} queued overall")
        c = s.get("cron") or {}
        if c.get("at"):
            v = c.get("value") or {}
            lines.append(f"last alert run: {e(c['at'][:16].replace('T', ' '))} UTC · rechecked {v.get('rechecked', 0)}"
                         f" · sent {v.get('sent', 0)}")
        else:
            lines.append("last alert run: none yet")
    return "\n".join(lines)
