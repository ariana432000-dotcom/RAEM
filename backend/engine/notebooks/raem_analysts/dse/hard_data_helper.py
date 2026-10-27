# raem_analysts/dse/hard_data_helper.py -- shared by abbank-dse, batbc-dse, bpml-dse, waltonhil-dse
# The company-specific values come from each notebook's RAEM_PROFILE:
# @@TICKER@@ ticker, @@FILE@@ ticker as used in file names, @@LOG@@ trading-log name, @@SLOT:key@@ lines the profile supplies.
# >>> shared code
# ================================================================
# 🛡️ HARD DATA HELPER — RSI/MACD/ইত্যাদি numeric hallucination রোধ
# ================================================================
# Bear/Bull/Risk debator prompt-এ market_report-কে [:500] অক্ষরে কেটে
# পাঠানো হয় (context ছোট রাখতে)। সমস্যা হলো, report-এর মধ্যে RSI-এর
# actual number ঠিক কোন অক্ষরে থাকবে সেটা আগে থেকে জানা যায় না -- এবং
# একটা real run-এ ঠিক ঐ [:500] cut-off RSI number-টা কেটে ফেলেছিল
# ("...As" পর্যন্ত রেখে "of July 17, RSI was 30.39" অংশ বাদ পড়ে যায়)।
# ফলে LLM (Bear Analyst) prompt-এ কোনো real RSI না পেয়ে নিজে থেকে
# "74" বানিয়ে ফেলেছিল, আর সেটা Facilitator → Trader → PM পুরো chain
# বেয়ে propagate হয়ে Decision Verifier-এ গিয়ে ধরা পড়েছিল (FLAGGED)।
#
# ফিক্স: raw numeric indicators (market_result["indicators_parsed"])
# সরাসরি, truncation-independent একটা "HARD DATA" লাইন হিসেবে সব
# debator prompt-এ আলাদা করে বসানো হবে -- report টেক্সট যেখানেই কাটুক
# না কেন, ground-truth number সবসময় prompt-এ থাকবে।

import re

def _last_close_from_stock_csv(txt):
    """Last Close in the 'Date,Open,High,Low,Close,Volume' CSV text of the market analyst (None if absent)."""
    last = None
    for line in str(txt).splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) >= 5 and re.match(r"\d{4}-\d{2}-\d{2}$", parts[0]):
            try:
                last = float(parts[4])
            except ValueError:
                pass
    return last


def _trend_fact_list(state: dict) -> list:
    """Relationships computed IN CODE from the raw indicators, so a 7B model cannot flip them
    (observed: price BELOW the 50-day SMA was called 'support' by the risk debators and the PM,
    and a stop-loss was set at the SMA, i.e. ABOVE the price of a long position)."""
    ind = state.get("indicators_parsed", {}) or {}
    mr = globals().get("market_result") or {}
    raw = state.get("market_raw_data") or mr.get("market_raw_data") or {}
    px = _last_close_from_stock_csv(raw.get("stock_data", ""))
    if px is None:
        try:
            px = float((globals().get("fundamentals_result") or {}).get("fund_metrics", {}).get("last_trading_price"))
        except (TypeError, ValueError):
            px = None
    f = []
    sma = ind.get("close_50_sma")
    if px is not None and isinstance(sma, (int, float)) and sma:
        gap = (px / sma - 1) * 100
        if px < sma:
            f.append(f"last close {px:.2f} is BELOW the 50-day SMA {sma:.2f} ({gap:+.1f}%): the SMA is overhead RESISTANCE, NOT support; a long stop-loss cannot sit at the SMA")
        else:
            f.append(f"last close {px:.2f} is ABOVE the 50-day SMA {sma:.2f} ({gap:+.1f}%): the SMA is potential SUPPORT below price")
    rsi = ind.get("rsi")
    if isinstance(rsi, (int, float)):
        f.append(f"RSI {rsi:.1f} = " + ("oversold (RSI <= 30)" if rsi <= 30 else "overbought (RSI >= 70)" if rsi >= 70 else "neutral zone (30-70)"))
    macd = ind.get("macd")
    if isinstance(macd, (int, float)):
        f.append(f"MACD {macd:+.3f} is " + ("negative (bearish momentum)" if macd < 0 else "positive (bullish momentum)"))
    ub, lb = ind.get("boll_ub"), ind.get("boll_lb")
    if px is not None and isinstance(ub, (int, float)) and isinstance(lb, (int, float)):
        f.append("price is " + ("ABOVE the upper Bollinger band" if px > ub else "BELOW the lower Bollinger band" if px < lb else "inside the Bollinger bands"))
    return f


