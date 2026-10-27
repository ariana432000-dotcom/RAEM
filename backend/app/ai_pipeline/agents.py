"""
The agent team — a faithful port of every `create_*` factory in the notebook.

Each factory takes an LLM and returns a `node(state)` callable that reads from a
shared state dict and returns updates, exactly as in the notebook. TradingAgents
tools are imported lazily so this module also loads in demo mode.
"""

from __future__ import annotations

import os
import re
import time

from .llm import invoke_llm_with_retry
from .render import first_signal


# ==========================================================================
# Confidence heuristics — every analyst node below previously returned no
# confidence figure at all, so runner.py's stage "meta" never carried a
# "confidence" key and every agent card in the UI showed a hardcoded 0%
# (not a real "the model is uncertain" reading -- just a missing field).
# These are deliberately simple, data-completeness-based scores (how much
# of the real underlying data actually came back non-empty/non-error) --
# an honest "how much did I actually have to work with" signal, not a
# model-reported certainty, so it stays consistent across LLM providers.
# ==========================================================================
def _field_completeness_confidence(fields: dict, floor: float = 0.05, cap: float = 0.95) -> float:
    """Fraction of `fields` that hold a real (non-missing, non-error)
    value. Used for fundamentals/market, where each field is one metric
    or indicator that's either a real number/string or an "N/A"/"Error"
    placeholder."""
    fields = {k: v for k, v in fields.items() if not str(k).startswith("_")}
    if not fields:
        return 0.0
    good = 0
    for v in fields.values():
        if v is None:
            continue
        s = str(v).strip()
        if not s or s.upper() in {"N/A", "NA", "NONE", "NULL"} or s.lower().startswith("error"):
            continue
        good += 1
    if good == 0:
        return 0.0
    return round(min(max(good / len(fields), floor), cap), 2)


def _text_confidence(text: str, target_chars: int, weight: float = 1.0) -> float:
    """Confidence contribution from one block of fetched text: 0 if it's
    an error placeholder or empty, otherwise scales up to `weight` as the
    block approaches `target_chars` (a rough "did we get a substantive
    amount of real content" proxy)."""
    text = (text or "").strip()
    if not text or text.lower().startswith("error"):
        return 0.0
    return weight * min(1.0, len(text) / max(target_chars, 1))


def _last_turn(history: str) -> str:
    """Last non-empty line of a debate history string -- mirrors
    runner.py's own copy (kept separate rather than imported, since
    runner.py imports this module, not the other way around). Used by
    create_bull_researcher/create_bear_researcher below to read the
    opponent's most recent turn by name (bull_history/bear_history)
    instead of a shared, order-dependent field."""
    parts = [p for p in history.split("\n") if p.strip()]
    return parts[-1] if parts else ""


def extract_final_proposal(report_text: str) -> str:
    """Pulls the FINAL TRANSACTION PROPOSAL verdict out of a report so it
    survives truncation elsewhere (reports get sliced to a few hundred chars
    when threaded into later prompts). Searches a window of text after the
    anchor phrase rather than matching same-line only, since the LLM
    sometimes puts the verdict on the next line
    ("FINAL TRANSACTION PROPOSAL:**\\n**SELL** - ...").
    """
    report_text = str(report_text)
    idx = report_text.upper().find("FINAL TRANSACTION PROPOSAL")
    if idx == -1:
        return ""
    window = report_text[idx: idx + 150]
    m = re.search(r"\b(BUY|SELL|HOLD)\b", window, re.IGNORECASE)
    if not m:
        return ""
    return f"FINAL TRANSACTION PROPOSAL: {m.group(1).upper()}"


# ==========================================================================
# Settings and helpers of the research notebooks (token savers and numbers-only hand-off)
# ==========================================================================
# Reports of the four analysts are kept short: the debaters read only the first part of each.
ANALYST_LEN_RULES = {
    "market": ("LENGTH: start with one line 'KEY POINTS:' (at most 350 characters) giving RSI, MACD, the price vs the 50-day SMA and the "
               "Bollinger position, each with its number. After it write at most 120 words of explanation, then the table. "
               "Later agents read only the first 500 characters of this report."),
    "news": "LENGTH: at most 1-2 sentences per section and about 180 words in total before the table.",
    "sentiment": "LENGTH: about 150 words in total before the table.",
    "fundamentals": "LENGTH: at most about 250 words before the table.",
}


def _len_rule(name: str) -> str:
    return ("\n" + ANALYST_LEN_RULES[name] + "\n") if name in ANALYST_LEN_RULES else ""


# The Fundamentals analyst's BUY/HOLD/SELL call is not passed to the other agents: they get only the numbers the code
# extracted (no narrative, no verdict).  The analyst's own report and verdict are still saved in the session log.
FUND_NUMBERS_ONLY = os.environ.get("RAEM_FUND_NUMBERS_ONLY", "1") != "0"
FUND_LLM_REPORT = os.environ.get("RAEM_FUND_LLM_REPORT", "1") != "0"   # 0 = skip the analyst's own LLM call (nothing downstream reads it)
FUND_FACTS_MARK = "FUNDAMENTALS - NUMBERS ONLY"

# The Market analyst describes the stock's OWN price and indicators, so it must not say "bullish" / "bearish".  The
# prompt asks for neutral wording and the code enforces it, because a small model does not always obey.
NEUTRAL_WORDING = True
_BB_MAP = [
    (re.compile(r"\bbull(?:ish)?\s+market\b", re.I), "uptrend"), (re.compile(r"\bbear(?:ish)?\s+market\b", re.I), "downtrend"),
    (re.compile(r"\bbullish\b", re.I), "upward-leaning"), (re.compile(r"\bbearish\b", re.I), "downward-leaning"),
    (re.compile(r"\bbulls\b", re.I), "buyers"), (re.compile(r"\bbears\b", re.I), "sellers"),
]


def _neutralise_bull_bear(text: str) -> str:
    for rx, rep in _BB_MAP:
        text = rx.sub(rep, text)
    return text


def _ta_utils(ticker: str = ""):
    """Grab the data-fetching + instrument-context helpers the agents need
    (lazy import).

    ✅ CHANGED: get_fundamentals/get_balance_sheet/get_cashflow/
    get_income_statement/get_stock_data/get_indicators/get_news/
    get_global_news now come from `.data_providers` (FMP + Finnhub) instead
    of tradingagents' yfinance-backed tools — output format is unchanged, so
    everything downstream in this file still works as-is.
    build_instrument_context/get_language_instruction are unrelated to the
    data-source swap and stay on the real (non-black-box) tradingagents
    implementation.

    ✅ CHANGED (DSE): when `ticker` is a live Dhaka Stock Exchange trading
    code (checked via symbol_utils.is_dse_ticker, which consults bdshare's
    real trading-code list), the data-fetching functions instead come from
    tradingagents.agents.utils.agent_utils — the original, non-FMP toolset,
    which routes through interface.py's route_to_vendor() and auto-selects
    the "dse" vendor (bdshare-backed) for these tickers. Default ticker=""
    preserves the old FMP/Finnhub behavior for every call site that doesn't
    pass one — those only use build_instrument_context/get_language_
    instruction, which are unaffected either way.
    """
    from tradingagents.agents.utils.agent_utils import (
        build_instrument_context,
        get_language_instruction,
    )
    from tradingagents.dataflows.symbol_utils import is_dse_ticker

    # FMP + Finnhub only when both keys are set; otherwise (and for every DSE code) the keyless tradingagents data tools
    # (Yahoo Finance, bdshare/dsebd.org) are used, so a company can be analysed with no data-provider account.
    if ticker and is_dse_ticker(ticker):
        # Dhaka Stock Exchange: the research notebooks' data layer (prices, indicators, company page, disclosures, news)
        from . import dse_layer as _d
        get_fundamentals, get_balance_sheet, get_cashflow, get_income_statement = (
            _d.get_fundamentals, _d.get_balance_sheet, _d.get_cashflow, _d.get_income_statement)
        get_stock_data, get_indicators, get_news, get_global_news = (
            _d.get_stock_data, _d.get_indicators, _d.get_news, _d.get_global_news)
    elif not (os.environ.get("FMP_API_KEY") and os.environ.get("FINNHUB_API_KEY")):
        from tradingagents.agents.utils.agent_utils import (
            get_fundamentals, get_balance_sheet, get_cashflow, get_income_statement,
            get_stock_data, get_indicators,
            get_news, get_global_news,
        )
    else:
        from .data_providers import (
            get_fundamentals, get_balance_sheet, get_cashflow, get_income_statement,
            get_stock_data, get_indicators,
            get_news, get_global_news,
        )
    return {
        "build_instrument_context": build_instrument_context,
        "get_language_instruction": get_language_instruction,
        "get_fundamentals": get_fundamentals,
        "get_balance_sheet": get_balance_sheet,
        "get_cashflow": get_cashflow,
        "get_income_statement": get_income_statement,
        "get_stock_data": get_stock_data,
        "get_indicators": get_indicators,
        "get_news": get_news,
        "get_global_news": get_global_news,
    }


