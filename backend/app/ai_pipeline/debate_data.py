"""
Hard data, fact sheet and shared context for the debate agents.

Ported from the research notebook (BATBC v10, "HARD DATA HELPER" and "SHARED CONTEXT HELPER"):

* ``format_hard_indicators`` -- the raw indicator numbers, never truncated, followed by a numbered FACT SHEET.
  Reports are cut to a few hundred characters before they reach the debaters, and a cut once removed the RSI
  and the model invented one; the HARD DATA block cannot be cut.
* ``build_fact_sheet`` / ``trend_fact_list`` -- relations a small model kept getting wrong are computed in code
  (price below the 50-day SMA is resistance, not support; a falling dividend is not "increasing") and written
  as numbered facts ([F#]) beside the dated news items ([N#] exchange notices, [W#] web snippets).
* ``format_shared_context`` -- news RAG, macro regime and memory for every agent that needs them, one place.

For DSE tickers the rules mention taka and long-only trading; other tickers get the same structure without
those lines (their currency is not known here, so prices are written without one).
"""
from __future__ import annotations

import re

NEWS_RAG_CAP = 1600


def is_dse(state: dict) -> bool:
    try:
        from tradingagents.dataflows.symbol_utils import is_dse_ticker
        return bool(is_dse_ticker(state.get("company_of_interest", "")))
    except Exception:  # noqa: BLE001
        return False


def _dsex_missing(state: dict) -> bool:
    """True for a DSE ticker whose macro snapshot has no DSEX reading (the regime then rests on other data only)."""
    if not is_dse(state):
        return False
    snap = state.get("macro_snapshot")
    if not isinstance(snap, dict):
        return False
    return not (snap.get("dsex") or {}).get("latest")


def _ccy(state: dict) -> str:
    return " BDT" if is_dse(state) else ""


def last_row_from_stock_csv(txt):
    """(date, close) of the last row of the market analyst's 'Date,Open,High,Low,Close,Volume' CSV text."""
    last = (None, None)
    for line in str(txt).splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) >= 5 and re.match(r"\d{4}-\d{2}-\d{2}", parts[0]):
            try:
                last = (parts[0][:10], float(parts[4]))
            except ValueError:
                pass
    return last


def last_close_from_stock_csv(txt):
    return last_row_from_stock_csv(txt)[1]


def _fs_val(v):
    return None if v is None or str(v).strip() in ("", "N/A", "n/a", "nan", "None", "data not available") else v


def _last_price(state: dict):
    raw = state.get("market_raw_data") or {}
    px = last_close_from_stock_csv(raw.get("stock_data", ""))
    if px is None:
        try:
            px = float((state.get("fund_metrics") or {}).get("last_trading_price"))
        except (TypeError, ValueError):
            px = None
    return px


def trend_fact_list(state: dict) -> list:
    """Relationships computed IN CODE from the raw indicators, so a small model cannot flip them
    (observed: price BELOW the 50-day SMA was called 'support' and a stop-loss was set at the SMA,
    i.e. ABOVE the price of a long position)."""
    ind = state.get("indicators_parsed", {}) or {}
    px = _last_price(state)
    f = []
    sma = ind.get("close_50_sma")
    if px is not None and isinstance(sma, (int, float)) and sma:
        gap = (px / sma - 1) * 100
        if px < sma:
            f.append(f"last close {px:.2f} is BELOW the 50-day SMA {sma:.2f} ({gap:+.1f}%): the SMA is overhead "
                     "RESISTANCE, NOT support; a long stop-loss cannot sit at the SMA")
        else:
            f.append(f"last close {px:.2f} is ABOVE the 50-day SMA {sma:.2f} ({gap:+.1f}%): the SMA is potential SUPPORT below price")
    rsi = ind.get("rsi")
    if isinstance(rsi, (int, float)):
        f.append(f"RSI {rsi:.1f} = " + ("oversold (RSI <= 30)" if rsi <= 30 else "overbought (RSI >= 70)" if rsi >= 70
                                         else "neutral zone (30-70)"))
    macd = ind.get("macd")
    if isinstance(macd, (int, float)):
        f.append(f"MACD {macd:+.3f} is " + ("negative (bearish momentum)" if macd < 0 else "positive (bullish momentum)"))
    ub, lb = ind.get("boll_ub"), ind.get("boll_lb")
    if px is not None and isinstance(ub, (int, float)) and isinstance(lb, (int, float)):
        f.append("price is " + ("ABOVE the upper Bollinger band" if px > ub else "BELOW the lower Bollinger band"
                               if px < lb else "inside the Bollinger bands"))
    return f


