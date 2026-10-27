# raem_analysts/international/fact_guard.py -- shared by AAPL, IBM, 0300_HK, 2222_SR
# The company-specific values come from each notebook's RAEM_PROFILE:
# @@TICKER@@ ticker, @@FILE@@ ticker as used in file names, @@LOG@@ trading-log name, @@SLOT:key@@ lines the profile supplies.
# >>> shared code
# ================================================================
# 🛡️ FACT GUARD v2 — handoff-level hallucination check + deterministic repair on EVERY LLM call (code only, no extra model)
# ================================================================
# Why: in a real run one bad step (`Dividend Yield: N/A%` -> "0%") was copied by 8 later agents, and the end-of-chain
# verifier could not catch it. This guard checks each agent's output the moment it is produced, BEFORE the next
# agent reads it. Papers: SafeFinAgent (ICLR 2026) puts guardrails at the inter-agent communication layer;
# the Deficiency-of-LLMs-in-Finance paper shows grounding in provided data is the main lever.
#
# v2 changes (from auditing a real ABBANK run: the guard had 3 false positives and missed 6 real errors):
#   * FIXED false positives: "sma" matched inside "small"; "supporting evidence" counted as 'SMA = support'; a negated
#     sentence ("a long stop-loss cannot be placed here") was flagged; MACD -0.12 was not accepted for -0.1190.
#   * NEW checks: RSI zone label (RSI 30.0 called 'neutral'), '$' instead of BDT (৳), share counts / money amounts /
#     '% of portfolio' invented by an LLM (sizing is computed by code later), stop-loss / entry price on a SELL
#     (long-only market: SELL = exit), 'short selling', invalid [F#] citations, un-cited event claims that are not in
#     the agent's input, and 'no financial data' claims while EPS data is in the input.
#   * NEW deterministic REPAIR: a flagged sentence is simply CUT OUT (code) instead of asking the 7B model to rewrite
#     the whole answer (which often kept the error). Re-ask is only a fallback when the cut would remove too much.
#
# Checks (all in Python):
#  (1) NUMBER GROUNDING -- every specific number in the answer must also appear in what the agent was GIVEN
#      (its own prompt). Scale-insensitive (118.64B == 118,640 mn) and rounding-tolerant (-0.1190 -> -0.12).
#      Exempt: years/dates, small integers, recommendation sentences (target, confidence, score...).
#  (2) SEMANTIC RULES -- SMA support/resistance direction, N/A field cited as a number, quarterly EPS called TTM,
#      missing DSEX read as 'neutral', GNN rank called a sector rank, RSI zone.
#  (3) MARKET RULES (active when the prompt carries the [MARKET RULES] block) and (4) CITATION RULES (active when the
#      prompt carries a FACT SHEET with [F#] ids).
# It does NOT make the model correct: it catches checkable errors (numbers, labels, directions, invented levels),
# not bad reasoning. Event-claim detection is keyword based, so it is a screen, not a proof.
# Everything is logged in session_log["summary_table"]["fact_guard"] and printed by fact_guard_report().
import re, inspect
from decimal import Decimal, ROUND_HALF_UP, ROUND_DOWN, InvalidOperation

GUARD_ENABLED       = True
GUARD_RETRY         = True        # LLM re-ask is the FALLBACK when the deterministic cut is not possible
GUARD_REPAIR        = True        # cut flagged sentences in code (no extra model call)
GUARD_MAX_CUT       = 0.5         # never cut more than half of an answer; above that fall back to a re-ask
GUARD_MAX_RETRIES   = 12          # total re-asks per notebook run (cap on extra Qwen calls)
GUARD_MIN_CHARS     = 80          # shorter outputs are not checked
GUARD_EXEMPT_SENT   = re.compile(r"stop[- ]?loss|take[- ]?profit|target|position size|allocation|of (?:the |your )?portfolio|recommend|confidence|probabilit|score|rank", re.I)
GUARD_STATS = {"checked": 0, "flagged": 0, "repaired": 0, "retried": 0, "fixed": 0}
GUARD_LOG   = []                  # one dict per flagged call

USE_RELATION_GUARD = True         # v9: cut sentences that flip a code-computed relation (dividend trend, peer out/under-performance)
USE_MEMORY_CLAIM_GUARD = True     # v9: cut "past/similar episodes were profitable" claims when no stored P&L in the prompt supports them
_G_UP    = re.compile(r"\b(?:increas\w*|ris(?:ing|en|es)|rose|grow\w*|higher|improv\w*|upward|upswing|expand\w*|climb\w*)\b", re.I)
_G_DOWN  = re.compile(r"\b(?:declin\w*|fell|fall(?:s|ing|en)?|decreas\w*|lower|cut|reduc\w*|drop\w*|shrank|shrink\w*|downward|slash\w*|dwindl\w*)\b", re.I)
_G_OUT   = re.compile(r"\b(?:outperform\w*|out-perform\w*|outpac\w*|beat\w*|ahead of|stronger than|better than|superior to|outshin\w*)\b", re.I)
_G_UNDER = re.compile(r"\b(?:underperform\w*|under-perform\w*|lag(?:s|ged|ging)?\b|trail(?:s|ed|ing)\s+(?:its\s+|the\s+)?(?:sector|peers?|market|competitors?)|behind|weaker than|worse than|inferior to)", re.I)
_G_LOCAL_NEG = re.compile(r"(?:\b(?:not|no|never|without|hardly)\b|n't)[^.]{0,22}$", re.I)
_G_PAST_EP = re.compile(r"\b(?:(?:similar|past|previous|prior|earlier)\s+(?:market\s+)?(?:episodes?|conditions?|situations?|regimes?|trades?|decisions?|cases?|instances?|occasions?)"
                        r"|historical(?:ly)?\s+(?:data|performance|outcomes?|results?|episodes?|record|precedent)|history\s+(?:shows|suggests|indicates))\b", re.I)