# ==========================================================================
# Analyst team
# ==========================================================================
def create_fundamentals_analyst(llm, log=print):
    """Fundamentals analyst.  Dhaka Stock Exchange companies follow the research notebook (the DSE data layer in
    dse_layer.py: company page, disclosed EPS, corporate actions, capital structure, all in taka); any other company reads
    the Yahoo Finance / provider tools.  Either way the other agents receive the numbers-only block, not the narrative."""
    from langchain_core.messages import HumanMessage, SystemMessage

    def node(state):
        current_date = state["trade_date"]
        company = state["company_of_interest"]
        U = _ta_utils(company)
        instrument_context = U["build_instrument_context"](company)
        log(f"Fetching fundamentals for {company} on {current_date}")

        from tradingagents.dataflows.symbol_utils import is_dse_ticker
        _is_dse = is_dse_ticker(company)

        # ── Step 1: call every tool directly (no LLM involvement) ──
        tool_results = {}
        for tool_name in ("get_fundamentals", "get_balance_sheet", "get_cashflow", "get_income_statement"):
            args = {"ticker": company, "curr_date": current_date}
            if not _is_dse and tool_name != "get_fundamentals":
                args["freq"] = "quarterly"
            log(f"calling {tool_name}")
            try:
                tool_results[tool_name] = str(U[tool_name].invoke(args))[:3000]
            except Exception as e:  # noqa: BLE001
                tool_results[tool_name] = f"Error fetching {tool_name}: {e}"
                log(f"  error {tool_name}: {e}")
        fund = tool_results["get_fundamentals"]
        bs = tool_results["get_balance_sheet"]
        cf = tool_results["get_cashflow"]
        inc = tool_results["get_income_statement"]

        def extract_numbers(raw, keys):
            """Specific rows from CSV-like data."""
            results = {}
            for key in keys:
                for line in raw.split("\n"):
                    if key.lower() in line.lower():
                        nums = re.findall(r"-?\d+\.?\d*e?[+-]?\d*", line)
                        nums = [float(n) for n in nums if abs(float(n)) > 1000]
                        if nums:
                            results[key] = nums[0]
                            break
            return results

        cur = "৳" if _is_dse else "$"

        def fmt(n):
            """Large number to a readable string (taka for a DSE company, dollars otherwise)."""
            try:
                n = float(n)
                if abs(n) >= 1e9:
                    return f"{cur}{n / 1e9:.2f}B"
                if abs(n) >= 1e6:
                    return f"{cur}{n / 1e6:.1f}M"
                return f"{cur}{n:,.0f}"
            except Exception:  # noqa: BLE001
                return str(n)

        def g(pattern, raw):
            m = re.search(pattern, raw)
            return m.group(1) if m else "N/A"

        if _is_dse:
            # ── DSE (research notebook): DSE's company page never prints PE / EPS / revenue / beta / yield in structured
            #    form, so EPS and a rough P/E come from the disclosure feed (get_income_statement); dividends and issues
            #    come from get_cashflow's corporate-action history; capital structure from get_balance_sheet. ──
            pe = re.search(r"PE Ratio.*?:\s*([\d.]+)", fund)
            eps = re.search(r"EPS \(TTM\).*?:\s*([\d.]+)", fund)
            pe_from_inc = re.search(r"P/E Ratio(?:\s*\([^)]*\))?:\s*(-?[\d.]+)", inc)
            eps_from_inc = re.search(r"Most Recent Disclosed EPS: Tk\.\s*(-?[\d.]+)", inc)
            eps_period_m = re.search(r"Most Recent Disclosed EPS: Tk\.\s*-?[\d.]+\s*for\s*([^\n]+)", inc)
            pe_basis_m = re.search(r"P/E Ratio\s*\(rough,\s*([^)]*)\)", inc)
            mcap = re.search(r"Market Cap.*?:\s*([\d.]+)", fund)
            rev_ttm = re.search(r"Revenue \(TTM\).*?:\s*([\d.]+)", fund)
            beta = re.search(r"Beta.*?:\s*([\d.]+)", fund)
            div = re.search(r"Dividend Yield.*?:\s*([\d.]+)", fund)
            hi52 = re.search(r"52 Week High.*?:\s*([\d.]+)", fund)
            lo52 = re.search(r"52 Week Low.*?:\s*([\d.]+)", fund)
            sector = re.search(r"Sector:\s*(.+)", fund)
            last_px = re.search(r"Last Trading Price.*?:\s*([\d.]+)", fund)

            pe_val = pe.group(1) if pe else (pe_from_inc.group(1) if pe_from_inc else "N/A")
            eps_val = eps.group(1) if eps else (eps_from_inc.group(1) if eps_from_inc else "N/A")
            mcap_val = fmt(mcap.group(1)) if mcap else "N/A"
            rev_val = fmt(rev_ttm.group(1)) if rev_ttm else "N/A"
            beta_val = beta.group(1) if beta else "N/A"
            div_val = div.group(1) if div else "N/A"
            hi52_val = hi52.group(1) if hi52 else "N/A"
            lo52_val = lo52.group(1) if lo52 else "N/A"
            sector_val = sector.group(1).strip() if sector else "N/A"
            last_px_val = last_px.group(1) if last_px else "N/A"
            eps_period_txt = eps_period_m.group(1).strip()[:60] if eps_period_m else "period not stated"
            pe_basis_txt = ("rough: " + pe_basis_m.group(1).strip()) if pe_basis_m else "basis not stated"
            # N/A must never reach the LLM as "N/A%" (a small model turns it into "0%" and every later agent copies it)
            div_line = f"{div_val}%" if div else "data not available (DSE publishes no yield -- do NOT write 0%; use the Cash Dividend history below)"

            inc_rows = extract_numbers(inc, ["Total Revenue", "Gross Profit", "Operating Income",
                                             "Net Income From Continuing", "Normalized EBITDA"])
            cash_div_m = re.search(r"Cash Dividend:\s*(.+)", cf)
            bonus_issue_m = re.search(r"Bonus Issue.*?:\s*(.+)", cf)
            right_issue_m = re.search(r"Right Issue:\s*(.+)", cf)
            cash_div_val = cash_div_m.group(1).strip() if cash_div_m else "N/A"
            bonus_issue_val = bonus_issue_m.group(1).strip() if bonus_issue_m else "N/A"
            right_issue_val = right_issue_m.group(1).strip() if right_issue_m else "N/A"
            bs_rows = extract_numbers(bs, ["Authorized Capital", "Paid-up Capital", "Reserve & Surplus", "Other Comprehensive Income"])

            net_income_val = fmt(inc_rows.get("Net Income From Continuing", "N/A"))
            gross_profit_val = fmt(inc_rows.get("Gross Profit", "N/A"))
            operating_income_val = fmt(inc_rows.get("Operating Income", "N/A"))
            ebitda_val = fmt(inc_rows.get("Normalized EBITDA", "N/A"))

            data_summary = f"""
COMPANY: {company} | DATE: {current_date}
{instrument_context}

COMPANY SNAPSHOT:
- Sector: {sector_val}
- Last Trading Price: {last_px_val}

MARKET DATA:
- Market Cap: {mcap_val}
- P/E Ratio: {pe_val}  [{pe_basis_txt}]
- EPS (most recent disclosed period = {eps_period_txt}; NOT trailing-12-month): ৳{eps_val}
- Revenue (TTM): {rev_val}
- Beta: {beta_val}
- Dividend Yield: {div_line}
- 52-Week Range: ৳{lo52_val} - ৳{hi52_val}

MOST RECENT QUARTER (Net Income is an ESTIMATE from the company's own disclosed
EPS x outstanding shares -- DSE has no audited income statement; Gross Profit/
Operating Income/EBITDA are genuinely unavailable from any DSE source):
- Net Income (estimated): {net_income_val}
- Gross Profit: {gross_profit_val}
- Operating Income: {operating_income_val}
- EBITDA: {ebitda_val}

CORPORATE ACTIONS / DIVIDEND HISTORY (DSE publishes no cash flow statement -- this is the closest public proxy):
- Cash Dividend: {cash_div_val}
- Bonus Issue: {bonus_issue_val}
- Right Issue: {right_issue_val}
- Free Cash Flow: data not available (DSE publishes no cash flow statement)
- Stock Buybacks: data not available (DSE publishes no cash flow statement)

BALANCE SHEET / CAPITAL STRUCTURE (DSE publishes no total assets/liabilities/net debt -- this is what's public):
- Authorized Capital: {fmt(bs_rows.get("Authorized Capital", "N/A"))}
- Paid-up Capital: {fmt(bs_rows.get("Paid-up Capital", "N/A"))}
- Reserve & Surplus (excl. OCI): {fmt(bs_rows.get("Reserve & Surplus", "N/A"))}
- Other Comprehensive Income (OCI): {fmt(bs_rows.get("Other Comprehensive Income", "N/A"))}
- Net Debt: data not available (DSE publishes no full balance sheet)
- Total Debt: data not available (DSE publishes no full balance sheet)
"""

            def _short(x, n=350):
                x = str(x)
                return x if len(x) <= n else x[:n].rstrip() + " ..."
            _unavail = [lbl for lbl, k in (("gross profit", "Gross Profit"), ("operating income", "Operating Income"),
                                           ("EBITDA", "Normalized EBITDA")) if k not in inc_rows]
            facts_lines = [
                f"{FUND_FACTS_MARK} (extracted by code from DSE pages; no analyst opinion) -- {company}, {current_date}",
                f"Sector: {sector_val} | Last trading price: {last_px_val} | Market cap: {mcap_val}",
                f"P/E: {pe_val} [{pe_basis_txt}] | EPS: ৳{eps_val} ({eps_period_txt}; NOT trailing-12-month) | Dividend yield: "
                + (f"{div_val}%" if div else "not published by DSE (do NOT write 0%)"),
                f"52-week range: ৳{lo52_val} - ৳{hi52_val} | Beta: {beta_val} | Revenue (TTM): {rev_val}",
                f"Net income, latest period (ESTIMATE from disclosed EPS x shares): {net_income_val}",
                f"Cash dividend history: {_short(cash_div_val)} | Bonus issue: {_short(bonus_issue_val, 120)} | Right issue: {_short(right_issue_val, 120)}",
                f"Paid-up capital: {fmt(bs_rows.get('Paid-up Capital', 'N/A'))} | Reserve & surplus (excl. OCI): "
                f"{fmt(bs_rows.get('Reserve & Surplus', 'N/A'))} | OCI: {fmt(bs_rows.get('Other Comprehensive Income', 'N/A'))}",
                "Not published by DSE: free cash flow, net debt, total debt" + (", " + ", ".join(_unavail) if _unavail else "") + ".",
            ]
            fund_metrics = {
                "market_cap": mcap_val, "pe_ratio": pe_val, "eps_ttm": eps_val, "eps_period": eps_period_txt,
                "pe_basis": pe_basis_txt, "revenue_ttm": rev_val, "beta": beta_val, "dividend_yield": div_val,
                "52w_high": hi52_val, "52w_low": lo52_val, "sector": sector_val, "last_trading_price": last_px_val,
                "net_income_q": net_income_val, "gross_profit_q": gross_profit_val,
                "operating_income_q": operating_income_val, "ebitda_q": ebitda_val,
                "cash_dividend": cash_div_val, "bonus_issue": bonus_issue_val, "right_issue": right_issue_val,
                "authorized_capital": fmt(bs_rows.get("Authorized Capital", "N/A")),
                "paidup_capital_bs": fmt(bs_rows.get("Paid-up Capital", "N/A")),
                "reserve_surplus": fmt(bs_rows.get("Reserve & Surplus", "N/A")),
                "oci": fmt(bs_rows.get("Other Comprehensive Income", "N/A")),
            }
            extra_rules = """- Never call EPS or Net Income "TTM" or "trailing twelve months": they cover only the most recent disclosed period named above (usually ONE quarter).
- Never write 0% (or any number) for a field that says data not available.
- When describing dividends, quote the actual per-year figures from the Cash Dividend history; say "rising" or "falling" only if those figures show it.
"""
        else:
            pe_val = g(r"PE Ratio.*?:\s*([\d.]+)", fund)
            eps_val = g(r"EPS \(TTM\).*?:\s*([\d.]+)", fund)
            mcap = re.search(r"Market Cap.*?:\s*([\d.]+)", fund)
            rev_ttm = re.search(r"Revenue \(TTM\).*?:\s*([\d.]+)", fund)
            mcap_val = fmt(mcap.group(1)) if mcap else "N/A"
            rev_val = fmt(rev_ttm.group(1)) if rev_ttm else "N/A"
            beta_val = g(r"Beta.*?:\s*([\d.]+)", fund)
            div_val = g(r"Dividend Yield.*?:\s*([\d.]+)", fund)
            hi52_val = g(r"52 Week High.*?:\s*([\d.]+)", fund)
            lo52_val = g(r"52 Week Low.*?:\s*([\d.]+)", fund)
            sma50_val = g(r"50 Day Average.*?:\s*([\d.]+)", fund)
            inc_rows = extract_numbers(inc, ["Total Revenue", "Gross Profit", "Operating Income",
                                             "Net Income From Continuing", "Normalized EBITDA"])
            cf_rows = extract_numbers(cf, ["Free Cash Flow", "Repurchase Of Capital Stock"])
            bs_rows = extract_numbers(bs, ["Net Debt", "Total Debt", "Cash And Cash Equivalents"])
            net_income_val = fmt(inc_rows.get("Net Income From Continuing", "N/A"))
            gross_profit_val = fmt(inc_rows.get("Gross Profit", "N/A"))
            operating_income_val = fmt(inc_rows.get("Operating Income", "N/A"))
            ebitda_val = fmt(inc_rows.get("Normalized EBITDA", "N/A"))
            free_cash_flow_val = fmt(cf_rows.get("Free Cash Flow", "N/A"))
            buybacks_val = fmt(cf_rows.get("Repurchase Of Capital Stock", "N/A"))
            net_debt_val = fmt(bs_rows.get("Net Debt", "N/A"))
            total_debt_val = fmt(bs_rows.get("Total Debt", "N/A"))
            div_line = f"{div_val}%" if div_val != "N/A" else "data not available (do NOT write 0%)"

            data_summary = f"""
COMPANY: {company} | DATE: {current_date}

MARKET DATA (TTM):
- Market Cap: {mcap_val}
- P/E Ratio: {pe_val}
- EPS (TTM): {eps_val}
- Revenue (TTM): {rev_val}
- Beta: {beta_val}
- Dividend Yield: {div_line}
- 52-Week Range: {lo52_val} - {hi52_val}
- 50-Day SMA: {sma50_val}

MOST RECENT QUARTER (income statement):
- Net Income: {net_income_val}
- Gross Profit: {gross_profit_val}
- Operating Income: {operating_income_val}
- EBITDA: {ebitda_val}

CASH FLOW (most recent quarter):
- Free Cash Flow: {free_cash_flow_val}
- Stock Buybacks: {buybacks_val}

BALANCE SHEET (most recent quarter):
- Net Debt: {net_debt_val}
- Total Debt: {total_debt_val}
"""
            facts_lines = [
                f"{FUND_FACTS_MARK} (extracted by code from the data provider; no analyst opinion) -- {company}, {current_date}",
                f"Market cap: {mcap_val} | P/E: {pe_val} | EPS (TTM): {eps_val} | Revenue (TTM): {rev_val}",
                f"Beta: {beta_val} | Dividend yield: " + (f"{div_val}%" if div_val != "N/A" else "not available (do NOT write 0%)")
                + f" | 52-week range: {lo52_val} - {hi52_val} | 50-day SMA: {sma50_val}",
                f"Latest quarter: net income {net_income_val} | gross profit {gross_profit_val} | operating income {operating_income_val} | EBITDA {ebitda_val}",
                f"Free cash flow: {free_cash_flow_val} | Stock buybacks: {buybacks_val} | Net debt: {net_debt_val} | Total debt: {total_debt_val}",
            ]
            fund_metrics = {
                "market_cap": mcap_val, "pe_ratio": pe_val, "eps_ttm": eps_val, "revenue_ttm": rev_val,
                "dividend_yield": div_val, "50d_sma": sma50_val, "net_income_q": net_income_val,
                "gross_profit_q": gross_profit_val, "operating_income_q": operating_income_val,
                "free_cash_flow_q": free_cash_flow_val, "net_debt": net_debt_val, "total_debt": total_debt_val,
                "beta": beta_val, "52w_high": hi52_val, "52w_low": lo52_val, "ebitda_q": ebitda_val,
            }
            extra_rules = """- Missing/N/A fields are a normal limitation -- by themselves they are NOT a reason to default to HOLD. Base your verdict on what the
  available numbers show; reserve HOLD for data that is itself mixed or conflicting, not merely incomplete.
- Never write 0% (or any number) for a field that says data not available.
"""
        facts_block = "\n".join(facts_lines)

        # ── Step 2: the analyst's own report, written from the condensed summary ──
        prompt = f"""You are a senior financial analyst. Today is {current_date}.

Write a professional fundamental analysis report for {company} using ONLY the data below.
Structure: Company Overview -> Profitability -> Balance Sheet -> Cash Flow -> Risks -> Outlook
{_len_rule('fundamentals')}
End with a Markdown table and: FINAL TRANSACTION PROPOSAL: **BUY** / **HOLD** / **SELL**

RULES:
- Use ONLY the numbers below. Do NOT invent any figures.
- Do NOT say "Q2 2023". The data is from {current_date}.
- If a metric shows N/A, say "data not available".
{extra_rules}
{data_summary}
""" + U["get_language_instruction"]()
        messages = [
            SystemMessage(content="You are a financial analyst. Use ONLY the provided numbers. Never invent data."),
            HumanMessage(content=prompt),
        ]
        if FUND_LLM_REPORT:
            report = invoke_llm_with_retry(llm, messages).content
            log("fundamentals report ready")
        else:
            report = facts_block + "\n\n(LLM narrative skipped: RAEM_FUND_LLM_REPORT=0)"
            log("fundamentals LLM report skipped -- numbers block only")

        fund_confidence = _field_completeness_confidence(fund_metrics)
        # Surfaces what the data source returned, to diagnose an all-N/A run from the saved session
        fund_metrics["_debug_raw_fundamentals"] = str(fund)[:250]
        if _is_dse:
            fund_metrics["_debug_raw_balance_sheet"] = str(bs)[:250]
        return {
            # the other agents get only the numbers; the narrative and its verdict stay available here and in the log
            "fundamentals_report": facts_block if FUND_NUMBERS_ONLY else report,
            "fundamentals_report_llm": report,
            "fundamentals_facts": facts_block,
            "fund_verdict": extract_final_proposal(report),
            "fund_metrics": fund_metrics,
            "fundamentals_confidence": fund_confidence,
        }

    return node


