"""The session an AI analysis has written so far, read from the notebook's own trading log while it runs."""
from __future__ import annotations

import sys

from app.core import config


def live_entry(ticker: str, started_at: str):
    """The log entry since `started_at` (the notebook saves its log after every agent), or None while it is being written."""
    try:
        if str(config.PIPELINE) not in sys.path:
            sys.path.insert(0, str(config.PIPELINE))
        from sessions import ai_entry
        return ai_entry(str(config.ENGINE / "notebooks"), ticker, since=started_at[:16])
    except Exception:  # the notebook may be writing its log right now; the page asks again
        return None