_G_PROFIT = re.compile(r"\b(?:profitabl[ey]|profits?|gains?|successful(?:ly)?|positive\s+(?:returns?|outcomes?|results?|pnl|p&l)|worked\s+well|win(?:s|ning)?\b|rewarded)\b", re.I)
_G_NOT_MEMORY = re.compile(r"\b(?:dividends?|earnings|revenues?|eps|price|prices|share\s+price|net\s+income|margins?|valuation)\b", re.I)
_G_HORIZON = re.compile(r"\b(?:year|years|12[- ]month|annual\w*|ytd|since|long[- ]term|52[- ]week|decade|multi[- ]year)\b", re.I)


def _g_hits(rx, text):
    """matches of rx in text that are NOT locally negated ('not increasing', 'no rise')."""
    hits = []
    for m in rx.finditer(text):
        if not _G_LOCAL_NEG.search(text[max(0, m.start() - 30):m.start()]):
            hits.append(m)
    return hits


def _g_memory_supported(prompt_text: str) -> bool:
    """True if the prompt contains at least one stored POSITIVE P&L / UP outcome that could back a 'past episodes were profitable' claim."""
    for v in re.findall(r"(?:P&L|pnl)[^\d\n+\-]{0,25}([+-]?\d+(?:\.\d+)?)\s*%?", prompt_text, re.I):
        try:
            if float(v) > 0.05:
                return True
        except ValueError:
            pass
    return bool(re.search(r"outcome[^\n]{0,25}\bUP\b|\(UP\)|\bUP\)", prompt_text))


_G_NUM  = re.compile(r"(?<![\w.])(-?\d[\d,]*(?:\.\d+)?)\s*(%|bn|mn|[BbMmKk](?![A-Za-z]))?")
_G_DATE = re.compile(r"\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}/\d{2,4}|\d{1,2}:\d{2}")
_G_URL  = re.compile(r"https?://\S+")
_G_CITE = re.compile(r"\[(?:F|N|W)(\d+)\]")
# sentence splitter: not after a list number ("1."), "e.g.", "i.e.", "Tk.", "approx."
_G_SENT = re.compile(r"(?<=[.!?])(?<!\b\d\.)(?<!e\.g\.)(?<!i\.e\.)(?<!Tk\.)(?<!approx\.)\s+")
_G_NEG_ZONE = re.compile(r"\b(?:not|isn't|never|no longer|neither|nor|if|until|unless)\s+(?:\w+\s+)?(?:neutral|mid[- ]range|overbought|oversold)\b", re.I)
_G_NEG  = re.compile(r"\b(?:not|no|never|cannot|without|lack|if|until|unless|neither|nor|unable|fails?|failed|isn't|doesn't|can't|couldn't|won't|shouldn't|wouldn't|"
                     r"does not|should not|would not|could not|break(?:s|ing)?\s+(?:below|through|above))\b|n't\b", re.I)


# ── helpers ──────────────────────────────────────────────────────
def _g_sig(tok: str) -> str:
    """Significant digits of a number token (sign, commas, decimal point, leading/trailing zeros removed)."""
    d = re.sub(r"[^\d]", "", tok.replace(",", ""))
    return d.strip("0")


def _g_segments(text: str):
    """[(line_idx, piece_idx, piece)] -- the sentence-level units used BOTH for detection and for repair."""
    out = []
    for li, line in enumerate(str(text).split("\n")):
        if not line.strip():
            continue
        for pi, piece in enumerate(_G_SENT.split(line)):
            if piece.strip():
                out.append((li, pi, piece))
    return out


def _g_clean(s: str) -> str:
    return _G_URL.sub(" ", _G_DATE.sub(" ", s))


def _g_numbers(text: str):
    """[(token, segment)] for every checkable specific number in text."""
    out = []
    for seg in _g_segments(text):
        sent = _g_clean(seg[2])
        for m in _G_NUM.finditer(sent):
            tok = m.group(1).replace(",", "")
            core = tok.lstrip("-")
            has_dec, has_pct = "." in core, (m.group(2) == "%")
            if re.fullmatch(r"(19|20)\d{2}", core):
                continue                                   # years
            if not has_dec and not has_pct and len(core) < 3:
                continue                                   # list numbers / counts / '50-day'
            if _g_sig(tok) == "":
                continue                                   # 0, 0.0
            out.append((m.group(0).strip(), seg))
    return out


def _g_dec(tok: str):
    m = re.search(r"\d[\d,]*(?:\.\d+)?", tok)
    if not m:
        return None
    try:
        return Decimal(m.group(0).replace(",", ""))
    except InvalidOperation:
        return None