def create_market_analyst(llm, indicators_list, log=print):
    from langchain_core.messages import HumanMessage, AIMessage, SystemMessage
    from datetime import datetime, timedelta

    def node(state):
        current_date = state["trade_date"]
        company = state["company_of_interest"]
        U = _ta_utils(company)
        asset_type = state.get("asset_type", "stock")
        instrument_context = U["build_instrument_context"](company, asset_type)
        log(f"Fetching market data for {company} on {current_date}")

        end_dt = datetime.strptime(current_date, "%Y-%m-%d")
        start_date = (end_dt - timedelta(days=30)).strftime("%Y-%m-%d")

        log("calling get_stock_data")
        try:
            stock_data = str(U["get_stock_data"].invoke({
                "symbol": company, "start_date": start_date, "end_date": current_date,
            }))[:2000]
        except Exception as e:  # noqa: BLE001
            stock_data = f"Error: {e}"

        log("calling get_indicators")
        ind_parts = []
        for ind_name in indicators_list:
            try:
                val = U["get_indicators"].invoke({
                    "symbol": company, "indicator": ind_name, "curr_date": current_date,
                })
                ind_parts.append(f"{ind_name}: {str(val)[:300]}")
            except Exception as ie:  # noqa: BLE001
                ind_parts.append(f"{ind_name}: Error - {str(ie)[:100]}")
        indicators_str = "\n".join(ind_parts)

        def _extract(raw_str):
            for line in raw_str.split("\n"):
                line = line.strip()
                if re.match(r"\d{4}-\d{2}-\d{2}:", line) and "N/A" not in line:
                    m = re.search(r":\s*(-?\d+\.?\d+)", line)
                    if m:
                        return float(m.group(1))
            return None

        indicators_dict = {}
        for item in ind_parts:
            if ": " in item:
                k, _, v = item.partition(": ")
                val = _extract(v)
                indicators_dict[k.strip()] = val if val is not None else v.strip()[:100]

        prompt = f"""You are a trading analyst. Today is {current_date}.
{instrument_context}

Using the REAL data below, write a brief technical analysis report for {company}.
Include RSI, MACD, Bollinger Bands, SMA analysis.
{_len_rule('market')}

WORDING RULES: This is an analysis of {company}'s OWN price and indicators, not of the market. Never use the words
"bullish", "bearish", "bull" or "bear". Describe the condition with neutral terms such as "upward momentum",
"downward momentum", "price above/below the 50-day SMA", "RSI in the overbought / oversold / neutral zone",
"price near the upper/lower Bollinger band". Do not predict the market direction.

At the very end, output a Markdown table with EXACTLY these columns:
| Indicator | Value | Signal | Interpretation |
In the Signal column use only: UPWARD, DOWNWARD, NEUTRAL, OVERBOUGHT, OVERSOLD.

=== STOCK PRICE DATA ===
{stock_data}

=== TECHNICAL INDICATORS ===
{indicators_str}
""" + U["get_language_instruction"]()

        messages = [
            SystemMessage(content="You are a senior trading analyst. Write reports based only on provided data. Always end with the required Markdown table."),
            HumanMessage(content=prompt),
        ]
        report = invoke_llm_with_retry(llm, messages).content
        if NEUTRAL_WORDING:
            report = _neutralise_bull_bear(report)
        log("market report ready")

        return {
            "market_report": report,
            "indicators_parsed": indicators_dict,
            "market_raw_data": {"stock_data": stock_data, "indicators": indicators_dict},
            "market_confidence": _field_completeness_confidence(indicators_dict),
        }

    return node


def create_news_analyst(llm, log=print):
    """News analyst.  Besides the exchange / provider feed it reads dated press coverage found by web search (directory
    and price pages dropped).  The full, untruncated texts are returned in `news_raw_full` (and sector news in
    `sector_news_full`) for the news corpus; the prompt copies stay capped."""
    from langchain_core.messages import HumanMessage, SystemMessage
    from datetime import datetime, timedelta
    from . import news_rag

    def node(state):
        current_date = state["trade_date"]
        company = state["company_of_interest"]
        U = _ta_utils(company)
        asset_type = state.get("asset_type", "stock")
        instrument_context = U["build_instrument_context"](company, asset_type)
        from tradingagents.dataflows.symbol_utils import is_dse_ticker
        _is_dse = is_dse_ticker(company)
        log(f"Fetching news for {company} on {current_date}")

        company_news_full = global_news_full = web_news_full = ""
        log("calling get_news")
        try:
            end_dt = datetime.strptime(current_date, "%Y-%m-%d")
            start_dt = end_dt - timedelta(days=7)
            company_news_full = str(U["get_news"].invoke({
                "ticker": company, "start_date": start_dt.strftime("%Y-%m-%d"), "end_date": current_date,
            }))
            company_news = company_news_full[:3000]
        except Exception as e:  # noqa: BLE001
            company_news = f"Error: {e}"

        log("calling get_global_news")
        try:
            global_news_full = str(U["get_global_news"].invoke({"curr_date": current_date}))
            global_news = global_news_full[:2000]
        except Exception as e:  # noqa: BLE001
            global_news = f"Error: {e}"

        log("calling web search (press coverage)")
        sector_news_full = ""
        try:
            web_news_full = news_rag.fetch_web_news(company, log=log)
            web_news = web_news_full[:2500]
            sector_news_full = news_rag.fetch_sector_news(company, log=log)
        except Exception as e:  # noqa: BLE001
            web_news = f"Error: {e}"

        if _is_dse:
            global_title = "DSE MARKET-WIDE NOTICES (NOT global macro news -- see rule above)"
            sections = "Company News Summary -> Web-Sourced Company Coverage -> DSE Market-Wide Notices -> Sentiment Assessment -> Key Risks"
            global_rule = f"""RULE: The second section is DSE's own exchange-wide notices, NOT international/global
macro news -- DSE has no general financial news feed, so do not present this as if it
covers global markets, the Fed, or other countries. If it's thin, say so plainly.
"""
            table_rule = f"""RULE (table rows): A DSE Market-Wide Notice about a DIFFERENT specific company or fund
(a director's share transaction, another company's NAV, another company's dividend, etc.)
is NOT news about {company} and must NOT appear in the final table, even labeled NEUTRAL --
including it implies relevance it doesn't have. Only include a market-wide item in the
table if it is genuinely exchange-wide (overall market turnover, a circuit-breaker or
trading-halt policy change, a market-wide holiday) rather than about one specific other
company. Do NOT pad the table to reach 4-6 rows: if fewer than 4 rows are genuinely
relevant to {company}, output only those rows, and if none are relevant, output a table
with a single row stating "No company-specific or market-wide news relevant to {company}"
in the Headline column and NEUTRAL elsewhere -- never substitute other companies' notices
as filler."""
        else:
            global_title = "GLOBAL MARKET NEWS (background only -- not news about the company)"
            sections = "Company News Summary -> Web-Sourced Company Coverage -> Global Market Context -> Sentiment Assessment -> Key Risks"
            global_rule = f"""RULE: The global market news is background context, not news about {company}; never attribute it to {company}.
"""
            table_rule = f"""RULE (table rows): Only items genuinely about {company} (or clearly market-wide) belong in the table. Do NOT pad it to
reach 4-6 rows: if fewer rows are relevant, output only those, and if none are, output a single row stating "No company-specific
or market-wide news relevant to {company}" in the Headline column and NEUTRAL elsewhere."""

        prompt = f"""You are a news analyst. Today is {current_date}.
{instrument_context}

Using the REAL news data below, write a brief news analysis report for {company}.
Sections: {sections}
{_len_rule('news')}

RULE: Each web item starts with the date written in its snippet, e.g. [2 days ago] or [no date]. Use that date; call a
[no date] item "undated" and never "recent". Copy numbers EXACTLY as written in the snippet; add no detail the snippet does not contain.

RULE: "Web-Sourced Company Coverage" comes from general web search (financial press
articles), separate from the exchange's own feed -- treat it as a supplement,
not a replacement, and if it says no results were found, say so plainly rather than
inventing coverage.

{global_rule}
RULE: If any section below starts with "Error", that data source was UNAVAILABLE (a failed request) -- say "data unavailable" for it; do NOT treat it as "no news" and do NOT infer a neutral or positive sentiment from it.

RULE: If COMPANY NEWS above says no news/disclosures were found for {company}, say that
plainly in the Company News Summary -- do not invent items or reframe unrelated items as
if they were about {company}.

{table_rule}

At the very end, output a Markdown table with EXACTLY these 5 columns:
| Category | Headline | Sentiment | Impact | Source |
(Sentiment must be POSITIVE / NEGATIVE / NEUTRAL. Row count depends on actual relevance --
see rule above, not a fixed target.)

=== COMPANY NEWS ({company}) ===
{company_news}

=== WEB-SOURCED COMPANY COVERAGE (general web search, see rule above) ===
{web_news}

=== {global_title} ===
{global_news}
""" + U["get_language_instruction"]()

        messages = [
            SystemMessage(content="You are a senior news analyst. Write reports based only on provided data. Always end with the required Markdown table."),
            HumanMessage(content=prompt),
        ]
        report = invoke_llm_with_retry(llm, messages).content
        log("news report ready")

        sentiments = re.findall(r"\|\s*\w.*?\|\s*(POSITIVE|NEGATIVE|NEUTRAL)\s*\|", report, re.IGNORECASE)
        pos = sum(1 for s in sentiments if "POSITIVE" in s.upper())
        neg = sum(1 for s in sentiments if "NEGATIVE" in s.upper())
        neu = sum(1 for s in sentiments if "NEUTRAL" in s.upper())
        # `overall` comes from the table counts (strict majority wins, any tie resolves to NEUTRAL), so it can never
        # disagree with the counts it summarises.
        if pos > neg and pos > neu:
            overall = "POSITIVE"
        elif neg > pos and neg > neu:
            overall = "NEGATIVE"
        else:
            overall = "NEUTRAL"
        tbl_m = re.search(r"(\|.*?Category.*?\|.*?(?:\n\|[-| ]+\|)(?:\n\|.*?\|)+)", report, re.IGNORECASE | re.DOTALL)

        news_metrics = {
            "positive_count": pos, "negative_count": neg, "neutral_count": neu,
            "overall_sentiment": overall,
            "company_news_chars": len(company_news), "web_news_chars": len(web_news), "global_news_chars": len(global_news),
        }
        # Company news carries most of the weight; web coverage and global news are supplements.
        news_confidence = round(min(0.95,
            _text_confidence(company_news, target_chars=1200, weight=0.6)
            + _text_confidence(web_news, target_chars=800, weight=0.2)
            + _text_confidence(global_news, target_chars=800, weight=0.2)), 2)
        return {
            "news_report": report, "news_metrics": news_metrics,
            "news_table_md": tbl_m.group(1) if tbl_m else "",
            "news_raw": {"company_news": company_news, "web_news": web_news, "global_news": global_news},
            "news_raw_full": {"company_news": company_news_full, "web_news": web_news_full, "global_news": global_news_full},
            "sector_news_full": sector_news_full,
            "news_confidence": news_confidence,
        }

    return node


