# raem_analysts/dse/sentiment_analyst.py -- shared by abbank-dse, batbc-dse, bpml-dse, waltonhil-dse
# The company-specific values come from each notebook's RAEM_PROFILE:
# @@TICKER@@ ticker, @@FILE@@ ticker as used in file names, @@LOG@@ trading-log name, @@SLOT:key@@ lines the profile supplies.
# >>> shared code
from langchain_ollama import ChatOllama
from langchain_core.messages import HumanMessage, AIMessage, SystemMessage
from tradingagents.agents.utils.agent_utils import get_language_instruction
import re
from datetime import datetime

# Try importing sentiment tools
sentiment_tools_available = {}
try:
    raise ImportError("Finnhub-only tool, no DSE equivalent")  # from tradingagents.agents.utils.agent_utils import get_stock_news_sentiment
    sentiment_tools_available["get_stock_news_sentiment"] = get_stock_news_sentiment
    print("✅ get_stock_news_sentiment loaded")
except ImportError:
    print("⚠️  get_stock_news_sentiment not found")

try:
    raise ImportError("Finnhub-only tool, no DSE equivalent")  # from tradingagents.agents.utils.agent_utils import get_reddit_news
    sentiment_tools_available["get_reddit_news"] = get_reddit_news
    print("✅ get_reddit_news loaded")
except ImportError:
    print("⚠️  get_reddit_news not found")

try:
    raise ImportError("Finnhub-only tool, no DSE equivalent")  # from tradingagents.agents.utils.agent_utils import get_reddit_global_news
    sentiment_tools_available["get_reddit_global_news"] = get_reddit_global_news
    print("✅ get_reddit_global_news loaded")
except ImportError:
    print("⚠️  get_reddit_global_news not found")

try:
    raise ImportError("Finnhub-only tool, no DSE equivalent")  # from tradingagents.agents.utils.agent_utils import get_news_sentiment
    sentiment_tools_available["get_news_sentiment"] = get_news_sentiment
    print("✅ get_news_sentiment loaded")
except ImportError:
    print("⚠️  get_news_sentiment not found")

print(f"\nAvailable sentiment tools: {list(sentiment_tools_available.keys())}")

llm_sentiment = ChatOllama(model="qwen2.5:7b", base_url="http://localhost:11434", num_ctx=8192, temperature=0, num_predict=_cap('sentiment') or 2048, repeat_penalty=1.15)

# ================================================================
# 📐 RULE-BASED NEWS SENTIMENT (computed in code, from the real news_corpus chunks)
# ================================================================
# Before: all 4 sentiment tools are disabled (Finnhub/Reddit only), so the LLM invented a 1-10 score from the first
# 1000 chars of the news report. Now: every company-scope news chunk (as-of filtered, recency weighted) is scored with
# a transparent keyword lexicon IN CODE; the LLM only explains it. The code-computed score/overall/confidence are what
# the rest of the pipeline (debate, Trader, episode memory) receives. The LLM's own table score is kept as `llm_score`.
# Honest limits: a keyword lexicon is crude (negation handled only for a few phrases), DSE notices are mostly factual
# (-> many NEUTRAL), and chunks are English; Bangla text is not scored. Use it as a screen, not as ground truth.
# Ablation switch for the thesis: USE_RULE_SENTIMENT = False reproduces the old LLM-only behaviour.
USE_RULE_SENTIMENT = True
SENT_MAX_AGE_DAYS = 30
SENT_HALF_LIFE_DAYS = 10.0
SENT_BULL_AT, SENT_BEAR_AT = 6.0, 4.0          # score >= 6 -> BULLISH, <= 4 -> BEARISH, else NEUTRAL

_SENT_POS = re.compile(r"\b(?:dividend (?:declared|announced|recommended|increase[sd]?)|(?:cash|stock|bonus) dividend|bonus shares?|"
                       r"profit (?:rose|rises|grew|growth|increase[sd]?|up)|(?:higher|increased|improved|rising|strong) (?:profit|earnings|eps|revenue|sales)|"
                       r"eps (?:rose|increased|up|improved)|record (?:profit|high|earnings)|upgrade[sd]?|surge[sd]?|rall(?:y|ied)|"
                       r"expansion|new (?:order|contract)|growth)\b", re.I)
_SENT_NEG = re.compile(r"\b(?:net loss|loss(?:es)?|profit (?:fell|falls|dropped|decline[sd]?|down)|(?:lower|decreased|declined|falling|weak) (?:profit|earnings|eps|revenue|sales)|"
                       r"eps (?:fell|decreased|down|declined)|no dividend|omit(?:ted)? dividend|downgrade[sd]?|penalt(?:y|ies)|fine[sd]?|"
                       r"suspend(?:ed|s)?|delist(?:ed|ing)?|default(?:ed)?|probe|investigation|fraud|plunge[sd]?|slump(?:ed)?|"
                       r"tax (?:hike|increase)|duty (?:hike|increase)|ban(?:ned)?|warning|circuit breaker (?:hit|lower))\b", re.I)


def score_text_sentiment(text: str):
    """(label, pos_hits, neg_hits) for one chunk. Pure function."""
    p, n = len(_SENT_POS.findall(text)), len(_SENT_NEG.findall(text))
    if p > n:
        return "POSITIVE", p, n
    if n > p:
        return "NEGATIVE", p, n
    return "NEUTRAL", p, n