def _g_round_eq(full: str, short: str) -> bool:
    """Does the significant-digit string `full` round (half-up) to `short`?  e.g. 287234 -> 2872 / 2873."""
    try:
        k = len(short)
        mant = Decimal("0." + full).quantize(Decimal(1).scaleb(-k), rounding=ROUND_HALF_UP)
        return str(mant).replace("0.", "", 1).replace(".", "").strip("0") == short
    except Exception:
        return False


def _g_grounded(tok: str, input_sigs: set, input_vals=()) -> bool:
    t = _g_sig(tok)
    d0 = _g_dec(tok)
    if len(t) <= 2:
        # short digit strings ('5', '12') collide across scales (50,000 vs the '50' in '50-day SMA'): require the same VALUE
        if d0 is not None and any(abs(v) == abs(d0) for v in input_vals):
            return True
    elif t in input_sigs:
        return True
    if len(t) >= 3:                                        # rounding: 118.6 vs 118.64
        for s in input_sigs:
            if len(s) > len(t) and (s.startswith(t) or _g_round_eq(s, t)):
                return True
    # value-level rounding / truncation for decimals: -0.12 vs -0.1190, 3.3 vs 3.29
    d = _g_dec(tok)
    if d is not None and "." in str(d):
        places = len(str(d).split(".")[1])
        q = Decimal(1).scaleb(-places)
        for v in input_vals:
            try:
                if v.quantize(q, rounding=ROUND_HALF_UP) == d or v.quantize(q, rounding=ROUND_DOWN) == d:
                    return True
            except InvalidOperation:
                continue
    return False


def _g_input_numbers(prompt_text: str):
    sigs, vals = set(), []
    for m in _G_NUM.finditer(_g_clean(prompt_text)):
        s = _g_sig(m.group(1))
        if s:
            sigs.add(s)
        d = _g_dec(m.group(1))
        if d is not None:
            vals.append(d)
    return sigs, vals


def _g_issue(msg, segs=None, sub=None):
    """sub = (regex, replacement): a known-label error is CORRECTED in place (e.g. RSI 'neutral' -> 'oversold') instead of cut."""
    return {"msg": msg, "segs": list(segs) if segs else None, "sub": sub}


def _g_decision(output_text: str):
    """BUY / SELL / HOLD direction of an output, from the usual rating lines (None if unclear)."""
    t = str(output_text)
    m = (re.search(r"FINAL TRANSACTION PROPOSAL:\s*\**\s*(BUY|SELL|HOLD)", t, re.I)
         or re.search(r"\*\*(?:Action|Rating|Recommendation)\*\*:?\s*\**\s*(Buy|Overweight|Hold|Underweight|Sell)", t, re.I)
         or re.search(r"[\"']?\b(?:rating|action|recommendation)[\"']?\s*[:=]\s*[\"'*\s]*(Buy|Overweight|Hold|Underweight|Sell)\b", t, re.I))
    if not m:
        return None
    w = m.group(1).upper()
    return {"OVERWEIGHT": "BUY", "UNDERWEIGHT": "SELL"}.get(w, w)


_G_EVENT = re.compile(r"\b(?:announced?|approved?|declared?|launched?|acquir\w+|signed|merger|merged|partnership|collaborat\w+|joined hands|"
                      r"dividend (?:cut|hike|declaration)|record date|AGM|rights? issue|authori[sz]ed representatives?|regulator\w*|"
                      r"central bank|bangladesh bank|NPL|non-performing|bad loans?|penalt\w+|fined?|investigation|downgrad\w+|upgrad\w+|"
                      r"lawsuit|restructur\w+|capital injection|bailout|bankruptcy|insolven\w+|scandal|fraud|sanction\w*)\b", re.I)


def _g_vocab_overlap(sentence: str, prompt_lower: str) -> float:
    words = {w for w in re.findall(r"[a-z]{6,}", sentence.lower())}
    if not words:
        return 1.0
    return sum(1 for w in words if w in prompt_lower) / len(words)