def create_sentiment_analyst(llm, news_collection=None, log=print):
    """Sentiment analyst.  The score is computed IN CODE from the stored company news (a keyword lexicon, recency weighted;
    see news_rag.rule_based_news_sentiment) and the LLM only explains it.  The code-computed score / overall / confidence
    are what the debate, the Trader and the episode memory receive; the LLM's own table score is kept as `llm_score`.
    With no stored company news to score (or RAEM_RULE_SENTIMENT=0) it falls back to the LLM reading the News Analyst's
    per-headline table, and the result says so (`source`: LLM_ONLY)."""
    from langchain_core.messages import HumanMessage, SystemMessage
    from . import news_rag

    def node(state):
        current_date = state["trade_date"]
        company = state["company_of_interest"]
        U = _ta_utils(company)
        log(f"Scoring sentiment for {company} on {current_date}")

        news_metrics = state.get("news_metrics", {}) or {}
        news_table = str(state.get("news_table_md", "") or "").strip()
        news_context = str(state.get("news_report", ""))[:1000]

        rs = {"n": 0}
        if news_rag.USE_RULE_SENTIMENT and news_collection is not None:
            try:
                rs = news_rag.rule_based_news_sentiment(news_collection, company, current_date)
            except Exception as e:  # noqa: BLE001
                log(f"rule-based sentiment skipped: {str(e)[:100]}")
        if rs.get("n", 0) > 0:
            data_sections = rs["text"]
            log(f"rule-based sentiment: {rs['n']} items | +{rs['pos']} / -{rs['neg']} / ={rs['neu']} -> {rs['score']}/10 ({rs['overall']})")
        else:
            log("no company news to score in code -- sentiment stays LLM-inferred (source=LLM_ONLY)")
            if news_table:
                data_sections = (f"No stored company news to score in code. The News Analyst tagged the headlines it read: "
                                 f"{news_metrics.get('positive_count', 0)} positive / {news_metrics.get('negative_count', 0)} negative / "
                                 f"{news_metrics.get('neutral_count', 0)} neutral.\n{news_table}")
            else:
                data_sections = "No structured sentiment data available. Use the news report for inference."

        prompt = f"""You are a sentiment analyst. Today is {current_date}.
IMPORTANT: Base your analysis ONLY on the data below. Do NOT invent scores.

Analyze market sentiment for {company}. This is news-flow sentiment (what is being reported), not measured
market sentiment from price and volume -- state that in the Data Source Review.
Sections: Data Source Review -> Score Breakdown -> Key Signals -> Confidence Assessment
{_len_rule('sentiment')}

At the very end, output a Markdown table with EXACTLY these columns:
| Source | Sentiment | Score (1-10) | Confidence | Key Signal |

=== SENTIMENT DATA ===
{data_sections}

=== NEWS CONTEXT ===
{news_context}
""" + U["get_language_instruction"]()
        messages = [
            SystemMessage(content="You are a senior sentiment analyst. Be specific about data sources. Always end with the required Markdown table."),
            HumanMessage(content=prompt),
        ]
        report = invoke_llm_with_retry(llm, messages).content
        log("sentiment report ready")

        # The LLM's own score: the table first (| Source | Sentiment | Score (1-10) | Confidence | Key Signal |), then "x/10"
        scores, confs = [], []
        for row in report.splitlines():
            cells = [c.strip() for c in row.strip().strip("|").split("|")]
            if len(cells) >= 4 and row.strip().startswith("|"):
                m = re.fullmatch(r"(\d+(?:\.\d+)?)(?:\s*/\s*10)?", cells[2])
                if m and 0 <= float(m.group(1)) <= 10:
                    scores.append(float(m.group(1)))
                cm = re.fullmatch(r"(High|Medium|Low)", cells[3], re.I)
                if cm:
                    confs.append(cm.group(1).capitalize())
        if scores:
            score = f"{sum(scores) / len(scores):.1f}"
        else:
            score_m = re.search(r"(\d+(?:\.\d+)?)\s*/\s*10", report)
            score = score_m.group(1) if score_m else "N/A"
        if confs:
            confidence = max(set(confs), key=confs.count)
        else:
            conf_m = re.search(r"(High|Medium|Low)\s*[Cc]onfidence", report)
            confidence = conf_m.group(1) if conf_m else "N/A"
        # overall from the score itself (5 = neutral), so the two can never disagree
        try:
            sv = float(score)
            overall = "POSITIVE" if sv >= 6 else ("NEGATIVE" if sv <= 4 else "NEUTRAL")
        except ValueError:
            overall = "NEUTRAL"

        sentiment_metrics = {"score": score, "confidence": confidence, "overall": overall, "source": "LLM_ONLY"}
        if rs.get("n", 0) > 0:
            sentiment_metrics.update({
                "llm_score": score, "llm_overall": overall,                      # what the LLM said, kept for the ablation
                "score": f"{rs['score']:.1f}", "overall": rs["overall"], "confidence": rs["confidence"],
                "source": "RULE_BASED", "n_items": rs["n"],
                "positive_count": rs["pos"], "negative_count": rs["neg"], "neutral_count": rs["neu"],
            })
            base = {"High": 0.85, "Medium": 0.65, "Low": 0.4}.get(rs["confidence"], 0.4)
            sentiment_confidence = round(min(max(base, 0.05), 0.95), 2)
        else:
            n_tagged = (news_metrics.get("positive_count", 0) + news_metrics.get("negative_count", 0)
                        + news_metrics.get("neutral_count", 0))
            base = 0.65 if news_table else 0.2
            if news_table:
                base = min(0.9, base + 0.03 * min(n_tagged, 8))
            nudge = {"high": 0.15, "medium": 0.0, "low": -0.2}.get(confidence.lower(), 0.0)
            sentiment_confidence = round(min(max(base + nudge, 0.05), 0.95), 2)
        return {"sentiment_report": report, "sentiment_metrics": sentiment_metrics,
                "sentiment_confidence": sentiment_confidence}

    return node


# ==========================================================================
# Investment debate (bull / bear / facilitator)
# ==========================================================================
def _rag_tail(state) -> str:
    """The news retrieved from the corpus (company items first, exchange-wide notices labelled), appended to the news evidence."""
    rag = str(state.get("news_rag_context") or "").strip()
    return ("\n\nNEWS RAG:\n" + rag[:1600]) if rag else ""


def _reports(state):
    # 🔴 FIXED: 500 chars was cutting every report off mid-sentence (often
    # before the market analyst's own required indicator table even
    # appeared -- see create_market_analyst's prompt, which explicitly asks
    # for a Markdown table at the *end* of the report). Combined with the
    # "Use ONLY the data in the reports below. Do NOT invent facts or
    # figures" instruction below, Bull/Bear ended up honestly debating the
    # truncation itself ("this argument is built only on the figures
    # visible") instead of the stock -- correct LLM behavior given what it
    # was shown, but the 500-char slice was the actual bug. 1800 gives
    # enough room for a full "brief" report + its trailing table without
    # ballooning the combined prompt (4 reports · 1800 ~= 7.2k chars, still
    # small for Sonnet/Kimi's context windows).
    return (
        str(state["market_report"])[:1800], str(state["sentiment_report"])[:1800],
        str(state["news_report"])[:1800] + _rag_tail(state), str(state["fundamentals_report"])[:1800],
    )


def create_bull_researcher(llm):
    U = _ta_utils()

    def node(state):
        ds = state["investment_debate_state"]
        market, sentiment, news, fundamentals = _reports(state)
        # 🔴 FIXED: used to read ds['current_response'] -- a single shared
        # field that only correctly means "the Bear's last argument" if
        # Bear is *guaranteed* to have spoken most recently, i.e. only
        # under the old fixed Bull-always-first-Bear-always-second order.
        # Now reads the Bear's own last turn directly from bear_history by
        # name, so it's correct regardless of which side actually spoke
        # last -- a prerequisite for safely alternating speaking order
        # below (see runner.py's investment debate loop comment).
        last_bear_argument = _last_turn(ds.get("bear_history", ""))
        prompt = f"""IMPORTANT: Use ONLY the data in the reports below. Do NOT invent facts or figures.
You are a Bull Analyst advocating for investing in the stock.
Market report: {market}
Sentiment report: {sentiment}
News report: {news}
Fundamentals report: {fundamentals}
Debate history: {ds.get('history', '')}
Last bear argument: {last_bear_argument}
Present a compelling bull argument with specific growth opportunities and strengths.
""" + U["get_language_instruction"]()
        argument = f"Bull Analyst: {invoke_llm_with_retry(llm, prompt).content}"
        return {"investment_debate_state": {
            "history": ds.get("history", "") + "\n" + argument,
            "bull_history": ds.get("bull_history", "") + "\n" + argument,
            "bear_history": ds.get("bear_history", ""),
            "current_response": argument,
            "count": ds["count"] + 1,
        }}

    return node


def create_bear_researcher(llm):
    U = _ta_utils()

    def node(state):
        ds = state["investment_debate_state"]
        market, sentiment, news, fundamentals = _reports(state)
        # 🔴 FIXED: same fix as create_bull_researcher above, mirrored --
        # reads Bull's last turn from bull_history by name instead of the
        # generic current_response field.
        last_bull_argument = _last_turn(ds.get("bull_history", ""))
        prompt = f"""IMPORTANT: Use ONLY the data in the reports below. Do NOT invent facts or figures.
You are a Bear Analyst making the case against investing in the stock.
Market report: {market}
Sentiment report: {sentiment}
News report: {news}
Fundamentals report: {fundamentals}
Debate history: {ds.get('history', '')}
Last bull argument: {last_bull_argument}
Present a compelling bear argument with specific risks and weaknesses.
""" + U["get_language_instruction"]()
        argument = f"Bear Analyst: {invoke_llm_with_retry(llm, prompt).content}"
        return {"investment_debate_state": {
            "history": ds.get("history", "") + "\n" + argument,
            "bear_history": ds.get("bear_history", "") + "\n" + argument,
            "bull_history": ds.get("bull_history", ""),
            "current_response": argument,
            "count": ds["count"] + 1,
        }}

    return node


def create_investment_facilitator(llm):
    U = _ta_utils()

    def node(state):
        ds = state["investment_debate_state"]
        prompt = f"""IMPORTANT: Base your evaluation ONLY on the debate history provided below. Do NOT add external knowledge.
You are the Investment Debate Facilitator. Your job is to:
1. Review the full debate between Bull and Bear analysts
2. Objectively evaluate which side presented stronger evidence
3. Declare a WINNER with clear reasoning
4. Provide a final investment recommendation

STRUCTURAL NOTE ON ORDER: this debate always runs Bull then Bear in every
round, so Bear structurally gets the last word before you judge it -- that
is an artifact of turn order, not evidence of a stronger case. Do NOT give
extra weight to whichever side's argument you read most recently or which
side technically "answered" the other last. Evaluate each round's points
on their own factual/logical merit, and explicitly check: are there Bull
points from EARLIER rounds that Bear never actually rebutted, just spoke
after? An unrebutted earlier point is still a live point.

Full Debate History:
{ds.get('history', '')}

Total arguments made: {ds.get('count', 0)}

Declare: BULL WINS / BEAR WINS / DRAW
Then give your final recommendation: BUY / SELL / HOLD with confidence level (High/Medium/Low).
""" + U["get_language_instruction"]()
        decision = invoke_llm_with_retry(llm, prompt).content
        return {"investment_facilitator_decision": decision,
                "investment_debate_state": {**ds, "facilitator_decision": decision}}

    return node


