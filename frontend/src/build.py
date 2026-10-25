import glob
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
data = {}
for p in glob.glob(os.path.join(HERE, "..", "..", "backend", "data", "tickers", "*.json")):
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