# ── the checks ───────────────────────────────────────────────────
def guard_check(prompt_text: str, output_text: str, decision=None) -> list:
    """List of issue dicts {"msg": str, "segs": [segment,...] or None}. `segs` = the offending sentences (repairable by cutting)."""
    issues = []
    prompt_text, output_text = str(prompt_text), str(output_text)
    segs = _g_segments(output_text)
    plow = prompt_text.lower()

    # (1) number grounding
    in_sigs, in_vals = _g_input_numbers(prompt_text)
    bad, bad_segs = [], []
    for tok, seg in _g_numbers(output_text):
        if GUARD_EXEMPT_SENT.search(seg[2]):
            continue
        if not _g_grounded(tok, in_sigs, in_vals):
            if tok not in bad:
                bad.append(tok)
            if seg not in bad_segs:
                bad_segs.append(seg)
    if bad:
        issues.append(_g_issue("These numbers are NOT in the data you were given (invented or your own arithmetic): "
                               + ", ".join(bad[:8]) + ". Remove them or say 'data not available'.", bad_segs))

    # (2) semantic rules
    m = re.search(r"last close ([\d.]+) is (BELOW|ABOVE) the 50-day SMA ([\d.]+)", prompt_text)
    if m:
        px, side, sma = m.group(1), m.group(2), m.group(3)
        for seg in segs:
            s, sl = seg[2], seg[2].lower()
            if not (re.search(r"\bsma\b", sl) or "moving average" in sl):
                continue
            if _G_NEG.search(sl):
                continue                                   # negated / conditional sentences are not claims
            sma_ref = r"(?:sma|moving average)"
            # NOUN use only: 'the SMA acts as / provides support', 'support at the SMA'. 'indicators support a sell' is a verb about
            # something else and must not be touched.
            sup_noun = (re.search(sma_ref + r".{0,45}?\b(?:acts? as|serves? as|provides?|offers?|forms?|is|as)\s+(?:a\s+|an\s+|the\s+)?(?:strong\s+|key\s+|major\s+|potential\s+|dynamic\s+)*support\b", sl)
                        or re.search(r"\bsupport\s+(?:level|zone|area|line)?\s*(?:at|near|from|around|of)\s+(?:the\s+)?(?:\d[\d.]*\s*)?(?:50[- ]day\s+)?" + sma_ref, sl))
            res_noun = (re.search(sma_ref + r".{0,45}?\b(?:acts? as|serves? as|provides?|offers?|forms?|is|as)\s+(?:a\s+|an\s+|the\s+)?(?:strong\s+|key\s+|major\s+|potential\s+|overhead\s+|dynamic\s+)*resistance\b", sl))
            if side == "BELOW" and re.search(r"stop[- ]?loss", sl) and not re.search(r"\b(above|over)\b", sl):
                issues.append(_g_issue(f"Stop-loss at the 50-day SMA {sma} is ABOVE the price {px}: invalid for a long position. Sentence: \"{s.strip()[:90]}\"", [seg]))
            elif side == "BELOW" and sup_noun and "resistance" not in sl:
                issues.append(_g_issue(f"Price {px} is BELOW the 50-day SMA {sma}, so the SMA is RESISTANCE, not support. Sentence: \"{s.strip()[:90]}\"", [seg],
                                       sub=(r"\bsupport\b", "resistance")))
            elif side == "ABOVE" and res_noun and "support" not in sl:
                issues.append(_g_issue(f"Price {px} is ABOVE the 50-day SMA {sma}, so the SMA is SUPPORT, not resistance. Sentence: \"{s.strip()[:90]}\"", [seg],
                                       sub=(r"\bresistance\b", "support")))

    for lab in set(re.findall(r"^\s*[-*]?\s*([A-Za-z][A-Za-z0-9 /&()\-\.]{2,40}?):\s*(?:N/A|data not available|None)\b", prompt_text, re.M)):
        base = re.sub(r"\s*\(.*?\)\s*", " ", lab).strip()
        if len(base) < 4:
            continue
        for seg in segs:
            s = seg[2]
            if re.search(re.escape(base) + r"[^.\n]{0,25}?[:=\s]\s*[৳$]?-?\d", s, re.I) and not re.search(r"not available|n/a|unavailable|no data|none|missing", s, re.I):
                issues.append(_g_issue(f"'{base}' is N/A in your data but you cite a value: \"{s.strip()[:90]}\". Say 'data not available' instead.", [seg]))
                break

    if "NOT trailing-12-month" in prompt_text:
        for seg in segs:
            if re.search(r"\b(EPS|net income)\b[^.\n]{0,25}\b(TTM|trailing)", seg[2], re.I) and not _G_NEG.search(seg[2]):
                issues.append(_g_issue("EPS / net income cover only the most recent disclosed period (usually one quarter), not TTM. Do not call them TTM.", [seg],
                                       sub=(r"\b(?:TTM|trailing(?:[- ]twelve[- ]months?|[- ]12[- ]months?)?)\b", "latest-period")))
    if re.search(r"DSEX data (?:is )?UNAVAILABLE", prompt_text):
        for seg in segs:
            if re.search(r"(absence|lack)\s+of[^.\n]{0,80}(volatil|trend|data)[^.\n]{0,100}\bneutral|\bno\s+(?:realized\s+)?volatil[^.\n]{0,80}\bneutral|DSEX[^.\n]{0,60}\bneutral", seg[2], re.I):
                issues.append(_g_issue("DSEX data is UNAVAILABLE: that is UNKNOWN, not 'neutral'. Do not infer a market state from missing data.", [seg]))
    for seg in segs:
        if re.search(r"sector\s+(?:position|rank)[^.\n]{0,25}\d+(?:st|nd|rd|th)?\s*(?:/|out of)\s*\d+", seg[2], re.I):
            issues.append(_g_issue("The GNN rank is across ALL graph stocks, not a sector position/rank.", [seg]))

    # RSI zone: <=30 oversold, >=70 overbought, otherwise neutral (computed from the agent's own input)
    mr = re.search(r"\brsi\s*=\s*(\d+(?:\.\d+)?)|\bRSI (\d+(?:\.\d+)?) = (?:oversold|overbought|neutral)", _g_clean(prompt_text), re.I)
    rsi = float(next(g for g in mr.groups() if g)) if mr else None
    if rsi is not None and 0 <= rsi <= 100:
        for seg in segs:
            s = seg[2]
            if not re.search(r"\bRSI\b", s) or _G_NEG_ZONE.search(s):
                continue
            if rsi <= 30 and re.search(r"\b(neutral|mid[- ]range|overbought)\b", s, re.I) and not re.search(r"\b(70|75|80)\b", s):
                issues.append(_g_issue(f"RSI is {rsi:g}: that is OVERSOLD (<=30), not neutral/overbought. Sentence: \"{s.strip()[:90]}\"", [seg],
                                       sub=(r"\b(?:neutral|mid[- ]range|overbought)\b", "oversold")))
            elif rsi >= 70 and re.search(r"\b(neutral|mid[- ]range|oversold)\b", s, re.I) and not re.search(r"\b(30|25|20)\b", s):
                issues.append(_g_issue(f"RSI is {rsi:g}: that is OVERBOUGHT (>=70), not neutral/oversold. Sentence: \"{s.strip()[:90]}\"", [seg],
                                       sub=(r"\b(?:neutral|mid[- ]range|oversold)\b", "overbought")))
            elif 30 < rsi < 70 and re.search(r"\b(oversold|overbought)\b", s, re.I) and not re.search(r"\b(30|70)\b|near|approach|close to|toward", s, re.I):
                issues.append(_g_issue(f"RSI is {rsi:g}: that is in the NEUTRAL zone (30-70), not oversold/overbought. Sentence: \"{s.strip()[:90]}\"", [seg],
                                       sub=(r"\b(?:oversold|overbought)\b", "neutral")))

    # (2b) v9 -- relations computed in code (FACT SHEET) must not be flipped by the model
    if USE_RELATION_GUARD:
        md = re.search(r"cash dividend trend \(code-computed[^)]*\):[^\n]*?\b(FELL|ROSE|ABOUT FLAT)\b", prompt_text)
        if md:
            for seg in segs:
                s_, sl = seg[2], seg[2].lower()
                if not re.search(r"dividend|payout", sl):
                    continue
                up, down = _g_hits(_G_UP, sl), _g_hits(_G_DOWN, sl)
                if md.group(1) == "FELL" and up and not down:
                    issues.append(_g_issue(f"Dividend trend: the FACT SHEET (code-computed) says the cash dividend FELL, but you wrote it rose/grew. Sentence: \"{s_.strip()[:90]}\"", [seg]))
                elif md.group(1) == "ROSE" and down and not up:
                    issues.append(_g_issue(f"Dividend trend: the FACT SHEET (code-computed) says the cash dividend ROSE, but you wrote it fell. Sentence: \"{s_.strip()[:90]}\"", [seg]))
        rel = {}
        for kind in ("sector", "correlated"):
            mp = re.search(r"vs " + kind + r" peers \(20d\):[^\n]*?\b(OUTPERFORMS|UNDERPERFORMS|IN LINE)\b", prompt_text)
            if mp:
                rel[kind] = mp.group(1)
        if rel:
            for seg in segs:
                s_, sl = seg[2], seg[2].lower()
                if not re.search(r"\bpeers?\b|\bsector\b|competitors?", sl) or _G_HORIZON.search(sl):
                    continue                                   # the code fact covers a 20-day window only
                kind = "correlated" if "correlated" in sl and "correlated" in rel else ("sector" if "sector" in rel else next(iter(rel)))
                word = rel[kind]
                out_, under_ = _g_hits(_G_OUT, sl), _g_hits(_G_UNDER, sl)
                if word == "UNDERPERFORMS" and out_ and not under_:
                    issues.append(_g_issue(f"Peer comparison: the FACT SHEET (code-computed, 20d) says the stock UNDERPERFORMS its {kind} peers, but you wrote it outperforms. Sentence: \"{s_.strip()[:90]}\"", [seg]))
                elif word == "OUTPERFORMS" and under_ and not out_:
                    issues.append(_g_issue(f"Peer comparison: the FACT SHEET (code-computed, 20d) says the stock OUTPERFORMS its {kind} peers, but you wrote it underperforms. Sentence: \"{s_.strip()[:90]}\"", [seg]))

    # (2c) v9 -- claims about stored past episodes need a stored positive outcome in the prompt
    if USE_MEMORY_CLAIM_GUARD and not _g_memory_supported(prompt_text):
        for seg in segs:
            s_, sl = seg[2], seg[2].lower()
            if _G_PAST_EP.search(sl) and _g_hits(_G_PROFIT, sl) and not _G_NOT_MEMORY.search(sl) and not _G_NEG.search(sl):
                issues.append(_g_issue(f"Memory claim: no stored past episode with a positive P&L is in your data, so you cannot say past/similar cases were profitable. Sentence: \"{s_.strip()[:90]}\"", [seg]))

    # (3) market rules (long-only DSE, BDT, sizing is computed by code)
    if "[MARKET RULES]" in prompt_text:
        decision = decision or _g_decision(output_text)
        pxm = re.search(r"last close ([\d.]+)", prompt_text)
        px_val = float(pxm.group(1)) if pxm else None
        for seg in segs:
            s = seg[2]
            if re.search(r"\$\s?\d", s) and not re.search(r"\$\s?\d", prompt_text):
                issues.append(_g_issue(f"Currency is BDT (৳), never '$'. Sentence: \"{s.strip()[:90]}\"", [seg], sub=(r"\$\s?", "৳")))
            for mm in re.finditer(r"(?<![\w.])(\d[\d,]*)\s+(?:shares|lots)\b", s, re.I):
                if not _g_grounded(mm.group(1), in_sigs, in_vals):
                    issues.append(_g_issue(f"Share counts are computed by code later; do not invent '{mm.group(0)}'. Sentence: \"{s.strip()[:90]}\"", [seg]))
                    break
            else:
                mm = re.search(r"(\d+(?:\.\d+)?)\s*%\s+of\s+(?:the\s+|your\s+|our\s+)?(?:portfolio|capital|equity|account)|"
                               r"(?:invest|allocat|deploy)\w*[^.\n]{0,40}?(?:[৳$]|Tk\.?|BDT)\s*(\d[\d,]*)|"
                               r"(?:position|portfolio)\s+(?:size|sizing|weight)\b[^.\n]{0,40}?(\d+(?:\.\d+)?)\s*%", s, re.I)
                if mm:
                    num = next((g for g in mm.groups() if g), "")
                    if num and not _g_grounded(num, in_sigs, in_vals):
                        issues.append(_g_issue(f"Position size / money amounts are computed by code later; do not state '{mm.group(0)[:40]}'. Sentence: \"{s.strip()[:90]}\"", [seg]))
            if re.search(r"\bshort[- ]sell\w*|\bgo(?:ing)? short\b|\bshort position\b|\bshorting\b", s, re.I) and not _G_NEG.search(s):
                issues.append(_g_issue(f"DSE retail investors are long-only: SELL means exit an existing holding, not short. Sentence: \"{s.strip()[:90]}\"", [seg]))
            if decision == "SELL" and re.search(r"stop[- ]?loss|take[- ]?profit|entry price", s, re.I) and not _G_NEG.search(s):
                issues.append(_g_issue(f"A SELL is an exit for a long-only investor: no stop-loss / take-profit / entry price. Sentence: \"{s.strip()[:90]}\"", [seg]))
            elif decision in ("BUY", "HOLD") and px_val:
                ms = re.search(r"stop[- ]?loss[^.\n\d]{0,25}(\d+(?:\.\d+)?)", s, re.I)
                if ms and float(ms.group(1)) >= px_val:
                    issues.append(_g_issue(f"A long stop-loss must be BELOW the last close {px_val:g}; {ms.group(1)} is not. Sentence: \"{s.strip()[:90]}\"", [seg]))

    # (4) citation rules (FACT SHEET with [F#] ids)
    if re.search(r"FACT SHEET", prompt_text) and re.search(r"\[F1\]", prompt_text):
        valid = {(k, n) for k, n in re.findall(r"\[([FNW])(\d+)\]", prompt_text)}
        for seg in segs:
            s = seg[2]
            bad_ids = [f"[{k}{n}]" for k, n in re.findall(r"\[([FNW])(\d+)\]", s) if (k, n) not in valid]
            if bad_ids:
                issues.append(_g_issue(f"Citation {', '.join(bad_ids)} does not exist in the FACT SHEET. Sentence: \"{s.strip()[:90]}\"", [seg]))
                continue
            if len(s.split()) >= 7 and not _G_CITE.search(s) and _G_EVENT.search(s) and not _G_NEG.search(s) \
                    and _g_vocab_overlap(s, plow) < 0.5:
                issues.append(_g_issue(f"Event/news claim with no [F#]/[N#]/[W#] citation and not in your data: \"{s.strip()[:90]}\". Remove it.", [seg]))
        if re.search(r"Most Recent Disclosed EPS|EPS \(latest", prompt_text):
            for seg in segs:
                if re.search(r"\b(?:no|lack of|absence of|without|missing)\s+(?:any\s+)?(?:recent\s+)?(?:financial|earnings|EPS)\s+(?:reports?|data|disclosures?|information|statements?)", seg[2], re.I) \
                        and not re.search(r"detailed|balance sheet|cash ?flow|income statement|revenue|dividend", seg[2], re.I):
                    issues.append(_g_issue("Your data DOES contain the latest disclosed EPS: do not say there are no recent financial reports/data.", [seg]))
    return issues


