# raem_analysts/dse/comparison_backtest.py -- shared by abbank-dse, batbc-dse, bpml-dse, waltonhil-dse
# The company-specific values come from each notebook's RAEM_PROFILE:
# @@TICKER@@ ticker, @@FILE@@ ticker as used in file names, @@LOG@@ trading-log name, @@SLOT:key@@ lines the profile supplies.
# >>> shared code
# ================================================================
# 📊 7-DAY COMPARISON & BACKTEST ANALYSIS
# ================================================================
import json, re
import pandas as pd
from datetime import datetime
from IPython.display import display, Markdown

LOG_FILE = "trading_log_@@LOG@@.json"

with open(LOG_FILE, "r", encoding="utf-8") as f:
    raw = json.load(f)

sessions = raw if isinstance(raw, list) else [raw]
sessions = sorted(sessions, key=lambda s: s.get("trade_date", ""))
print(f"✅ Loaded {len(sessions)} sessions\n")


def get_step(session, step_name):
    for step in session.get("steps", []):
        if step_name.lower() in step["step"].lower():
            return step
    return None


# ── 1. DECISION COMPARISON TABLE ─────────────────────────────────
print("=" * 60)
print("📋 DAILY DECISION COMPARISON")
print("=" * 60)

rows = []
for s in sessions:
    date    = s.get("trade_date", "?")
    company = s.get("company", "?")
    fd      = s.get("final_decision", "")
    sig     = s.get("summary_table", {}).get("final_signal", "")

    match = re.search(r"\*{0,2}(BUY|HOLD|SELL)\*{0,2}", fd, re.IGNORECASE)
    decision = sig if sig and sig != "N/A" else (match.group(1).upper() if match else "N/A")

    rows.append({
        "Date":     date,
        "Company":  company,
        "Decision": decision,
        "Summary":  fd[:120].replace("\n", " "),
    })

df_decisions = pd.DataFrame(rows)
display(df_decisions.to_string(index=False))


# ── 2. INDICATOR TREND TABLE ──────────────────────────────────────
print("\n" + "=" * 60)
print("📈 TECHNICAL INDICATOR TRENDS")
print("=" * 60)

ind_rows = []
for s in sessions:
    # ✅ Try summary_table first (structured), fallback to step output string
    inds = s.get("summary_table", {}).get("market_indicators", {})
    if not inds:
        step = get_step(s, "Market Analyst")
        if step:
            out = step.get("output", {})
            if isinstance(out, dict):
                inds = out.get("indicators", {})
            else:
                for line in str(out).split("\n"):
                    if ": " in line:
                        k, _, v = line.partition(": ")
                        inds[k.strip()] = v.strip()

    def parse_first_num(val):
        if isinstance(val, (int, float)):
            return float(val)
        nums = re.findall(r"-?\d+\.?\d*", str(val))
        return float(nums[0]) if nums else None

    ind_rows.append({
        "Date":    s.get("trade_date", "?"),
        "RSI":     parse_first_num(inds.get("rsi", "")),
        "MACD":    parse_first_num(inds.get("macd", "")),
        "50-SMA":  parse_first_num(inds.get("close_50_sma", "")),
        "Boll_UB": parse_first_num(inds.get("boll_ub", "")),
        "Boll_LB": parse_first_num(inds.get("boll_lb", "")),
    })

df_ind = pd.DataFrame(ind_rows)
if not df_ind.empty:
    display(df_ind.to_string(index=False))
else:
    print("  (No indicator data yet)")


# ── 3. FUNDAMENTALS TRACKING TABLE ───────────────────────────────
print("\n" + "=" * 60)
print("🏦 FUNDAMENTALS TRACKING")
print("=" * 60)

fund_rows = []
for s in sessions:
    # ✅ Try summary_table first
    m = s.get("summary_table", {}).get("fundamentals_analyst", {})
    if not m:
        step = get_step(s, "Fundamentals Analyst")
        if step:
            out = step.get("output", {})
            if isinstance(out, dict):
                m = out.get("metrics", {})

    fund_rows.append({
        "Date":       s.get("trade_date", "?"),
        "Market Cap": m.get("market_cap", "N/A"),
        "P/E":        m.get("pe_ratio", "N/A"),
        "EPS":        m.get("eps_ttm", "N/A"),
        "FCF (Q)":    m.get("free_cash_flow_q", "N/A"),
        "Net Debt":   m.get("net_debt", "N/A"),
    })

df_fund = pd.DataFrame(fund_rows)
if not df_fund.empty:
    display(df_fund.to_string(index=False))
else:
    print("  (No fundamentals data yet)")


