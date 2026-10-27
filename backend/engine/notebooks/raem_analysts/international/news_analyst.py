# raem_analysts/international/news_analyst.py -- shared by AAPL, IBM, 0300_HK, 2222_SR
# The company-specific values come from each notebook's RAEM_PROFILE:
# @@TICKER@@ ticker, @@FILE@@ ticker as used in file names, @@LOG@@ trading-log name, @@SLOT:key@@ lines the profile supplies.
# >>> shared code
from langchain_ollama import ChatOllama
from langchain_core.messages import HumanMessage, AIMessage, SystemMessage
from tradingagents.agents.utils.agent_utils import (
    build_instrument_context,
    get_global_news,
    get_language_instruction,
    get_news,
)

llm_news = ChatOllama(model="qwen2.5:7b", base_url="http://localhost:11434", temperature=0, repeat_penalty=1.15, num_predict=2048)

def create_news_analyst(llm):
    def news_analyst_node(state):
        current_date = state["trade_date"]
        company = state["company_of_interest"]
        asset_type = state.get("asset_type", "stock")
        instrument_context = resolve_instrument_context(
            company, build_instrument_context(company, asset_type)
        )

        print(f"📰 Fetching news for {company} on {current_date}...")

        # ── Step 1: get_news ──
        print("⚙️  Calling: get_news")
        try:
            from datetime import datetime, timedelta
            end_dt = datetime.strptime(current_date, "%Y-%m-%d")
            start_dt = end_dt - timedelta(days=7)
            company_news = get_news.invoke({
                "ticker": company,
                "start_date": start_dt.strftime("%Y-%m-%d"),
                "end_date": current_date,
            })
            company_news = str(company_news)[:3000]
            print(f"   ✅ Got {len(company_news)} chars")
        except Exception as e:
            company_news = f"Error: {str(e)}"
            print(f"   ❌ {e}")

        # ── Step 2: get_global_news ──
        print("⚙️  Calling: get_global_news")
        try:
            global_news = get_global_news.invoke({"curr_date": current_date})
            global_news = str(global_news)[:2000]
            print(f"   ✅ Got {len(global_news)} chars")
        except Exception as e:
            global_news = f"Error: {str(e)}"
            print(f"   ❌ {e}")

        # ── Step 3: LLM writes the report WITH enforced table ──
        prompt = f"""You are a news analyst. Today is {current_date}.
{instrument_context}

Using the REAL news data below, write a brief news analysis report for {company}.
Sections: Company News Summary → Global Macro Trends → Sentiment Assessment → Key Risks

At the very end, output a Markdown table with EXACTLY these 5 columns:
| Category | Headline | Sentiment | Impact | Source |
(Fill 4-6 rows. Sentiment must be POSITIVE / NEGATIVE / NEUTRAL.)

=== COMPANY NEWS ({company}) ===
{company_news}

=== GLOBAL MARKET NEWS ===
{global_news}
""" + get_language_instruction()

        messages = [
            SystemMessage(content="You are a senior news analyst. Write reports based only on provided data. Always end with the required Markdown table."),
            HumanMessage(content=prompt),
        ]

        response = llm.invoke(messages)
        report = response.content
        print("✅ News Report ready!")

        # ── Step 4: Parse sentiment counts ──
        import re
        sentiments = re.findall(r"\|\s*\w.*?\|\s*(POSITIVE|NEGATIVE|NEUTRAL)\s*\|", report, re.IGNORECASE)
        pos = sum(1 for s in sentiments if "POSITIVE" in s.upper())
        neg = sum(1 for s in sentiments if "NEGATIVE" in s.upper())
        neu = sum(1 for s in sentiments if "NEUTRAL" in s.upper())
        overall_m = re.search(r"(POSITIVE|NEGATIVE|NEUTRAL|BULLISH|BEARISH)", report, re.IGNORECASE)
        overall = overall_m.group(1).upper() if overall_m else "NEUTRAL"

        # ── Extract markdown table from report ──
        tbl_m = re.search(
            r"(\|.*?Category.*?\|.*?(?:\n\|[-| ]+\|)(?:\n\|.*?\|)+)",
            report, re.IGNORECASE | re.DOTALL
        )
        news_table_md = tbl_m.group(1) if tbl_m else ""

        # ✅ Structured metrics dict
        news_metrics = {
            "positive_count":     pos,
            "negative_count":     neg,
            "neutral_count":      neu,
            "overall_sentiment":  overall,
            "company_news_chars": len(company_news),
            "global_news_chars":  len(global_news),
        }

        all_msgs = list(state.get("messages", [])) + messages + [AIMessage(content=report)]
        return {
            "messages":        all_msgs,
            "news_report":     report,
            "news_metrics":    news_metrics,
            "news_table_md":   news_table_md,
            "news_raw": {
                "company_news": company_news,
                "global_news":  global_news,
            },
        }

    return news_analyst_node


news_analyst = create_news_analyst(llm_news)

news_state = {
    "trade_date": TRADE_DATE,
    "company_of_interest": "@@TICKER@@",
    "asset_type": "stock",
    "messages": [],
}

result = news_analyst(news_state)

# ✅ FIXED: structured dict log
log_step(
    "News Analyst",
    {
        "company":    news_state["company_of_interest"],
        "date":       news_state["trade_date"],
        "tools_used": "get_news, get_global_news",
    },
    {
        "news_metrics":  result.get("news_metrics", {}),
        "news_table_md": result.get("news_table_md", ""),
        "report":        result.get("news_report", ""),
    }
)

news_result = result
from IPython.display import Markdown
Markdown(result["news_report"])