def guard_issues(prompt_text: str, output_text: str) -> list:
    """Messages only (kept for backward compatibility)."""
    return [i["msg"] for i in guard_check(prompt_text, output_text)]


# ── deterministic repair ─────────────────────────────────────────
def guard_repair(output_text: str, issues: list):
    """Cut the flagged sentences out of `output_text` IN CODE. Returns (new_text, n_cut) or None if not possible
    (an issue without a sentence anchor, JSON output, or more than GUARD_MAX_CUT of the answer would go)."""
    text = str(output_text)
    if text.lstrip().startswith(("{", "[{")):
        return None
    drop, subs = set(), {}
    for it in issues:
        if not it.get("segs"):
            return None
        for sg in it["segs"]:
            if it.get("sub"):
                subs.setdefault((sg[0], sg[1]), []).append(it["sub"])
            else:
                drop.add((sg[0], sg[1]))
    if not drop and not subs:
        return None
    lines, kept_chars, total = text.split("\n"), 0, max(1, len(text.strip()))
    out_lines, cut_chars = [], 0
    for li, line in enumerate(lines):
        if not line.strip():
            out_lines.append(line)
            continue
        pieces = _G_SENT.split(line)
        keep = []
        for pi, piece in enumerate(pieces):
            if (li, pi) in drop:
                cut_chars += len(piece)
            else:
                for pat, repl in subs.get((li, pi), []):
                    piece = re.sub(pat, repl, piece, flags=re.I)
                keep.append(piece)
        if keep:
            out_lines.append(" ".join(keep))
    # drop headings / bare labels that lost their body
    cleaned = []
    for i, line in enumerate(out_lines):
        is_head = bool(re.match(r"\s*(?:#{1,6}\s|\*\*[^*]{2,60}\*\*:?\s*$)", line))
        if is_head:
            nxt = next((x for x in out_lines[i + 1:] if x.strip()), "")
            if not nxt or re.match(r"\s*(?:#{1,6}\s|\*\*[^*]{2,60}\*\*:?\s*$)", nxt):
                continue
        cleaned.append(line)
    new = re.sub(r"\n{3,}", "\n\n", "\n".join(cleaned)).strip("\n")
    if cut_chars / total > GUARD_MAX_CUT or len(new.strip()) < GUARD_MIN_CHARS // 2:
        return None
    return new, len(drop) + len(subs)


