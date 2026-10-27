"""Render agent markdown into safe HTML for the dashboard."""

from __future__ import annotations

import re

try:
    import markdown as _md
    _MD = _md.Markdown(extensions=["tables", "fenced_code", "sane_lists"])
except Exception:  # noqa: BLE001
    _MD = None


def md_to_html(text: str) -> str:
    if not text:
        return ""
    text = str(text)
    if _MD is not None:
        _MD.reset()
        return _MD.convert(text)
    # Minimal fallback if the markdown package isn't installed.
    return "<p>" + text.replace("\n\n", "</p><p>").replace("\n", "<br>") + "</p>"


_SIGNAL_WORD_RE = re.compile(r"\b(BUY|HOLD|SELL)\b", re.IGNORECASE)


def first_signal(text: str) -> str:
    # 🔴 FIXED: same bug as orchestrator.py's _signal() (see its comment
    # for the full story) -- the old pattern had no word boundary and
    # .search() took the first match anywhere in the text, so a word like
    # "shareholders"/"stakeholders"/"withholding" appearing before the
    # Portfolio Manager's own concluding "FINAL TRANSACTION PROPOSAL:
    # BUY/HOLD/SELL" line could get mis-read as the verdict. This is used
    # for the pipeline's OVERALL final signal (portfolio_manager stage
    # meta, decision_verifier's input, and -- via run_decision_verifier in
    # agents.py, which now reuses this same function -- the final_signal/
    # effective_signal that gets saved onto the episode for backtesting),
    # so a wrong match here was the most consequential of the three
    # instances of this bug found in the codebase.
    if not text:
        return "N/A"
    text = str(text)
    idx = text.upper().find("FINAL TRANSACTION PROPOSAL")
    if idx != -1:
        window = text[idx: idx + 150]
        m = _SIGNAL_WORD_RE.search(window)
        if m:
            return m.group(1).upper()
    m = _SIGNAL_WORD_RE.search(text)
    return m.group(1).upper() if m else "N/A"