def derive_trend_facts(state: dict) -> str:
    return "; ".join(_trend_fact_list(state))


def _last_row_from_stock_csv(txt):
    """(date, close) of the last row of the market analyst's 'Date,Open,High,Low,Close,Volume' CSV text."""
    last = (None, None)
    for line in str(txt).splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) >= 5 and re.match(r"\d{4}-\d{2}-\d{2}$", parts[0]):
            try:
                last = (parts[0], float(parts[4]))
            except ValueError:
                pass
    return last


def _fs_val(v):
    return None if v is None or str(v).strip() in ("", "N/A", "n/a", "nan", "None", "data not available") else v


MARKET_RULES = (
    "[MARKET RULES] DSE retail investors are LONG-ONLY: SELL = exit an existing holding (no shorting), and a stop-loss / "
    "take-profit / entry price only makes sense for a BUY. All prices are BDT (৳), never '$'. Do NOT state share counts, "
    "money amounts or '% of portfolio': position size is computed later by code. "
    "CITATION RULE: put the [F#]/[N#]/[W#] id after every number, indicator, price, valuation, peer or news claim. "
    "State NO event (announcement, regulatory action, dividend, NPL figure, management change) that is not listed in the "
    "FACT SHEET; [W#] items are web search snippets: use only the date written in the item ('no date' = undated) and never call an item 'recent' unless it says so. If the FACT SHEET does not support a claim, leave it out.")


# ── v9: relations the 7B model kept getting wrong are now computed IN CODE and written into the FACT SHEET ──
# (observed: dividend history 30% 2025, 300% 2024, 100% 2023 ... was summarised as "payout has been increasing")
PEER_REL_BAND = 1.0          # |own 20d return - peer 20d return| below this many percentage points = IN LINE


def dividend_trend_fact(raw) -> str:
    """Parse a DSE cash-dividend history such as '30% 2025, 300% 2024, 100% 2023' (any order) into a code-computed trend
    sentence. Returns '' when fewer than two years can be parsed."""
    pairs = {}
    for v, y in re.findall(r"(\d+(?:\.\d+)?)\s*%\s*\(?\s*((?:19|20)\d{2})", str(raw or "")):
        pairs.setdefault(int(y), float(v))
    ys = sorted(pairs, reverse=True)
    if len(ys) < 2 or pairs[ys[1]] <= 0:
        return ""
    y0, y1 = ys[0], ys[1]
    v0, v1 = pairs[y0], pairs[y1]
    ch = (v0 / v1 - 1) * 100
    word = "FELL" if ch <= -10 else ("ROSE" if ch >= 10 else "ABOUT FLAT")
    txt = (f"cash dividend trend (code-computed from the DSE history): latest {v0:g}% ({y0}) vs {v1:g}% ({y1}): "
           + (f"{word} {abs(ch):.0f}%" if word != "ABOUT FLAT" else f"{word} ({ch:+.0f}%)"))
    prev = [pairs[y] for y in ys[1:4]]
    if len(prev) >= 2:
        txt += f"; average of the previous {len(prev)} listed years {sum(prev) / len(prev):.0f}%"
    pk = max(ys, key=lambda y: pairs[y])
    txt += f"; highest listed {pairs[pk]:g}% ({pk})"
    if word == "FELL" and ch <= -50:
        txt += "; a sharp drop can be an interim or partial-year figure and the code cannot tell, so say only that it fell"
    return txt