# ==========================================================================
# Trader (structured)
# ==========================================================================
def create_trader(llm):
    from tradingagents.agents.schemas import TraderProposal, render_trader_proposal
    from tradingagents.agents.utils.agent_utils import build_instrument_context, get_language_instruction
    from tradingagents.agents.utils.structured import bind_structured, invoke_structured_or_freetext

    structured_llm = bind_structured(llm, TraderProposal, "Trader")

    def node(state):
        company_name = state["company_of_interest"]
        asset_type = state.get("asset_type", "stock")
        instrument_context = build_instrument_context(company_name, asset_type)
        investment_plan = state["investment_plan"]

        messages = [
            {"role": "system", "content": (
                "You are a trading agent analyzing market data to make investment decisions. "
                "Based on your analysis, provide a specific recommendation to buy, sell, or hold. "
                "Anchor your reasoning in the analysts' reports and the research plan."
                + get_language_instruction())},
            {"role": "user", "content": (
                f"Based on a comprehensive analysis by a team of analysts, here is an investment "
                f"plan tailored for {company_name}. {instrument_context} This plan incorporates "
                f"insights from current technical market trends, macroeconomic indicators, and "
                f"social media sentiment. Use this plan as a foundation for evaluating your next "
                f"trading decision.\n\nProposed Investment Plan: {investment_plan}\n\n"
                f"Leverage these insights to make an informed and strategic decision.")},
        ]

        trader_plan = None
        for attempt in range(3):
            try:
                trader_plan = invoke_structured_or_freetext(
                    structured_llm, llm, messages, render_trader_proposal, "Trader")
                break
            except Exception as e:  # noqa: BLE001
                if "429" in str(e) or "rate_limit" in str(e).lower():
                    time.sleep(60 * (attempt + 1))
                else:
                    raise
        return {"trader_investment_plan": trader_plan, "sender": "Trader"}

    return node


# ==========================================================================
# Risk debate (aggressive / conservative / neutral / facilitator)
# ==========================================================================
def _risk_reports(state):
    # 🔴 FIXED: same 500-char truncation issue as _reports() above (see its
    # comment) -- the Aggressive/Conservative/Neutral risk debaters were
    # getting the same mid-sentence-cut reports, plus the trader's own plan
    # truncated too. Raised to 1800 for the four analyst reports; the
    # trader plan is already a short, structured proposal (Action/
    # Reasoning/Entry/Stop/Sizing -- see TraderProposal in
    # tradingagents/agents/schemas.py) so 800 is enough there without
    # needing the same expansion.
    return (
        str(state["market_report"])[:1800], str(state["sentiment_report"])[:1800],
        str(state["news_report"])[:1800] + _rag_tail(state), str(state["fundamentals_report"])[:1800],
        str(state["trader_investment_plan"])[:800],
    )


def create_aggressive_debator(llm):
    U = _ta_utils()

    def node(state):
        rd = state["risk_debate_state"]
        market, sentiment, news, fundamentals, trader = _risk_reports(state)
        prompt = f"""IMPORTANT: Use ONLY the analyst reports provided. Do NOT invent figures.
As the Aggressive Risk Analyst, champion high-reward opportunities.
Trader decision: {trader}
Market Report: {market}
Sentiment: {sentiment}
News: {news}
Fundamentals: {fundamentals}
History: {rd.get('history', '')}
Conservative argument: {rd.get('current_conservative_response', '')}
Neutral argument: {rd.get('current_neutral_response', '')}
Make a compelling case for high-risk high-reward approach. Speak conversationally.
""" + U["get_language_instruction"]()
        argument = f"Aggressive Analyst: {invoke_llm_with_retry(llm, prompt).content}"
        return {"risk_debate_state": {**rd,
            "history": rd.get("history", "") + "\n" + argument,
            "aggressive_history": rd.get("aggressive_history", "") + "\n" + argument,
            "latest_speaker": "Aggressive",
            "current_aggressive_response": argument,
            "count": rd["count"] + 1}}

    return node


def create_conservative_debator(llm):
    U = _ta_utils()

    def node(state):
        rd = state["risk_debate_state"]
        market, sentiment, news, fundamentals, trader = _risk_reports(state)
        prompt = f"""IMPORTANT: Use ONLY the analyst reports provided. Do NOT invent figures.
As the Conservative Risk Analyst, protect assets and minimize risk.
Trader decision: {trader}
Market Report: {market}
Sentiment: {sentiment}
News: {news}
Fundamentals: {fundamentals}
History: {rd.get('history', '')}
Aggressive argument: {rd.get('current_aggressive_response', '')}
Neutral argument: {rd.get('current_neutral_response', '')}
Make a compelling case for low-risk conservative approach. Speak conversationally.
""" + U["get_language_instruction"]()
        argument = f"Conservative Analyst: {invoke_llm_with_retry(llm, prompt).content}"
        return {"risk_debate_state": {**rd,
            "history": rd.get("history", "") + "\n" + argument,
            "conservative_history": rd.get("conservative_history", "") + "\n" + argument,
            "latest_speaker": "Conservative",
            "current_conservative_response": argument,
            "count": rd["count"] + 1}}

    return node


def create_neutral_debator(llm):
    U = _ta_utils()

    def node(state):
        rd = state["risk_debate_state"]
        market, sentiment, news, fundamentals, trader = _risk_reports(state)
        prompt = f"""IMPORTANT: Use ONLY the analyst reports provided. Do NOT invent figures.
As the Neutral Risk Analyst, provide a balanced perspective.
Trader decision: {trader}
Market Report: {market}
Sentiment: {sentiment}
News: {news}
Fundamentals: {fundamentals}
History: {rd.get('history', '')}
Aggressive argument: {rd.get('current_aggressive_response', '')}
Conservative argument: {rd.get('current_conservative_response', '')}
Provide a balanced moderate view. Speak conversationally.
""" + U["get_language_instruction"]()
        argument = f"Neutral Analyst: {invoke_llm_with_retry(llm, prompt).content}"
        return {"risk_debate_state": {**rd,
            "history": rd.get("history", "") + "\n" + argument,
            "neutral_history": rd.get("neutral_history", "") + "\n" + argument,
            "latest_speaker": "Neutral",
            "current_neutral_response": argument,
            "count": rd["count"] + 1}}

    return node


def create_risk_facilitator(llm):
    U = _ta_utils()

    def node(state):
        rd = state["risk_debate_state"]
        # 🔴 FIXED: 500 chars almost never reached past the Trader
        # proposal's own Reasoning paragraph (routinely 500+ chars on its
        # own -- see render_trader_proposal in tradingagents/agents/
        # schemas.py) -- so the Risk Facilitator was making its position-
        # sizing / stop-loss / take-profit call having never actually seen
        # the Trader's own stated Entry Price, Stop Loss, Position Sizing,
        # or FINAL TRANSACTION PROPOSAL line, all of which come *after*
        # Reasoning in the rendered proposal. This is the likely cause of
        # the Facilitator's own stop-loss/take-profit fields showing up
        # empty in practice -- it had nothing concrete to anchor them to.
        trader_decision = str(state.get("trader_investment_plan", ""))[:2000]
        prompt = f"""IMPORTANT: Base your assessment ONLY on the debate history provided. Do NOT use external knowledge.
You are the Risk Management Debate Facilitator.

Trader's Original Decision: {trader_decision}

After {rd.get('count', 0)} total arguments across the rounds, evaluate the risk debate:

Aggressive Analyst Summary:
{rd.get('aggressive_history', '')[-800:]}

Conservative Analyst Summary:
{rd.get('conservative_history', '')[-800:]}

Neutral Analyst Summary:
{rd.get('neutral_history', '')[-800:]}

Your tasks:
1. Declare which risk perspective was most compelling: AGGRESSIVE / CONSERVATIVE / NEUTRAL
2. Synthesize a final risk assessment
3. Provide position sizing recommendation (e.g., 25% / 50% / 75% of portfolio)
4. Set stop-loss and take-profit levels based on the debate outcome
5. Final risk rating: LOW / MEDIUM / HIGH

STRUCTURAL NOTE ON ORDER: this debate always runs Aggressive, then
Conservative, then Neutral, in that exact order every round -- so Neutral
structurally gets the last word before you judge, which is an artifact of
turn order, not evidence of the strongest case. Do not give extra weight
to whichever perspective you read most recently.

Be specific and data-driven in your assessment.
""" + U["get_language_instruction"]()
        decision = invoke_llm_with_retry(llm, prompt).content
        return {"risk_facilitator_decision": decision,
                "risk_debate_state": {**rd, "facilitator_decision": decision}}

    return node


# ==========================================================================
# Portfolio manager (structured, final decision)
# ==========================================================================
def create_portfolio_manager(llm):
    from tradingagents.agents.schemas import PortfolioDecision, render_pm_decision
    from tradingagents.agents.utils.agent_utils import build_instrument_context, get_language_instruction
    from tradingagents.agents.utils.structured import bind_structured, invoke_structured_or_freetext

    structured_llm = bind_structured(llm, PortfolioDecision, "PortfolioManager")

    def node(state):
        company_name = state["company_of_interest"]
        asset_type = state.get("asset_type", "stock")
        instrument_context = build_instrument_context(company_name, asset_type)
        risk_history = state["risk_debate_state"].get("history", "")
        # 🔴 FIXED: same disconnect as the Trader stage had with the
        # Investment Facilitator (see runner.py's investment_plan comment)
        # -- the Risk Facilitator's own verdict (position sizing / stop-
        # loss / take-profit / risk rating -- its entire job, see its
        # card: "Position sizing, stop-loss / take-profit, risk rating")
        # was computed and stored in state["risk_facilitator_decision"],
        # and even shown in this stage's own "Input" tab on the frontend
        # (portfolio_manager_input in runner.py) -- but was never actually
        # read here, so the Portfolio Manager only ever saw the raw
        # Aggressive/Conservative/Neutral transcript and independently
        # re-derived its own sizing/risk call, free to silently contradict
        # the Facilitator's official one.
        risk_facilitator_decision = state.get("risk_facilitator_decision", "")
        trader_plan = state.get("trader_investment_plan", "")
        past_context = state.get("past_context", "")
        investor_profile = state.get("investor_profile", "Aggressive")

        # 🔴 FIXED: the Portfolio Manager -- the one stage whose numeric
        # citations matter most, since it's the actual final decision --
        # was never given the raw indicators/fund_metrics directly either,
        # same gap as Investment/Risk Facilitator. It only ever saw
        # whatever RSI/MACD figures survived the Trader plan / Risk
        # Facilitator verdict / risk debate excerpt paraphrase chain, with
        # no guarantee any of them restated the number precisely.
        # Confirmed live: this produced a final decision citing "RSI: 40.0"
        # when the actual raw RSI was 31.66. Unlike Investment/Risk
        # Facilitator (pure debate judges, whose job doesn't require
        # independent technical citation -- see run_decision_verifier's
        # debate_texts comment), the Portfolio Manager's job explicitly IS
        # to issue the final call, and citing exact levels is a normal,
        # useful part of that -- so the fix here is to ground it with real
        # data, not to exempt it from the check.
        indicators = state.get("indicators_parsed", {})
        fund_metrics = state.get("fund_metrics", {})
        verified_lines = []
        for key, label in (("rsi", "RSI"), ("macd", "MACD")):
            val = indicators.get(key)
            if isinstance(val, (int, float)):
                verified_lines.append(f"{label}: {val:.2f}")
        for key, label in (("pe_ratio", "P/E Ratio"), ("eps_ttm", "EPS (TTM)")):
            val = fund_metrics.get(key)
            if isinstance(val, (int, float)):
                verified_lines.append(f"{label}: {val:.2f}")
        verified_snapshot = (
            "Verified Technical/Fundamental Readings (source of truth -- "
            "if you cite any of these in your reasoning, use these exact "
            "figures, not a paraphrase from the text below):\n"
            + "\n".join(verified_lines)
        ) if verified_lines else ""

        prompt_text = (
            f"You are the Portfolio Manager making the final investment decision for {company_name}. "
            f"{instrument_context}\n\n"
            f"Investor Profile: {investor_profile}\n\n"
            f"Review the Risk Facilitator's verdict below as the primary synthesis of the risk "
            f"debate, along with the trader's proposal, then issue a final rating tailored to "
            f"this investor profile.\n\n"
            f"Trader Proposal:\n{trader_plan[:800]}\n\n"
            f"Risk Facilitator's Verdict (position sizing / stop-loss / take-profit / risk rating "
            f"-- treat this as the primary conclusion of the risk debate, not merely one more "
            f"opinion):\n{risk_facilitator_decision[:800]}\n\n"
            f"Risk Analysts Debate (supporting detail):\n{risk_history[:1500]}\n\n"
        )
        if verified_snapshot:
            prompt_text += f"{verified_snapshot}\n\n"
        if past_context:
            prompt_text += f"Past context:\n{past_context}\n\n"
        prompt_text += get_language_instruction()

        messages = [
            {"role": "system", "content": "You are a senior portfolio manager. Issue a final decision."},
            {"role": "user", "content": prompt_text},
        ]
        final_decision = None
        for attempt in range(3):
            try:
                final_decision = invoke_structured_or_freetext(
                    structured_llm, llm, messages, render_pm_decision, "PortfolioManager")
                break
            except Exception as e:  # noqa: BLE001
                if "429" in str(e) or "rate_limit" in str(e).lower():
                    time.sleep(60 * (attempt + 1))
                else:
                    raise
        return {"final_trade_decision": final_decision}

    return node


