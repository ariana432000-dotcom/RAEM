"""v10 Fact Guard additions: numbers bound to their label, direction claims, N/A in the FACT SHEET format. Offline, standard library only."""
from app.ai_pipeline import fact_guard as fg

PROMPT = ("HARD DATA: rsi=55.2000, macd=-1.2340, close_50_sma=233.4600, boll_ub=265.0000, boll_lb=212.0000\nFACT SHEET:\n"
          "[F1] last close 241.10 is ABOVE the 50-day SMA 233.46 (+3.3%): the SMA is potential SUPPORT below price\n"
          "[F2] RSI 55.2 = neutral zone (30-70)\n[F3] MACD -1.234 is negative (bearish momentum)\n"
          "[F4] P/E: 29.5730\n[F5] EPS (latest disclosed period, NOT trailing-12-month): 11.72\n"
          "[F6] dividend yield: data not available (this is NOT 0%)\n")
PAD = " The balance of technical and valuation evidence supports a modest position size here."


def bad(text):
    return fg.guard_check(PROMPT, text + PAD)


def test_clean_text_in_every_phrasing_is_not_flagged():
    for t in ["RSI (55.2) is neutral and MACD at -1.2340 is negative; P/E of 29.57 and EPS 11.72.",
              "RSI of 55 is neutral, MACD: -1.23 negative, P/E=30, EPS=12.",
              "Price is above the 50-day SMA of 233.46 and the trailing P/E of 29.6 is below the sector P/E of 38.0.",
              "Price is above the 50-day SMA; the RSI (14) reading is neutral.", "The dividend yield is not available."]:
        assert bad(t) == [], t


def test_a_real_number_under_the_wrong_label_is_flagged():
    assert bad("Valuation looks fine with a P/E of 11.72 and EPS of 29.57.")             # swapped, both numbers exist in the data


def test_a_wrong_two_digit_integer_is_flagged():
    assert bad("Momentum is oversold with RSI of 25.")


def test_sentences_with_recommend_or_target_are_no_longer_a_loophole():
    assert bad("We recommend holding because the P/E is 38.")


def test_sma_direction_and_macd_sign_are_checked_against_the_code_facts():
    assert bad("The latest close sits below the 50-day SMA of 233.46.")
    assert bad("Trend momentum is positive, based on MACD -1.2340.")
    assert bad("The latest close sits above the 50-day SMA of 233.46.") == []


def test_na_dividend_cited_as_zero_is_flagged_in_the_fact_sheet_format():
    assert bad("The dividend yield is 0% so income is limited.")


def test_label_checks_have_an_off_switch(monkeypatch):
    swapped = "Valuation looks fine with a P/E of 11.72 and EPS of 29.57."      # only the label-bound check can see this
    assert bad(swapped)
    monkeypatch.setenv("RAEM_GUARD_LABELS", "0")
    assert bad(swapped) == []