def _g_json_fix(prompt_text: str, text: str):
    """Structured (JSON) model output: fix each string field with the same checks, null the sizing fields (code computes them).
    Returns (new_text, [issue msgs], n_left) or None when `text` is not a JSON object."""
    import json as _json
    try:
        obj = _json.loads(text)
    except Exception:
        return None
    if not isinstance(obj, dict):
        return None
    dec, msgs, left = _g_decision(text), [], 0
    for k, v in list(obj.items()):
        if k in ("position_sizing", "position_size") and v:
            obj[k] = None
            msgs.append(f"{k} is computed by code, not by the model")
        elif k in ("stop_loss", "entry_price", "take_profit", "price_target") and dec == "SELL" and v is not None:
            obj[k] = None
            msgs.append(f"{k} removed: a SELL is an exit for a long-only investor")
        elif isinstance(v, str) and len(v) >= GUARD_MIN_CHARS // 2:
            iss = guard_check(prompt_text, v, decision=dec)
            if not iss:
                continue
            msgs += [i["msg"] for i in iss]
            rep = guard_repair(v, iss)
            if rep and len(guard_check(prompt_text, rep[0], decision=dec)) < len(iss):
                obj[k] = rep[0]
                left += len(guard_check(prompt_text, rep[0], decision=dec))
            else:
                left += len(iss)
    return _json.dumps(obj, ensure_ascii=False), msgs, left