def peer_relation_facts(gm) -> list:
    """'OUTPERFORMS' / 'UNDERPERFORMS' / 'IN LINE' vs sector peers and vs correlated peers, from the Graph Analyst's 20-day returns."""
    out, own = [], (gm or {}).get("own_ret20_pct")
    if not isinstance(own, (int, float)):
        return out
    for key, lab in (("sector_peer_ret20_pct", "sector peers"), ("corr_peer_ret20_pct", "correlated peers")):
        v = gm.get(key)
        if isinstance(v, (int, float)):
            d = own - v
            word = "OUTPERFORMS" if d >= PEER_REL_BAND else ("UNDERPERFORMS" if d <= -PEER_REL_BAND else "IN LINE")
            out.append(f"vs {lab} (20d): own {own:+.2f}% vs {v:+.2f}%: {word}"
                       + (f" by {abs(d):.2f} pts" if word != "IN LINE" else f" (gap {d:+.2f} pts)")
                       + " [20-day window only; says nothing about longer horizons]")
    return out


def build_fact_sheet(state: dict, max_chars: int = 2800) -> str:
    """Numbered facts [F#] / news items [N#] [W#] computed or copied IN CODE from this run's data. Agents must cite these ids
    (the Fact Guard checks that the ids exist and that un-cited event claims are not invented)."""
    lines = []

    def add(t):
        lines.append(f"[F{len(lines) + 1}] {t}")

    mr = globals().get("market_result") or {}
    raw = state.get("market_raw_data") or mr.get("market_raw_data") or {}
    dt, px = _last_row_from_stock_csv(raw.get("stock_data", ""))
    fr = globals().get("fundamentals_result") or {}
    fm = (state.get("fund_metrics") or fr.get("fund_metrics") or {})
    if px is None:
        try:
            px = float(fm.get("last_trading_price"))
        except (TypeError, ValueError):
            px = None
    if px is not None:
        add(f"last close {px:.2f} BDT" + (f" (last data row {dt}, a past trading day, not today)" if dt else ""))
    for t in _trend_fact_list(state):
        add(t)
    eps = _fs_val(fm.get("eps_ttm"))
    if eps is not None:
        add(f"EPS (latest disclosed period, NOT trailing-12-month): {eps}" + (f" for {fm.get('eps_period')}" if _fs_val(fm.get("eps_period")) else ""))
    else:
        add("EPS: data not available")
    pe = _fs_val(fm.get("pe_ratio"))
    add(f"P/E: {pe}" + (f" ({fm.get('pe_basis')})" if _fs_val(fm.get("pe_basis")) else "") if pe is not None else "P/E: data not available (do not compute one)")
    mc, hi, lo = _fs_val(fm.get("market_cap")), _fs_val(fm.get("52w_high")), _fs_val(fm.get("52w_low"))
    if mc is not None or hi is not None:
        add(f"market cap {mc if mc is not None else 'n/a'}; 52-week range {lo if lo is not None else 'n/a'} - {hi if hi is not None else 'n/a'}")
    dy = _fs_val(fm.get("dividend_yield"))
    add(f"dividend yield: {dy}" if dy is not None else "dividend yield: data not available (this is NOT 0%)")
    _dt = dividend_trend_fact(fm.get("cash_dividend"))
    if _dt:
        add(_dt)
    gm = ((globals().get("graph_result") or {}).get("metrics")) or {}
    gparts = []
    for k, lab in (("own_ret20_pct", "own 20d return"), ("sector_peer_ret20_pct", "sector peers"), ("corr_peer_ret20_pct", "correlated peers"), ("universe_ret20_pct", "whole universe")):
        v = gm.get(k)
        if isinstance(v, (int, float)):
            gparts.append(f"{lab} {v:+.2f}%")
    if gparts:
        add("peer graph (secondary evidence; a GNN rank is across ALL graph stocks, not a sector rank): " + ", ".join(gparts))
    for _pl in peer_relation_facts(gm):
        add(_pl)
    _fc = globals().get("forecast_result")
    if isinstance(_fc, dict) and _fc.get("fact"):
        add(_fc["fact"])
    mac = state.get("macro_result") or globals().get("macro_result") or {}
    if isinstance(mac, dict) and mac.get("macro_regime"):
        add(f"macro regime: {mac['macro_regime']}" + ("" if mac.get("dsex_data_ok", True) else " (DSEX data UNAVAILABLE -- unknown, not neutral)"))
    nraw = ((state.get("news_raw_full") or (globals().get("news_result") or {}).get("news_raw_full")) or {})
    n_i = 0
    for ln in str(nraw.get("company_news", "")).splitlines():
        ln = ln.strip(" -*")
        if len(ln) > 25 and not re.match(r"(?i)(error|no |none)", ln) and n_i < 3:
            n_i += 1
            lines.append(f"[N{n_i}] (DSE notice) {ln[:170]}")
    w_i = 0
    for ln in str(nraw.get("web_news", "")).splitlines():
        ln = ln.strip()
        if ln.startswith("- ") and w_i < 3:
            w_i += 1
            lines.append(f"[W{w_i}] (web snippet) {ln[2:175]}")
    out = "\n".join(lines)
    return out[:max_chars]