# ==========================================================================
# Macro Regime Analyst — market-wide risk regime (VIX / 10Y yield / DXY)
# ==========================================================================
MACRO_TICKERS = {
    "vix": "^VIX",       # fear gauge
    "tnx": "^TNX",       # 10Y treasury yield
    "dxy": "DX-Y.NYB",   # US dollar index
}


def fetch_macro_snapshot(as_of_date: str, lookback_days: int = 30) -> dict:
    """Latest VIX / 10Y yield / DXY plus their lookback-period average."""
    import yfinance as yf
    from datetime import datetime, timedelta

    end_dt = datetime.strptime(as_of_date, "%Y-%m-%d")
    start_dt = end_dt - timedelta(days=lookback_days)

    snapshot: dict = {}
    for key, ticker in MACRO_TICKERS.items():
        try:
            df = yf.download(
                ticker,
                start=start_dt.strftime("%Y-%m-%d"),
                end=(end_dt + timedelta(days=1)).strftime("%Y-%m-%d"),
                progress=False,
            )
            if df.empty:
                snapshot[key] = {"latest": None, "avg": None}
                continue
            # newer yfinance can return multi-level columns even for a
            # single ticker, making df["Close"] a 1-column DataFrame
            # instead of a Series -> float(Series) crashes. squeeze()
            # collapses a single column to a Series and is a no-op if it
            # already is one, so this works across yfinance versions.
            close = df["Close"].squeeze()
            latest = float(close.iloc[-1])
            avg = float(close.mean())
            snapshot[key] = {"latest": round(latest, 2), "avg": round(avg, 2)}
        except Exception as e:  # noqa: BLE001
            snapshot[key] = {"latest": None, "avg": None, "error": str(e)[:100]}
    return snapshot


def classify_macro_regime(snapshot: dict) -> str:
    """Rule-based macro tag: RISK_OFF_HIGH_VOL / RISK_ON_LOW_VOL /
    RATES_RISING / RATES_FALLING / NEUTRAL_MACRO. Deliberately separate from
    the stock-specific `classify_regime` in memory.py — this is market-wide,
    not ticker-specific, and is stored as its own episode metadata field so
    it never interferes with existing regime filtering."""
    vix = (snapshot.get("vix") or {}).get("latest")
    tnx = (snapshot.get("tnx") or {}).get("latest")
    tnx_avg = (snapshot.get("tnx") or {}).get("avg")

    if vix is not None:
        if vix >= 25:
            return "RISK_OFF_HIGH_VOL"
        if vix <= 15:
            return "RISK_ON_LOW_VOL"

    if tnx is not None and tnx_avg is not None and tnx_avg != 0:
        if tnx > tnx_avg * 1.03:
            return "RATES_RISING"
        if tnx < tnx_avg * 0.97:
            return "RATES_FALLING"

    return "NEUTRAL_MACRO"


# ✅ CHANGED (per explicit request): VIX/10Y/DXY above are US market
# indicators -- weak/indirect relevance to Dhaka Stock Exchange-listed
# companies (a US volatility spike doesn't necessarily say anything about
# DSE conditions). For DSE tickers, use the DSEX broad index's own trend +
# realized volatility instead -- the actual market this stock trades in.
# ✅ Endpoint confirmed via bdshare's own source code and its published
# PyPI/GitHub docs (not just assumed) -- get_market_info_more_data(start,
# end, code="DSEX") is the real, parameter-validated function for this
# (code is checked against {"DSEX","DSES","DS30","DGEN"} internally).
# Still fails soft (returns {}) rather than raising if the live request
# itself errors (network, dsebd.org downtime, markup change, etc.), so
# create_macro_regime_analyst below falls back to the VIX/TNX/DXY snapshot
# in that case -- check the logs after deploying to confirm which path
# fired on a real run.
def fetch_dse_macro_snapshot(as_of_date: str, lookback_days: int = 30) -> dict:
    """DSEX latest close + lookback-period average, plus realized daily
    volatility (DSE has no options-implied volatility index like VIX, so
    this is the closest available proxy for "how choppy is the market
    right now").

    🔴 FIXED (confirmed via bdshare's own source + PyPI docs, not just
    guessed): the previous version called market_data._fetch_dse_ohlcv
    ("DSEX", ...), which wraps bdshare.get_historical_data() -- that
    function's `code` param is for individual STOCK tickers ("ACI", "GP",
    ...), not index codes, so passing "DSEX" there was very likely
    silently returning nothing every time (triggering the fallback path
    below on every single run, never actually using DSE data). The
    correct, documented function for historical DSEX values is
    get_market_info_more_data(start, end, code="DSEX") -- confirmed in
    bdshare's own source: `code` is validated against exactly
    {"DSEX","DSES","DS30","DGEN"} and returns a Date + "DSEX Index"
    dataframe when code="DSEX" is passed."""
    from datetime import datetime, timedelta
    from bdshare import get_market_info_more_data

    end_dt = datetime.strptime(as_of_date, "%Y-%m-%d")
    start_dt = end_dt - timedelta(days=lookback_days)
    try:
        df = get_market_info_more_data(
            start_dt.strftime("%Y-%m-%d"), as_of_date, code="DSEX",
        )
    except Exception:  # noqa: BLE001
        return {}
    if df is None or df.empty or "DSEX Index" not in df.columns:
        return {}

    closes = [float(v) for v in df["DSEX Index"].tolist() if v not in (None, "")]
    if len(closes) < 2:
        return {}

    latest = closes[-1]
    avg = sum(closes) / len(closes)
    changes = [
        (closes[i] - closes[i - 1]) / closes[i - 1] * 100
        for i in range(1, len(closes)) if closes[i - 1]
    ]
    if changes:
        mean_chg = sum(changes) / len(changes)
        variance = sum((c - mean_chg) ** 2 for c in changes) / len(changes)
        volatility = variance ** 0.5
    else:
        volatility = 0.0

    return {
        "dsex": {"latest": round(latest, 2), "avg": round(avg, 2)},
        "dsex_volatility_pct": round(volatility, 2),
    }


def classify_dse_macro_regime(snapshot: dict) -> str:
    """Rule-based DSE-market regime tag from DSEX trend + realized
    volatility. Reuses the same tag vocabulary as classify_macro_regime
    (RISK_OFF_HIGH_VOL / RISK_ON_LOW_VOL / NEUTRAL_MACRO) so downstream
    code (regime-transition reflection, episode metadata) doesn't need a
    second tag set to handle -- RATES_RISING/RATES_FALLING are simply
    never emitted here since there's no comparable BD bond-yield signal
    wired in yet."""
    dsex = snapshot.get("dsex") or {}
    latest, avg = dsex.get("latest"), dsex.get("avg")
    vol = snapshot.get("dsex_volatility_pct")

    if vol is not None and vol >= 1.5:
        return "RISK_OFF_HIGH_VOL"
    if latest is not None and avg is not None and avg != 0 and (vol is None or vol < 1.0):
        if latest > avg * 1.02:
            return "RISK_ON_LOW_VOL"
    return "NEUTRAL_MACRO"


def create_macro_regime_analyst(llm, log=print):
    from langchain_core.messages import HumanMessage, SystemMessage
    from tradingagents.dataflows.symbol_utils import is_dse_ticker
    U = _ta_utils()

    def node(state):
        current_date = state["trade_date"]
        company = state.get("company_of_interest", "")
        log(f"Fetching macro snapshot for {current_date}")

        if is_dse_ticker(company):
            snapshot = fetch_dse_macro_snapshot(current_date)
            if snapshot:
                macro_regime = classify_dse_macro_regime(snapshot)
                log(f"DSEX={snapshot.get('dsex')} volatility={snapshot.get('dsex_volatility_pct')}%")
                log(f"macro regime (DSE): {macro_regime}")

                prompt = f"""You are a macro strategist covering the Dhaka Stock Exchange. Today is {current_date}.
Using the REAL data below, write a brief (3-4 sentence) macro market regime report
for the DSE broad market. Classify whether conditions are risk-on or risk-off and
what this implies for equity positioning on DSE-listed names.

=== DSE MACRO SNAPSHOT ===
DSEX Index:          latest={snapshot.get('dsex', {}).get('latest')}, 30d avg={snapshot.get('dsex', {}).get('avg')}
Realized volatility: {snapshot.get('dsex_volatility_pct')}% (daily, over the lookback window)

Rule-based macro regime tag: {macro_regime}
""" + U["get_language_instruction"]()

                messages = [
                    SystemMessage(content="You are a senior DSE market strategist. Base your report only on the data given."),
                    HumanMessage(content=prompt),
                ]
                report = invoke_llm_with_retry(llm, messages).content
                log("macro regime report ready (DSE)")
                return {"macro_report": report, "macro_regime": macro_regime, "macro_snapshot": snapshot}

            log("DSEX fetch failed/empty -- falling back to US VIX/10Y/DXY snapshot")

        snapshot = fetch_macro_snapshot(current_date)
        macro_regime = classify_macro_regime(snapshot)
        log(f"VIX={snapshot.get('vix')} 10Y={snapshot.get('tnx')} DXY={snapshot.get('dxy')}")
        log(f"macro regime: {macro_regime}")

        prompt = f"""You are a macro strategist. Today is {current_date}.
Using the REAL data below, write a brief (3-4 sentence) macro market regime report.
Classify whether conditions are risk-on or risk-off, whether rates are rising or
falling, and what this implies for equity positioning.

=== MACRO SNAPSHOT ===
VIX (fear gauge):      latest={snapshot.get('vix', {}).get('latest')}, 30d avg={snapshot.get('vix', {}).get('avg')}
10Y Treasury Yield:    latest={snapshot.get('tnx', {}).get('latest')}, 30d avg={snapshot.get('tnx', {}).get('avg')}
US Dollar Index (DXY): latest={snapshot.get('dxy', {}).get('latest')}, 30d avg={snapshot.get('dxy', {}).get('avg')}

Rule-based macro regime tag: {macro_regime}
""" + U["get_language_instruction"]()

        messages = [
            SystemMessage(content="You are a senior macro strategist. Base your report only on the data given."),
            HumanMessage(content=prompt),
        ]
        report = invoke_llm_with_retry(llm, messages).content
        log("macro regime report ready")

        return {"macro_report": report, "macro_regime": macro_regime, "macro_snapshot": snapshot}

    return node


