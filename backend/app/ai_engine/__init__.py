"""The AI side of the server: the notebook runner (run_notebook.py) that executes a company's notebook headlessly.

The analyst code itself is shared by the notebooks and lives beside them, one file per analyst:
    engine/notebooks/raem_analysts/international/   AAPL, IBM, 0300.HK, 2222.SR
    engine/notebooks/raem_analysts/dse/             ABBANK, BATBC, BPML, WALTONHIL
It stays there so the notebooks also run on a PC from the research folder (predict/notebooks/).
"""
from pathlib import Path

from app.core import config

ANALYSTS_DIR = config.ENGINE / "notebooks" / "raem_analysts"


def analyst_files(group: str) -> list[str]:
    """Names of the shared analyst files of a market group ('international' or 'dse')."""
    return sorted(p.stem for p in (ANALYSTS_DIR / group).glob("*.py") if p.stem != "__init__")
