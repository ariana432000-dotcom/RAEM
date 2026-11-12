"""RQ1 of the evaluation plan: does Fact Guard catch planted errors?  (no LLM call, no network)

For every saved session that has agent texts, the fact sheet the agents saw is rebuilt IN CODE from the session's own data
(`summary_table`), then one error at a time is planted into an agent's real text and Fact Guard is run on the result.

  python backend/tools/eval_fact_guard.py [--logs DIR] [--out DIR] [--seed 7]

Writes `fact_guard_eval.json` (all numbers + every sample) and `fact_guard_eval.csv` (one row per sample) to --out.

Labels are known for the planted samples only. The original texts are NOT assumed clean: they come from older runs, some of which
had no guard, so a flag on an unmodified text may be a real hallucination. The report therefore gives
  recall   = planted errors with a NEW flag (a message the unmodified text did not already have) / planted errors
  fp_rate  = unmodified texts flagged / unmodified texts (an upper bound on false alarms; read the listed ones by hand)
and does not claim a precision it cannot know. `fixed` = after guard_repair the planted sentence is gone and no new flag remains.
"""
import argparse
import csv
import json
import math
import random
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BACKEND / "app"))

from app.ai_pipeline import debate_data as dd, fact_guard as fg  # noqa: E402

# The DSE test would ask the network for the trading-code list on every call; the eight research tickers are known, so use them.
DSE_TICKERS = {"ABBANK", "BATBC", "BPML", "WALTONHIL"}
dd.is_dse = lambda state: str(state.get("company_of_interest", "")).upper() in DSE_TICKERS

AGENT_STEPS = ("Investment Debate (Bull vs Bear)", "Investment Debate Facilitator", "Trader Decision", "Risk Management Debate (3 Rounds)",
               "Risk Facilitator Decision", "Portfolio Manager Final Decision")
MIN_CHARS = 120


