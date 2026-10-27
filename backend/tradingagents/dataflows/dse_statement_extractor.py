"""
Full financial-statement extraction for DSE-listed companies.

Unlike US tickers (Alpha Vantage / yfinance), there is no single API or
scrape target that returns a clean, structured balance sheet / income
statement / cash flow for a Bangladeshi listed company -- DSE and BSEC
both only publish these as audited-annual-report PDFs, and the PDF layout
differs company to company (and often year to year for the same company).

This module:
  1. Reads a locally-stored annual report PDF for the ticker + fiscal year.
     You (or a small per-company downloader you write later) put these on
     disk yourself -- see REPORTS_DIR below. DSE's own site disallows
     automated crawling in robots.txt, so treat dsebd.org's "Financial
     Statements" tab and company investor-relations pages as
     browse-and-download-by-hand sources rather than scrape targets.
  2. Extracts the statement pages with pdfplumber (layout-aware, better
     for tables than plain text extraction).
  3. Sends that text to an LLM and asks for strict JSON matching an
     Alpha-Vantage-shaped schema, so fundamentals_analyst.py and the rest
     of the pipeline don't need to change to consume it.
  4. Caches the structured result to disk keyed by ticker + fiscal year,
     since PDF parsing + the LLM call are the expensive part and a given
     annual report never changes once published.

Expected PDF layout on disk:
    data/dse_reports/<TICKER>/<FISCAL_YEAR>.pdf
    e.g. data/dse_reports/SQURPHARMA/2025.pdf

Notes on the numbers you'll get back:
  - BD companies commonly use a July-to-June fiscal year (per BSEC
    directive), and P/E / EPS figures on DSE itself are calculated on
    that convention -- don't assume calendar-year alignment when you
    compare fiscalDateEnding across tickers.
  - Figures in the source PDF may be reported in BDT thousands, lakhs, or
    crores depending on the company. The extraction prompt asks the model
    to normalize to plain BDT and note the original unit, but spot-check
    a few results against the PDF before trusting them at scale.
"""

import json
import logging
import os
import re
from pathlib import Path

import pdfplumber

logger = logging.getLogger(__name__)

REPORTS_DIR = Path(os.environ.get("DSE_REPORTS_DIR", "data/dse_reports"))
CACHE_DIR = Path(os.environ.get("DSE_REPORTS_CACHE_DIR", "data/dse_reports_cache"))
CACHE_DIR.mkdir(parents=True, exist_ok=True)

# Point this at whatever Claude model your backend is already configured
# to call (you're on Claude Sonnet via RunPod per your existing setup).
ANTHROPIC_MODEL = os.environ.get("DSE_EXTRACTOR_MODEL", "claude-sonnet-4-6")

STATEMENT_SCHEMAS = {
    "balance_sheet": [
        "fiscalDateEnding", "reportedUnit", "totalAssets", "totalCurrentAssets",
        "totalLiabilities", "totalCurrentLiabilities", "totalShareholderEquity",
        "cashAndCashEquivalents", "longTermDebt", "shortTermDebt",
        "inventory", "netReceivables",
    ],
    "income_statement": [
        "fiscalDateEnding", "reportedUnit", "totalRevenue", "costOfRevenue",
        "grossProfit", "operatingExpenses", "operatingIncome", "netIncome",
        "ebit", "ebitda", "incomeTaxExpense", "interestExpense", "eps",
    ],
    "cashflow": [
        "fiscalDateEnding", "reportedUnit", "operatingCashflow",
        "cashflowFromInvestment", "cashflowFromFinancing",
        "capitalExpenditures", "dividendPayout",
        "changeInCashAndCashEquivalents", "netIncome",
    ],
}

STATEMENT_KEYWORDS = re.compile(
    r"balance sheet|statement of financial position|"
    r"income statement|profit and loss|statement of profit|"
    r"statement of cash flow",
    re.IGNORECASE,
)


def _cache_path(ticker: str) -> Path:
    return CACHE_DIR / f"{ticker}.json"


def _load_cache(ticker: str) -> dict:
    path = _cache_path(ticker)
    return json.loads(path.read_text()) if path.exists() else {}


def _save_cache(ticker: str, cache: dict) -> None:
    _cache_path(ticker).write_text(json.dumps(cache, indent=2))