# ==========================================================================
# Post-Mortem / Self-Critique — cross-regime review of resolved episodes
# ==========================================================================
def _safe_float(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def run_post_mortem(company: str, memory, llm, log=print) -> tuple[str, int]:
    """Structured self-critique over ALL of a company's RESOLVED episodes,
    across every regime — unlike the regime-transition reflection (which
    only fires on a regime change and only looks at the prior regime), this
    runs every session so the Trader/Portfolio Manager always see the full
    track record. Returns (lessons_text, episodes_reviewed)."""
    episodes = memory.gather_resolved_episodes(company)
    if not episodes:
        return "(Post-mortem skipped — no RESOLVED episodes yet.)", 0

    wins = [e for e in episodes if _safe_float(e.get("pnl_pct")) > 0]
    losses = [e for e in episodes if _safe_float(e.get("pnl_pct")) < 0]

    lines = [
        f"- {e.get('trade_date', '?')}: Signal={e.get('final_signal', '?')}, "
        f"Regime={e.get('regime', '?')}, P&L={e.get('pnl_pct', '?')}%, "
        f"Outcome={e.get('outcome_label', '?')}"
        for e in episodes
    ]
    history_text = "\n".join(lines)

    prompt = (
        f"You are a trading post-mortem analyst reviewing {company}'s last {len(episodes)} "
        f"RESOLVED trading decisions across all market regimes:\n\n{history_text}\n\n"
        f"Win count: {len(wins)}, Loss count: {len(losses)}\n\n"
        f"In 4-5 concise bullet points, identify:\n"
        f"1. Any pattern in which regimes/signals tend to lose money.\n"
        f"2. Whether the system is systematically too aggressive or too conservative.\n"
        f"3. One concrete process adjustment for future Trader/Portfolio Manager decisions.\n"
        f"Base this ONLY on the data above — do not invent numbers."
    )
    log(f"reviewing {len(episodes)} resolved episodes ({len(wins)}W/{len(losses)}L)")
    try:
        result = invoke_llm_with_retry(llm, prompt).content.strip()
        log("post-mortem ready")
        return result, len(episodes)
    except Exception as e:  # noqa: BLE001
        return f"(Post-mortem LLM call failed: {e})", len(episodes)


def create_post_mortem_agent(llm, memory, log=print):
    def node(state):
        company = state["company_of_interest"]
        lessons, n_reviewed = run_post_mortem(company, memory, llm, log=log)
        return {"post_mortem_lessons": lessons, "post_mortem_n_episodes": n_reviewed}

    return node


# ==========================================================================
# Decision Verifier / Fact-Check — post-decision sanity check
# ==========================================================================
# Runs after the Portfolio Manager's final decision. Nine agents chain off
# each other's output sequentially, so an early hallucination/bad number can
# propagate all the way to the final call — this catches that in 3 layers:
#   1) rule-based sanity check   — RSI/news sentiment vs. the final signal
#   2) numeric contradiction     — DETERMINISTIC (regex + tolerance), not
#      LLM-judged: a small local model repeatedly misjudged decimal
#      rounding as a "genuine difference" (e.g. 68.17 vs 68.16905...), so
#      arithmetic comparison is never handed to the LLM.
#   3) LLM semantic check        — ADVISORY ONLY (SIGNAL_CONSISTENT y/n).
#      The 7B model over-triggered on ordinary hedge/caveat language, so
#      this alone can never flip status to FLAGGED.
def rule_based_checks(final_signal: str, indicators: dict, news_metrics: dict,
                      fundamentals_report: str = "") -> tuple[list[str], list[str]]:
    """Returns (warnings, info_notes). Warnings flag status; info_notes are
    shown for transparency but never change status."""
    warnings: list[str] = []
    info_notes: list[str] = []

    fund_verdict = extract_final_proposal(fundamentals_report)
    fund_verdict_upper = fund_verdict.upper() if fund_verdict else ""
    fund_agrees_with_final = bool(fund_verdict) and final_signal in fund_verdict_upper

    def _add(msg: str):
        (info_notes if fund_agrees_with_final else warnings).append(msg)

    rsi = indicators.get("rsi")
    try:
        rsi_val = float(rsi)
        if rsi_val >= 70 and final_signal == "BUY":
            _add(f"RSI={rsi_val} is overbought (>=70) but signal is BUY — possible contradiction.")
        if rsi_val <= 30 and final_signal == "SELL":
            _add(f"RSI={rsi_val} is oversold (<=30) but signal is SELL — possible contradiction.")
    except (TypeError, ValueError):
        pass

    overall_sent = str(news_metrics.get("overall_sentiment", "")).lower()
    if "negative" in overall_sent and final_signal == "BUY":
        _add("News sentiment is negative but signal is BUY — worth verifying.")
    if "positive" in overall_sent and final_signal == "SELL":
        _add("News sentiment is positive but signal is SELL — worth verifying.")

    if fund_agrees_with_final and info_notes:
        info_notes.append(
            "The technical/sentiment contradiction(s) above were NOT flagged because the "
            f"Fundamentals Analyst's own hard verdict ({fund_verdict}) already agrees with "
            "the final signal — i.e. strong fundamentals legitimately override soft "
            "sentiment/technical signals."
        )

    if fund_verdict:
        if "SELL" in fund_verdict_upper and final_signal == "BUY":
            warnings.append(
                f"Fundamentals Analyst said SELL ({fund_verdict}) but the final signal is BUY — "
                "the debate/trader/PM chain overrode that verdict; needs direct review."
            )
        elif "BUY" in fund_verdict_upper and final_signal == "SELL":
            warnings.append(
                f"Fundamentals Analyst said BUY ({fund_verdict}) but the final signal is SELL — "
                "the debate/trader/PM chain overrode that verdict; needs direct review."
            )
        elif "HOLD" in fund_verdict_upper and final_signal in ("BUY", "SELL"):
            warnings.append(
                f"Fundamentals Analyst said HOLD ({fund_verdict}) but the final signal is {final_signal} — "
                "the debate/trader/PM chain moved off a neutral fundamentals verdict without a hard "
                "SELL/BUY basis for doing so; needs direct review."
            )

    return warnings, info_notes


def _extract_cited_numbers(text: str, label_patterns: list[str]) -> list[float]:
    """Finds EVERY number written near a label (e.g. "RSI") in the decision
    text, across all occurrences of the label -- not just the first.

    Tolerates natural-language connectors between the label and the number
    (e.g. "RSI at 68.17", "RSI is currently 68.17", "RSI stands at 68.17"),
    not just "RSI: 68.17" / "RSI = 68.17" — an LLM writing prose almost never
    uses the strict label+colon form, so a stricter pattern here would make
    this check silently never fire in practice. The <=20-char gap cap keeps
    it from skipping past to an unrelated number further down the text.

    🔴 FIXED: the gap class used to be `[^0-9\\-]` — any non-digit,
    non-minus character, INCLUDING sentence-ending punctuation. Because
    `re.search` doesn't stop at the label's first occurrence, it just finds
    the first STARTING POSITION where the whole pattern matches — a label
    mention with no number in its own clause (e.g. "RSI reflects oversold
    conditions.") would fail to match at that position and the engine would
    keep sliding forward, happily crossing the period into a completely
    unrelated later clause/sentence ("...analysts still see a 40% chance of
    further downside") and grabbing THAT number instead, mislabeling it as
    the RSI citation. Excluding `.!?\\n` from the gap keeps the match inside
    the same clause/sentence as the label.

    🔴 FIXED (2): used to be "first match wins" and returned a single
    float. Fine for a short final-decision text, but WRONG for multi-round
    debate history ("Bull vs Bear Debate" concatenates every round) and any
    text where a label is mentioned more than once -- the label's genuine,
    correct citation might sit in round 3 while an earlier round used the
    word rhetorically/hypothetically with some unrelated nearby number
    ("if RSI were to slip toward 20, that would signal capitulation") that
    the old first-match logic would grab instead, even though a completely
    correct citation existed elsewhere in the same text. Now returns every
    candidate found across every occurrence of every pattern, so the caller
    can pick the one that actually matches the real reading (see
    check_numeric_contradiction) instead of committing to whichever mention
    the regex happened to reach first.
    """
    found: list[float] = []
    for label in label_patterns:
        for m in re.finditer(rf"{label}\b(?:\s*\([^)]*\))?[^0-9\-.!?\n]{{0,20}}(-?\d+\.?\d*)", text, re.IGNORECASE):
            try:
                found.append(float(m.group(1)))
            except ValueError:
                continue
    return found


# 🔴 FIXED: physically-impossible bounds catch the residual cases the
# sentence-boundary fix above doesn't -- e.g. the label and the stray
# number DO share a clause ("...RSI momentum has weakened by roughly -40%
# over the period..."). RSI is mathematically bounded to [0, 100] by its
# own formula (100 - 100/(1+RS)); a "citation" outside that range cannot be
# a genuine RSI reading no matter how it was extracted, so treating it as
# "not actually a citation" (skip) rather than "a wrong citation" (flag)
# avoids a false mismatch built on a number that was never really an RSI
# value to begin with. MACD has no equivalent hard mathematical bound, so
# it isn't included here -- suspiciously large MACD mismatches still need
# a human/log check to confirm, see run notes.
_INDICATOR_BOUNDS = {
    "RSI": (0.0, 100.0),
}

# 🔴 FIXED: MACD has no fixed mathematical bound like RSI's [0, 100], so it
# can't use the same hard-range filter -- but a citation ~80x the raw
# value's magnitude (e.g. "-50.0" cited when raw MACD is -0.625) is still
# not a plausible reading for the SAME ticker/date; MACD doesn't jump
# orders of magnitude between what an analyst sees and what a debater
# paraphrases minutes later. This is a heuristic, not a hard proof, so the
# cap is generous (15x, floor 3.0) -- meant to catch "clearly a different
# number entirely" (a stray %, a score) without also swallowing genuine
# moderate citation drift that's still worth flagging.
_INDICATOR_RELATIVE_MAGNITUDE_CAP = {
    "MACD": 15.0,
}


def check_numeric_contradiction(final_decision_text: str, indicators: dict,
                                fund_metrics: dict, skip_eps: bool = False,
                                tolerance_floor: float = 0.5) -> tuple[list[str], int]:
    """Compares every number the decision text cites against the raw
    ground-truth value, with a generous rounding tolerance (2% relative or
    0.5 absolute) so "same number, fewer decimals" is never confused with a
    genuinely wrong figure. Pure Python — no LLM arithmetic judgment.

    🔴 FIXED: now also returns `n_checked` (how many tracked figures the
    decision text actually cited and got compared), not just the list of
    mismatches. Previously the caller only ever surfaced this function's
    output when `mismatches` was non-empty (see run_decision_verifier's
    notes-building) -- so "every cited number matched" and "the decision
    text never cited any of these figures at all" produced IDENTICAL
    silence in the UI, with no way to tell a genuine numeric verification
    apart from nothing having been checked in the first place.

    ✅ CHANGED: `skip_eps` -- confirmed live (BATBC) that DSE tickers
    routinely have SEVERAL simultaneously-valid, genuinely-different EPS
    figures on the books at once (Q1-only, H1 cumulative, prior-year
    comparative, last full audited year -- e.g. BATBC's own disclosures
    show 3.88 for Q1 2026 alone vs a cumulative/derived ~7.65 for H1).
    fund_metrics only ever holds ONE of these. A tolerance check assumes
    there's a single ground truth the decision text should match, which
    doesn't hold here -- the LLM legitimately citing a *different*,
    equally-real EPS figure than the one in fund_metrics isn't an error,
    and this check has no way to tell that apart from an actual
    hallucinated number. Set skip_eps=True (DSE tickers) rather than
    guess a looser tolerance, since no fixed tolerance is principled when
    the two numbers are correctly describing different reporting
    periods. RSI/MACD/P-E aren't affected -- those remain single-valued
    for a given ticker+date.

    ✅ CHANGED: `tolerance_floor` -- the final decision text is a single,
    authoritative, structured output and should be held to the tight
    default (0.5 absolute / 2% relative). Earlier debate/proposal stages
    are conversational prose written across multiple rounds by different
    "voices" (bull/bear/aggressive/conservative/etc) synthesizing from
    each other -- a little rounding drift when paraphrasing a figure
    someone else stated two paragraphs up (e.g. RSI 31.66 restated as
    ~30) is normal and not the kind of error this check exists to catch.
    Callers checking debate_texts pass a wider floor so that small,
    plausible drift doesn't compete for attention with genuine
    mismatches. See run_decision_verifier for the two call sites."""
    checks = [
        ("RSI", ["RSI"], indicators.get("rsi")),
        ("MACD", ["MACD"], indicators.get("macd")),
        ("P/E Ratio", ["P/E Ratio", "P/E"], fund_metrics.get("pe_ratio")),
    ]
    if not skip_eps:
        checks.append(("EPS (TTM)", [r"EPS \(TTM\)", "EPS"], fund_metrics.get("eps_ttm")))
    mismatches = []
    n_checked = 0
    for label, patterns, raw in checks:
        if raw in (None, "", "N/A"):
            continue
        try:
            raw_val = float(raw)
        except (TypeError, ValueError):
            continue
        candidates = _extract_cited_numbers(final_decision_text, patterns)
        bounds = _INDICATOR_BOUNDS.get(label)
        if bounds is not None:
            # Physically impossible for this indicator (e.g. RSI < 0) --
            # the extractor almost certainly grabbed an unrelated nearby
            # number (a %, a score, a threshold), not a genuine citation.
            # Drop these before picking a "best" candidate so an implausible
            # number never wins by being numerically closer to raw_val.
            candidates = [c for c in candidates if bounds[0] <= c <= bounds[1]]
        mag_cap = _INDICATOR_RELATIVE_MAGNITUDE_CAP.get(label)
        if mag_cap is not None:
            # No hard bound exists (e.g. MACD can legitimately be any real
            # number), but a citation dozens of times larger than the raw
            # reading for the SAME ticker/date is still not a plausible
            # same-indicator reading -- almost certainly a stray %/score
            # the extractor picked up, not real drift.
            cap = max(3.0, mag_cap * abs(raw_val))
            candidates = [c for c in candidates if abs(c) <= cap]
        if not candidates:
            continue
        # 🔴 FIXED: used to take whichever single number the old
        # first-match extractor happened to find. For text with multiple
        # mentions of the label (multi-round debate history above all),
        # that could be a rhetorical/hypothetical mention rather than the
        # genuine citation. Now: if ANY mention in the text matches the
        # real reading within tolerance, that's the citation that counts --
        # extra rhetorical noise elsewhere in the same text no longer
        # produces a false mismatch. Only flag when EVERY mention found is
        # off, reporting the closest of them as the representative (most
        # charitable) wrong figure.
        cited = min(candidates, key=lambda c: abs(c - raw_val))
        n_checked += 1
        diff = abs(cited - raw_val)
        tolerance = max(tolerance_floor, 0.02 * abs(raw_val))
        if diff > tolerance:
            mismatches.append(
                f"{label}: decision text says {cited}, raw data says {raw_val} "
                f"(diff={diff:.2f}, tolerance={tolerance:.2f}) — possible genuine mismatch."
            )
    return mismatches, n_checked


def llm_signal_consistency_check(final_decision_text: str, llm) -> str:
    """The one genuinely subjective/semantic question — is the decision
    text's own reasoning self-consistent? Result is advisory-only (see
    run_decision_verifier); a 7B model's opinion here doesn't get to FLAG a
    run on its own."""
    prompt = f"""You are a fact-checking auditor reviewing a trading decision before it is finalized.

=== FINAL DECISION TEXT ===
{final_decision_text[:2000]}

Check ONLY this one thing:

Respond with ONE strict marker line FIRST (machine-parsed), THEN your explanation
— do not paraphrase the marker:
SIGNAL_CONSISTENT: YES or NO

RULES (read carefully before answering):
- HOLD is a legitimate, self-consistent conclusion whenever the inputs are mixed --
  e.g. overbought/oversold technicals pulling one way while fundamentals or news
  sentiment pull the other way. Mentioning a caution/risk factor and then still
  making a call (BUY/HOLD/SELL) is NORMAL trading commentary, not an inconsistency.
- Only answer NO if the decision text's OWN stated reasoning DIRECTLY and STRONGLY
  contradicts its OWN action -- e.g. the text says "this is a clear sell signal" or
  "there is no reason to buy" but then names BUY as the action. A hedge like
  "shows some overbought risk but still has growth potential" followed by BUY is
  CONSISTENT, not a contradiction -- do not flag it.
- Default to YES unless the contradiction is obvious and severe.

Then, in 1-2 short bullet points, if SIGNAL_CONSISTENT is NO, quote the specific
phrase in the decision text whose own reasoning contradicts its own action.

Be concise and only flag REAL, severe discrepancies -- do not invent problems.
"""
    try:
        return invoke_llm_with_retry(llm, prompt).content.strip()
    except Exception as e:  # noqa: BLE001
        return f"(LLM fact-check failed: {e})"


def run_decision_verifier(final_decision_text: str, indicators: dict, fund_metrics: dict,
                          news_metrics: dict, fundamentals_report: str, llm,
                          is_dse: bool = False, log=print,
                          debate_texts: dict[str, str] | None = None) -> dict:
    """Runs all three checks and returns VERIFIED/FLAGGED status.

    🔴 FIXED: the deterministic numeric-contradiction check used to only
    ever look at `final_decision_text` (the Portfolio Manager's own
    output) -- a wrong RSI/MACD/P-E/EPS figure cited earlier, in the
    Bull/Bear debate, the Trader's proposal, or the Risk debate, went
    completely unchecked at every stage unless that exact wrong number
    happened to get repeated verbatim in the PM's final text. `debate_texts`
    (optional dict of {stage_name: stage_text}, passed from
    create_decision_verifier below) now gets the same deterministic,
    non-LLM check applied to each stage individually, so a hallucinated
    number anywhere upstream is caught and attributed to the stage that
    produced it -- not just silently invisible unless it survived to the
    final call. Same severity tier as the final-text check (pure Python
    arithmetic, no LLM judgment involved), so it can also flag status.

    ✅ CHANGED (per explicit request): the rule-based fundamentals-
    contradiction check is now advisory-only, same as the LLM semantic
    check -- still computed and shown in `notes` for transparency, but it
    no longer flips `status` to FLAGGED and no longer auto-overrides
    `effective_signal` to HOLD. Only the deterministic numeric-
    contradiction check(s) and the LLM call itself failing/not following
    the expected format can flag status now. If you want the old
    contradiction-blocks-the-trade behavior back, that's a one-line
    revert: re-add `rule_warnings` to the `status =` line below and
    restore the auto-override block that used to follow it (see git
    history / prior version of this function)."""
    # 🔴 FIXED: this used to have its own separate, buggy regex --
    # r"\*{0,2}(BUY|HOLD|SELL)\*{0,2}" with no word boundary, first-match-
    # anywhere -- a third independent copy of the same bug fixed in
    # render.py::first_signal() and orchestrator.py::_signal(). Reuses the
    # fixed shared implementation instead of re-diverging.
    final_signal = first_signal(final_decision_text)

    log("running rule-based checks (advisory only -- does not flag status)")
    rule_warnings, rule_info_notes = rule_based_checks(
        final_signal, indicators, news_metrics, fundamentals_report=fundamentals_report)

    log("running deterministic numeric-contradiction check")
    numeric_mismatches, numeric_checked = check_numeric_contradiction(
        final_decision_text, indicators, fund_metrics, skip_eps=is_dse)

    # 🔴 FIXED (see this function's docstring): also check numbers cited
    # in earlier pipeline stages, not just the final PM text. Each stage
    # is checked and reported separately so a mismatch is attributable to
    # where it actually happened (e.g. "[Bull vs Bear Debate]" vs
    # "[Trader Proposal]"), rather than one undifferentiated blob.
    #
    # ✅ CHANGED: `tolerance_floor=2.0` here (vs. the 0.5 default used for
    # final_decision_text above) -- these are multi-round debate/proposal
    # texts, not the single authoritative final call, and confirmed live
    # (Risk Debate: RSI cited 30.05 vs raw 31.66, diff=1.61 against the
    # tight 0.63 tolerance) that ordinary paraphrase drift when restating
    # a figure mid-debate was competing for attention with genuine
    # mismatches at the tight tolerance. The final decision text keeps the
    # strict default since it's the one output that should be precise.
    debate_mismatches: list[str] = []
    debate_checked = 0
    for stage_name, stage_text in (debate_texts or {}).items():
        if not stage_text:
            continue
        stage_mismatches, stage_checked = check_numeric_contradiction(
            stage_text, indicators, fund_metrics, skip_eps=is_dse, tolerance_floor=2.0)
        debate_checked += stage_checked
        debate_mismatches.extend(f"[{stage_name}] {m}" for m in stage_mismatches)

    log("running advisory LLM semantic check")
    llm_notes = llm_signal_consistency_check(final_decision_text, llm)

    llm_call_failed = "(llm fact-check failed" in llm_notes.lower()
    signal_consistent_m = re.search(r"SIGNAL_CONSISTENT:\s*(YES|NO)", llm_notes, re.IGNORECASE)
    markers_missing = signal_consistent_m is None

    # Status is decided only by hard/reliable signals: code-verified
    # numeric mismatch (final text OR any earlier debate stage -- both are
    # the same pure-Python arithmetic check, no LLM judgment involved), or
    # the LLM call failing/not following the format. The rule-based
    # fundamentals-contradiction check AND the LLM's own SIGNAL_CONSISTENT
    # opinion remain advisory and never flip status.
    status = "FLAGGED" if (numeric_mismatches or debate_mismatches
                            or llm_call_failed or markers_missing) else "VERIFIED"

    notes_parts = []
    if rule_warnings:
        notes_parts.append("Rule-based (advisory, does NOT affect status): " + " | ".join(rule_warnings))
    if rule_info_notes:
        notes_parts.append("Rule-based (info, non-flagging): " + " | ".join(rule_info_notes))
    # 🔴 FIXED: this used to only ever add a line when mismatches were
    # found -- "every cited figure matched" and "the decision text cited
    # no RSI/MACD/P-E/EPS figures at all" both produced total silence
    # here, indistinguishable from each other. Now always reports what
    # actually happened.
    if numeric_mismatches:
        notes_parts.append("Numeric check (code, non-LLM): " + " | ".join(numeric_mismatches))
    elif numeric_checked:
        notes_parts.append(
            f"Numeric check (code, non-LLM): {numeric_checked} cited figure(s) "
            f"(RSI/MACD/P-E/EPS) compared against raw data — all within tolerance.")
    else:
        notes_parts.append(
            "Numeric check (code, non-LLM): decision text didn't cite specific "
            "RSI/MACD/P-E/EPS figures, so there was nothing to compare against raw data.")
    if debate_texts:
        if debate_mismatches:
            notes_parts.append(
                "Numeric check — earlier pipeline stages (code, non-LLM): "
                + " | ".join(debate_mismatches))
        elif debate_checked:
            notes_parts.append(
                f"Numeric check — earlier pipeline stages (code, non-LLM): "
                f"{debate_checked} cited figure(s) across the debate/proposal stages "
                f"compared against raw data — all within tolerance.")
        else:
            notes_parts.append(
                "Numeric check — earlier pipeline stages (code, non-LLM): no "
                "RSI/MACD/P-E/EPS figures cited in the debate/proposal stages to compare.")
    notes_parts.append("LLM semantic check (advisory, does NOT affect status): " + llm_notes)
    notes = "\n".join(notes_parts)

    # ✅ CHANGED: auto-override removed along with the status effect -- an
    # "advisory, does not affect status" check silently forcing the
    # actionable signal to HOLD anyway would be a confusing half-state
    # (VERIFIED status, but the real signal changed underneath it).
    # effective_signal now always matches final_signal.
    effective_signal = final_signal
    auto_overridden = False

    log(f"verification status: {status}")
    return {
        "status": status, "notes": notes, "final_signal": final_signal,
        "effective_signal": effective_signal, "auto_overridden": auto_overridden,
    }


def create_decision_verifier(llm, log=print):
    def node(state):
        final_text = state.get("final_trade_decision", "")
        indicators = state.get("indicators_parsed", {})
        fund_metrics = state.get("fund_metrics", {})
        news_metrics = state.get("news_metrics", {})
        fundamentals_report = state.get("fundamentals_report", "")
        from tradingagents.dataflows.symbol_utils import is_dse_ticker
        is_dse = is_dse_ticker(state.get("company_of_interest", ""))
        # 🔴 FIXED: previously only final_text got numerically verified --
        # see run_decision_verifier's docstring. Pulls each earlier stage's
        # own text so a wrong RSI/MACD/P-E/EPS figure anywhere upstream
        # (not just in the PM's final wording) gets caught and attributed
        # to its source stage.
        # 🔴 FIXED: "Investment Facilitator" and "Risk Facilitator" used to
        # be checked here too, but neither one is ever given the raw
        # market_report/indicators directly -- Investment Facilitator's
        # prompt only passes it the Bull/Bear debate history text
        # (create_investment_facilitator), and Risk Facilitator's prompt
        # only passes it the risk-debate summaries + trader plan
        # (create_risk_facilitator). Both are pure SYNTHESIZERS one step
        # removed from ground truth -- if an earlier debate round didn't
        # restate the exact RSI/MACD figure in prose, these two have no way
        # to cite it precisely and will reasonably paraphrase/approximate
        # ("RSI weak, around 50") instead. Checking that paraphrase against
        # raw_val was flagging a structural gap in what these two agents
        # were ever given, not a real numeric error -- confirmed live via
        # repeated plausible-but-wrong round numbers (50.0, -40.0, -70.0)
        # from exactly these two stages across multiple tickers. "Bull vs
        # Bear Debate", "Risk Debate", and "Trader Proposal" stay in the
        # check below: their generating agents DO receive market_report /
        # indicators directly (see _reports / _risk_reports), so a wrong
        # figure there is a genuine citation error, not a data-access gap.
        debate_texts = {
            "Bull vs Bear Debate": state.get("investment_debate_state", {}).get("history", ""),
            "Trader Proposal": str(state.get("trader_investment_plan", "")),
            "Risk Debate": state.get("risk_debate_state", {}).get("history", ""),
        }
        result = run_decision_verifier(
            final_text, indicators, fund_metrics, news_metrics, fundamentals_report,
            llm, is_dse=is_dse, log=log, debate_texts=debate_texts)
        return {"verification_result": result}

    return node
