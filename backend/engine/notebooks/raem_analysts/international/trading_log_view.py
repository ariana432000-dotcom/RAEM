# raem_analysts/international/trading_log_view.py -- shared by AAPL, IBM, 0300_HK, 2222_SR
# The company-specific values come from each notebook's RAEM_PROFILE:
# @@TICKER@@ ticker, @@FILE@@ ticker as used in file names, @@LOG@@ trading-log name, @@SLOT:key@@ lines the profile supplies.
# >>> shared code
import json

with open("trading_log_@@LOG@@.json", "r", encoding="utf-8") as f:
    raw = json.load(f)

sessions = raw if isinstance(raw, list) else [raw]
log = sessions[-1]

print(f"📦 Total sessions in file: {len(sessions)}")
print(f"📅 Showing: {log.get('trade_date', '?')} (most recent)\n")
print(f"Session start : {log['session_start']}")
print(f"Company       : {log['company']}")
print(f"Total steps   : {len(log['steps'])}\n")

for step in log['steps']:
    print("=" * 70)
    print(f"⏰ {step['timestamp']} | 📌 {step['step']}")
    print("-" * 70)
    print("📥 INPUT:")
    print(str(step['input'])[:1000])
    print("\n📤 OUTPUT:")
    out = step['output']
    if isinstance(out, dict):
        for k, v in out.items():
            if k == "report":
                print(f"  [REPORT]: {str(v)[:1000]}...")
            elif k in ("news_table_md", "news_summary_markdown"):
                print(f"  [NEWS TABLE MD]:\n{str(v)[:1000]}")
            else:
                print(f"  [{k.upper()}]: {str(v)[:1000]}")
    else:
        print(str(out)[:600])
    print()

# ── summary_table ──
tbl = log.get("summary_table", {})
if tbl:
    print("=" * 70)
    print("📋 SUMMARY TABLE (top-level structured data):")
    print("=" * 70)
    for k, v in tbl.items():
        if isinstance(v, dict):
            print(f"  [{k.upper()}]:")
            for kk, vv in v.items():
                print(f"    {kk}: {str(vv)[:200]}")
        elif k in ("news_summary_markdown", "final_decision", "market_stock_data"):
            print(f"  [{k.upper()}]: {str(v)[:150]}...")
        else:
            print(f"  [{k.upper()}]: {v}")

# ── all sessions summary ──
if len(sessions) > 1:
    print("\n" + "=" * 70)
    print(f"📋 ALL SESSIONS SUMMARY ({len(sessions)} sessions)")
    print("=" * 70)
    for s in sessions:
        sig = s.get("summary_table", {}).get("final_signal", "?")
        fd = s.get("final_decision", "")[:60].replace("\n", " ")
        print(f"  {s.get('trade_date','?')}  {s.get('company','?'):6s}  [{sig:4s}]  →  {fd}...")