def derive_trend_facts(state: dict) -> str:
    return "; ".join(trend_fact_list(state))


def dividend_trend_fact(raw) -> str:
    """Parse a cash-dividend history such as '30% 2025, 300% 2024, 100% 2023' (any order) into a code-computed
    trend sentence. Returns '' when fewer than two years can be parsed."""
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
    txt = (f"cash dividend trend (code-computed from the dividend history): latest {v0:g}% ({y0}) vs {v1:g}% ({y1}): "
           + (f"{word} {abs(ch):.0f}%" if word != "ABOUT FLAT" else f"{word} ({ch:+.0f}%)"))
    prev = [pairs[y] for y in ys[1:4]]
    if len(prev) >= 2:
        txt += f"; average of the previous {len(prev)} listed years {sum(prev) / len(prev):.0f}%"
    pk = max(ys, key=lambda y: pairs[y])
    txt += f"; highest listed {pairs[pk]:g}% ({pk})"
    if word == "FELL" and ch <= -50:
        txt += "; a sharp drop can be an interim or partial-year figure and the code cannot tell, so say only that it fell"
    return txt


def build_fact_sheet(state: dict, max_chars: int = 2800) -> str:
    """Numbered facts [F#] and news items [N#] [W#] computed or copied IN CODE from this run's data."""
    lines = []

    def add(t):
        lines.append(f"[F{len(lines) + 1}] {t}")

    ccy = _ccy(state)
    raw = state.get("market_raw_data") or {}
    dt, px = last_row_from_stock_csv(raw.get("stock_data", ""))
    fm = state.get("fund_metrics") or {}
    if px is None:
        try:
            px = float(fm.get("last_trading_price"))
        except (TypeError, ValueError):
            px = None
    if px is not None:
        add(f"last close {px:.2f}{ccy}" + (f" (last data row {dt}, a past trading day, not today)" if dt else ""))
    for t in trend_fact_list(state):
        add(t)
    eps = _fs_val(fm.get("eps_ttm"))
    if eps is not None:
        add(f"EPS (latest disclosed period, NOT trailing-12-month): {eps}"
            + (f" for {fm.get('eps_period')}" if _fs_val(fm.get("eps_period")) else ""))
    else:
        add("EPS: data not available")
    pe = _fs_val(fm.get("pe_ratio"))
    add(f"P/E: {pe}" + (f" ({fm.get('pe_basis')})" if _fs_val(fm.get("pe_basis")) else "") if pe is not None
        else "P/E: data not available (do not compute one)")
    mc, hi, lo = _fs_val(fm.get("market_cap")), _fs_val(fm.get("52w_high")), _fs_val(fm.get("52w_low"))
    if mc is not None or hi is not None:
        add(f"market cap {mc if mc is not None else 'n/a'}; 52-week range "
            f"{lo if lo is not None else 'n/a'} - {hi if hi is not None else 'n/a'}")
    dy = _fs_val(fm.get("dividend_yield"))
    add(f"dividend yield: {dy}" if dy is not None else "dividend yield: data not available (this is NOT 0%)")
    dt_fact = dividend_trend_fact(fm.get("cash_dividend"))
    if dt_fact:
        add(dt_fact)
    macro = str(state.get("macro_regime") or "").strip()
    if macro:
        add(f"macro regime: {macro}" + (" (DSEX data UNAVAILABLE -- unknown, not neutral)" if _dsex_missing(state) else ""))
    nraw = state.get("news_raw_full") or {}
    n_i = 0
    for ln in str(nraw.get("company_news", "")).splitlines():
        ln = ln.strip(" -*")
        if len(ln) > 25 and not re.match(r"(?i)(error|no |none)", ln) and n_i < 3:
            n_i += 1
            lines.append(f"[N{n_i}] (company notice) {ln[:170]}")
    w_i = 0
    for ln in str(nraw.get("web_news", "")).splitlines():
        ln = ln.strip()
        if ln.startswith("- ") and w_i < 3:
            w_i += 1
            lines.append(f"[W{w_i}] (web snippet) {ln[2:175]}")
    return "\n".join(lines)[:max_chars]


