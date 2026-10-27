# raem_analysts/dse/portfolio_manager_decision.py -- shared by abbank-dse, batbc-dse, bpml-dse, waltonhil-dse
# The company-specific values come from each notebook's RAEM_PROFILE:
# @@TICKER@@ ticker, @@FILE@@ ticker as used in file names, @@LOG@@ trading-log name, @@SLOT:key@@ lines the profile supplies.
# >>> shared code
# ================================================================
# 📊 FINAL SUMMARY — সব results একসাথে
# ================================================================
from IPython.display import Markdown, display

final_decision_text = pm_result.get("final_trade_decision", "")

# Finalize: stamp session_end + final_decision, save summary_table
finalize_session(final_decision_text)

# Also log the Portfolio Manager step
log_step(
    "Portfolio Manager Final Decision",
    "All analyst + risk inputs",
    final_decision_text,
)

print("=" * 60)
print("📁 Full session log saved to:", LOG_FILE)
print("=" * 60)
print(f"\nTotal steps this session: {len(session_log['steps'])}")
print("\nSteps summary:")
for step in session_log["steps"]:
    print(f"  ✅ {step['timestamp']} — {step['step']}")

# ── Print structured summary table ──
print_summary_table()

print_log_summary()
try:
    print("\n" + fact_guard_report())
except NameError:
    pass

print("\n" + "=" * 60)
print("🏁 FINAL PORTFOLIO MANAGER DECISION:")
print("=" * 60)
display(Markdown(final_decision_text))

