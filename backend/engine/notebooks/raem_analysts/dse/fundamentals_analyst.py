# raem_analysts/dse/fundamentals_analyst.py -- shared by abbank-dse, batbc-dse, bpml-dse, waltonhil-dse
# The company-specific values come from each notebook's RAEM_PROFILE:
# @@TICKER@@ ticker, @@FILE@@ ticker as used in file names, @@LOG@@ trading-log name, @@SLOT:key@@ lines the profile supplies.
# >>> shared code
from langchain_ollama import ChatOllama
from langchain_core.messages import HumanMessage, AIMessage, SystemMessage
from tradingagents.agents.utils.agent_utils import (
    build_instrument_context,
    get_language_instruction,
)
# get_fundamentals / get_balance_sheet / get_cashflow / get_income_statement are
# now the DSE versions defined in the "DSE Data Source Layer" cell above
# (global names) -- no longer imported from tradingagents (Finnhub-based).

def create_fundamentals_analyst(llm):
    def fundamentals_analyst_node(state):
        current_date = state["trade_date"]
        company = state["company_of_interest"]
        instrument_context = resolve_instrument_context(
            company, build_instrument_context(company)
        )

        print(f"📊 Fetching fundamentals data for {company} on {current_date}...")

        # ── Step 1: Call all tools directly (no LLM involvement) ──
        tool_results = {}
        tool_calls = [
            ("get_fundamentals",     get_fundamentals,     {"ticker": company, "curr_date": current_date}),
            ("get_balance_sheet",    get_balance_sheet,    {"ticker": company, "curr_date": current_date}),
            ("get_cashflow",         get_cashflow,         {"ticker": company, "curr_date": current_date}),
            ("get_income_statement", get_income_statement, {"ticker": company, "curr_date": current_date}),
        ]

        for tool_name, tool_fn, tool_args in tool_calls:
            print(f"⚙️  Calling: {tool_name}")
            try:
                result = tool_fn.invoke(tool_args)
                tool_results[tool_name] = str(result)[:3000]
                print(f"   ✅ Got {len(str(result))} chars")
            except Exception as e:
                tool_results[tool_name] = f"Error fetching {tool_name}: {str(e)}"
                print(f"   ❌ Error: {e}")

        # ── Step 2: Parse key numbers from raw data ──
        import re

        def extract_numbers(raw, keys):
            """Extract specific rows from CSV-like data"""
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

        def fmt(n):
            """Format large number to readable string. DSE-listed company --
            everything here is Bangladeshi Taka (৳), not USD."""
            try:
                n = float(n)
                if abs(n) >= 1e9: return f"৳{n/1e9:.2f}B"
                if abs(n) >= 1e6: return f"৳{n/1e6:.1f}M"
                return f"৳{n:,.0f}"
            except: return str(n)

        # Extract key metrics
        fund = tool_results["get_fundamentals"]
        bs   = tool_results["get_balance_sheet"]
        cf   = tool_results["get_cashflow"]
        inc  = tool_results["get_income_statement"]

        # From fundamentals (already readable)
        pe   = re.search(r"PE Ratio.*?:\s*([\d.]+)", fund)
        eps  = re.search(r"EPS \(TTM\).*?:\s*([\d.]+)", fund)
        # get_fundamentals ALWAYS prints "PE Ratio: N/A" / no EPS line (DSE's
        # company page never has these) -- so pe/eps above never match. The
        # real values, when available, come from get_income_statement's own
        # EPS disclosure + rough P/E instead. Fall back to those.
        pe_from_inc  = re.search(r"P/E Ratio(?:\s*\([^)]*\))?:\s*(-?[\d.]+)", inc)
        eps_from_inc = re.search(r"Most Recent Disclosed EPS: Tk\.\s*(-?[\d.]+)", inc)
        eps_period_m = re.search(r"Most Recent Disclosed EPS: Tk\.\s*-?[\d.]+\s*for\s*([^\n]+)", inc)
        pe_basis_m   = re.search(r"P/E Ratio\s*\(rough,\s*([^)]*)\)", inc)
        mcap = re.search(r"Market Cap.*?:\s*([\d.]+)", fund)
        rev_ttm = re.search(r"Revenue \(TTM\).*?:\s*([\d.]+)", fund)
        beta = re.search(r"Beta.*?:\s*([\d.]+)", fund)
        div  = re.search(r"Dividend Yield.*?:\s*([\d.]+)", fund)
        hi52 = re.search(r"52 Week High.*?:\s*([\d.]+)", fund)
        lo52 = re.search(r"52 Week Low.*?:\s*([\d.]+)", fund)
        # NOTE: "50 Day Average" was removed here -- get_fundamentals never
        # emits that label (it's a technical indicator computed elsewhere
        # from OHLCV data, not a fundamentals-page field), so this regex
        # could never match and always produced a fake-looking N/A.
        sector = re.search(r"Sector:\s*(.+)", fund)
        last_px = re.search(r"Last Trading Price.*?:\s*([\d.]+)", fund)

        pe_val   = pe.group(1)   if pe   else (pe_from_inc.group(1)  if pe_from_inc  else "N/A")
        eps_val  = eps.group(1)  if eps  else (eps_from_inc.group(1) if eps_from_inc else "N/A")
        mcap_val = fmt(mcap.group(1)) if mcap else "N/A"
        rev_val  = fmt(rev_ttm.group(1)) if rev_ttm else "N/A"
        beta_val = beta.group(1) if beta else "N/A"
        div_val  = div.group(1)  if div  else "N/A"
        hi52_val = hi52.group(1) if hi52 else "N/A"
        lo52_val = lo52.group(1) if lo52 else "N/A"
        sector_val = sector.group(1).strip() if sector else "N/A"
        last_px_val = last_px.group(1) if last_px else "N/A"
        eps_period_txt = eps_period_m.group(1).strip()[:60] if eps_period_m else "period not stated"
        pe_basis_txt   = ("rough: " + pe_basis_m.group(1).strip()) if pe_basis_m else "basis not stated"
        # N/A must never reach the LLM as "N/A%" (Qwen turned it into "0%" and every later agent copied it)
        div_line = f"{div_val}%" if div else "data not available (DSE publishes no yield -- do NOT write 0%; use the Cash Dividend history below)"

        # Income statement — genuinely not published by DSE (see get_income_statement docstring)
        inc_rows = extract_numbers(inc, [
            "Total Revenue", "Gross Profit", "Operating Income",
            "Net Income From Continuing", "Normalized EBITDA"
        ])

        # Cash flow — DSE has no cash flow statement; use dividend/corporate-action
        # history (get_cashflow's real proxy fields) as text, not filtered-numbers
        cash_div_m    = re.search(r"Cash Dividend:\s*(.+)", cf)
        bonus_issue_m = re.search(r"Bonus Issue.*?:\s*(.+)", cf)
        right_issue_m = re.search(r"Right Issue:\s*(.+)", cf)
        cash_div_val    = cash_div_m.group(1).strip()    if cash_div_m    else "N/A"
        bonus_issue_val = bonus_issue_m.group(1).strip() if bonus_issue_m else "N/A"
        right_issue_val = right_issue_m.group(1).strip() if right_issue_m else "N/A"

        # Balance sheet — real capital-structure/reserves numbers from get_balance_sheet
        bs_rows = extract_numbers(bs, [
            "Authorized Capital", "Paid-up Capital",
            "Reserve & Surplus", "Other Comprehensive Income"
        ])

        # Build condensed data summary (< 1500 chars total)
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
- Net Income (estimated): {fmt(inc_rows.get("Net Income From Continuing", "N/A"))}
- Gross Profit: {fmt(inc_rows.get("Gross Profit", "N/A"))}
- Operating Income: {fmt(inc_rows.get("Operating Income", "N/A"))}
- EBITDA: {fmt(inc_rows.get("Normalized EBITDA", "N/A"))}

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

        # ── v8: NUMBERS-ONLY block for the other agents (built in code, no LLM, no opinion) ──
        def _short(x, n=350):
            x = str(x)
            return x if len(x) <= n else x[:n].rstrip() + " ..."
        _unavail = [lbl for lbl, k in (("gross profit", "Gross Profit"), ("operating income", "Operating Income"), ("EBITDA", "Normalized EBITDA"))
                    if k not in inc_rows]
        facts_lines = [
            f"{FUND_FACTS_MARK} (extracted by code from DSE pages; no analyst opinion) -- {company}, {current_date}",
            f"Sector: {sector_val} | Last trading price: {last_px_val} | Market cap: {mcap_val}",
            f"P/E: {pe_val} [{pe_basis_txt}] | EPS: ৳{eps_val} ({eps_period_txt}; NOT trailing-12-month) | Dividend yield: "
            + (f"{div_val}%" if div else "not published by DSE (do NOT write 0%)"),
            f"52-week range: ৳{lo52_val} - ৳{hi52_val} | Beta: {beta_val} | Revenue (TTM): {rev_val}",
            f"Net income, latest period (ESTIMATE from disclosed EPS x shares): {fmt(inc_rows.get('Net Income From Continuing', 'N/A'))}",
            f"Cash dividend history: {_short(cash_div_val)} | Bonus issue: {_short(bonus_issue_val, 120)} | Right issue: {_short(right_issue_val, 120)}",
            f"Paid-up capital: {fmt(bs_rows.get('Paid-up Capital', 'N/A'))} | Reserve & surplus (excl. OCI): {fmt(bs_rows.get('Reserve & Surplus', 'N/A'))} | OCI: {fmt(bs_rows.get('Other Comprehensive Income', 'N/A'))}",
            "Not published by DSE: free cash flow, net debt, total debt" + (", " + ", ".join(_unavail) if _unavail else "") + ".",
        ]
        facts_block = "\n".join(facts_lines)

        # ── Step 3: Ask LLM to write report from condensed summary ──
        prompt = f"""You are a senior financial analyst. Today is {current_date}.

Write a professional fundamental analysis report for {company} using ONLY the data below.
Structure: Company Overview → Profitability → Balance Sheet → Cash Flow → Risks → Outlook
{_len_rule('fundamentals')}
End with a Markdown table and: FINAL TRANSACTION PROPOSAL: **BUY** / **HOLD** / **SELL**

RULES:
- Use ONLY the numbers below. Do NOT invent any figures.
- Do NOT say "Q2 2023". The data is from {current_date}.
- If a metric shows N/A, say "data not available".
- Never call EPS or Net Income "TTM" or "trailing twelve months": they cover only the most recent disclosed period named above (usually ONE quarter).
- Never write 0% (or any number) for a field that says data not available.
- When describing dividends, quote the actual per-year figures from the Cash Dividend history; say "rising" or "falling" only if those figures show it.

{data_summary}
""" + get_language_instruction()

        messages = [
            SystemMessage(content="You are a financial analyst. Use ONLY the provided numbers. Never invent data."),
            HumanMessage(content=prompt),
        ]

        if FUND_LLM_REPORT:
            response = cached_analyst_invoke("fundamentals", llm, messages)
            report = response.content
        else:
            report = facts_block + "\n\n(LLM narrative skipped: FUND_LLM_REPORT=False)"
            print("⏭️  Fundamentals LLM report skipped (FUND_LLM_REPORT=False) -- numbers block only.")
        print("✅ Fundamentals Report ready!")

        # ── Build metrics dict inside function so it's returned ──
        fund_metrics = {
            "market_cap":         mcap_val,
            "pe_ratio":           pe_val,
            "eps_ttm":            eps_val,
            "eps_period":         eps_period_txt,
            "pe_basis":           pe_basis_txt,
            "revenue_ttm":        rev_val,
            "beta":               beta_val,
            "dividend_yield":     div_val,
            "52w_high":           hi52_val,
            "52w_low":            lo52_val,
            "sector":             sector_val,
            "last_trading_price": last_px_val,
            "net_income_q":       fmt(inc_rows.get("Net Income From Continuing", "N/A")),
            "gross_profit_q":     fmt(inc_rows.get("Gross Profit", "N/A")),
            "operating_income_q": fmt(inc_rows.get("Operating Income", "N/A")),
            "ebitda_q":           fmt(inc_rows.get("Normalized EBITDA", "N/A")),
            "cash_dividend":      cash_div_val,
            "bonus_issue":        bonus_issue_val,
            "right_issue":        right_issue_val,
            "authorized_capital": fmt(bs_rows.get("Authorized Capital", "N/A")),
            "paidup_capital_bs":  fmt(bs_rows.get("Paid-up Capital", "N/A")),
            "reserve_surplus":    fmt(bs_rows.get("Reserve & Surplus", "N/A")),
            "oci":                fmt(bs_rows.get("Other Comprehensive Income", "N/A")),
        }

        all_msgs = list(state.get("messages", [])) + messages + [AIMessage(content=report)]
        return {
            "messages":           all_msgs,
            # v8: other agents get the numbers-only block; the LLM narrative + its verdict stay available here only
            "fundamentals_report": facts_block if FUND_NUMBERS_ONLY else report,
            "fundamentals_report_llm": report,
            "fundamentals_facts": facts_block,
            "fund_verdict": extract_final_proposal(report),
            "fund_metrics":       fund_metrics,   # ← accessible outside
        }

    return fundamentals_analyst_node


