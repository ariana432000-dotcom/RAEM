"""Copy the forecasting code and the state it reads from the research project into backend/engine/.

    python backend/tools/sync_engine.py                      # from the project that contains web/
    python backend/tools/sync_engine.py D:/path/to/project   # from another copy of the project

Run it whenever the research project changes (new prices, retrained cache, new notebook logs,
code changes), so the standalone web folder uses the same models and data.
Backup files (*.bak*) are skipped. Existing files in backend/engine/ are overwritten.
"""
import glob
import os
import shutil
import sys

BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENGINE = os.path.join(BACKEND, "engine")
# default source: the research project that contains web/ (web/backend/tools -> project)
SRC = os.path.abspath(sys.argv[1]) if len(sys.argv) > 1 else os.path.dirname(os.path.dirname(BACKEND))

LIBS = ["predictor_lib.py", "forecast_lib.py", "advanced_forecast.py", "forecast_agent.py"]
DIRS = ["data", "daily_data", "forecast_log", "cache", "chroma_memory", "paper", os.path.join("notebooks", "raem_chroma_db"),
        os.path.join("notebooks", "raem_analysts")]   # the analyst code the notebooks share
NOTEBOOK_FILES = ["trading_log_*.json", "trading_log_*.csv", "analyst_cache.json"]
# per-company notebooks the AI-analysis runner executes (backups, Forecast and agent scratch notebooks are left out)
SKIP_NOTEBOOKS = {"Forecast.ipynb", "agent.ipynb"}
# only the importable package of the vendored TradingAgents is needed by the notebooks
TRADINGAGENTS = os.path.join("TradingAgents-main", "tradingagents")
skip = shutil.ignore_patterns("*.bak", "*.bak_*", "__pycache__")

if not os.path.exists(os.path.join(SRC, "forecast_lib.py")):
    sys.exit(f"{SRC} does not look like the RAEM project (no forecast_lib.py).")
os.makedirs(os.path.join(ENGINE, "notebooks"), exist_ok=True)
# keep the engine's own sessions (from runs on the website) and the project's before any log is overwritten
sys.path.insert(0, os.path.join(BACKEND, "app", "pipeline"))
from sessions import archive_logs  # noqa: E402
archive_logs(os.path.join(ENGINE, "notebooks"))
archive_logs(os.path.join(SRC, "notebooks"))
for f in LIBS:
    shutil.copy2(os.path.join(SRC, f), os.path.join(ENGINE, f))
# the website registers companies added from its search box (engine/raem_companies.py): put the hook back into the copied library
_ANCHOR = 'FX_PAIRS = {"USD": None, "SAR": "USDSAR=X", "HKD": "USDHKD=X", "BDT": "USDBDT=X"}\n'
_HOOK = _ANCHOR + (
    "\ntry:  # companies added on the website (backend/data/companies.json); the eight above are never replaced\n"
    "    import raem_companies as _raem_companies\n"
    "    _raem_companies.register(TICKERS, FX_PAIRS)\n"
    "except ImportError:  # the research project has no such module\n"
    "    pass\n")
_pl = os.path.join(ENGINE, "predictor_lib.py")
_txt = open(_pl, encoding="utf-8").read()
if "raem_companies" not in _txt and _ANCHOR in _txt:
    open(_pl, "w", encoding="utf-8", newline="\n").write(_txt.replace(_ANCHOR, _HOOK))
    print("predictor_lib.py: company registry hook added")
for d in DIRS:
    src = os.path.join(SRC, d)
    if os.path.isdir(src):
        shutil.copytree(src, os.path.join(ENGINE, d), ignore=skip, dirs_exist_ok=True)
n = 0
for pat in NOTEBOOK_FILES:
    for f in glob.glob(os.path.join(SRC, "notebooks", pat)):
        if ".bak" not in os.path.basename(f):
            shutil.copy2(f, os.path.join(ENGINE, "notebooks", os.path.basename(f)))
            n += 1
nb = 0
for f in glob.glob(os.path.join(SRC, "notebooks", "*.ipynb")):
    name = os.path.basename(f)
    if ".bak" not in name and name not in SKIP_NOTEBOOKS:
        shutil.copy2(f, os.path.join(ENGINE, "notebooks", name))
        nb += 1
ta = os.path.join(SRC, TRADINGAGENTS)
if os.path.isdir(ta):
    shutil.copytree(ta, os.path.join(ENGINE, TRADINGAGENTS), ignore=skip, dirs_exist_ok=True)
    lic = os.path.join(SRC, "TradingAgents-main", "LICENSE")
    if os.path.exists(lic):
        shutil.copy2(lic, os.path.join(ENGINE, "TradingAgents-main", "LICENSE"))
print(f"engine synced from {SRC}: {len(LIBS)} libraries, {len(DIRS)} folders, {n} notebook files, {nb} notebooks, TradingAgents package")
