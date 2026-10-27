# raem_analysts/dse/dse_endpoint_probe.py -- shared by abbank-dse, batbc-dse, bpml-dse, waltonhil-dse
# The company-specific values come from each notebook's RAEM_PROFILE:
# @@TICKER@@ ticker, @@FILE@@ ticker as used in file names, @@LOG@@ trading-log name, @@SLOT:key@@ lines the profile supplies.
# >>> shared code
# ================================================================
# 🩺 DSE ENDPOINT PROBE — run this when fundamentals/news fail with HTTP 410/404
# ================================================================
# Prints, per endpoint: HTTP status, size, redirect target and whether the page really contains the
# ticker. If the day-end archive (prices) answers 200 but displayCompany.php / old_news.php answer
# 410, DSE has retired those pages for scripted clients -> send this output (and the URL your
# browser shows for the @@TICKER@@ company page) so the scraper can be pointed at the new location.
# Set RUN_DSE_PROBE = False once things work (it makes ~8 requests).
RUN_DSE_PROBE = False   # set True to re-run the probe if DSE starts returning 410/404 again

def dse_probe(symbol: str = "@@TICKER@@"):
    import datetime as _dt
    today = _dt.date.today()
    start, end = (today - _dt.timedelta(days=7)).isoformat(), today.isoformat()
    tests = [
        ("company page  old.dsebd.org", "https://old.dsebd.org/displayCompany.php", {"name": symbol}),
        ("company page  https://www", "https://www.dsebd.org/displayCompany.php", {"name": symbol}),
        ("company page  http://www", "http://www.dsebd.org/displayCompany.php", {"name": symbol}),
        ("company page  https://(no www)", "https://dsebd.org/displayCompany.php", {"name": symbol}),
        ("news archive", "https://www.dsebd.org/old_news.php",
         {"startDate": start, "endDate": end, "inst": symbol, "criteria": 4, "archive": "data"}),
        ("day-end archive (prices)", "https://www.dsebd.org/day_end_archive.php",
         {"startDate": start, "endDate": end, "inst": symbol, "archive": "data"}),
        ("live prices", "https://www.dsebd.org/latest_share_price_scroll_l.php", None),
        ("homepage www.dsebd.org", "https://www.dsebd.org/", None),
        ("homepage www.dse.com.bd", "https://www.dse.com.bd/", None),
    ]
    print(f"{'endpoint':34s} {'HTTP':>5s} {'bytes':>8s}  has-{symbol}  note")
    for name, url, params in tests:
        s = requests.Session(); s.headers.update(DSE_HEADERS)
        try:
            r = s.get(url, params=params, timeout=15, verify=_dse_ssl_verify[0], allow_redirects=False)
            body = r.text or ""
            note = f"-> {r.headers.get('Location')}" if r.is_redirect else ""
            if r.status_code >= 400:
                note = (note + " body: " + " ".join(body.split())[:140]).strip()
            print(f"{name:34s} {r.status_code:>5d} {len(r.content):>8d}  {str(symbol in body):9s} {note}")
        except Exception as e:
            print(f"{name:34s} {'ERR':>5s} {'-':>8s}  {'-':9s} {str(e)[:120]}")
        _dse_time.sleep(1.0)

if RUN_DSE_PROBE:
    dse_probe("@@TICKER@@")