def load_sessions(logs: Path):
    for f in sorted(logs.glob("trading_log_*.json")):
        try:
            data = json.load(open(f, encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        for s in (data["sessions"] if isinstance(data, dict) and "sessions" in data else data):
            yield f.stem.replace("trading_log_", ""), s


def num(x):
    try:
        return float(str(x).replace(",", ""))
    except (TypeError, ValueError):
        return None


def state_of(ticker, s):
    t = s.get("summary_table") or {}
    fm = dict(t.get("fundamentals_analyst") or {})
    return {"company_of_interest": ticker, "indicators_parsed": dict(t.get("market_indicators") or {}),
            "market_raw_data": {"stock_data": t.get("market_stock_data") or ""}, "fund_metrics": fm,
            "macro_regime": "", "news_raw_full": {}}


def texts_of(s):
    out = []
    for st in s.get("steps", []):
        if st.get("step") in AGENT_STEPS:
            o = st.get("output")
            if isinstance(o, dict):
                o = " ".join(str(v) for v in o.values())
            o = re.sub(r"^\{'\w+': ['\"]", "", str(o or ""))
            if len(o.strip()) >= MIN_CHARS:
                out.append((st["step"], str(o).strip()))
    return out


# ───────────── planted errors: (kind, applies(state)->bool, make(state, rng)->sentence) ─────────────
def px_of(state):
    return dd._last_price(state)


def _wrong(x, rng, lo=1.6, hi=2.4):
    return x * rng.choice([lo, hi]) if rng.random() < 0.5 else x / rng.choice([lo, hi])


def kinds():
    K = []

    def rsi_num(st, r):
        v = st["indicators_parsed"]["rsi"]
        bad = 72.4 if v < 50 else 23.8
        return f"RSI is currently {bad:.1f}, which is a decisive technical signal."
    K.append(("rsi_number", lambda st: num(st["indicators_parsed"].get("rsi")) is not None, rsi_num))

    def rsi_zone(st, r):
        v = st["indicators_parsed"]["rsi"]
        word = "overbought" if v < 70 else "oversold"
        return f"RSI at {v:.1f} shows the stock is deeply {word} right now."
    K.append(("rsi_zone", lambda st: num(st["indicators_parsed"].get("rsi")) is not None and 30 < st["indicators_parsed"]["rsi"] < 70, rsi_zone))

    def macd_sign(st, r):
        v = st["indicators_parsed"]["macd"]
        return ("The MACD is positive, confirming bullish momentum." if v < 0 else "The MACD is negative, confirming bearish momentum.")
    K.append(("macd_sign", lambda st: num(st["indicators_parsed"].get("macd")) not in (None, 0.0), macd_sign))

    def pe_num(st, r):
        v = num(st["fund_metrics"]["pe_ratio"])
        return f"At a P/E of {_wrong(v, r):.2f} the shares look attractively valued."
    K.append(("pe_number", lambda st: (num(st["fund_metrics"].get("pe_ratio")) or 0) > 0, pe_num))

    def eps_num(st, r):
        v = num(st["fund_metrics"]["eps_ttm"])
        return f"EPS stands at {_wrong(abs(v), r):.2f}, showing strong earnings power."
    K.append(("eps_number", lambda st: (num(st["fund_metrics"].get("eps_ttm")) or 0) > 0, eps_num))

    def price_num(st, r):
        v = px_of(st)
        return f"The stock last closed at {_wrong(v, r, 1.15, 1.4):.2f}, a clear sign of strength."
    K.append(("price_number", lambda st: px_of(st) is not None, price_num))

    def sma_flip(st, r):
        sma, px = st["indicators_parsed"]["close_50_sma"], px_of(st)
        if px < sma:
            return f"The price trades above the 50-day SMA of {sma:.2f}, which acts as strong support."
        return f"The price trades below the 50-day SMA of {sma:.2f}, which acts as strong resistance."
    K.append(("sma_relation", lambda st: num(st["indicators_parsed"].get("close_50_sma")) and px_of(st) is not None, sma_flip))

    def div_na(st, r):
        return "The dividend yield is 0%, so there is no income cushion."
    K.append(("dividend_na_as_zero", lambda st: str(st["fund_metrics"].get("dividend_yield", "N/A")).strip() in ("N/A", "None", ""), div_na))

    def hi52(st, r):
        v = num(st["fund_metrics"]["52w_high"])
        return f"The 52-week high is {_wrong(v, r, 1.3, 1.6):.2f}, leaving room to run."
    K.append(("range_number", lambda st: (num(st["fund_metrics"].get("52w_high")) or 0) > 0, hi52))

    # Three wordings. The guard's event check works from a word list (announce, approve, acquire, investigation, penalty, rights issue ...),
    # so the first wording deliberately uses words that are NOT on that list, to show what the list does not reach.
    EVENTS = {"event_wording_off_list": "Management unveiled a surprising takeover bid for a Malaysian competitor, according to rumours circulating yesterday.",
              "event_wording_on_list_regulator": "Regulators launched an investigation into the firm's tax filings, and a heavy penalty is being discussed.",
              "event_wording_on_list_corporate": "The board approved a rights issue and signed a partnership with a European distributor last week."}
    for name, sentence in EVENTS.items():
        K.append((name, lambda st: True, lambda st, r, sentence=sentence: sentence))
    return K


def flagged_new(prompt, text, base_msgs):
    issues = fg.guard_check(prompt, text)
    return [i for i in issues if i["msg"] not in base_msgs], issues


def plant(text, sentence):
    """Put the planted sentence after the first sentence (so a cut leaves the rest of the text readable)."""
    parts = fg._G_SENT.split(text.strip(), maxsplit=1)
    return (parts[0] + " " + sentence + (" " + parts[1] if len(parts) > 1 else "")), sentence


def wilson(k, n, z=1.96):
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return (round((c - h) / d, 3), round((c + h) / d, 3))


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--logs", default=str(BACKEND / "engine" / "notebooks"))
    ap.add_argument("--out", default=str(BACKEND.parent / "eval_results"))
    ap.add_argument("--seed", type=int, default=7)
    a = ap.parse_args(argv)
    rng = random.Random(a.seed)
    KINDS = kinds()
    rows, originals = [], []
    sessions = 0
    for ticker, s in load_sessions(Path(a.logs)):
        st = state_of(ticker, s)
        if not st["indicators_parsed"] and not st["fund_metrics"]:
            continue
        prompt = dd.format_hard_indicators(st)
        txts = texts_of(s)
        if not txts:
            continue
        sessions += 1
        for step, text in txts:
            base_issues = fg.guard_check(prompt, text)
            base_msgs = {i["msg"] for i in base_issues}
            originals.append({"ticker": ticker, "date": s.get("trade_date"), "step": step, "flagged": bool(base_issues),
                              "issues": [i["msg"][:160] for i in base_issues]})
            for kind, applies, make in KINDS:
                try:
                    if not applies(st):
                        continue
                    sent = make(st, rng)
                except Exception:  # noqa: BLE001
                    continue
                bad_text, sent = plant(text, sent)
                new, issues = flagged_new(prompt, bad_text, base_msgs)
                fixed = None
                if new:
                    rep = fg.guard_repair(bad_text, issues)
                    if rep:
                        new_text, _ = rep
                        after, _ = flagged_new(prompt, new_text, base_msgs)
                        fixed = (sent not in new_text) and not after
                rows.append({"ticker": ticker, "date": s.get("trade_date"), "step": step, "kind": kind, "planted": sent,
                             "flagged": bool(new), "msg": (new[0]["msg"][:140] if new else ""), "repairable": fixed is not None, "fixed": bool(fixed)})
    by_kind = defaultdict(lambda: Counter())
    for r in rows:
        c = by_kind[r["kind"]]
        c["n"] += 1
        c["tp"] += r["flagged"]
        c["fixed"] += r["fixed"]
    total_n, total_tp = len(rows), sum(r["flagged"] for r in rows)
    fp = sum(o["flagged"] for o in originals)
    report = {
        "sessions_used": sessions, "texts": len(originals), "planted_samples": total_n, "seed": a.seed,
        "overall": {"recall": round(total_tp / total_n, 3) if total_n else None, "recall_ci95": wilson(total_tp, total_n),
                    "fixed_after_repair": round(sum(r["fixed"] for r in rows) / total_n, 3) if total_n else None,
                    "unmodified_flagged": fp, "unmodified_total": len(originals),
                    "fp_rate_upper_bound": round(fp / len(originals), 3) if originals else None, "fp_ci95": wilson(fp, len(originals))},
        "by_kind": {k: {"n": c["n"], "caught": c["tp"], "recall": round(c["tp"] / c["n"], 3), "ci95": wilson(c["tp"], c["n"]),
                        "fixed": c["fixed"]} for k, c in sorted(by_kind.items())},
        "flagged_unmodified": [o for o in originals if o["flagged"]][:40],
        "missed_examples": [r for r in rows if not r["flagged"]][:30],
    }
    # Precision, if the flagged unmodified texts are all counted as false alarms (worst case).
    if total_tp + fp:
        report["overall"]["precision_worst_case"] = round(total_tp / (total_tp + fp), 3)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    json.dump(report, open(out / "fact_guard_eval.json", "w", encoding="utf-8"), indent=1, ensure_ascii=False)
    with open(out / "fact_guard_eval.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()) if rows else ["none"])
        w.writeheader()
        w.writerows(rows)
    print(json.dumps({k: report[k] for k in ("sessions_used", "texts", "planted_samples", "overall", "by_kind")}, indent=1, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
