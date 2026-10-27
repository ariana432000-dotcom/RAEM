"""
Session logger — a faithful port of the notebook's "SESSION LOGGER v2".

Each run is a session: an ordered list of steps plus a structured summary_table
that the dashboard reads. Sessions are stored per-ticker as JSON and pruned to a
rolling window (LOG_MAX_DAYS) so the backtest view stays tidy.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta
from pathlib import Path

from . import config
from .render import first_signal


class SessionLog:
    """Owns one trading session and its persistence."""

    def __init__(self, company: str, trade_date: str, asset_type: str = "stock"):
        self.company = company.upper()
        self.trade_date = trade_date
        self.asset_type = asset_type
        self.log_file: Path = config.log_file_for(self.company)
        now = datetime.now()
        self.data = {
            "session_id": now.strftime("%Y-%m-%d_%H-%M-%S"),
            "session_start": now.strftime("%Y-%m-%d %H:%M:%S"),
            "company": self.company,
            "trade_date": self.trade_date,
            "asset_type": asset_type,
            "steps": [],
            "summary_table": {},
        }

    # -- persistence helpers -------------------------------------------------
    def _load_sessions(self) -> list:
        if not self.log_file.exists():
            return []
        try:
            with open(self.log_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                return [data]
            return data if isinstance(data, list) else []
        except (json.JSONDecodeError, IOError):
            return []

    def _prune(self, sessions: list) -> list:
        cutoff = datetime.now() - timedelta(days=config.LOG_MAX_DAYS)
        kept = []
        for s in sessions:
            try:
                if datetime.strptime(s["session_start"], "%Y-%m-%d %H:%M:%S") >= cutoff:
                    kept.append(s)
            except (KeyError, ValueError):
                kept.append(s)
        return kept

    def _save(self, sessions: list) -> None:
        with open(self.log_file, "w", encoding="utf-8") as f:
            json.dump(sessions, f, indent=2, ensure_ascii=False)

    @staticmethod
    def _pretty(data) -> str:
        if isinstance(data, dict):
            return "\n".join(f"  [{k.upper()}]: {str(v)[:300]}" for k, v in data.items())
        return str(data)[:800]

    def _persist(self) -> int:
        sessions = self._prune(self._load_sessions())
        sessions = [
            s for s in sessions
            if not (s.get("company") == self.company and s.get("trade_date") == self.trade_date)
        ]
        sessions.append(self.data)
        self._save(sessions)
        return len(sessions)

    # -- public API ----------------------------------------------------------
    def log_step(self, step_name: str, input_data, output_data) -> None:
        entry = {
            "step": step_name,
            "timestamp": datetime.now().strftime("%H:%M:%S"),
            "input": self._pretty(input_data),
            "output": output_data,
        }
        self.data["steps"].append(entry)

        st = self.data["summary_table"]
        if isinstance(output_data, dict):
            if output_data.get("metrics"):
                st[step_name.lower().replace(" ", "_")] = output_data["metrics"]
            if "news_metrics" in output_data:
                st["news_analyst"] = output_data["news_metrics"]
            if "sentiment_metrics" in output_data:
                st["sentiment_analyst"] = output_data["sentiment_metrics"]
            if "indicators" in output_data:
                st["market_indicators"] = output_data["indicators"]
            if "stock_data" in output_data:
                raw_csv = str(output_data["stock_data"])
                latest_close = self._latest_close(raw_csv)
                if latest_close is not None:
                    st["market_latest_close"] = latest_close
                st["market_stock_data"] = raw_csv

        self._persist()

    @staticmethod
    def _latest_close(raw_csv: str):
        lines = [l for l in raw_csv.split("\n") if l.strip() and not l.startswith("#")]
        if len(lines) > 1:
            for row in reversed(lines[1:]):
                parts = row.split(",")
                if len(parts) >= 5 and parts[4].strip() and parts[4].strip() not in ("", "Close"):
                    try:
                        return float(parts[4].strip())
                    except ValueError:
                        pass
        return None

    def finalize(self, final_decision: str) -> None:
        self.data["session_end"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.data["final_decision"] = final_decision

        st = self.data["summary_table"]
        # 🔴 FIXED: fifth (and last found) copy of the same buggy regex --
        # see render.py::first_signal()'s comment for the full story.
        # Lower severity than the other four instances since this
        # summary_table["final_signal"] is only ever written to the
        # persisted session-log JSON file and never read back by any
        # decision-making or backtesting code -- but still worth fixing so
        # the log file itself isn't misleading if read directly later.
        st["final_signal"] = first_signal(final_decision)
        st["final_decision"] = final_decision[:600]

        tbl = re.search(
            r"(\|.*?Category.*?\|.*?(?:\n\|[-| ]+\|)(?:\n\|.*?\|)+)",
            final_decision, re.IGNORECASE | re.DOTALL,
        )
        if tbl:
            st["news_summary_markdown"] = tbl.group(1)
        self._persist()


# -- module-level readers for the dashboard ---------------------------------
def load_sessions(company: str) -> list:
    """All stored sessions for a ticker, newest last (as written)."""
    path = config.log_file_for(company)
    if not path.exists():
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return [data]
        return data if isinstance(data, list) else []
    except (json.JSONDecodeError, IOError):
        return []


def all_logged_companies() -> list:
    """Tickers that have a log file on disk."""
    out = []
    for p in config.DATA_DIR.glob("trading_log_*.json"):
        name = p.stem.replace("trading_log_", "")
        if name:
            out.append(name)
    return sorted(out)