def guard_postprocess(prompt_text: str, text: str, who: str = "postprocess") -> str:
    """Code-only check + cut on an already-rendered answer (used for the structured Trader / PM output, which is
    rendered to markdown after the model call). Never raises; returns the (possibly cleaned) text."""
    try:
        if not globals().get("GUARD_ENABLED", True) or len(str(text)) < GUARD_MIN_CHARS:
            return text
        issues = guard_check(prompt_text, text)
        if not issues:
            return text
        GUARD_STATS["checked"] += 1
        GUARD_STATS["flagged"] += 1
        rep = guard_repair(text, issues) if GUARD_REPAIR else None
        entry = {"agent": who, "issues": [i["msg"] for i in issues[:6]], "retried": False, "fixed": False, "repaired": False}
        final = text
        if rep:
            left = guard_check(prompt_text, rep[0])
            if len(left) < len(issues):
                final = rep[0]
                entry["repaired"] = True
                entry["fixed"] = not left
                entry["remaining"] = [i["msg"] for i in left[:4]]
                GUARD_STATS["repaired"] += 1
                if entry["fixed"]:
                    GUARD_STATS["fixed"] += 1
        if not entry["repaired"]:
            entry["remaining"] = entry["issues"][:4]
        GUARD_LOG.append(entry)
        print(f"   🛡️ FactGuard [{who}]: {len(issues)} issue(s) -> "
              + (f"fixed {rep[1]} sentence(s) in code ✅" if entry["fixed"] else "cut in code, some left ⚠️" if entry["repaired"] else "flagged, not cut ⚠️")
              + f" | {issues[0]['msg'][:110]}")
        _g_update_summary()
        return final
    except Exception as e:
        print(f"   ⚠️ FactGuard postprocess error ignored: {str(e)[:100]}")
        return text


def _g_update_summary():
    try:
        session_log["summary_table"]["fact_guard"] = {**GUARD_STATS, "unresolved": [{"agent": e["agent"], "issues": e.get("remaining", [])} for e in GUARD_LOG if not e["fixed"]][:8]}
    except Exception:
        pass


def _g_flat(inp) -> str:
    items = inp if isinstance(inp, (list, tuple)) and not (isinstance(inp, tuple) and len(inp) == 2 and isinstance(inp[0], str)) else [inp]
    return "\n".join(str(getattr(m, "content", m[1] if isinstance(m, tuple) else m)) for m in items)


def _g_caller() -> str:
    try:
        for fr in inspect.stack()[2:14]:
            n = fr.function
            if n.endswith("_node") or n.startswith(("run_", "llm_", "create_")) or "analyst" in n:
                return n
    except Exception:
        pass
    return "llm_call"


def _g_with_content(out, new_text):
    try:
        return out.model_copy(update={"content": new_text})
    except Exception:
        try:
            return type(out)(content=new_text)
        except Exception:
            return out


