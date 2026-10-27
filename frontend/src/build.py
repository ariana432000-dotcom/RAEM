import glob
import json
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
data = {}
for p in sorted(glob.glob(os.path.join(HERE, "..", "..", "backend", "data", "tickers", "*.json"))):   # sorted: same bytes on every OS
    d = json.load(open(p, encoding="utf-8"))
    d["forecast"].pop("scoreboard", None)
    shown = {"N-BEATS", "LASSO", "Ridge", "RandomForest", "Theta", "ARIMA", "Ensemble", "Fusion", "Fusion-Top3"}
    d["hitrate"]["per_model"] = [r for r in d["hitrate"].get("per_model") or [] if r.get("model") in shown]
    if d.get("agent"):
        d["agent"].pop("table", None)
    data[d["ticker"]] = d
tpl = open(os.path.join(HERE, "template3.html"), encoding="utf-8").read()
assert "/*__DATA__*/null" in tpl
out = tpl.replace("/*__DATA__*/null", json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/"))
extra = json.load(open(os.path.join(HERE, "..", "..", "backend", "data", "extra.json"), encoding="utf-8"))
shown = {"N-BEATS", "LASSO", "Ridge", "RandomForest", "Theta", "ARIMA", "Ensemble", "Fusion", "Fusion-Top3"}
extra["backtest"] = {t: {m: r for m, r in v.items() if m in shown} for t, v in extra["backtest"].items()}
# "Data as of" date: written by export_extra.py on each refresh; the newest price date is the fallback for older exports
asof = extra.get("asof") or max(d["forecast"]["last_date"] for d in data.values())
assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", asof), asof
assert out.count('/*__ASOF__*/"2026-10-20"') == 1
out = out.replace('/*__ASOF__*/"2026-10-20"', json.dumps(asof))
out = out.replace("/*__EXTRA__*/null", json.dumps(extra, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/"))
# The template is a page fragment (the claude.ai artifact viewer adds the document skeleton). A hosted page needs its own:
# without a doctype browsers render in quirks mode, and without the viewport tag phones show the desktop layout zoomed out.
ICON = ("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'%3E%3Crect width='32' height='32' rx='8' "
        "fill='%234f46e5'/%3E%3Cpath d='M7 21l6-6 4 4 8-9' stroke='white' stroke-width='3' fill='none' stroke-linecap='round' "
        "stroke-linejoin='round'/%3E%3C/svg%3E")
HEAD = ('<!doctype html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f'<link rel="icon" href="{ICON}">\n')
out = HEAD + out
DIST = os.path.join(HERE, "..", "dist")
os.makedirs(DIST, exist_ok=True)
open(os.path.join(DIST, "index.html"), "w", encoding="utf-8", newline="\n").write(out)  # LF on every OS, like git stores it
print("tickers", sorted(data), "bytes", len(out))