# ── 4. NEWS SENTIMENT TREND TABLE ────────────────────────────────
print("\n" + "=" * 60)
print("📰 NEWS SENTIMENT TREND")
print("=" * 60)

news_rows = []
for s in sessions:
    # ✅ Try summary_table first
    nm = s.get("summary_table", {}).get("news_analyst", {})
    if not nm:
        step = get_step(s, "News Analyst")
        if step:
            out = step.get("output", {})
            if isinstance(out, dict):
                nm = out.get("news_metrics", out.get("metrics", {}))

    news_rows.append({
        "Date":        s.get("trade_date", "?"),
        "Overall":     nm.get("overall_sentiment", "N/A"),
        "Positive":    nm.get("positive_count", "N/A"),
        "Negative":    nm.get("negative_count", "N/A"),
        "Neutral":     nm.get("neutral_count", "N/A"),
    })

df_news = pd.DataFrame(news_rows)
if not df_news.empty:
    display(df_news.to_string(index=False))
else:
    print("  (No news sentiment data yet)")


# ── 5. SIMPLE BACKTEST ────────────────────────────────────────────
print("\n" + "=" * 60)
print("💰 SIMPLE BACKTEST — Follow-the-Signal Strategy")
print("=" * 60)

portfolio = 10000.0
position  = 0.0
buy_price = None
trades    = []
price     = None

for s in sessions:
    date = s.get("trade_date", "?")
    fd   = s.get("final_decision", "")
    sig  = s.get("summary_table", {}).get("final_signal", "")
    match = re.search(r"\*{0,2}(BUY|HOLD|SELL)\*{0,2}", fd, re.IGNORECASE)
    signal = sig if sig and sig != "N/A" else (match.group(1).upper() if match else "HOLD")

    # ✅ Try summary_table stock_data first
    raw_str = s.get("summary_table", {}).get("market_stock_data", "")
    if not raw_str:
        mstep = get_step(s, "Market Analyst")
        if mstep:
            out = mstep.get("output", {})
            raw_str = out.get("stock_data", "") if isinstance(out, dict) else str(out)

    # Close price: summary_table latest_close থেকে আগে দেখো
    price = s.get("summary_table", {}).get("market_latest_close")

    # Fallback: CSV থেকে extract
    if price is None:
        fallback_csv = s.get("summary_table", {}).get("market_stock_data", raw_str)
        csv_lines = [l for l in str(fallback_csv).split("\n") if l.strip() and not l.startswith("#")]
        if len(csv_lines) > 1:
            for row in reversed(csv_lines[1:]):
                parts = row.split(",")
                if len(parts) >= 5 and parts[4].strip() and parts[4].strip() not in ("", "Close"):
                    try:
                        price = float(parts[4].strip())
                        break
                    except ValueError:
                        pass
    if price is None:
        trades.append({"Date": date, "Signal": signal, "Price": "N/A",
                       "Action": "—", "Portfolio": f"${portfolio:,.0f}"})
        continue

    action = "—"
    if signal == "BUY" and position == 0:
        shares = portfolio / price
        position  = shares
        buy_price = price
        portfolio = 0
        action    = f"BOUGHT {shares:.2f} shares @ ${price:.2f}"
    elif signal == "SELL" and position > 0:
        portfolio = position * price
        action    = f"SOLD {position:.2f} shares @ ${price:.2f} | P&L: ${(price-buy_price)*position:+,.0f}"
        position  = 0
        buy_price = None

    trades.append({
        "Date":      date,
        "Signal":    signal,
        "Price":     f"${price:.2f}",
        "Action":    action,
        "Portfolio": f"${(portfolio + position * price):,.0f}",
    })

final_value = portfolio + (position * (price if price else 0))
pnl_pct     = ((final_value - 10000) / 10000) * 100
df_bt = pd.DataFrame(trades)
display(df_bt.to_string(index=False))
print(f"\n  Starting Capital : $10,000")
print(f"  Final Value      : ${final_value:,.0f}")
print(f"  Total Return     : {pnl_pct:+.2f}%")


# ── 6. DECISION CONSISTENCY SCORE ────────────────────────────────
print("\n" + "=" * 60)
print("🎯 DECISION CONSISTENCY")
print("=" * 60)

decisions = [r["Decision"] for r in rows if r["Decision"] != "N/A"]
if decisions:
    from collections import Counter
    counts = Counter(decisions)
    total  = len(decisions)
    for dec, cnt in counts.most_common():
        bar = "█" * cnt
        print(f"  {dec:5s}  {bar}  ({cnt}/{total} days — {cnt/total*100:.0f}%)")
else:
    print("  (No decisions yet)")

print("\n✅ Analysis complete!")

