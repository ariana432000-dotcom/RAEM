# raem_analysts/international/llm_setup_and_logger.py -- shared by AAPL, IBM, 0300_HK, 2222_SR
# The company-specific values come from each notebook's RAEM_PROFILE:
# @@TICKER@@ ticker, @@FILE@@ ticker as used in file names, @@LOG@@ trading-log name, @@SLOT:key@@ lines the profile supplies.
# >>> shared code
import os, sys, json, time, csv
from datetime import datetime, timedelta

# ================================================================
# 📁 SESSION LOGGER v2 — structured output, summary_table, 10-day
# ================================================================
LOG_FILE   = "trading_log_@@LOG@@.json"
CSV_FILE   = "trading_log_@@LOG@@.csv"  # ✅ NEW: same data, mirrored as CSV
MAX_DAYS   = 10
COMPANY    = "@@TICKER@@"
TRADE_DATE = datetime.now().strftime("%Y-%m-%d")

def _load_sessions() -> list:
    if not os.path.exists(LOG_FILE):
        return []
    try:
        with open(LOG_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return [data]
        return data if isinstance(data, list) else []
    except (json.JSONDecodeError, IOError):
        return []

def _prune(sessions: list) -> list:
    cutoff = datetime.now() - timedelta(days=MAX_DAYS)
    kept = []
    for s in sessions:
        try:
            if datetime.strptime(s["session_start"], "%Y-%m-%d %H:%M:%S") >= cutoff:
                kept.append(s)
        except (KeyError, ValueError):
            kept.append(s)
    return kept

# ✅ NEW: CSV mirror — same session data as the JSON log, flattened into
# one row per session. Nested parts (all step outputs, full summary_table)
# are kept intact as JSON strings in their own columns, so nothing that's
# in trading_log_@@LOG@@.json is missing from trading_log_@@LOG@@.csv.
STEP_NAME_TO_KEY = {
    "Fundamentals Analyst":              "fundamentals",
    "Market Analyst":                    "market",
    "Macro Regime Analyst":              "macro_regime",
    "News Analyst":                      "news",
    "Sentiment Analyst":                 "sentiment",
    "Investment Debate (Bull vs Bear)":  "debate_bull_bear",
    "Investment Debate Facilitator":     "debate_facilitator",
    "Post-Mortem Self-Critique":         "post_mortem",
    "Trader Decision":                   "trader",
    "Risk Management Debate (3 Rounds)": "risk_debate",
    "Risk Facilitator Decision":         "risk_facilitator",
    "Portfolio Manager Final Decision":  "portfolio_manager",
    "Prediction Engine":                 "prediction_engine",
    "Decision Verifier":                 "decision_verifier",
}

def _stringify_for_csv(value) -> str:
    """dict/list -> compact JSON string; everything else -> str(), newlines
    flattened so one step's answer can't break the CSV into extra rows."""
    if isinstance(value, (dict, list)):
        text = json.dumps(value, ensure_ascii=False)
    else:
        text = "" if value is None else str(value)
    return text.replace(chr(13) + chr(10), " ").replace(chr(10), " ")

def _step_io_by_name(steps: list, step_name: str):
    """Last matching step's (input, output) -- last, in case a step name is
    ever logged more than once in a single session."""
    match = None
    for step in steps:
        if step.get("step") == step_name:
            match = step
    if match is None:
        return "", ""
    return _stringify_for_csv(match.get("input")), _stringify_for_csv(match.get("output"))

# ✅ EXPANDED: every agent's full input AND output now gets its own pair of
# readable CSV columns (<step>_input / <step>_output), instead of being
# reachable only by parsing the steps_json blob. steps_json/summary_table_json
# are kept too, for anything that still wants the raw structure.
CSV_FIELDS = [
    "session_id", "session_start", "session_end", "company", "trade_date",
    "final_signal", "final_decision", "market_latest_close",
    "rsi", "macd", "close_50_sma", "boll_ub", "boll_lb",
    "pe_ratio", "eps_ttm", "market_cap", "net_debt",
    "overall_sentiment", "positive_count", "negative_count", "neutral_count",
    "sentiment_score",
] + [f"{key}_input" for key in STEP_NAME_TO_KEY.values()] \
  + [f"{key}_output" for key in STEP_NAME_TO_KEY.values()] \
  + ["steps_json", "summary_table_json"]

def _flatten_session_for_csv(s: dict) -> dict:
    tbl   = s.get("summary_table", {}) or {}
    inds  = tbl.get("market_indicators", {}) or {}
    fm    = tbl.get("fundamentals_analyst", {}) or {}
    nm    = tbl.get("news_analyst", {}) or {}
    sm    = tbl.get("sentiment_analyst", {}) or {}
    steps = s.get("steps", [])

    row = {
        "session_id":          s.get("session_id", ""),
        "session_start":       s.get("session_start", ""),
        "session_end":         s.get("session_end", ""),
        "company":             s.get("company", ""),
        "trade_date":          s.get("trade_date", ""),
        "final_signal":        tbl.get("final_signal", ""),
        "final_decision":      (s.get("final_decision", "") or "")[:500].replace(chr(10), " "),
        "market_latest_close": tbl.get("market_latest_close", ""),
        "rsi":                 inds.get("rsi", ""),
        "macd":                inds.get("macd", ""),
        "close_50_sma":        inds.get("close_50_sma", ""),
        "boll_ub":             inds.get("boll_ub", ""),
        "boll_lb":             inds.get("boll_lb", ""),
        "pe_ratio":            fm.get("pe_ratio", ""),
        "eps_ttm":             fm.get("eps_ttm", ""),
        "market_cap":          fm.get("market_cap", ""),
        "net_debt":            fm.get("net_debt", ""),
        "overall_sentiment":   nm.get("overall_sentiment", ""),
        "positive_count":      nm.get("positive_count", ""),
        "negative_count":      nm.get("negative_count", ""),
        "neutral_count":       nm.get("neutral_count", ""),
        "sentiment_score":     sm.get("score", ""),
        "steps_json":          json.dumps(steps, ensure_ascii=False),
        "summary_table_json":  json.dumps(tbl, ensure_ascii=False),
    }
    for step_name, key in STEP_NAME_TO_KEY.items():
        row[f"{key}_input"], row[f"{key}_output"] = _step_io_by_name(steps, step_name)
    return row

def _save_sessions_csv(sessions: list) -> None:
    """JSON-এ যা যা session store হচ্ছে, ঠিক সেই same sessions গুলো
    CSV_FILE-এও mirror করে রাখে (Excel/Sheets-এ সরাসরি খোলা যায়)।"""
    with open(CSV_FILE, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        writer.writeheader()
        for s in sessions:
            writer.writerow(_flatten_session_for_csv(s))


def _save_sessions(sessions: list) -> None:
    with open(LOG_FILE, "w", encoding="utf-8") as f:
        json.dump(sessions, f, indent=2, ensure_ascii=False)
    # ✅ NEW: every time the JSON log is (re)written, mirror the same
    # sessions into the CSV file too — single source of truth, so the
    # two files can never drift out of sync.
    _save_sessions_csv(sessions)

def _pretty(data) -> str:
    """Human-readable string for terminal display only."""
    if isinstance(data, dict):
        return "\n".join(f"  [{k.upper()}]: {str(v)[:300]}" for k, v in data.items())
    return str(data)[:800]

SESSION_ID = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")

session_log = {
    "session_id":    SESSION_ID,
    "session_start": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    "company":       COMPANY,
    "trade_date":    TRADE_DATE,
    "steps":         [],
    "summary_table": {},  # ← top-level structured summary
}

def log_step(step_name: str, input_data, output_data) -> None:
    """
    FIXED: output_data dict এখন JSON-এ raw dict হিসেবে save হয়।
    summary_table-এ metrics থাকলে top-level-এও extract হয়।
    """
    entry = {
        "step":      step_name,
        "timestamp": datetime.now().strftime("%H:%M:%S"),
        "input":     _pretty(input_data),
        # ✅ KEY FIX: dict হলে dict-ই রাখো, string-এ convert করো না
        "output":    output_data,
    }
    session_log["steps"].append(entry)

    # ── summary_table update ─────────────────────────────────────
    if isinstance(output_data, dict):
        # "metrics" key থাকলে step নামে store করো
        if "metrics" in output_data and output_data["metrics"]:
            skey = step_name.lower().replace(" ", "_")
            session_log["summary_table"][skey] = output_data["metrics"]

        # News analyst structured metrics
        if "news_metrics" in output_data:
            session_log["summary_table"]["news_analyst"] = output_data["news_metrics"]

        # Sentiment structured metrics
        if "sentiment_metrics" in output_data:
            session_log["summary_table"]["sentiment_analyst"] = output_data["sentiment_metrics"]

        # Market indicators
        if "indicators" in output_data:
            session_log["summary_table"]["market_indicators"] = output_data["indicators"]

        # Market stock_data — latest Close price extract করে রাখো (raw CSV নয়)
        if "stock_data" in output_data:
            import re as _re2
            raw_csv = str(output_data["stock_data"])
            latest_close = None
            csv_lines = [l for l in raw_csv.split("\n") if l.strip() and not l.startswith("#")]
            if len(csv_lines) > 1:
                for row in reversed(csv_lines[1:]):
                    parts = row.split(",")
                    if len(parts) >= 5 and parts[4].strip() and parts[4].strip() not in ("", "Close"):
                        try:
                            latest_close = float(parts[4].strip())
                            break
                        except ValueError:
                            pass
            if latest_close is not None:
                session_log["summary_table"]["market_latest_close"] = latest_close
            # raw CSV রাখো backtest-এর জন্য (আলাদা key)
            session_log["summary_table"]["market_stock_data"] = raw_csv

    # ── persist ──────────────────────────────────────────────────
    # ✅ CHANGED: dedup by session_id (this exact run), not by (company, trade_date).
    # Old code wiped out ANY earlier session from the same calendar date before
    # appending — so running the pipeline every 2 hours meant only the last run
    # of the day ever survived on disk. Now every run keeps its own entry; we
    # only replace a session if it's literally the SAME run being re-saved
    # (this function is called multiple times per run as steps complete).
    sessions = _prune(_load_sessions())
    sessions = [s for s in sessions if s.get("session_id") != SESSION_ID]
    sessions.append(session_log)
    _save_sessions(sessions)
    print(f"📝 Logged: {step_name}  (sessions stored: {len(sessions)})")

def finalize_session(final_decision: str) -> None:
    """Session শেষে একবার call করো।"""
    import re
    session_log["session_end"]    = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    session_log["final_decision"] = final_decision

    # BUY/HOLD/SELL parse করে summary_table-এ রাখো
    match = re.search(r"\*{0,2}(BUY|HOLD|SELL)\*{0,2}", final_decision, re.IGNORECASE)
    session_log["summary_table"]["final_signal"]   = match.group(1).upper() if match else "N/A"
    session_log["summary_table"]["final_decision"] = final_decision[:600]

    # News markdown table capture (if present in final decision)
    tbl = re.search(
        r"(\|.*?Category.*?\|.*?(?:\n\|[-| ]+\|)(?:\n\|.*?\|)+)",
        final_decision, re.IGNORECASE | re.DOTALL
    )
    if tbl:
        session_log["summary_table"]["news_summary_markdown"] = tbl.group(1)

    # ✅ CHANGED: same session_id-based dedup as log_step (see comment there).
    sessions = _prune(_load_sessions())
    sessions = [s for s in sessions if s.get("session_id") != SESSION_ID]
    sessions.append(session_log)
    _save_sessions(sessions)
    print(f"\n✅ Session finalised → {LOG_FILE}  ({len(sessions)} sessions stored)")

def get_session(company: str, trade_date: str) -> dict | None:
    matches = [s for s in _load_sessions()
               if s.get("company") == company and s.get("trade_date") == trade_date]
    return matches[-1] if matches else None

def get_all_sessions(company: str, trade_date: str) -> list:
    return [s for s in _load_sessions()
            if s.get("company") == company and s.get("trade_date") == trade_date]

def get_step_output(session: dict, step_name: str):
    """Step output সরাসরি dict হিসেবে return করে।"""
    for step in session.get("steps", []):
        if step_name.lower() in step["step"].lower():
            return step.get("output")
    return None

def print_log_summary() -> None:
    sessions = _prune(_load_sessions())
    print(f"\n{'─'*60}")
    print(f"  SESSION LOG SUMMARY  ({len(sessions)} sessions, last {MAX_DAYS} days)")
    print(f"{'─'*60}")
    for s in sessions:
        signal = s.get("summary_table", {}).get("final_signal", "?")
        d = s.get("final_decision", "")[:50].replace("\n", " ")
        print(f"  {s.get('trade_date')}  {s.get('company','?'):6s}  [{signal:4s}]  →  {d}...")
    print(f"{'─'*60}\n")

def print_summary_table(session: dict | None = None) -> None:
    """Current session বা দেওয়া session-এর summary_table সুন্দরভাবে print করে।"""
    s   = session or session_log
    tbl = s.get("summary_table", {})
    if not tbl:
        print("  (summary_table empty — run analysts first)")
        return

    print(f"\n{'═'*65}")
    print(f"  📊 SESSION SUMMARY TABLE — {s.get('company','?')} | {s.get('trade_date','?')}")
    print(f"{'═'*65}")

    sig = tbl.get("final_signal", "—")
    print(f"  🏁 Final Signal : {sig}")

    # Fundamentals
    fm = tbl.get("fundamentals_analyst", {})
    if fm:
        print(f"\n  📈 FUNDAMENTALS")
        for k, label in [
            ("market_cap","Market Cap"), ("pe_ratio","P/E Ratio"),
            ("eps_ttm","EPS (latest period)"), ("revenue_ttm","Revenue TTM"),
            ("free_cash_flow_q","Free Cash Flow (Q)"), ("net_debt","Net Debt"),
            ("52w_high","52W High"), ("52w_low","52W Low"),
        ]:
            print(f"     {label:20s}: {fm.get(k,'N/A')}")

    # Market / Technical
    inds = tbl.get("market_indicators", {})
    if inds:
        import re
        print(f"\n  📉 TECHNICAL INDICATORS")
        for k in ["rsi", "macd", "close_50_sma", "boll_ub", "boll_lb"]:
            v = inds.get(k, "N/A")
            nums = re.findall(r"-?\d+\.?\d*", str(v))
            print(f"     {k:16s}: {nums[0] if nums else v}")

    # News
    nm = tbl.get("news_analyst", {})
    if nm:
        print(f"\n  📰 NEWS SENTIMENT")
        print(f"     Overall    : {nm.get('overall_sentiment','N/A')}")
        print(f"     Positive   : {nm.get('positive_count','N/A')}")
        print(f"     Negative   : {nm.get('negative_count','N/A')}")
        print(f"     Neutral    : {nm.get('neutral_count','N/A')}")

    # Sentiment
    sm = tbl.get("sentiment_analyst", {})
    if sm:
        print(f"\n  💬 SENTIMENT SCORE")
        print(f"     Score (1-10): {sm.get('score','N/A')}")
        print(f"     Confidence  : {sm.get('confidence','N/A')}")
        print(f"     Overall     : {sm.get('overall','N/A')}")

    # Prediction Engine (quantitative forecast)
    pe = tbl.get("prediction_engine", {})
    if pe:
        print("\n  QUANTITATIVE PREDICTION")
        model_forecasts = pe.get("model_forecasts") or {}
        ccy0 = pe.get("currency", "")
        for mname, mf in model_forecasts.items():
            mark = "  <<<" if mf.get("is_winner") else ""
            print(f"     {mname:15s}: {mf.get('predicted_price','N/A')} {ccy0} "
                  f"({mf.get('predicted_return_pct','N/A')}%)  "
                  f"acc={mf.get('holdout_direction_accuracy','N/A')}{mark}")
        model = pe.get("winner_model", "N/A")
        print(f"     Winner model : {model}")
        hz = pe.get("horizon_days", "?")
        pp = pe.get("predicted_price", "N/A")
        ccy = pe.get("currency", "")
        pr = pe.get("predicted_return_pct", "N/A")
        print(f"     FINAL {hz}-day forecast: {pp} {ccy} ({pr}%)")
        acc = pe.get("holdout_direction_accuracy", "N/A")
        print(f"     Confidence   : {acc}")
        if pe.get("memory_correction_applied"):
            mcp = pe.get("memory_correction_pct", "N/A")
            n_sim = pe.get("memory_n_similar_episodes", "?")
            print(f"     Memory adj.  : {mcp}% (from {n_sim} similar past episodes)")

    # News markdown table
    md = tbl.get("news_summary_markdown", "")
    if md:
        print(f"\n  📋 NEWS HEADLINES TABLE:")
        print(md[:900])

    print(f"{'═'*65}\n")

# ================================================================
# 🤖 LLM Setup — Ollama (Qwen2.5 7B)
# ================================================================
from langchain_ollama import ChatOllama

llm = ChatOllama(
    model="qwen2.5:7b",
    base_url="http://localhost:11434",
    temperature=0,
    num_ctx=8192,
    repeat_penalty=1.15,
    num_predict=2048,
)

log_step("LLM Setup", "Ollama Qwen2.5 7B", "LLM initialized")
print(f"✅ Logger + LLM ready!  trade_date = {TRADE_DATE}")


# ================================================================
# 🔁 SHARED LLM RETRY HELPER
# ================================================================
# ✅ FIXED: previously each agent cell (Trader, Aggressive/Conservative/
# Neutral Risk debators, Portfolio Manager) had its own copy-pasted
# "for attempt in range(3): ... except: sleep" retry loop. If every
# attempt hit a rate limit, the loop finished WITHOUT ever assigning
# `response`, causing a NameError on the next line. This single helper
# is used everywhere instead and raises a clear error if all retries
# are exhausted.
def invoke_llm_with_retry(llm, prompt_or_messages, max_attempts: int = 4):
    """llm.invoke() with backoff for two known transient Ollama failures:
    rate limiting (429), and the local model getting stuck in a degenerate
    repetition loop that Ollama aborts mid-generation
    (ResponseError: "prediction aborted, token repeat limit reached").

    The repeat-limit abort is retried with the model's temperature nudged
    upward each attempt (greedy temperature=0 decoding tends to repeat the
    same loop if retried unchanged; a small temperature bump changes the
    sampling path enough to usually break it). Temperature is restored
    after the call whether it succeeds or fails, so it never leaks into
    later unrelated calls on the same llm object.

    Raises RuntimeError only if every attempt fails, instead of silently
    leaving a variable undefined."""
    last_err = None
    original_temperature = getattr(llm, "temperature", None)
    try:
        for attempt in range(max_attempts):
            try:
                return llm.invoke(prompt_or_messages)
            except Exception as e:
                last_err = e
                msg = str(e).lower()
                if "429" in msg or "rate_limit" in msg:
                    wait_time = 60 * (attempt + 1)
                    print(f"\u23f3 Rate limit! {wait_time} \u09b8\u09c7\u0995\u09c7\u09a8\u09cd\u09a1 \u0985\u09aa\u09c7\u0995\u09cd\u09b7\u09be...")
                    time.sleep(wait_time)
                elif "repeat limit" in msg or "aborted" in msg:
                    if original_temperature is not None and attempt < max_attempts - 1:
                        llm.temperature = min(1.0, original_temperature + 0.2 * (attempt + 1))
                    new_temp = getattr(llm, 'temperature', '?')
                    print(f"\U0001f501 Repeat-limit abort (attempt {attempt + 1}/{max_attempts}); "
                          f"retrying with temperature={new_temp}...")
                else:
                    raise
    finally:
        if original_temperature is not None:
            llm.temperature = original_temperature
    raise RuntimeError(
        f"invoke_llm_with_retry: {max_attempts} attempts exhausted, last error: {last_err}"
    )


# ================================================================
# ✅ STOPGAP FIX: build_instrument_context() (from the tradingagents
# package, not editable here) appears to always return
# "Consumer Electronics | NASDAQ" regardless of the actual company —
# that's a bug inside that external package, not in this notebook.
# This override table corrects it for tickers we've verified; any other
# ticker not listed here still falls back to the (possibly wrong)
# package output — extend this dict if you add a new ticker.
# ================================================================
KNOWN_INSTRUMENT_OVERRIDES = {
    "2323.TW": "Sector: Technology (Electronic Equipment & Parts) | Exchange: Taiwan Stock Exchange (TWSE)",
    "TTD": "Sector: Communication Services (Advertising Agencies) | Exchange: NASDAQ",
    "2222.SR": "Sector: Energy (Oil & Gas Integrated) | Exchange: Tadawul (Saudi Exchange)",
"0300.HK": "Sector: Consumer Discretionary (Household Appliances) | Exchange: Hong Kong Stock Exchange (HKEX)",
    "AAPL": "Sector: Technology (Consumer Electronics) | Exchange: NASDAQ",
    "IBM":  "Sector: Information Technology (IT Services) | Exchange: NYSE",
    "INTU": "Sector: Technology (Software - Application) | Exchange: NASDAQ",
    "NOK":  "Sector: Technology (Communication Equipment) | Exchange: NYSE",
    "BHC":  "Sector: Healthcare (Specialty Pharmaceuticals) | Exchange: NYSE",
    "CZR":  "Sector: Consumer Cyclical (Resorts & Casinos) | Exchange: NASDAQ",
    "PNR":  "Sector: Industrials (Water Treatment & Pool Equipment) | Exchange: NYSE",
    "AA":   "Sector: Basic Materials (Aluminum) | Exchange: NYSE",
}

def resolve_instrument_context(ticker, raw_context):
    """Overrides build_instrument_context()'s output for known tickers."""
    if ticker in KNOWN_INSTRUMENT_OVERRIDES:
        return KNOWN_INSTRUMENT_OVERRIDES[ticker]
    print(f"[!]  No verified sector/exchange override for {ticker}  -  "
          f"using build_instrument_context() output, which may be wrong. "
          f"Add {ticker} to KNOWN_INSTRUMENT_OVERRIDES if incorrect.")
    return raw_context


# ================================================================
# ✅ FIXED: the Fundamentals Analyst's own report ends with an explicit
# "FINAL TRANSACTION PROPOSAL: BUY/SELL/HOLD" verdict  -  but Bull/Bear
# analysts only ever saw fundamentals_report[:500], which routinely cut
# that verdict off before it arrived (it sits near the end of the report).
# This meant the Fundamentals Analyst could explicitly say SELL (backed by
# real numbers: net loss, negative FCF, high debt) and the Bull Analyst
# would never even see it. This helper preserves that verdict regardless
# of truncation.
# ================================================================
import re as _re_helper

def extract_final_proposal(report_text: str) -> str:
    """Pulls out the FINAL TRANSACTION PROPOSAL verdict so it survives truncation.
    [FIXED]: the old version used r"FINAL TRANSACTION PROPOSAL:.*" which only
    matches same-line text. The LLM sometimes formats this as
    "FINAL TRANSACTION PROPOSAL:**\n**SELL** - ..." with the verdict on the
    NEXT line (markdown bold + line break) -- the old regex silently matched
    nothing but empty markdown stars and never found SELL/BUY/HOLD at all.
    This version searches a window of text after the anchor phrase instead
    of relying on same-line matching, so it survives markdown/newline
    formatting variance.
    """
    report_text = str(report_text)
    idx = report_text.upper().find("FINAL TRANSACTION PROPOSAL")
    if idx == -1:
        return ""
    window = report_text[idx: idx + 150]
    m = _re_helper.search(r"\b(BUY|SELL|HOLD)\b", window, _re_helper.IGNORECASE)
    if not m:
        return ""
    return f"FINAL TRANSACTION PROPOSAL: {m.group(1).upper()}"

# ═══════════════ v10: TOKEN SAVERS (every one has an on/off switch) ═══════════════
import hashlib as _hl, json as _json, os as _os, time as _time
from collections import Counter as _Counter
from types import SimpleNamespace as _NS

# (1) ANALYST REPORT LENGTH -- the debaters read only the first 500 characters of the Market / News / Sentiment reports, and
#     since v8 nothing downstream reads the Fundamentals narrative at all, so long analyst reports were mostly wasted output tokens.
#     A prompt rule asks for a short report; `num_predict` is a GENEROUS runaway guard only (the reports end with a table that code
#     parses, so the cap must not bite in normal use).
USE_ANALYST_LENGTH_CAP = True
ANALYST_MAX_TOKENS = {"market": 900, "news": 900, "sentiment": 700, "macro": 350, "fundamentals": 800}
ANALYST_LEN_RULES = {
    "market": ("LENGTH: start with one line 'KEY POINTS:' (at most 350 characters) giving RSI, MACD, the price vs the 50-day SMA and the "
               "Bollinger position, each with its number. After it write at most 120 words of explanation, then the table. "
               "Later agents read only the first 500 characters of this report."),
    "news": "LENGTH: at most 1-2 sentences per section and about 180 words in total before the table.",
    "sentiment": "LENGTH: about 150 words in total before the table.",
    "fundamentals": "LENGTH: at most about 250 words before the table.",
}


def _cap(name):
    return ANALYST_MAX_TOKENS.get(name) if USE_ANALYST_LENGTH_CAP else None


def _len_rule(name):
    return ("\n" + ANALYST_LEN_RULES[name] + "\n") if (USE_ANALYST_LENGTH_CAP and name in ANALYST_LEN_RULES) else ""


FUND_LLM_REPORT = True        # False = skip the Fundamentals analyst's LLM call entirely (nothing downstream reads its narrative since v8;
                              #         you lose the logged 'analyst's own call' and the written report)

# (2) ANALYST CACHE -- only for temperature-0 analyst calls. The key contains EVERYTHING the output depends on: company, trade date,
#     model, temperature, context size, output cap, and a hash of the full prompt text (which already contains all fetched data:
#     prices, news, fundamentals). Any change -> different key -> the analyst runs fresh. Only the LLM TEXT is cached; numbers, fact
#     sheet, regime and sizing are recomputed in code every run. Turn it OFF for the runs you report in the thesis.
USE_ANALYST_CACHE = True
ANALYST_CACHE_FILE = "analyst_cache.json"
ANALYST_CACHE_VERSION = "v10"
ANALYST_CACHE_MAX_ENTRIES = 200
ANALYST_CACHE_STATS = {"hits": 0, "misses": 0}


def _ac_load():
    try:
        with open(ANALYST_CACHE_FILE, "r", encoding="utf-8") as f:
            d = _json.load(f)
        return d if isinstance(d, dict) and isinstance(d.get("entries"), dict) else {"entries": {}}
    except Exception:
        return {"entries": {}}


def _ac_save(d):
    try:
        ents = d.get("entries", {})
        if len(ents) > ANALYST_CACHE_MAX_ENTRIES:
            keep = sorted(ents.items(), key=lambda kv: kv[1].get("ts", 0), reverse=True)[:ANALYST_CACHE_MAX_ENTRIES]
            d["entries"] = dict(keep)
        with open(ANALYST_CACHE_FILE, "w", encoding="utf-8") as f:
            _json.dump(d, f, ensure_ascii=False)
    except Exception as e:
        print(f"   \u26a0\ufe0f analyst cache not saved: {str(e)[:80]}")


def _ac_key(name, llm, messages):
    texts = "\x1f".join(str(getattr(m, "content", m)) for m in messages)
    parts = [ANALYST_CACHE_VERSION, name, globals().get("COMPANY", ""), globals().get("TRADE_DATE", ""), getattr(llm, "model", ""),
             getattr(llm, "temperature", ""), getattr(llm, "num_ctx", ""), getattr(llm, "num_predict", ""),
             _hl.sha256(texts.encode("utf-8", "ignore")).hexdigest()]
    return _hl.sha256("|".join(map(str, parts)).encode("utf-8")).hexdigest()[:32]


def cached_analyst_invoke(name, llm, messages):
    """Drop-in for `llm.invoke(messages)` in the analyst cells."""
    try:
        deterministic = float(getattr(llm, "temperature", 1)) == 0.0
    except (TypeError, ValueError):
        deterministic = False
    if not (USE_ANALYST_CACHE and deterministic):
        return llm.invoke(messages)
    key = _ac_key(name, llm, messages)
    cache = _ac_load()
    hit = cache["entries"].get(key)
    if hit and str(hit.get("text", "")).strip():
        ANALYST_CACHE_STATS["hits"] += 1
        print(f"   \u267b\ufe0f  cached analyst output: {name} (saved {hit.get('saved_at', '?')}; identical data + prompt + model -> 0 LLM tokens)")
        return _NS(content=hit["text"])
    ANALYST_CACHE_STATS["misses"] += 1
    out = llm.invoke(messages)
    text = str(getattr(out, "content", out))
    if text.strip() and not text.lstrip().startswith(("Error", "(")):
        cache["entries"][key] = {"text": text, "saved_at": _time.strftime("%Y-%m-%d %H:%M:%S"), "ts": _time.time(), "agent": name}
        _ac_save(cache)
    return out


# (3) POST-MORTEM / REFLECTION GATE -- these LLM calls read only the RESOLVED outcomes in memory. HOLD records no P&L (always 0.00%), so
#     with only HOLDs they have nothing to say (and tend to invent "missed profits"). Rules:
#       * same resolved outcomes as the last time  -> reuse the previous text (no LLM call)
#       * no resolved outcome with a non-zero P&L -> no LLM call, a code-built factual line instead
#       * otherwise                                -> call the LLM and remember the answer
USE_OUTCOME_GATE = True
OUTCOME_CACHE_FILE = "outcome_llm_cache.json"
OUTCOME_GATE_VERSION = "v10"


def _oc_pnl(row):
    try:
        return float(row.get("pnl_pct"))
    except (TypeError, ValueError):
        return 0.0


def outcome_is_informative(rows):
    return any(abs(_oc_pnl(r)) >= 0.005 for r in rows)


def outcome_fingerprint(rows, key):
    items = sorted((str(r.get("trade_date", "")), str(r.get("final_signal", "")), str(r.get("regime", "")), str(r.get("pnl_pct", "")),
                    str(r.get("outcome_label", "")), str(r.get("session_id", ""))) for r in rows)
    return _hl.sha256((OUTCOME_GATE_VERSION + "|" + key + "|" + repr(items)).encode("utf-8", "ignore")).hexdigest()[:24]


def gated_outcome_llm(key, rows, run_llm, no_info_text, label):
    if not USE_OUTCOME_GATE:
        return run_llm()
    fp = outcome_fingerprint(rows, key)
    try:
        with open(OUTCOME_CACHE_FILE, "r", encoding="utf-8") as f:
            cache = _json.load(f)
        cache = cache if isinstance(cache, dict) else {}
    except Exception:
        cache = {}
    entry = cache.get(key)
    if entry and entry.get("fp") == fp and str(entry.get("text", "")).strip():
        print(f"   \u267b\ufe0f  {label}: no new resolved outcome since the last run -> reusing the previous text (0 LLM tokens)")
        return entry["text"]
    if not outcome_is_informative(rows):
        print(f"   \u23ed\ufe0f  {label}: {len(rows)} resolved outcome(s), all with 0.00% P&L (HOLD records no P&L) -> LLM call skipped")
        return no_info_text
    text = run_llm()
    if str(text).strip() and not str(text).lstrip().startswith("("):
        cache[key] = {"fp": fp, "text": text, "saved_at": _time.strftime("%Y-%m-%d %H:%M:%S")}
        try:
            with open(OUTCOME_CACHE_FILE, "w", encoding="utf-8") as f:
                _json.dump(cache, f, ensure_ascii=False)
        except Exception as e:
            print(f"   \u26a0\ufe0f outcome cache not saved: {str(e)[:80]}")
    return text


# ── v8: the Fundamentals analyst's BUY/HOLD/SELL call is NOT passed to the other agents ──
# FUND_NUMBERS_ONLY=True  -> every later agent gets only the code-extracted numbers (no LLM narrative, no verdict),
#                            and the "HARD FACT" blocks, the "must out-cite the verdict" rules and the code gate that
#                            downgraded contradicting calls to HOLD all switch themselves off (they need a verdict).
# FUND_NUMBERS_ONLY=False -> v7 behaviour exactly (full LLM report + verdict everywhere).
# FUND_VERDICT_TO_VERIFIER=True -> only the Decision Verifier (after the PM) may still see the verdict as a cross-check.
#                            NOTE: that check can auto-override the final signal to HOLD, so it is OFF by default.
FUND_NUMBERS_ONLY = False   # yfinance notebooks keep the v7 Fundamentals analyst
FUND_VERDICT_TO_VERIFIER = False
FUND_FACTS_MARK = "FUNDAMENTALS - NUMBERS ONLY"


def truncate_preserving_verdict(report_text: str, limit: int = 500) -> str:
    """Truncates a report to `limit` chars but appends the Fundamentals
    Analyst's own FINAL TRANSACTION PROPOSAL verdict if it would otherwise
    be cut off."""
    report_text = str(report_text)
    if report_text.startswith(FUND_FACTS_MARK):          # v8: the numbers-only block is never cut mid-number
        return report_text[:max(limit, 1300)]
    snippet = report_text[:limit]
    verdict = extract_final_proposal(report_text)
    if verdict and verdict not in snippet:
        snippet += f"\n...\n{verdict}"
    return snippet


# ================================================================
# \u2705 NEW: NUMERIC-EVIDENCE VERIFICATION GATE
# ================================================================
# Observed failure mode: even with an explicit rule ("cite >=2 specific
# numbers to override the Fundamentals HARD FACT") and a calibration example,
# the local model sometimes writes "The Bull side cited at least 2 specific
# numbers..." in its own reasoning while citing ZERO actual numbers -- it
# confabulates compliance with the rule instead of following it. Prompting
# alone cannot fix this reliably on a small local model, so this checks the
# claim in code instead of trusting the model's self-report.

def count_numeric_citations(text: str) -> int:
    """Counts distinct 'real' numeric figures in text -- dollar amounts,
    percentages, or multi-digit/decimal numbers. Bare single digits (list
    markers like '1.', '2.') are ignored so they can't inflate the count."""
    text = str(text)
    tokens = _re_helper.findall(r"\$?-?\d[\d,]*\.?\d*%?", text)
    real = set()
    for t in tokens:
        digits_only = _re_helper.sub(r"[^0-9]", "", t)
        if len(digits_only) >= 2 or "%" in t or "$" in t:
            real.add(t)
    return len(real)


def enforce_numeric_evidence_gate(decision_text: str, fund_verdict: str, min_citations: int = 2) -> tuple:
    """If `decision_text`'s own BUY/SELL/HOLD signal contradicts the Fundamentals
    Analyst's hard verdict, verify the decision text actually contains at least
    `min_citations` real numbers before letting the contradiction stand -- do NOT
    trust a sentence in the decision claiming numbers were cited.
    Returns (possibly-corrected text, was_overridden: bool, reason: str).
    """
    decision_text = str(decision_text)
    sig_match = _re_helper.search(r"\*{0,2}(BUY|HOLD|SELL)\*{0,2}", decision_text, _re_helper.IGNORECASE)
    signal = sig_match.group(1).upper() if sig_match else "N/A"
    fund_verdict_upper = (fund_verdict or "").upper()

    contradicts = (
        ("SELL" in fund_verdict_upper and signal == "BUY")
        or ("BUY" in fund_verdict_upper and signal == "SELL")
    )
    if not contradicts:
        return decision_text, False, ""

    n_cited = count_numeric_citations(decision_text)
    if n_cited >= min_citations:
        return decision_text, False, f"Contradiction allowed to stand: {n_cited} real numeric citations found."

    corrected = (
        decision_text
        + f"\n\n[AUTO-CORRECTED by code, not LLM] This decision claims to override the "
        f"Fundamentals Analyst's own verdict ({fund_verdict}) but the text contains only "
        f"{n_cited} real numeric citation(s) -- at least {min_citations} required. Vague "
        f"optimism is not evidence. Downgrading to HOLD.\n\nFINAL TRANSACTION PROPOSAL: **HOLD**"
    )
    return corrected, True, f"Overridden: only {n_cited} real numeric citation(s), needed {min_citations}."