# ── Run ────────────────────────────────────────────────────────
llm_fund = ChatOllama(model="qwen2.5:7b", base_url="http://localhost:11434", num_ctx=8192, temperature=0, num_predict=_cap('fundamentals') or 2048, repeat_penalty=1.15)
fundamentals_analyst = create_fundamentals_analyst(llm_fund)

fundamentals_state = {
    "trade_date": TRADE_DATE,
    "company_of_interest": "@@TICKER@@",
    "messages": [],
}

result = fundamentals_analyst(fundamentals_state)

log_step(
    "Fundamentals Analyst",
    {
        "company":    fundamentals_state["company_of_interest"],
        "date":       fundamentals_state["trade_date"],
        "tools_used": "get_fundamentals, get_balance_sheet, get_cashflow, get_income_statement",
    },
    {
        "metrics": result.get("fund_metrics", {}),        # ← structured numbers
        "report":  result.get("fundamentals_report_llm", result.get("fundamentals_report", "")),   # full LLM report (for you)
        "facts_sent_to_other_agents": result.get("fundamentals_facts", ""),
        "llm_verdict_hidden_from_agents": (result.get("fund_verdict", "") if FUND_NUMBERS_ONLY else "NOT HIDDEN"),
    }
)

fundamentals_result = result
from IPython.display import Markdown
if FUND_NUMBERS_ONLY:
    print("ℹ️  v8: the other agents receive ONLY the numbers block below (no narrative, no BUY/HOLD/SELL). "
          f"The analyst's own call ({result.get('fund_verdict') or 'none found'}) is logged but hidden from them.\n")
    print(result["fundamentals_facts"])
Markdown(result.get("fundamentals_report_llm", result["fundamentals_report"]))
