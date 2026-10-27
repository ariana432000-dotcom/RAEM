"""Shared analyst code for the company notebooks.

Every company notebook used to carry its own full copy of the pipeline (analysts, debate, trader, risk, verifier ...).
That code now lives here, once per market group, one file per analyst:

    raem_analysts/international/   AAPL, IBM, 0300.HK, 2222.SR  (yfinance data)
    raem_analysts/dse/             ABBANK, BATBC, BPML, WALTONHIL (Dhaka Stock Exchange data)

A notebook keeps only its COMPANY PROFILE cell (RAEM_PROFILE: ticker, log-file name and the few lines that are
specific to that company) and one `use_shared("<analyst>")` cell per analyst.  `use_shared` renders the shared file with
the notebook's profile and runs it in the notebook's own namespace, which is exactly what the cell used to do.

In a shared file, `@@TICKER@@`, `@@FILE@@` (the ticker as used in file names) and `@@LOG@@` stand for the profile values and `@@SLOT:<key>@@` for a block of lines that
the profile supplies (a peer list, a sector description).  The code starts after the `# >>> shared code` line.
"""
from __future__ import annotations

import ast
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
MARK = "# >>> shared code"
_CALL = re.compile(r'^\s*use_shared\(\s*["\']([A-Za-z0-9_]+)["\']\s*\)\s*$')


def _split(text: str) -> tuple[int, str]:
    """(number of header lines, code) of a shared file."""
    lines = text.split("\n")
    for i, line in enumerate(lines):
        if line.strip() == MARK:
            code = "\n".join(lines[i + 1:])
            return i + 1, code[:-1] if code.endswith("\n") else code  # the file adds one final newline
    return 0, text


def module_path(group: str, name: str) -> str:
    return os.path.join(HERE, group, name + ".py")


def render(group: str, name: str, profile: dict) -> tuple[int, str]:
    """(header lines, code) of shared file `name` with the profile's values filled in."""
    with open(module_path(group, name), encoding="utf-8") as fh:
        head, code = _split(fh.read())
    slots = profile.get("slots", {})
    lines = []
    for line in code.split("\n"):
        m = re.fullmatch(r"@@SLOT:([A-Za-z0-9_.]+)@@", line)
        lines.extend(slots[m.group(1)] if m else [line])        # a slot can be several lines, or none
    code = "\n".join(lines)
    file_name = profile.get("FILE") or profile["TICKER"].replace(".", "_")
    code = code.replace("@@FILE@@", file_name).replace("@@LOG@@", profile["LOG"]).replace("@@TICKER@@", profile["TICKER"])
    return head, code


def use_shared(name: str, g: dict | None = None) -> None:
    """Run the shared analyst file `name` in the notebook's namespace (what the notebook cell used to contain)."""
    import sys
    g = g if g is not None else sys._getframe(1).f_globals  # the notebook cell's own namespace
    profile = g["RAEM_PROFILE"]
    head, code = render(profile["group"], name, profile)
    # `%pip ...` lines are notebook magics; run them through IPython when it is there, else skip them
    ip = g.get("get_ipython")
    shell = ip() if callable(ip) else None
    if shell is not None and re.search(r"^\s*[%!]", code, re.M):
        code = shell.input_transformer_manager.transform_cell(code)
        head = 0
    elif re.search(r"^\s*[%!]", code, re.M):
        code = re.sub(r"^(\s*)[%!].*$", r"\1pass", code, flags=re.M)
    path = module_path(profile["group"], name)
    exec(compile("\n" * head + code, path, "exec"), g)  # blank lines keep tracebacks on the shared file's own line numbers


def profile_of(sources: list[str]) -> dict | None:
    """The RAEM_PROFILE literal of a notebook (given its code-cell sources), without running anything."""
    for src in sources:
        if "RAEM_PROFILE" not in src:
            continue
        try:
            for node in ast.parse(src).body:
                if isinstance(node, ast.Assign) and any(getattr(t, "id", "") == "RAEM_PROFILE" for t in node.targets):
                    return ast.literal_eval(node.value)
        except (SyntaxError, ValueError):
            continue
    return None


def expand_cell(src: str, profile: dict) -> str:
    """A `use_shared("x")` cell becomes the full code it stands for; any other cell is returned unchanged.
    The web runner calls this before it adapts the notebook (providers, memory store), so it sees the complete code."""
    body = [ln for ln in src.split("\n") if ln.strip() and not ln.lstrip().startswith("#")]
    if len(body) == 1:
        m = _CALL.match(body[0])
        if m:
            return render(profile["group"], m.group(1), profile)[1]
    return src


def expand_sources(sources: list[str]) -> list[str]:
    profile = profile_of(sources)
    return [expand_cell(s, profile) for s in sources] if profile else list(sources)