def _extract_pdf_text(pdf_path: Path) -> str:
    """Pulls out just the statement pages to keep the LLM prompt small;
    falls back to the whole document if the keyword filter finds nothing
    (some annual reports use unusual section headers)."""
    with pdfplumber.open(pdf_path) as pdf:
        matched = [
            page.extract_text() or ""
            for page in pdf.pages
            if STATEMENT_KEYWORDS.search(page.extract_text() or "")
        ]
        if matched:
            return "\n\n".join(matched)
        return "\n\n".join(page.extract_text() or "" for page in pdf.pages)


def _extract_with_llm(statement_text: str, fiscal_year: str) -> dict:
    """Requires ANTHROPIC_API_KEY in the environment."""
    import anthropic

    client = anthropic.Anthropic()
    schema_hint = json.dumps(STATEMENT_SCHEMAS, indent=2)

    prompt = f"""You are extracting structured financial data from a Bangladeshi
company's financial statement (label used for this file: {fiscal_year}).
This may be an audited ANNUAL report or an UN-AUDITED QUARTERLY/interim
report -- read the document's own text (e.g. "for the three-month period
ended...", "Un-audited interim condensed financial statements", "Audited
Financial Statements") to determine which, and record the ACTUAL period
end date you find in "fiscalDateEnding" (do not assume it matches
{fiscal_year}). Figures may be reported in BDT thousands, lakhs, or
crores -- normalize every number to plain BDT and record the original
unit you saw in "reportedUnit".

Return ONLY a JSON object with exactly these three top-level keys:
"balance_sheet", "income_statement", "cashflow". Each value is a single
object (not a list) with these fields -- use null for anything not present
in the text, never guess or estimate a figure that isn't stated:

{schema_hint}

Annual report text:
---
{statement_text[:120000]}
---

Respond with JSON only. No markdown code fences, no commentary before or
after the JSON."""

    response = client.messages.create(
        model=ANTHROPIC_MODEL,
        max_tokens=2000,
        messages=[{"role": "user", "content": prompt}],
    )
    raw = response.content[0].text.strip()
    raw = re.sub(r"^```(?:json)?|```$", "", raw, flags=re.MULTILINE).strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        logger.error("LLM did not return valid JSON for fiscal year %s", fiscal_year)
        return {}


def _ensure_extracted(ticker: str) -> dict:
    """Extracts every PDF for `ticker` that isn't already cached."""
    cache = _load_cache(ticker)
    ticker_dir = REPORTS_DIR / ticker
    if not ticker_dir.exists():
        logger.warning(
            "No reports found for %s at %s -- download the annual report "
            "PDF there first (see module docstring).", ticker, ticker_dir,
        )
        return cache

    for pdf_path in sorted(ticker_dir.glob("*.pdf")):
        fiscal_year = pdf_path.stem
        if fiscal_year in cache:
            continue
        logger.info("Extracting %s FY%s from %s", ticker, fiscal_year, pdf_path)
        text = _extract_pdf_text(pdf_path)
        result = _extract_with_llm(text, fiscal_year)
        if result:
            cache[fiscal_year] = result
            _save_cache(ticker, cache)

    return cache


def get_statement(ticker: str, statement_type: str, freq: str = "annual", curr_date: str = None) -> dict:
    """
    Returns Alpha-Vantage-shaped statement data:
        {"annualReports": [ {fiscalDateEnding, ...}, ... ]}

    statement_type: "balance_sheet" | "income_statement" | "cashflow"
    """
    if statement_type not in STATEMENT_SCHEMAS:
        raise ValueError(f"Unknown statement_type: {statement_type}")

    cache = _ensure_extracted(ticker)
    reports = []
    for fiscal_year, extracted in sorted(cache.items()):
        entry = extracted.get(statement_type)
        if not entry:
            continue
        fiscal_date = entry.get("fiscalDateEnding") or f"{fiscal_year}-06-30"
        if curr_date and fiscal_date > curr_date:
            continue  # avoid look-ahead bias -- same convention as alpha_vantage_fundamentals.py
        reports.append(entry)

    key = "annualReports" if freq == "annual" else "quarterlyReports"
    return {key: reports}