def rule_based_news_sentiment(company: str, as_of: str) -> dict:
    """Aggregate chunk-level sentiment into a 1-10 score. Returns {"n":0} if there is nothing to score."""
    try:
        got = news_corpus.get()
    except Exception:
        return {"n": 0}
    items, seen = [], set()
    for doc, m in zip(got.get("documents", []), got.get("metadatas", [])):
        m = m or {}
        if "scope" not in m or m.get("company") != company:           # company-tagged news only (sector / market chunks carry other tags); legacy v1 chunks ignored
            continue
        if _is_junk_news(doc) or _is_directory_text(doc):
            continue
        pub = m.get("pub_date") or m.get("trade_date", "")
        if pub and pub > as_of:                                      # leakage guard
            continue
        try:
            age = max((datetime.strptime(as_of, "%Y-%m-%d") - datetime.strptime(pub, "%Y-%m-%d")).days, 0) if pub else 0
        except Exception:
            age = 0
        if age > SENT_MAX_AGE_DAYS:
            continue
        key = re.sub(r"\s+", " ", doc).lower()[:120]
        if key in seen:
            continue
        seen.add(key)
        items.append((doc, age))
    if not items:
        return {"n": 0}
    pos = neg = neu = 0
    num = den = 0.0
    for doc, age in items:
        label, _p, _n = score_text_sentiment(doc)
        w = 0.5 ** (age / SENT_HALF_LIFE_DAYS)
        s = 1.0 if label == "POSITIVE" else (-1.0 if label == "NEGATIVE" else 0.0)
        pos, neg, neu = pos + (label == "POSITIVE"), neg + (label == "NEGATIVE"), neu + (label == "NEUTRAL")
        num, den = num + w * s, den + w
    net = num / den if den else 0.0                                  # -1 .. +1
    score = round(min(10.0, max(1.0, 5.5 + 4.5 * net)), 1)
    n = len(items)
    overall = "BULLISH" if score >= SENT_BULL_AT else ("BEARISH" if score <= SENT_BEAR_AT else "NEUTRAL")
    confidence = "High" if n >= 8 else ("Medium" if n >= 3 else "Low")
    text = (f"RULE-BASED NEWS SENTIMENT (computed in code from {n} company news item(s) in the last {SENT_MAX_AGE_DAYS} days; "
            f"keyword lexicon, recency weighted): {pos} positive, {neg} negative, {neu} neutral. "
            f"Sentiment score {score}/10 ({overall}), data confidence {confidence}. "
            f"Use exactly this score for the company-news row of the table; do not invent other scores.")
    return {"n": n, "pos": pos, "neg": neg, "neu": neu, "score": score, "overall": overall, "confidence": confidence, "text": text}



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
        rs = {"n": 0}
        if globals().get("USE_RULE_SENTIMENT", True):
            try:
                rs = rule_based_news_sentiment(company, current_date)
            except Exception as e:
                print(f"   ⚠️  Rule-based sentiment skipped: {str(e)[:100]}")
        if rs.get("n", 0) > 0:
            data_sections = rs["text"] + "\n" + data_sections
            print(f"   📐 Rule-based sentiment: {rs['n']} items | +{rs['pos']} / -{rs['neg']} / ={rs['neu']} -> {rs['score']}/10 ({rs['overall']})")
        else:
            print("   ℹ️  No company news to score in code -- sentiment stays LLM-inferred (source=LLM_ONLY).")

        prompt = f"""You are a sentiment analyst. Today is {current_date}.
IMPORTANT: Base your analysis ONLY on the data below. Do NOT invent scores.

Analyze market sentiment for {company}.
Sections: Data Source Review → Score Breakdown → Key Signals → Confidence Assessment
{_len_rule('sentiment')}

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

        response = cached_analyst_invoke("sentiment", llm, messages)
        report = response.content
        print("✅ Sentiment Report ready!")

        # ── Parse score from report ──
        # table first (| Source | Sentiment | Score (1-10) | Confidence | Key Signal |), then "x/10" in the text
        _scores, _confs = [], []
        for _row in report.splitlines():
            _cells = [c.strip() for c in _row.strip().strip("|").split("|")]
            if len(_cells) >= 4 and _row.strip().startswith("|"):
                _m = re.fullmatch(r"(\d+(?:\.\d+)?)(?:\s*/\s*10)?", _cells[2])
                if _m and 0 <= float(_m.group(1)) <= 10:
                    _scores.append(float(_m.group(1)))
                _cm = re.fullmatch(r"(High|Medium|Low)", _cells[3], re.I)
                if _cm:
                    _confs.append(_cm.group(1).capitalize())
        if _scores:
            score = f"{sum(_scores) / len(_scores):.1f}"
        else:
            score_m = re.search(r"(\d+(?:\.\d+)?)\s*/\s*10", report)
            score = score_m.group(1) if score_m else "N/A"
        if _confs:
            confidence = max(set(_confs), key=_confs.count)
        else:
            conf_m = re.search(r"(High|Medium|Low)\s*[Cc]onfidence", report)
            confidence = conf_m.group(1) if conf_m else "N/A"
        overall_m = re.search(r"(BULLISH|BEARISH|NEUTRAL|POSITIVE|NEGATIVE)", report, re.IGNORECASE)
        overall = overall_m.group(1).upper() if overall_m else "NEUTRAL"

        # ✅ Structured sentiment metrics -- code-computed values win when there was real news to score
        sentiment_metrics = {
            "score":      score,
            "confidence": confidence,
            "overall":    overall,
            "source":     "LLM_ONLY",
        }
        if rs.get("n", 0) > 0:
            sentiment_metrics.update({
                "llm_score": score, "llm_overall": overall,                      # what the LLM said, kept for the ablation
                "score": f"{rs['score']:.1f}", "overall": rs["overall"], "confidence": rs["confidence"],
                "source": "RULE_BASED", "n_items": rs["n"],
                "positive_count": rs["pos"], "negative_count": rs["neg"], "neutral_count": rs["neu"],
            })

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
