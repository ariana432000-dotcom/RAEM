# raem_analysts/dse/scenario_estimate.py -- shared by abbank-dse, batbc-dse, bpml-dse, waltonhil-dse
# The company-specific values come from each notebook's RAEM_PROFILE:
# @@TICKER@@ ticker, @@FILE@@ ticker as used in file names, @@LOG@@ trading-log name, @@SLOT:key@@ lines the profile supplies.
# >>> shared code
# ================================================================
# 📈 10-DAY SCENARIO ESTIMATE -- "if I buy today, what range of gain/loss could 10 trading days bring?"
# ================================================================
# Method: for EVERY day in the last 12 months compute
#     10-day return % = (close 10 trading days later / close that day - 1) * 100
# then apply the percentiles of those ~230 returns to today's buy price.
# This is the historical RANGE of outcomes. It is NOT a forecast and NOT investment advice
# (in this project ARIMA / Kalman / Ridge forecasts did not beat a "price stays the same" baseline).
# Prices come live from DSE through fetch_dse_ohlcv_range (data layer cell), so the history is up to TRADE_DATE.
import numpy as np
import pandas as pd
from datetime import datetime, timedelta

SCEN_BUY_PRICE     = None   # None = latest DSE close; or type today's real buy price, e.g. 218.60
SCEN_SHARES        = 100    # number of shares you buy
SCEN_COST_PCT      = 0.5    # ROUND-TRIP cost (buy + sell) as % of invested value -- PLACEHOLDER: set your broker's real cost
SCEN_HORIZON       = 10     # trading days you hold
SCEN_LOOKBACK_DAYS = 365    # calendar days of history used (12 months)


def ten_day_scenarios(company, as_of, buy_price=None, shares=SCEN_SHARES, cost_pct=SCEN_COST_PCT,
                      horizon=SCEN_HORIZON, lookback_days=SCEN_LOOKBACK_DAYS):
    start = (datetime.strptime(as_of, "%Y-%m-%d") - timedelta(days=lookback_days + 20)).strftime("%Y-%m-%d")
    df = fetch_dse_ohlcv_range(company, start, as_of)
    if df is None or df.empty:
        print("   ⚠️ 10-day scenario: no price data returned by DSE.")
        return None
    c = df["Close"].where(df["Close"] > 0).dropna()
    last = c.index.max()
    w = c[c.index >= last - pd.Timedelta(days=lookback_days)]
    r = ((w.shift(-horizon) / w - 1).dropna() * 100)
    if len(r) < 100:
        print(f"   ⚠️ 10-day scenario: only {len(r)} usable windows (need >= 100) -- skipped.")
        return None

    buy = float(buy_price) if buy_price is not None else float(c.iloc[-1])
    invested = buy * shares
    cost = invested * cost_pct / 100.0
    age = (pd.Timestamp(as_of) - last.normalize()).days

    lines = [f"📈 10-DAY SCENARIO ESTIMATE -- {company} | as of {as_of}",
             f"   Price data: {w.index.min().date()} -> {last.date()}  ({len(w)} days, {len(r)} ten-day windows)"]
    if age > 5:
        lines.append(f"   ⚠️ latest DSE close is {age} days old -- check the data fetch.")
    lines.append(f"   Buy {shares:g} share(s) at {buy:,.2f} = {invested:,.0f} invested | assumed round-trip cost {cost_pct}% = {cost:,.0f}")
    lines.append("")
    lines.append(f"   {'Scenario':24s}{'10d return':>11s}{'Sell price':>12s}{'Net P/L':>12s}{'Net %':>9s}")

    scen = []
    for lab, q in (("Worst case (5th pct)", .05), ("Bad (25th pct)", .25), ("Typical (median)", .50),
                   ("Good (75th pct)", .75), ("Best case (95th pct)", .95)):
        p = float(r.quantile(q))
        sell = buy * (1 + p / 100)
        net = invested * p / 100 - cost
        scen.append({"scenario": lab, "pct": round(p, 2), "sell_price": round(sell, 2),
                     "net_pl": round(net, 0), "net_pct": round(net / invested * 100, 2)})
        lines.append(f"   {lab:24s}{p:10.1f}%{sell:12.2f}{net:12,.0f}{net / invested * 100:8.1f}%")

    p_gain = float((r > 0).mean() * 100)
    p_net = float((r > cost_pct).mean() * 100)
    lines += ["",
              f"   Chance of any gain (>0%)                 : {p_gain:.0f}%",
              f"   Chance of net profit after costs (>{cost_pct}%) : {p_net:.0f}%",
              f"   Chance of losing more than 5% / 10%      : {(r < -5).mean() * 100:.0f}% / {(r < -10).mean() * 100:.0f}%",
              f"   Worst / best 10-day window (12 months)   : {r.min():.1f}% / {r.max():.1f}%   (mean {r.mean():+.2f}%)"]

    lr = np.log(w).diff().dropna()
    vol = {}
    for lab, n in (("20d", 20), ("60d", 60)):
        sd = float(lr.tail(n).std() * np.sqrt(horizon) * 100)
        vol[lab] = round(sd, 2)
        lines.append(f"   Recent-volatility range (last {lab}): +/-{sd:.1f}% (68%), +/-{2 * sd:.1f}% (95%)"
                     f" -> {buy * (1 - sd / 100):,.2f} to {buy * (1 + sd / 100):,.2f}")
    lines.append("   Past distribution only -- not a prediction, not investment advice.")

    return {"company": company, "as_of": as_of, "data_end": str(last.date()), "n_windows": int(len(r)),
            "buy_price": round(buy, 2), "shares": shares, "cost_pct": cost_pct, "horizon": horizon,
            "scenarios": scen, "p_gain": round(p_gain, 1), "p_net_profit": round(p_net, 1),
            "mean_pct": round(float(r.mean()), 2), "median_pct": round(float(r.median()), 2),
            "vol_10d_1sigma_pct": vol, "report": "\n".join(lines)}


# ── RUN ──
print("=" * 60)
try:
    scenario_result = ten_day_scenarios(COMPANY, TRADE_DATE, buy_price=SCEN_BUY_PRICE)
except Exception as _e:
    scenario_result = None
    print(f"   ⚠️ 10-day scenario skipped: {str(_e)[:160]}")
if scenario_result:
    print(scenario_result["report"])
print("=" * 60 + "\n")

if scenario_result:
    try:
        session_log["summary_table"]["ten_day_scenario"] = {
            "buy_price": scenario_result["buy_price"], "shares": scenario_result["shares"],
            "p_gain": scenario_result["p_gain"], "p_net_profit": scenario_result["p_net_profit"],
            "median_pct": scenario_result["median_pct"],
            "worst_5pct": scenario_result["scenarios"][0]["pct"], "best_95pct": scenario_result["scenarios"][-1]["pct"]}
    except Exception:
        pass
    log_step("10-Day Scenario Estimate",
             {"buy_price": scenario_result["buy_price"], "shares": scenario_result["shares"],
              "cost_pct": scenario_result["cost_pct"], "horizon": scenario_result["horizon"]},
             {"scenario": {k: v for k, v in scenario_result.items() if k != "report"},
              "report": scenario_result["report"]})

