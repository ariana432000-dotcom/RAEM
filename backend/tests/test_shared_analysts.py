"""The analyst code is shared by the company notebooks (raem_analysts/). These tests keep that split honest."""
import ast
import glob
import json
import os
import re
import shutil
import sys

import pytest

NB_DIR = os.path.join(os.path.dirname(__file__), "..", "engine", "notebooks")
sys.path.insert(0, NB_DIR)
import raem_analysts as ra  # noqa: E402


def _notebooks():
    return [p for p in sorted(glob.glob(os.path.join(NB_DIR, "*.ipynb"))) if ".bak" not in p
            and os.path.basename(p) not in ("Forecast.ipynb", "agent.ipynb")]


def _code(path):
    nb = json.load(open(path, encoding="utf-8"))
    return [("".join(c["source"])) for c in nb["cells"] if c["cell_type"] == "code"]


def test_every_notebook_has_a_profile_and_its_company_is_found():
    sys.path[:0] = [os.path.join(os.path.dirname(__file__), "..", "app", "pipeline"), os.path.join(os.path.dirname(__file__), "..", "app", "ai_engine")]
    from sessions import notebook_map
    found = notebook_map(NB_DIR)
    for p in _notebooks():
        prof = ra.profile_of(_code(p))
        assert prof, f"{os.path.basename(p)} has no RAEM_PROFILE"
        assert found.get(prof["TICKER"]) == os.path.basename(p)


def test_shared_cells_expand_to_complete_valid_code():
    for p in _notebooks():
        src = _code(p)
        out = ra.expand_sources(src)
        assert len(out) == len(src)
        expanded = 0
        for a, b in zip(src, out):
            if a != b:
                expanded += 1
            assert not re.search(r"@@(TICKER|FILE|LOG|SLOT)", b), f"unfilled placeholder in {os.path.basename(p)}"
            ast.parse(re.sub(r"^(\s*)[%!].*$", r"\1pass", b, flags=re.M))
        assert expanded >= 40, f"{os.path.basename(p)}: only {expanded} cells use the shared analyst files"


def test_no_shared_file_is_orphaned_and_none_is_missing():
    used = {}
    for p in _notebooks():
        prof = ra.profile_of(_code(p))
        for s in _code(p):
            m = re.search(r'^use_shared\("([a-z0-9_]+)"\)$', s, re.M)
            if m:
                used.setdefault(prof["group"], set()).add(m.group(1))
                assert os.path.exists(ra.module_path(prof["group"], m.group(1))), m.group(1)
    for group, names in used.items():
        files = {os.path.basename(f)[:-3] for f in glob.glob(os.path.join(NB_DIR, "raem_analysts", group, "*.py"))}
        assert files == names, f"{group}: unused {files - names}, missing {names - files}"


def test_company_notebooks_hold_no_analyst_code_of_their_own():
    for p in _notebooks():
        big = [len(s) for s in _code(p) if len(s) > 12000]
        assert not big, f"{os.path.basename(p)} still has a {max(big)}-character cell; move it into raem_analysts/"


def test_use_shared_fills_the_profile_and_runs_in_the_notebook_namespace(tmp_path):
    import importlib.util
    pkg = tmp_path / "raem_analysts_copy"
    (pkg / "demo").mkdir(parents=True)
    shutil.copy(os.path.join(NB_DIR, "raem_analysts", "__init__.py"), pkg / "__init__.py")
    (pkg / "demo" / "unit.py").write_text(
        "# head\n# >>> shared code\n%pip install not-needed -q\nCOMPANY = \"@@TICKER@@\"\nLOGF = \"trading_log_@@LOG@@_@@FILE@@.json\"\n"
        "@@SLOT:unit.1@@\nHERE = globals().get(\"COMPANY\")\n", encoding="utf-8")
    spec = importlib.util.spec_from_file_location("raem_analysts_copy", pkg / "__init__.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)              # a private copy, so the real package stays untouched
    ns = {"RAEM_PROFILE": {"group": "demo", "TICKER": "0300.HK", "LOG": "MIDEA", "FILE": "0300_HK",
                           "slots": {"unit.1": ["PEERS = [\"a\",", "         \"b\"]"]}}}
    mod.use_shared("unit", ns)                # outside IPython the %pip line is skipped
    assert ns["COMPANY"] == "0300.HK" and ns["LOGF"] == "trading_log_MIDEA_0300_HK.json"
    assert ns["PEERS"] == ["a", "b"] and ns["HERE"] == "0300.HK"


@pytest.mark.parametrize("group,ticker,extra", [("international", "ZZTEST", []), ("dse", "ZZDSE", ["--name", "Zz Dse Ltd", "--sector", "Testing"])])
def test_new_company_makes_a_working_notebook(tmp_path, group, ticker, extra):
    from raem_analysts import new_company
    folder = tmp_path / "nb"
    shutil.copytree(NB_DIR, folder, ignore=shutil.ignore_patterns("raem_chroma_db*", "__pycache__", "trading_log*", "*.bak*"))
    assert new_company.main(["--ticker", ticker, "--group", group, "--folder", str(folder), *extra]) == 0
    made = [p for p in glob.glob(str(folder / "*.ipynb")) if ticker.lower() in os.path.basename(p).lower()]
    assert len(made) == 1
    src = _code(made[0])
    prof = ra.profile_of(src)
    assert prof["TICKER"] == ticker and prof["group"] == group
    out = ra.expand_sources(src)
    assert any(f'COMPANY    = "{ticker}"' in s or f'COMPANY = "{ticker}"' in s for s in out)
    assert not any(re.search(r"@@(TICKER|FILE|LOG|SLOT)", s) for s in out)
    nb = json.load(open(made[0], encoding="utf-8"))
    assert all(not c.get("outputs") for c in nb["cells"] if c["cell_type"] == "code")