def market_rules(state: dict) -> str:
    rules = ["[RULES]"]
    if is_dse(state):
        rules.append("DSE retail investors are LONG-ONLY: SELL = exit an existing holding (no shorting), and a stop-loss / "
                     "take-profit / entry price only makes sense for a BUY. All prices are BDT (৳), never '$'.")
    rules.append("CITATION RULE: put the [F#]/[N#]/[W#] id after every number, indicator, price, valuation or news claim. "
                 "State NO event (announcement, regulatory action, dividend, management change) that is not listed in the "
                 "FACT SHEET or the news evidence; [W#] items are web search snippets: use only the date written in the "
                 "item ('no date' = undated) and never call an item 'recent' unless it says so. If the FACT SHEET does "
                 "not support a claim, leave it out.")
    return " ".join(rules)


def format_hard_indicators(state: dict) -> str:
    """The raw indicator floats (never truncated), the FACT SHEET and the rules, as one block for a prompt."""
    ind = state.get("indicators_parsed", {}) or {}
    if not ind:
        base = "N/A (raw indicators not found in state -- do NOT guess a number, say 'not available')"
    else:
        base = ", ".join(f"{k}={v:.4f}" if isinstance(v, (int, float)) else f"{k}={v}" for k, v in ind.items())
    sheet = build_fact_sheet(state)
    if sheet:
        base += ("\nFACT SHEET (trend facts, fundamentals and news items computed or copied in code from this run's "
                 "data -- authoritative, never contradict them; cite the [F#]/[N#]/[W#] id after every number or "
                 "claim):\n" + sheet)
    return base + "\n" + market_rules(state)


def format_shared_context(state: dict, news: bool = True, macro: bool = True, memory: bool = True) -> str:
    """News RAG / macro regime / memory, for every debater, facilitator and the PM."""
    parts = []
    if news:
        rag = str(state.get("news_rag_context") or "").strip()
        if rag:
            parts.append("NEWS RAG:\n" + rag[:NEWS_RAG_CAP])
    if macro:
        regime = str(state.get("macro_regime") or "").strip()
        if regime:
            dq = (" [DATA QUALITY: DSEX data UNAVAILABLE -- missing data is NOT a neutral or risk-off signal]"
                  if _dsex_missing(state) else "")
            parts.append(f"MACRO REGIME: {regime}{dq} -- {str(state.get('macro_report', ''))[:350]}")
    if memory:
        mem = str(state.get("past_context") or state.get("memory_context") or "")
        mem = mem.split("MACRO REGIME CONTEXT")[0].strip()
        if mem:
            parts.append("MEMORY (similar past episodes / reflection / lessons):\n" + mem[:700])
    return "\n".join(parts) if parts else "(none available)"


# Rules the debaters share (from the notebook's Bull/Bear prompts).
MISATTRIBUTION_RULE = (
    "IMPORTANT: If a source is cited in the reports as reporting NEGATIVE news (a miss, downgrade, guidance cut, etc.), "
    "do NOT reframe that same source as expressing optimism or \"potential for recovery\" unless the reports explicitly "
    "say so -- that is a factual misattribution, not bullish spin.")
HARD_DATA_RULE = (
    "IMPORTANT: HARD DATA below is the ONLY authoritative source for RSI/MACD/SMA/Bollinger numbers -- if you cite any "
    "of these, use the HARD DATA value exactly, never a number from your own memory or estimate.")
EVIDENCE_RULE = (
    "Evidence rule: do not just restate the RSI/MACD/SMA numbers already in the debate. Support your case with specific "
    "facts from at least TWO other categories (fundamentals incl. the per-year dividend history, news, macro, memory) "
    "that the opponent has not already used, and never cite a number you cannot find in the data above.")