def install_fact_guard():
    from langchain_ollama import ChatOllama
    if getattr(ChatOllama, "_raem_guard_patched", False):
        return
    _prev = ChatOllama.invoke

    def _guard_invoke(self, input, *args, **kwargs):
        out = _prev(self, input, *args, **kwargs)
        if not globals().get("GUARD_ENABLED", True) or getattr(self, "format", None) == "json":
            return out
        try:
            text, ptxt = str(getattr(out, "content", out)), _g_flat(input)
            if len(text) < GUARD_MIN_CHARS or "fact-checking auditor" in ptxt or text.lstrip().startswith("SIGNAL_CONSISTENT"):
                return out
            GUARD_STATS["checked"] += 1
            if text.lstrip().startswith("{"):                          # structured output: fix field by field, never re-ask
                jf = _g_json_fix(ptxt, text)
                if jf is None or not jf[1]:
                    return out
                GUARD_STATS["flagged"] += 1
                GUARD_STATS["repaired"] += 1
                if jf[2] == 0:
                    GUARD_STATS["fixed"] += 1
                who = _g_caller()
                GUARD_LOG.append({"agent": who, "issues": jf[1][:6], "retried": False, "fixed": jf[2] == 0, "repaired": True, "remaining": jf[1][:4] if jf[2] else []})
                print(f"   🛡️ FactGuard [{who}] (structured): {len(jf[1])} issue(s) -> fixed field-by-field in code {'✅' if jf[2] == 0 else '⚠️'} | {jf[1][0][:110]}")
                _g_update_summary()
                return _g_with_content(out, jf[0])
            issues = guard_check(ptxt, text)
            if not issues:
                return out
            GUARD_STATS["flagged"] += 1
            who = _g_caller()
            entry = {"agent": who, "issues": [i["msg"] for i in issues[:6]], "retried": False, "fixed": False, "repaired": False}
            final = out
            rep = guard_repair(text, issues) if globals().get("GUARD_REPAIR", True) else None
            if rep:                                                    # 1) deterministic cut, no extra model call
                left = guard_check(ptxt, rep[0])
                if len(left) < len(issues):
                    final = _g_with_content(out, rep[0])
                    entry["repaired"], entry["fixed"] = True, not left
                    entry["remaining"] = [i["msg"] for i in left[:4]]
                    GUARD_STATS["repaired"] += 1
                    if entry["fixed"]:
                        GUARD_STATS["fixed"] += 1
            if not entry["fixed"] and globals().get("GUARD_RETRY", True) and GUARD_STATS["retried"] < GUARD_MAX_RETRIES:
                from langchain_core.messages import HumanMessage, AIMessage       # 2) fallback: ask the model once
                base = list(input) if isinstance(input, (list, tuple)) and not isinstance(input, str) else [HumanMessage(content=str(input))]
                cur_text = str(getattr(final, "content", final))
                cur_issues = [i["msg"] for i in guard_check(ptxt, cur_text)] or entry["issues"]
                fix = HumanMessage(content="FACT CHECK FAILED on your previous answer. Problems:\n- " + "\n- ".join(cur_issues[:6])
                                   + "\nRewrite the FULL answer fixing ONLY these problems. Use only the data you were given.")
                GUARD_STATS["retried"] += 1
                entry["retried"] = True
                out2 = _prev(self, base + [AIMessage(content=cur_text), fix], *args, **kwargs)
                issues2 = guard_check(ptxt, str(getattr(out2, "content", out2)))
                if len(issues2) < len(cur_issues):
                    final = out2
                    entry["fixed"] = len(issues2) == 0
                    entry["remaining"] = [i["msg"] for i in issues2[:4]]
                    if entry["fixed"]:
                        GUARD_STATS["fixed"] += 1
                elif "remaining" not in entry:
                    entry["remaining"] = entry["issues"][:4]
            elif "remaining" not in entry:
                entry["remaining"] = entry["issues"][:4]
            GUARD_LOG.append(entry)
            how = ("cut in code ✅" if entry["repaired"] and entry["fixed"] and not entry["retried"]
                   else "fixed by re-ask ✅" if entry["fixed"]
                   else "cut/re-ask, some left ⚠️" if (entry["repaired"] or entry["retried"]) and final is not out
                   else "re-asked, unchanged ⚠️" if entry["retried"] else "flagged ⚠️")
            print(f"   🛡️ FactGuard [{who}]: {len(issues)} issue(s) -> {how} | {issues[0]['msg'][:110]}")
            _g_update_summary()
            return final
        except Exception as e:                                # the guard must never break the pipeline
            print(f"   ⚠️ FactGuard internal error ignored: {str(e)[:100]}")
            return out

    ChatOllama.invoke = _guard_invoke
    ChatOllama._raem_guard_patched = True


def fact_guard_report() -> str:
    lines = [f"🛡️ FACT GUARD: {GUARD_STATS['checked']} LLM outputs checked, {GUARD_STATS['flagged']} flagged, "
             f"{GUARD_STATS['repaired']} cut in code, {GUARD_STATS['retried']} re-asked, {GUARD_STATS['fixed']} fully fixed"]
    for e in GUARD_LOG:
        if not e["fixed"]:
            lines.append(f"   ⚠️ unresolved [{e['agent']}]: " + " | ".join(i[:100] for i in e.get("remaining", e["issues"])[:2]))
    return "\n".join(lines)


install_fact_guard()
print("🛡️ Fact Guard v2 installed -- numbers must come from the agent's own input; label/direction/sizing/currency/citation errors are cut in code.")

