# raem_analysts/dse/forecast_hit_rate.py -- shared by abbank-dse, batbc-dse, bpml-dse, waltonhil-dse
# The company-specific values come from each notebook's RAEM_PROFILE:
# @@TICKER@@ ticker, @@FILE@@ ticker as used in file names, @@LOG@@ trading-log name, @@SLOT:key@@ lines the profile supplies.
# >>> shared code
# ================================================================
# 7-DAY FORECAST + HIT RATE -- standalone, run this cell on its own
# ================================================================
import os, sys, warnings
if os.path.abspath("..") not in sys.path:
    sys.path.insert(0, os.path.abspath(".."))

import matplotlib.pyplot as plt
from IPython.display import display

import predictor_lib as pl
import forecast_lib as fl

# after the imports: statsmodels re-enables its own warnings when first imported
warnings.filterwarnings("ignore")

FORECAST_TICKER = "@@TICKER@@"

# During "Run All" the 7-DAY FORECAST GRAPH cell above has already trained
# every model for this ticker -- reuse that instead of training twice.
if isinstance(globals().get("fcast"), dict) and fcast.get("ticker") == FORECAST_TICKER:
    print("Reusing the forecast trained earlier in this run.")
else:
    pl.top_up_data_csv(FORECAST_TICKER)            # adds finished trading days only
    fcast = fl.forecast_ticker(FORECAST_TICKER)    # 14 base models, best-two Ensemble, 2 fusions (~2 min)
    fl.record_forecast(FORECAST_TICKER, fcast)     # archived, graded on later runs
    newly_graded = fl.grade_forecasts(FORECAST_TICKER)
    print(f"Graded {newly_graded} past prediction(s) whose target date has now arrived.")

hit_report = fl.hit_rate_report(FORECAST_TICKER)
fl.plot_forecast_report(fcast, hit_report)
plt.show()
fl.plot_all_models(fcast)                          # one panel per model
plt.show()

print(f"Ensemble = average of the best two models: {' + '.join(fcast['ensemble_members'])}")
print(f"Every model's {fl.FORECAST_DAYS}-day forecast ({fcast['currency']}); error_vs_naive < 1 beats 'no change':")
display(fl.all_models_table(fcast))
print("Predicted price by day, every model:")
display(fcast["paths"].round(4 if fcast["currency"] == "BDT" else 2).rename(index=lambda d: d.date()))

print(f"Holdout scoreboard -- every base model on the last {100 - int(fl.SPLIT_RATIO * 100)}% of history "
      f"(mae_vs_naive < 1 beats 'no change'; always-UP would score "
      f"{fcast['always_up_holdout_acc']:.1%} direction):")
display(fcast["scoreboard"].drop(columns="params").round(4))
if fcast["failed_models"]:
    print("Skipped this run:", fcast["failed_models"])

fusion_check = fcast["fusion_report"]
if not fusion_check.empty:
    a = fusion_check.attrs
    print(f"Fusion check -- same dates for every model: fusion weights learned on {a['fit_origins']} holdout "
          f"dates, then everything scored on the next {a['test_origins']} ({a['test_from']} to {a['test_to']}):")
    display(fusion_check.round(4))
    print("Fusion weights behind today's forecast:", fcast["results"]["fusion"]["params"]["weights"])

print("HIT RATE --", hit_report["verdict"])
print("\nPer model (all graded forecasts; most-tested first):")
display(hit_report["per_model"])
print("Ensemble, by days ahead:")
display(hit_report["by_horizon"])
print("Most recent graded Ensemble calls:")
display(hit_report["recent_calls"])
print(f"{hit_report['pending_calls']} call(s) still waiting for their target date "
      f"(next one: {hit_report['next_grading_date']}); {hit_report['ungradable_calls']} "
      f"fell on a holiday or missing day and can never be graded.")

