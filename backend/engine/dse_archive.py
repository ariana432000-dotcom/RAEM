"""Daily prices of Dhaka Stock Exchange companies, from the day-end archive of old.dsebd.org.

www.dsebd.org and dse.com.bd (which bdshare reads) answer 410 Gone; old.dsebd.org still serves the same archive page.
Used by app/pipeline/add_company.py (two years for a new company) and predictor_lib.top_up_data_csv (the new days).
"""
from __future__ import annotations

import time
from datetime import date, timedelta

import pandas as pd

URL = "https://old.dsebd.org"
CHUNK_DAYS = 180        # one archive request per half year
COLUMNS = ["date", "symbol", "ltp", "high", "low", "open", "close", "ycp", "trade", "value", "volume"]


def _get(path: str, params: dict) -> str:
    import requests
    import urllib3
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36",
               "Referer": "https://www.dsebd.org/"}
    try:
        r = requests.get(f"{URL}/{path}", params=params, headers=headers, timeout=60)
    except requests.exceptions.SSLError:  # the site does not send its intermediate certificate
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        r = requests.get(f"{URL}/{path}", params=params, headers=headers, timeout=60, verify=False)
    r.raise_for_status()
    return r.text


def _num(s: str | None) -> float | None:
    s = (s or "").replace(",", "").strip()
    try:
        return float(s)
    except ValueError:
        return None


def history(symbol: str, start: date, end: date) -> pd.DataFrame:
    """Daily prices of one trading code between start and end, indexed by date, in the DSE/bdshare column names."""
    from bs4 import BeautifulSoup
    symbol = symbol.upper()
    rows, a, end = [], pd.Timestamp(start).date(), pd.Timestamp(end).date()
    while a <= end:
        b = min(end, a + timedelta(days=CHUNK_DAYS))
        html = _get("day_end_archive.php", {"startDate": a.isoformat(), "endDate": b.isoformat(), "inst": symbol, "archive": "data"})
        table = BeautifulSoup(html, "lxml").find("table", class_="shares-table")
        if table is not None and table.find("thead") is not None:
            head = [th.get_text(strip=True).upper() for th in table.find("thead").find_all("th")]
            for tr in (table.find("tbody") or table).find_all("tr"):
                cells = [td.get_text(strip=True) for td in tr.find_all("td")]
                if len(cells) != len(head):
                    continue
                r = dict(zip(head, cells))
                if r.get("TRADING CODE", "").upper() != symbol:
                    continue
                rows.append({"date": r.get("DATE", ""), "symbol": symbol, "ltp": _num(r.get("LTP*")), "high": _num(r.get("HIGH")),
                             "low": _num(r.get("LOW")), "open": _num(r.get("OPENP*")), "close": _num(r.get("CLOSEP*")),
                             "ycp": _num(r.get("YCP")), "trade": _num(r.get("TRADE")),
                             "value": _num(r.get("VALUE (MN)") or r.get("VALUE")), "volume": _num(r.get("VOLUME"))})
        a = b + timedelta(days=1)
        if a <= end:
            time.sleep(1.0)  # be polite to the exchange's server
    if not rows:
        return pd.DataFrame(columns=COLUMNS[1:], index=pd.DatetimeIndex([], name="date"))
    df = pd.DataFrame(rows)
    df.index = pd.to_datetime(df.pop("date"), errors="coerce")
    df.index.name = "date"
    df = df[df.index.notna() & (df["close"] > 0)]
    return df[~df.index.duplicated(keep="last")].sort_index()
