# raem_analysts/international/sentiment_analyst.py -- shared by AAPL, IBM, 0300_HK, 2222_SR
# The company-specific values come from each notebook's RAEM_PROFILE:
# @@TICKER@@ ticker, @@FILE@@ ticker as used in file names, @@LOG@@ trading-log name, @@SLOT:key@@ lines the profile supplies.
# >>> shared code
from langchain_ollama import ChatOllama
from langchain_core.messages import HumanMessage, AIMessage, SystemMessage
from tradingagents.agents.utils.agent_utils import get_language_instruction
import re

# Try importing sentiment tools
sentiment_tools_available = {}
try:
    from tradingagents.agents.utils.agent_utils import get_stock_news_sentiment
    sentiment_tools_available["get_stock_news_sentiment"] = get_stock_news_sentiment
    print("✅ get_stock_news_sentiment loaded")
except ImportError:
    print("⚠️  get_stock_news_sentiment not found")

try:
    from tradingagents.agents.utils.agent_utils import get_reddit_news
    sentiment_tools_available["get_reddit_news"] = get_reddit_news
    print("✅ get_reddit_news loaded")
except ImportError:
    print("⚠️  get_reddit_news not found")

try:
    from tradingagents.agents.utils.agent_utils import get_reddit_global_news
    sentiment_tools_available["get_reddit_global_news"] = get_reddit_global_news
    print("✅ get_reddit_global_news loaded")
except ImportError:
    print("⚠️  get_reddit_global_news not found")

try:
    from tradingagents.agents.utils.agent_utils import get_news_sentiment
    sentiment_tools_available["get_news_sentiment"] = get_news_sentiment
    print("✅ get_news_sentiment loaded")
except ImportError:
    print("⚠️  get_news_sentiment not found")

print(f"\nAvailable sentiment tools: {list(sentiment_tools_available.keys())}")

llm_sentiment = ChatOllama(model="qwen2.5:7b", base_url="http://localhost:11434", temperature=0, repeat_penalty=1.15, num_predict=2048)

def create_sentiment_analyst(llm):
    def sentiment_analyst_node(state):
        current_date = state["trade_date"]
        company = state["company_of_interest"]

        print(f"📊 Fetching sentiment data for {company} on {current_date}...")

        sentiment_data = {}
        arg_patterns = [
            {"ticker": company, "curr_date": current_date},
            {"symbol": company, "curr_date": current_date},
            {"ticker": company, "start_date": current_date},
        ]

        for tool_name, tool_fn in sentiment_tools_available.items():
            print(f"⚙️  Calling: {tool_name}")
            success = False
            for args in arg_patterns:
                try:
                    result = tool_fn.invoke(args)
                    sentiment_data[tool_name] = str(result)[:2000]
                    print(f"   ✅ Got {len(str(result))} chars")
                    success = True
                    break
                except Exception:
                    continue
            if not success:
                sentiment_data[tool_name] = "No data available"
                print(f"   ⚠️  No data retrieved")

        news_context = ""
        try:
            news_context = news_result.get("news_report", "")[:1000]
        except Exception as e:
            # ✅ FIXED: was a bare `except: pass` that silently swallowed
            # any error (e.g. NameError if news_result doesn't exist yet).
            print(f"   ⚠️  Could not attach news_context: {e}")

        data_sections = ""
        for tool_name, data in sentiment_data.items():
            data_sections += f"\n=== {tool_name.upper()} ===\n{data}\n"
        if not data_sections.strip() or all(v == "No data available" for v in sentiment_data.values()):
            data_sections = "No direct sentiment API data available. Use news report for inference."

        prompt = f"""You are a sentiment analyst. Today is {current_date}.
IMPORTANT: Base your analysis ONLY on the data below. Do NOT invent scores.

Analyze market sentiment for {company}.
Sections: Data Source Review → Score Breakdown → Key Signals → Confidence Assessment

At the very end, output a Markdown table with EXACTLY these columns:
| Source | Sentiment | Score (1-10) | Confidence | Key Signal |

=== SENTIMENT DATA ===
{data_sections}

=== NEWS CONTEXT ===
{news_context}
""" + get_language_instruction()

        messages = [
            SystemMessage(content="You are a senior sentiment analyst. Be specific about data sources. Always end with the required Markdown table."),
            HumanMessage(content=prompt),
        ]

        response = llm.invoke(messages)
        report = response.content
        print("✅ Sentiment Report ready!")

        # ── Parse score from report ──
        score_m = re.search(r"(\d+(?:\.\d+)?)\s*/\s*10", report)
        score = score_m.group(1) if score_m else "N/A"
        conf_m = re.search(r"(High|Medium|Low)\s*[Cc]onfidence", report)
        confidence = conf_m.group(1) if conf_m else "N/A"
        overall_m = re.search(r"(BULLISH|BEARISH|NEUTRAL|POSITIVE|NEGATIVE)", report, re.IGNORECASE)
        overall = overall_m.group(1).upper() if overall_m else "NEUTRAL"

        # ✅ Structured sentiment metrics
        sentiment_metrics = {
            "score":      score,
            "confidence": confidence,
            "overall":    overall,
        }

        all_msgs = list(state.get("messages", [])) + messages + [AIMessage(content=report)]
        return {
            "messages":          all_msgs,
            "sentiment_report":  report,
            "sentiment_metrics": sentiment_metrics,
        }

    return sentiment_analyst_node


sentiment_analyst = create_sentiment_analyst(llm_sentiment)

sentiment_state = {
    "trade_date": TRADE_DATE,
    "company_of_interest": "@@TICKER@@",
    "messages": [],
}

result = sentiment_analyst(sentiment_state)

# ✅ FIXED: structured dict log
log_step(
    "Sentiment Analyst",
    {
        "company":         sentiment_state["company_of_interest"],
        "date":            sentiment_state["trade_date"],
        "tools_available": str(list(sentiment_tools_available.keys())),
    },
    {
        "sentiment_metrics": result.get("sentiment_metrics", {}),
        "report":            result.get("sentiment_report", ""),
    }
)

sentiment_result = result
from IPython.display import Markdown
Markdown(result["sentiment_report"])

