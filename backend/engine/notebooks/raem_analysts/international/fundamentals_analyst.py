# raem_analysts/international/fundamentals_analyst.py -- shared by AAPL, IBM, 0300_HK, 2222_SR
# The company-specific values come from each notebook's RAEM_PROFILE:
# @@TICKER@@ ticker, @@FILE@@ ticker as used in file names, @@LOG@@ trading-log name, @@SLOT:key@@ lines the profile supplies.
# >>> shared code
from langchain_ollama import ChatOllama
from langchain_core.messages import HumanMessage, AIMessage, SystemMessage
from tradingagents.agents.utils.agent_utils import (
    build_instrument_context,
    get_balance_sheet,
    get_cashflow,
    get_fundamentals,
    get_income_statement,
    get_language_instruction,
)

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
            """Format large number to readable string"""
            try:
                n = float(n)
                if abs(n) >= 1e9: return f"${n/1e9:.2f}B"
                if abs(n) >= 1e6: return f"${n/1e6:.1f}M"
                return f"${n:,.0f}"
            except: return str(n)

        # Extract key metrics
        fund = tool_results["get_fundamentals"]
        bs   = tool_results["get_balance_sheet"]
        cf   = tool_results["get_cashflow"]
        inc  = tool_results["get_income_statement"]

        # From fundamentals (already readable)
        pe   = re.search(r"PE Ratio.*?:\s*([\d.]+)", fund)
        eps  = re.search(r"EPS \(TTM\).*?:\s*([\d.]+)", fund)
        mcap = re.search(r"Market Cap.*?:\s*([\d.]+)", fund)
        rev_ttm = re.search(r"Revenue \(TTM\).*?:\s*([\d.]+)", fund)
        beta = re.search(r"Beta.*?:\s*([\d.]+)", fund)
        div  = re.search(r"Dividend Yield.*?:\s*([\d.]+)", fund)
        hi52 = re.search(r"52 Week High.*?:\s*([\d.]+)", fund)
        lo52 = re.search(r"52 Week Low.*?:\s*([\d.]+)", fund)
        sma50 = re.search(r"50 Day Average.*?:\s*([\d.]+)", fund)

        pe_val   = pe.group(1)   if pe   else "N/A"
        eps_val  = eps.group(1)  if eps  else "N/A"
        mcap_val = fmt(mcap.group(1)) if mcap else "N/A"
        rev_val  = fmt(rev_ttm.group(1)) if rev_ttm else "N/A"
        beta_val = beta.group(1) if beta else "N/A"
        div_val  = div.group(1)  if div  else "N/A"
        hi52_val = hi52.group(1) if hi52 else "N/A"
        lo52_val = lo52.group(1) if lo52 else "N/A"
        sma50_val = sma50.group(1) if sma50 else "N/A"

        # Income statement — first column = most recent quarter
        inc_rows = extract_numbers(inc, [
            "Total Revenue", "Gross Profit", "Operating Income",
            "Net Income From Continuing", "Normalized EBITDA"
        ])

        # Cash flow — most recent quarter
        cf_rows = extract_numbers(cf, ["Free Cash Flow", "Repurchase Of Capital Stock"])

        # Balance sheet — most recent quarter
        bs_rows = extract_numbers(bs, ["Net Debt", "Total Debt", "Cash And Cash Equivalents"])

        # Build condensed data summary (< 1500 chars total)
        data_summary = f"""
COMPANY: {company} | DATE: {current_date}
{instrument_context}

MARKET DATA (TTM):
- Market Cap: {mcap_val}
- P/E Ratio: {pe_val}
- EPS (TTM): ${eps_val}
- Revenue (TTM): {rev_val}
- Beta: {beta_val}
- Dividend Yield: {div_val}%
- 52-Week Range: ${lo52_val} - ${hi52_val}
- 50-Day SMA: ${sma50_val}

MOST RECENT QUARTER (from income statement, as of {current_date}):
- Net Income: {fmt(inc_rows.get("Net Income From Continuing", "N/A"))}
- Gross Profit: {fmt(inc_rows.get("Gross Profit", "N/A"))}
- Operating Income: {fmt(inc_rows.get("Operating Income", "N/A"))}
- EBITDA: {fmt(inc_rows.get("Normalized EBITDA", "N/A"))}

CASH FLOW (most recent quarter):
- Free Cash Flow: {fmt(cf_rows.get("Free Cash Flow", "N/A"))}
- Stock Buybacks: {fmt(cf_rows.get("Repurchase Of Capital Stock", "N/A"))}

BALANCE SHEET (most recent quarter):
- Net Debt: {fmt(bs_rows.get("Net Debt", "N/A"))}
- Total Debt: {fmt(bs_rows.get("Total Debt", "N/A"))}
"""

        # ── Step 3: Ask LLM to write report from condensed summary ──
        prompt = f"""You are a senior financial analyst. Today is {current_date}.

Write a professional fundamental analysis report for {company} using ONLY the data below.
Structure: Company Overview → Profitability → Balance Sheet → Cash Flow → Risks → Outlook
End with a Markdown table and: FINAL TRANSACTION PROPOSAL: **BUY** / **HOLD** / **SELL**

RULES:
- Use ONLY the numbers below. Do NOT invent any figures.
- Do NOT say "Q2 2023". The data is from {current_date}.
- If a metric shows N/A, say "data not available".

{data_summary}
""" + get_language_instruction()

        messages = [
            SystemMessage(content="You are a financial analyst. Use ONLY the provided numbers. Never invent data."),
            HumanMessage(content=prompt),
        ]

        response = llm.invoke(messages)
        report = response.content
        print("✅ Fundamentals Report ready!")

        # ── Build metrics dict inside function so it's returned ──
        fund_metrics = {
            "market_cap":         mcap_val,
            "pe_ratio":           pe_val,
            "eps_ttm":            eps_val,
            "revenue_ttm":        rev_val,
            "beta":               beta_val,
            "dividend_yield":     div_val,
            "52w_high":           hi52_val,
            "52w_low":            lo52_val,
            "50d_sma":            sma50_val,
            "net_income_q":       fmt(inc_rows.get("Net Income From Continuing", "N/A")),
            "gross_profit_q":     fmt(inc_rows.get("Gross Profit", "N/A")),
            "operating_income_q": fmt(inc_rows.get("Operating Income", "N/A")),
            "ebitda_q":           fmt(inc_rows.get("Normalized EBITDA", "N/A")),
            "free_cash_flow_q":   fmt(cf_rows.get("Free Cash Flow", "N/A")),
            "stock_buybacks_q":   fmt(cf_rows.get("Repurchase Of Capital Stock", "N/A")),
            "net_debt":           fmt(bs_rows.get("Net Debt", "N/A")),
            "total_debt":         fmt(bs_rows.get("Total Debt", "N/A")),
        }

        all_msgs = list(state.get("messages", [])) + messages + [AIMessage(content=report)]
        return {
            "messages":           all_msgs,
            "fundamentals_report": report,
            "fund_metrics":       fund_metrics,   # ← accessible outside
        }

    return fundamentals_analyst_node


# ── Run ────────────────────────────────────────────────────────
fundamentals_analyst = create_fundamentals_analyst(llm)

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
        "report":  result.get("fundamentals_report", ""), # ← full LLM report
    }
)

fundamentals_result = result
from IPython.display import Markdown
Markdown(result["fundamentals_report"])