def format_hard_indicators(state: dict) -> str:
    """state['indicators_parsed']-এর raw float dict থেকে একটা compact,
    সবসময়-visible numeric summary string বানায়। এটা কখনো truncate হয় না,
    তাই LLM-এর RSI/MACD ইত্যাদি সংখ্যা বানিয়ে ফেলার কোনো অজুহাত থাকে না।

    ✅ NEW: Graph Analyst-এর peer-graph context (graph_context) এখানে append হয় --
    state-এ "graph_context" থাকলে সেটা, নইলে notebook-global graph_context।
    এক জায়গায় বদলালে Bull/Bear/Trader/Aggressive/Conservative/Neutral/PM সবাই পায়।"""
    ind = state.get("indicators_parsed", {}) or {}
    if not ind:
        base = "N/A (raw indicators not found in state — do NOT guess a number, say 'not available')"
    else:
        parts = []
        for k, v in ind.items():
            if isinstance(v, (int, float)):
                parts.append(f"{k}={v:.4f}")
            else:
                parts.append(f"{k}={v}")
        base = ", ".join(parts)
    sheet = build_fact_sheet(state)
    if sheet:
        base += ("\nFACT SHEET (TREND FACTS, fundamentals, peers and news items computed/copied in code from this run's data -- "
                 "authoritative, never contradict them; cite the [F#]/[N#]/[W#] id after every number or claim):\n" + sheet)
    graph_ctx = state.get("graph_context") or globals().get("graph_context", "")
    if graph_ctx:
        base += ("\nPEER-GRAPH CONTEXT (secondary evidence from the DSE peer graph; it is NOT a source "
                 "for RSI/MACD/SMA/Bollinger numbers, and a GNN score marked as failed validation must be ignored): "
                 + str(graph_ctx)[:900])
    base += "\n" + MARKET_RULES
    return base

print("✅ format_hard_indicators() ready — will be used by Bear/Bull/Risk debators below.")

# ================================================================
# 🔗 SHARED CONTEXT HELPER — one place that decides what extra evidence an agent sees
# ================================================================
# Audit fix: news RAG reached only Bull/Bear/Trader/PM, macro reached nobody but the
# Trader (300 chars), and episodic memory/lessons reached only the Trader. Every
# debator/facilitator/PM prompt now calls this. It reads the state dict first and
# falls back to the notebook globals, so manual hand-built state dicts keep working.
def format_shared_context(state: dict, news: bool = True, macro: bool = True, memory: bool = True) -> str:
    parts = []
    if news:
        rag = state.get("news_rag_context") or globals().get("news_rag_context", "")
        if rag:
            parts.append("NEWS RAG:\n" + str(rag)[:globals().get("NEWS_RAG_CAP", 1600)])
    if macro:
        mr = state.get("macro_result") or globals().get("macro_result") or {}
        if isinstance(mr, dict) and mr.get("macro_regime"):
            _dq = "" if mr.get("dsex_data_ok", True) else " [DATA QUALITY: DSEX data UNAVAILABLE -- regime rests on USD/BDT only; missing data is NOT a neutral or risk-off signal]"
            parts.append(f"MACRO REGIME: {mr['macro_regime']}{_dq} -- {str(mr.get('macro_report', ''))[:350]}")
    if memory:
        mem = state.get("past_context") or globals().get("retrieved_memory_context", "")
        mem = str(mem).split("MACRO REGIME CONTEXT")[0].strip()   # macro already added above
        if mem:
            parts.append("MEMORY (similar past episodes / reflection / lessons):\n" + mem[:700])
    return "\n".join(parts) if parts else "(none available)"

print("✅ format_shared_context() ready — news RAG / macro / memory for every debator, facilitator and the PM.")
