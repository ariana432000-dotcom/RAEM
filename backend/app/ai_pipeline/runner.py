"""
The orchestrator. Runs the full RAEM pipeline in the same order as the notebook
and yields a stream of events the web layer turns into Server-Sent Events.

Two paths share one event protocol:
  - real mode: drives the ported agents against Claude Sonnet 5 (or another
    RAEM_LLM_PROVIDER) + TradingAgents (identity/schemas) + FMP/Finnhub
    (.data_providers) + Chroma
  - demo mode: emits realistic simulated output with pauses, no dependencies

Event shape (all dicts):
  {"type": "stage_start", "stage": id, "label": str, "index": i, "total": n}
  {"type": "log",         "stage": id, "line": str}
  {"type": "debate_turn", "stage": id, "speaker": str, "side": str, "html": str, "round": r}
  {"type": "stage_done",  "stage": id, "html": str, "meta": {...}}
  {"type": "regime",      "regime": str, "reflection": str, "episodes": [...]}
  {"type": "summary",     "summary": {...}}
  {"type": "final",       "signal": str, "html": str, "session_id": str}
  {"type": "error",       "stage": id, "message": str}
  {"type": "done"}
"""

from __future__ import annotations

import time
import traceback
from datetime import datetime

from . import config, memory as mem
from .logger import SessionLog
from .render import md_to_html, first_signal


STAGES = [
    ("outcome_backfill", "Outcome Backfill"),
    ("fundamentals", "Fundamentals Analyst"),
    ("market", "Market Analyst"),
    ("news", "News Analyst"),
    ("sentiment", "Sentiment Analyst"),
    ("macro_regime", "Macro Regime Analyst"),
    ("investment_debate", "Bull vs Bear Debate"),
    ("investment_facilitator", "Investment Facilitator"),
    ("memory", "Episodic Memory (RAEM)"),
    ("post_mortem", "Post-Mortem Review"),
    ("trader", "Trader Proposal"),
    ("risk_debate", "Risk Debate"),
    ("risk_facilitator", "Risk Facilitator"),
    ("portfolio_manager", "Portfolio Manager"),
    ("decision_verifier", "Decision Verifier"),
    ("save_episode", "Save Episode"),
]


class PipelineRunner:
    def __init__(self, company, trade_date, asset_type="stock",
                 investment_rounds=None, risk_rounds=None, investor_profile=None,
                 provider_override: str | None = None):
        self.company = company.upper()
        self.trade_date = trade_date
        self.asset_type = asset_type
        self.investment_rounds = investment_rounds or config.DEFAULT_INVESTMENT_ROUNDS
        self.risk_rounds = risk_rounds or config.DEFAULT_RISK_ROUNDS
        self.investor_profile = investor_profile or config.DEFAULT_INVESTOR_PROFILE
        # 🔴 FIXED: previously dropped on the floor -- see the comment on
        # llm.py::_provider() for the full story. Lets a per-request
        # ExecutionRequest.provider actually pick the LLM for this run
        # instead of only being written into display metadata.
        self.provider_override = provider_override
        self.session = SessionLog(self.company, self.trade_date, asset_type)
        self.state = {
            "company_of_interest": self.company,
            "trade_date": self.trade_date,
            "asset_type": asset_type,
            "messages": [],
        }

    # -- event helpers -------------------------------------------------------
    def _start(self, stage, idx):
        label = dict(STAGES)[stage]
        return {"type": "stage_start", "stage": stage, "label": label,
                "index": idx, "total": len(STAGES)}

    def run(self):
        """Yield the full event stream for one pipeline run."""
        try:
            if config.DEMO_MODE:
                yield from self._run_demo()
            else:
                yield from self._run_real()
        except Exception as e:  # noqa: BLE001
            yield {"type": "error", "stage": "pipeline",
                   "message": f"{e}\n{traceback.format_exc()}"}
        yield {"type": "done", "session_id": self.session.data["session_id"]}

    # ======================================================================
    # REAL MODE
    # ======================================================================
    def _run_real(self):
        if not config.ensure_tradingagents_on_path():
            yield {"type": "error", "stage": "pipeline",
                   "message": (f"Couldn't find the tradingagents package. Set TRADINGAGENTS_PATH "
                               f"to the folder that contains 'tradingagents/'. Tried: "
                               f"{config.TRADINGAGENTS_PATH}")}
            return

        from . import agents
        from .llm import make_llm, provider_label

        llm = make_llm(provider_override=self.provider_override)
        self._llm_provider = provider_label(self.provider_override)
        memory = mem.RAEMMemory()
        idx = 0

        def logline(stage):
            return lambda line: None  # replaced per-stage below

        # -- outcome backfill --
        idx += 1
        yield self._start("outcome_backfill", idx)
        backfill_input = {"company": self.company, "trade_date": self.trade_date,
                           "purpose": "resolve older PENDING episodes with today's close price"}
        try:
            res = memory.backfill_pending_outcomes(self.company, self.trade_date)
            html = self._backfill_html(res)
            yield {"type": "stage_done", "stage": "outcome_backfill", "html": html, "meta": res,
                   "input": backfill_input}
        except Exception as e:  # noqa: BLE001
            yield {"type": "stage_done", "stage": "outcome_backfill",
                   "html": f"<p class='muted'>Backfill skipped: {e}</p>", "meta": {},
                   "input": backfill_input}

        # -- fundamentals --
        idx += 1
        yield self._start("fundamentals", idx)
        logs = []
        node = agents.create_fundamentals_analyst(llm, log=logs.append)
        fundamentals_input = {"company": self.company, "date": self.trade_date,
                               "tools_used": "get_fundamentals, get_balance_sheet, get_cashflow, get_income_statement"}
        out = node(self.state)
        self.state.update(out)
        for ln in logs:
            yield {"type": "log", "stage": "fundamentals", "line": ln}
        self.session.log_step("Fundamentals Analyst", fundamentals_input,
                              {"metrics": out.get("fund_metrics", {}), "report": out.get("fundamentals_report", "")})
        yield {"type": "stage_done", "stage": "fundamentals",
               "html": md_to_html(out["fundamentals_report"]),
               "meta": {"metrics": out.get("fund_metrics", {}),
                        "confidence": out.get("fundamentals_confidence", 0.0)},
               "input": fundamentals_input}

        # -- market --
        idx += 1
        yield self._start("market", idx)
        logs = []
        node = agents.create_market_analyst(llm, config.MARKET_INDICATORS, log=logs.append)
        market_input = {"company": self.company, "date": self.trade_date,
                         "tools_used": "get_stock_data, get_indicators",
                         "indicators_requested": config.MARKET_INDICATORS}
        out = node(self.state)
        self.state.update(out)
        for ln in logs:
            yield {"type": "log", "stage": "market", "line": ln}
        indicators = out.get("indicators_parsed", {})
        self.session.log_step("Market Analyst", market_input,
                              {"indicators": indicators,
                               "stock_data": out.get("market_raw_data", {}).get("stock_data", ""),
                               "report": out.get("market_report", "")})
        yield {"type": "stage_done", "stage": "market",
               "html": md_to_html(out["market_report"]),
               "meta": {"indicators": indicators, "regime": mem.classify_regime(indicators),
                        "confidence": out.get("market_confidence", 0.0)},
               "input": market_input}

        # -- news (+ RAG ingest) --
        idx += 1
        yield self._start("news", idx)
        logs = []
        node = agents.create_news_analyst(llm, log=logs.append)
        news_input = {"company": self.company, "date": self.trade_date,
                      "tools_used": "get_news, get_global_news"}
        out = node(self.state)
        self.state.update(out)
        for ln in logs:
            yield {"type": "log", "stage": "news", "line": ln}
        try:
            n_chunks = memory.ingest_news(self.company, self.trade_date,
                                          out.get("news_raw", {}).get("company_news", ""),
                                          out.get("news_raw", {}).get("global_news", ""))
            yield {"type": "log", "stage": "news", "line": f"ingested {n_chunks} news chunks into RAG corpus"}
        except Exception as e:  # noqa: BLE001
            yield {"type": "log", "stage": "news", "line": f"news RAG ingest skipped: {e}"}
        self.session.log_step("News Analyst", news_input,
                              {"news_metrics": out.get("news_metrics", {}), "report": out.get("news_report", "")})
        yield {"type": "stage_done", "stage": "news",
               "html": md_to_html(out["news_report"]),
               "meta": {"news_metrics": out.get("news_metrics", {}),
                        "confidence": out.get("news_confidence", 0.0)},
               "input": news_input}

        # -- sentiment --
        idx += 1
        yield self._start("sentiment", idx)
        logs = []
        node = agents.create_sentiment_analyst(llm, log=logs.append)
        sentiment_input = {"company": self.company, "date": self.trade_date,
                            "source": "News Analyst's per-headline sentiment table"}
        out = node(self.state)
        self.state.update(out)
        for ln in logs:
            yield {"type": "log", "stage": "sentiment", "line": ln}
        self.session.log_step("Sentiment Analyst", sentiment_input,
                              {"sentiment_metrics": out.get("sentiment_metrics", {}), "report": out.get("sentiment_report", "")})
        yield {"type": "stage_done", "stage": "sentiment",
               "html": md_to_html(out["sentiment_report"]),
               "meta": {"sentiment_metrics": out.get("sentiment_metrics", {}),
                        "confidence": out.get("sentiment_confidence", 0.0)},
               "input": sentiment_input}

        # -- macro regime (market-wide risk regime, independent of the stock-specific regime) --
        idx += 1
        yield self._start("macro_regime", idx)
        logs = []
        node = agents.create_macro_regime_analyst(llm, log=logs.append)
        macro_input = {"date": self.trade_date, "tickers": "^VIX, ^TNX, DX-Y.NYB"}
        out = node(self.state)
        self.state.update(out)
        for ln in logs:
            yield {"type": "log", "stage": "macro_regime", "line": ln}
        self.session.log_step("Macro Regime Analyst", macro_input,
                              {"macro_regime": out.get("macro_regime", ""), "report": out.get("macro_report", "")})
        yield {"type": "stage_done", "stage": "macro_regime",
               "html": md_to_html(out["macro_report"]),
               "meta": {"macro_regime": out.get("macro_regime", ""),
                        "macro_snapshot": out.get("macro_snapshot", {})},
               "input": macro_input}

        # -- investment debate --
        idx += 1
        yield self._start("investment_debate", idx)
        self.state["investment_debate_state"] = {
            "history": "", "bull_history": "", "bear_history": "",
            "current_response": "", "count": 0,
        }
        bull = agents.create_bull_researcher(llm)
        bear = agents.create_bear_researcher(llm)
        for r in range(1, self.investment_rounds + 1):
            # 🔴 FIXED: Bull spoke first and Bear spoke second in every
            # single round -- confirmed (across many tickers) to
            # correlate with Bear winning the debate almost every time,
            # regardless of the stock's actual fundamentals. This isn't
            # just the judge's recency bias (already addressed via the
            # Investment Facilitator's "structural note" prompt) -- it's
            # a real informational asymmetry: whoever speaks second gets
            # to read and specifically target the other side's actual
            # argument, which the first speaker never gets to do for that
            # round. Alternating who leads (odd rounds: Bull first, even
            # rounds: Bear first) means each side gets that second-mover
            # advantage roughly equally often across a multi-round debate
            # instead of one side having it in literally every round.
            # Requires create_bull_researcher/create_bear_researcher to
            # read the opponent's last turn by name (bear_history/
            # bull_history) rather than the old shared current_response
            # field, which only meant the right thing under the old fixed
            # order -- see agents.py's version of this fix.
            bull_first = (r % 2 == 1)
            speakers = [(bull, "Bull Analyst", "bull", "bull_history"),
                        (bear, "Bear Analyst", "bear", "bear_history")]
            if not bull_first:
                speakers = list(reversed(speakers))
            for fn, name, side, hist_key in speakers:
                out = fn(self.state); self.state.update(out)
                yield {"type": "debate_turn", "stage": "investment_debate", "speaker": name,
                       "side": side, "round": r,
                       "html": md_to_html(_last_turn(out["investment_debate_state"][hist_key]))}
        final_debate = self.state["investment_debate_state"]
        debate_input = {
            "fundamentals_report": str(self.state.get("fundamentals_report", ""))[:300],
            "market_report": str(self.state.get("market_report", ""))[:300],
            "news_report": str(self.state.get("news_report", ""))[:300],
            "sentiment_report": str(self.state.get("sentiment_report", ""))[:300],
            "rounds": self.investment_rounds,
        }
        self.session.log_step("Investment Debate (Bull vs Bear)", debate_input, final_debate["history"][:1500])
        yield {"type": "stage_done", "stage": "investment_debate",
               "html": md_to_html(final_debate["history"][:3000]),
               "meta": {"count": final_debate["count"]},
               "input": debate_input}

        # -- investment facilitator --
        idx += 1
        yield self._start("investment_facilitator", idx)
        facilitator_input = {
            "debate_history": final_debate["history"][:500],
            "bull_arguments": final_debate.get("bull_history", "")[:300],
            "bear_arguments": final_debate.get("bear_history", "")[:300],
        }
        out = agents.create_investment_facilitator(llm)(self.state)
        self.state.update(out)
        self.session.log_step("Investment Debate Facilitator", facilitator_input, out["investment_facilitator_decision"])
        yield {"type": "stage_done", "stage": "investment_facilitator",
               "html": md_to_html(out["investment_facilitator_decision"]), "meta": {},
               "input": facilitator_input}

        # -- memory: regime reflection + retrieval --
        idx += 1
        yield self._start("memory", idx)
        today_regime = mem.classify_regime(indicators)
        reflection = ""
        prev_regime, prev_date = memory.most_recent_regime(self.company, exclude_date=self.trade_date)
        if prev_regime and prev_regime != today_regime:
            yield {"type": "log", "stage": "memory", "line": f"regime shift {prev_regime} -> {today_regime}, reflecting"}
            reflection = memory.reflect_on_regime_transition(self.company, prev_regime, today_regime, llm)
        episodes = memory.retrieve_similar_episodes(
            f"{self.company} trading decision in {today_regime} market regime",
            company=self.company, regime_filter=today_regime, n_results=3)
        memory_context = _memory_context(today_regime, episodes, reflection)
        macro_regime = self.state.get("macro_regime", "")
        if macro_regime:
            memory_context = (memory_context or "") + (
                f"\n\n**Macro Regime (market-wide):** {macro_regime}\n"
                f"{str(self.state.get('macro_report', ''))[:800]}"
            )
        memory_input = {
            "company": self.company, "today_regime": today_regime,
            "query": f"{self.company} trading decision in {today_regime} market regime",
            "n_results": 3,
            "prev_regime": prev_regime or "(none — first session)",
        }
        yield {"type": "regime", "regime": today_regime, "reflection": reflection,
               "episodes": [_ep_view(e) for e in episodes]}
        yield {"type": "stage_done", "stage": "memory",
               "html": md_to_html(memory_context or "_No prior episodes in this regime yet._"),
               "meta": {"regime": today_regime, "n_episodes": len(episodes)},
               "input": memory_input}

        # -- post-mortem / self-critique (cross-regime track record, every session) --
        idx += 1
        yield self._start("post_mortem", idx)
        post_mortem_input = {"company": self.company, "scope": "all RESOLVED episodes, cross-regime"}
        logs = []
        node = agents.create_post_mortem_agent(llm, memory, log=logs.append)
        out = node(self.state)
        self.state.update(out)
        post_mortem_lessons = out.get("post_mortem_lessons", "")
        for ln in logs:
            yield {"type": "log", "stage": "post_mortem", "line": ln}
        self.session.log_step("Post-Mortem Review", post_mortem_input, post_mortem_lessons)
        yield {"type": "stage_done", "stage": "post_mortem",
               "html": md_to_html(post_mortem_lessons),
               "meta": {"episodes_reviewed": out.get("post_mortem_n_episodes", 0)},
               "input": post_mortem_input}

        # -- trader --
        idx += 1
        yield self._start("trader", idx)
        # 🔴 FIXED: this used to be just `final_debate["history"][:2000] +
        # memory_context` -- the raw, unweighted Bull vs Bear back-and-forth,
        # with NO mention of what the Investment Facilitator actually
        # decided. The Facilitator's entire job (see its card: "Declares a
        # debate winner + a BUY/SELL/HOLD recommendation") was computed
        # just above and stored in self.state["investment_facilitator_decision"]
        # -- but that verdict was never threaded into investment_plan, so
        # the Trader re-litigated the raw transcript from scratch and could
        # (and did, in practice) land on a recommendation that flatly
        # contradicts the Facilitator's official one -- e.g. Facilitator
        # says "BEAR WINS -- SELL" while the Trader, never having seen that,
        # independently proposes BUY. `final_debate` also can't fix this by
        # itself: it's a reference captured *before* the facilitator ran,
        # so its dict doesn't carry the "facilitator_decision" key even
        # though the facilitator node technically writes one into a fresh
        # copy of investment_debate_state -- self.state["investment_facilitator_decision"]
        # (the plain top-level key, freshly set via self.state.update(out)
        # right above) is the one guaranteed to be current.
        facilitator_decision = self.state.get("investment_facilitator_decision", "")
        # Just the two *final* positions (post-rebuttal) rather than the
        # full multi-round transcript -- final_debate["history"] repeats
        # every round from both sides, most of which gets superseded by
        # each side's own later rebuttal anyway. bull_history/bear_history
        # accumulate per-side across rounds (see create_bull_researcher /
        # create_bear_researcher above), so _last_turn() on each pulls out
        # exactly the last, most-refined argument from that side.
        last_bull = _last_turn(final_debate.get("bull_history", ""))
        last_bear = _last_turn(final_debate.get("bear_history", ""))
        self.state["investment_plan"] = (
            "**Investment Facilitator's Verdict** (the synthesized outcome of the debate below "
            "-- weigh this as the primary conclusion, not merely one more opinion alongside the "
            f"raw transcript):\n{facilitator_decision[:800]}\n\n"
            f"**Bull Analyst's final position:**\n{last_bull[:1000]}\n\n"
            f"**Bear Analyst's final position:**\n{last_bear[:1000]}"
            + memory_context
        )
        if post_mortem_lessons:
            self.state["investment_plan"] += (
                f"\n\n**Post-Mortem Lessons (cross-regime track record):**\n{post_mortem_lessons}"
            )
        trader_input = {
            "investment_plan_from_debate": self.state["investment_plan"][:500],
            "facilitator_decision": self.state.get("investment_facilitator_decision", "")[:300],
        }
        out = agents.create_trader(llm)(self.state)
        self.state.update(out)
        self.session.log_step("Trader Decision", trader_input, out.get("trader_investment_plan", ""))
        yield {"type": "stage_done", "stage": "trader",
               "html": md_to_html(out["trader_investment_plan"]), "meta": {},
               "input": trader_input}

        # -- risk debate --
        idx += 1
        yield self._start("risk_debate", idx)
        self.state["risk_debate_state"] = {
            "history": "", "aggressive_history": "", "conservative_history": "",
            "neutral_history": "", "latest_speaker": "",
            "current_aggressive_response": "", "current_conservative_response": "",
            "current_neutral_response": "", "count": 0,
        }
        agg = agents.create_aggressive_debator(llm)
        con = agents.create_conservative_debator(llm)
        neu = agents.create_neutral_debator(llm)
        # 🔴 FIXED: Aggressive, then Conservative, then Neutral -- in that
        # exact order, every single round -- so Neutral always got the
        # same second-mover (well, third-mover) informational advantage
        # discussed in the investment-debate fix above, and confirmed
        # (across many tickers) to correlate with Neutral winning almost
        # every risk debate regardless of the actual risk picture. The
        # per-debater prompts already read each other's response by name
        # (current_aggressive_response / current_conservative_response /
        # current_neutral_response, not a shared order-dependent field),
        # so rotating the call order is safe with no other changes needed.
        # With the default risk_rounds=3, this rotation gives each side
        # the last-word slot exactly once across the full debate instead
        # of Neutral having it in every round.
        speaker_fns = {
            "aggressive": (agg, "Aggressive", "current_aggressive_response"),
            "conservative": (con, "Conservative", "current_conservative_response"),
            "neutral": (neu, "Neutral", "current_neutral_response"),
        }
        order_cycle = ["aggressive", "conservative", "neutral"]
        for r in range(1, self.risk_rounds + 1):
            offset = (r - 1) % len(order_cycle)
            rotation = order_cycle[offset:] + order_cycle[:offset]
            for key in rotation:
                fn, name, resp_key = speaker_fns[key]
                out = fn(self.state); self.state.update(out)
                yield {"type": "debate_turn", "stage": "risk_debate", "speaker": name, "side": key,
                       "round": r, "html": md_to_html(_strip(out["risk_debate_state"][resp_key]))}
        final_risk = self.state["risk_debate_state"]
        risk_debate_input = {
            "trader_decision": self.state.get("trader_investment_plan", "")[:300],
            "fundamentals": str(self.state.get("fundamentals_report", ""))[:200],
            "market": str(self.state.get("market_report", ""))[:200],
            "rounds": self.risk_rounds,
        }
        self.session.log_step("Risk Management Debate", risk_debate_input, {
            "aggressive": final_risk["aggressive_history"][-500:],
            "conservative": final_risk["conservative_history"][-500:],
            "neutral": final_risk["neutral_history"][-500:]})
        yield {"type": "stage_done", "stage": "risk_debate",
               "html": md_to_html(final_risk["history"][:3000]) if final_risk.get("history") else "",
               "meta": {"count": final_risk["count"]},
               "input": risk_debate_input}

        # -- risk facilitator --
        idx += 1
        yield self._start("risk_facilitator", idx)
        risk_facilitator_input = {
            "trader_decision": self.state.get("trader_investment_plan", "")[:300],
            "risk_debate_summary": final_risk.get("history", "")[:500],
        }
        out = agents.create_risk_facilitator(llm)(self.state)
        self.state.update(out)
        self.session.log_step("Risk Facilitator Decision", risk_facilitator_input, out["risk_facilitator_decision"])
        yield {"type": "stage_done", "stage": "risk_facilitator",
               "html": md_to_html(out["risk_facilitator_decision"]), "meta": {},
               "input": risk_facilitator_input}

        # -- portfolio manager (final) --
        idx += 1
        yield self._start("portfolio_manager", idx)
        self.state["investment_plan"] = self.state.get("trader_investment_plan", "")
        self.state["investor_profile"] = self.investor_profile
        self.state["past_context"] = ""
        portfolio_manager_input = {
            "investor_profile": self.investor_profile,
            "trader_plan": self.state.get("trader_investment_plan", "")[:400],
            "risk_facilitator_decision": self.state.get("risk_facilitator_decision", "")[:400],
        }
        out = agents.create_portfolio_manager(llm)(self.state)
        self.state.update(out)
        final_text = out["final_trade_decision"]
        self.session.log_step("Portfolio Manager Final Decision", portfolio_manager_input, final_text)
        self.session.finalize(final_text)
        yield {"type": "stage_done", "stage": "portfolio_manager",
               "html": md_to_html(final_text), "meta": {"signal": first_signal(final_text)},
               "input": portfolio_manager_input}

        # -- decision verifier (post-decision sanity check; can auto-downgrade to HOLD) --
        idx += 1
        yield self._start("decision_verifier", idx)
        decision_verifier_input = {"final_signal": first_signal(final_text)}
        logs = []
        node = agents.create_decision_verifier(llm, log=logs.append)
        out = node(self.state)
        self.state.update(out)
        for ln in logs:
            yield {"type": "log", "stage": "decision_verifier", "line": ln}
        verification = out.get("verification_result", {})
        self.session.log_step("Decision Verifier", decision_verifier_input, verification)
        verifier_md = f"**Verification Status: {verification.get('status', 'N/A')}**\n\n{verification.get('notes', '')}"
        yield {"type": "stage_done", "stage": "decision_verifier",
               "html": md_to_html(verifier_md), "meta": verification,
               "input": decision_verifier_input}
        effective_signal = verification.get("effective_signal") or first_signal(final_text)

        # -- save episode --
        idx += 1
        yield self._start("save_episode", idx)
        save_episode_input = {
            "company": self.company, "trade_date": self.trade_date,
            "regime": today_regime, "final_signal": effective_signal,
            "rsi": indicators.get("rsi"), "macd": indicators.get("macd"),
        }
        # 🔴 FIXED: mirrors orchestrator.py's own _synthesize() confidence
        # calc (mean of the analysts' own reported confidences) -- the
        # same 4 fields are already sitting in self.state, one per
        # analyst node's own return value (see e.g.
        # create_fundamentals_analyst's "fundamentals_confidence").
        # Computed here, independently, rather than importing from
        # orchestrator.py, since orchestrator.py is the layer that WRAPS
        # PipelineRunner, not the other way around -- runner.py can't
        # depend on it without a circular import. May differ slightly
        # from the displayed synthesis confidence (which can also factor
        # in debate/verifier agreement when no analyst confidence is
        # available), but this is the same core signal and, critically,
        # is now actually saved rather than discarded.
        _conf_fields = ("fundamentals_confidence", "market_confidence",
                        "news_confidence", "sentiment_confidence")
        _confs = [self.state.get(k) for k in _conf_fields]
        _confs = [c for c in _confs if isinstance(c, (int, float)) and c > 0]
        episode_confidence = round(sum(_confs) / len(_confs), 3) if _confs else None
        try:
            saved = memory.save_episode(
                self.company, self.trade_date, indicators,
                self.state.get("fund_metrics", {}), self.state.get("news_metrics", {}),
                self.state.get("sentiment_metrics", {}), final_text,
                self.state.get("market_raw_data", {}).get("stock_data", ""),
                macro_regime=self.state.get("macro_regime"),
                verifier_status=verification.get("status"),
                llm_provider=self._llm_provider,
                confidence=episode_confidence,
                # 🔴 FIXED: without this, two runs for the same ticker on
                # the same calendar day (e.g. Kimi then Sonnet -- exactly
                # the documented workflow for a provider comparison, or
                # just re-running after a bad result) hashed to the same
                # episode id and the second run silently overwrote the
                # first's episode in ChromaDB. session_start is already a
                # per-run, second-precision timestamp (see logger.py's
                # SessionLog.__init__) -- reusing it here rather than
                # calling datetime.now() again keeps "this run's identity"
                # defined in exactly one place.
                run_key=self.session.data.get("session_start"))
            yield {"type": "stage_done", "stage": "save_episode",
                   "html": f"<p class='muted'>Episode saved &middot; regime <b>{saved['regime']}</b> &middot; "
                           f"signal <b>{saved['signal']}</b></p>", "meta": saved.get("metadata", {}),
                   "input": save_episode_input}
        except Exception as e:  # noqa: BLE001
            yield {"type": "stage_done", "stage": "save_episode",
                   "html": f"<p class='muted'>Episode not saved: {e}</p>", "meta": {},
                   "input": save_episode_input}

        yield {"type": "summary", "summary": self.session.data.get("summary_table", {})}
        yield {"type": "final", "signal": effective_signal,
               "html": md_to_html(final_text), "session_id": self.session.data["session_id"]}

    # ======================================================================
    # DEMO MODE (no external deps) — realistic simulated run
    # ======================================================================
    def _demo_input(self, stage: str) -> dict:
        """Representative 'what this agent fetched' snapshot for demo mode —
        same shape as the real-mode inputs, so the pipeline flow view works
        identically whether or not Ollama/TradingAgents are configured."""
        d = {
            "outcome_backfill": {"company": self.company, "trade_date": self.trade_date},
            "fundamentals": {"company": self.company, "date": self.trade_date,
                              "tools_used": "get_fundamentals, get_balance_sheet, get_cashflow, get_income_statement"},
            "market": {"company": self.company, "date": self.trade_date,
                       "tools_used": "get_stock_data, get_indicators"},
            "news": {"company": self.company, "date": self.trade_date,
                     "tools_used": "get_news, get_global_news"},
            "sentiment": {"company": self.company, "date": self.trade_date},
            "macro_regime": {"date": self.trade_date, "tickers": "^VIX, ^TNX, DX-Y.NYB"},
            "investment_debate": {"fundamentals_report": "(Fundamentals Analyst report, first 300 chars)",
                                   "market_report": "(Market Analyst report, first 300 chars)",
                                   "news_report": "(News Analyst report, first 300 chars)",
                                   "sentiment_report": "(Sentiment Analyst report, first 300 chars)",
                                   "rounds": self.investment_rounds},
            "investment_facilitator": {"debate_history": "(Bull vs Bear debate history, first 500 chars)"},
            "memory": {"company": self.company, "today_regime": mem.classify_regime(demo.INDICATORS),
                       "query": f"{self.company} trading decision in similar market regime", "n_results": 3},
            "post_mortem": {"company": self.company, "scope": "all RESOLVED episodes, cross-regime"},
            "trader": {"investment_plan_from_debate": "(debate history + RAEM memory context, first 500 chars)"},
            "risk_debate": {"trader_decision": "(Trader Proposal, first 300 chars)", "rounds": self.risk_rounds},
            "risk_facilitator": {"risk_debate_summary": "(Risk Debate history, first 500 chars)"},
            "portfolio_manager": {"investor_profile": self.investor_profile,
                                   "trader_plan": "(Trader Proposal, first 400 chars)",
                                   "risk_facilitator_decision": "(Risk Facilitator verdict, first 400 chars)"},
            "decision_verifier": {"final_signal": first_signal(demo.FINAL)},
            "save_episode": {"company": self.company, "trade_date": self.trade_date,
                              "regime": demo.REGIME, "final_signal": first_signal(demo.FINAL)},
        }
        return d.get(stage, {})

    def _run_demo(self):
        from . import demo, agents
        idx = 0
        indicators = demo.INDICATORS
        for stage, _label in STAGES:
            idx += 1
            yield self._start(stage, idx)
            time.sleep(0.35)
            stage_input = self._demo_input(stage)

            if stage == "outcome_backfill":
                yield {"type": "stage_done", "stage": stage,
                       "html": md_to_html(demo.BACKFILL), "meta": {}, "input": stage_input}
            elif stage == "fundamentals":
                for ln in demo.FUND_LOGS:
                    yield {"type": "log", "stage": stage, "line": ln}; time.sleep(0.15)
                self.session.log_step("Fundamentals Analyst", stage_input, {"metrics": demo.FUND_METRICS, "report": demo.FUNDAMENTALS})
                yield {"type": "stage_done", "stage": stage, "html": md_to_html(demo.FUNDAMENTALS),
                       "meta": {"metrics": demo.FUND_METRICS,
                                "confidence": agents._field_completeness_confidence(demo.FUND_METRICS)},
                       "input": stage_input}
            elif stage == "market":
                for ln in demo.MARKET_LOGS:
                    yield {"type": "log", "stage": stage, "line": ln}; time.sleep(0.12)
                self.session.log_step("Market Analyst", stage_input, {"indicators": indicators,
                                      "stock_data": demo.STOCK_CSV, "report": demo.MARKET})
                yield {"type": "stage_done", "stage": stage, "html": md_to_html(demo.MARKET),
                       "meta": {"indicators": indicators, "regime": mem.classify_regime(indicators),
                                "confidence": agents._field_completeness_confidence(indicators)},
                       "input": stage_input}
            elif stage == "news":
                for ln in demo.NEWS_LOGS:
                    yield {"type": "log", "stage": stage, "line": ln}; time.sleep(0.12)
                self.session.log_step("News Analyst", stage_input, {"news_metrics": demo.NEWS_METRICS, "report": demo.NEWS})
                yield {"type": "stage_done", "stage": stage, "html": md_to_html(demo.NEWS),
                       "meta": {"news_metrics": demo.NEWS_METRICS, "confidence": 0.8}, "input": stage_input}
            elif stage == "sentiment":
                self.session.log_step("Sentiment Analyst", stage_input, {"sentiment_metrics": demo.SENT_METRICS, "report": demo.SENTIMENT})
                yield {"type": "stage_done", "stage": stage, "html": md_to_html(demo.SENTIMENT),
                       "meta": {"sentiment_metrics": demo.SENT_METRICS, "confidence": 0.7}, "input": stage_input}
            elif stage == "macro_regime":
                self.session.log_step("Macro Regime Analyst", stage_input, {"macro_regime": demo.MACRO_REGIME, "report": demo.MACRO_REPORT})
                yield {"type": "stage_done", "stage": stage, "html": md_to_html(demo.MACRO_REPORT),
                       "meta": {"macro_regime": demo.MACRO_REGIME, "macro_snapshot": demo.MACRO_SNAPSHOT},
                       "input": stage_input}
            elif stage == "investment_debate":
                turns_html = []
                for r in range(1, self.investment_rounds + 1):
                    for side, speaker, text in demo.investment_turns(r):
                        turns_html.append(f"**{speaker}:** {text}")
                        yield {"type": "debate_turn", "stage": stage, "speaker": speaker,
                               "side": side, "round": r, "html": md_to_html(text)}
                        time.sleep(0.3)
                yield {"type": "stage_done", "stage": stage, "html": md_to_html("\n\n".join(turns_html)),
                       "meta": {"count": self.investment_rounds * 2}, "input": stage_input}
            elif stage == "investment_facilitator":
                yield {"type": "stage_done", "stage": stage, "html": md_to_html(demo.INV_FACILITATOR), "meta": {},
                       "input": stage_input}
            elif stage == "memory":
                yield {"type": "regime", "regime": demo.REGIME, "reflection": demo.REFLECTION,
                       "episodes": demo.EPISODES}
                yield {"type": "stage_done", "stage": stage, "html": md_to_html(demo.MEMORY_CONTEXT),
                       "meta": {"regime": demo.REGIME, "n_episodes": len(demo.EPISODES)}, "input": stage_input}
            elif stage == "post_mortem":
                self.session.log_step("Post-Mortem Review", stage_input, demo.POST_MORTEM)
                yield {"type": "stage_done", "stage": stage, "html": md_to_html(demo.POST_MORTEM),
                       "meta": {"episodes_reviewed": 6}, "input": stage_input}
            elif stage == "trader":
                self.session.log_step("Trader Decision", stage_input, demo.TRADER)
                yield {"type": "stage_done", "stage": stage, "html": md_to_html(demo.TRADER), "meta": {},
                       "input": stage_input}
            elif stage == "risk_debate":
                turns_html = []
                for r in range(1, self.risk_rounds + 1):
                    for side, speaker, text in demo.risk_turns(r):
                        turns_html.append(f"**{speaker}:** {text}")
                        yield {"type": "debate_turn", "stage": stage, "speaker": speaker,
                               "side": side, "round": r, "html": md_to_html(text)}
                        time.sleep(0.25)
                yield {"type": "stage_done", "stage": stage, "html": md_to_html("\n\n".join(turns_html)),
                       "meta": {"count": self.risk_rounds * 3}, "input": stage_input}
            elif stage == "risk_facilitator":
                yield {"type": "stage_done", "stage": stage, "html": md_to_html(demo.RISK_FACILITATOR), "meta": {},
                       "input": stage_input}
            elif stage == "portfolio_manager":
                self.session.log_step("Portfolio Manager Final Decision", stage_input, demo.FINAL)
                self.session.finalize(demo.FINAL)
                yield {"type": "stage_done", "stage": stage, "html": md_to_html(demo.FINAL),
                       "meta": {"signal": first_signal(demo.FINAL)}, "input": stage_input}
            elif stage == "decision_verifier":
                verifier_md = f"**Verification Status: {demo.VERIFIER_RESULT['status']}**\n\n{demo.VERIFIER_RESULT['notes']}"
                self.session.log_step("Decision Verifier", stage_input, demo.VERIFIER_RESULT)
                yield {"type": "stage_done", "stage": stage, "html": md_to_html(verifier_md),
                       "meta": demo.VERIFIER_RESULT, "input": stage_input}
            elif stage == "save_episode":
                yield {"type": "stage_done", "stage": stage,
                       "html": f"<p class='muted'>Episode saved &middot; regime <b>{demo.REGIME}</b> &middot; "
                               f"signal <b>{first_signal(demo.FINAL)}</b></p>", "meta": {}, "input": stage_input}

        yield {"type": "summary", "summary": self.session.data.get("summary_table", {})}
        yield {"type": "final", "signal": demo.VERIFIER_RESULT.get("effective_signal") or first_signal(demo.FINAL),
               "html": md_to_html(demo.FINAL), "session_id": self.session.data["session_id"]}

    # -- misc render helpers -------------------------------------------------
    @staticmethod
    def _backfill_html(res):
        if res.get("resolved"):
            rows = "".join(
                f"<tr><td>{d['date']}</td><td>{d['signal']}</td>"
                f"<td class='{'pos' if d['pnl_pct'] > 0 else 'neg' if d['pnl_pct'] < 0 else ''}'>"
                f"{d['pnl_pct']:+.2f}%</td><td>{d['label']}</td></tr>"
                for d in res.get("details", []))
            return (f"<p>Resolved <b>{res['resolved']}</b> pending episode(s) using close "
                    f"<b>${res.get('today_close', '—')}</b>.</p>"
                    f"<table class='mini'><thead><tr><th>Date</th><th>Signal</th>"
                    f"<th>P&amp;L</th><th>Outcome</th></tr></thead><tbody>{rows}</tbody></table>")
        return f"<p class='muted'>{res.get('note', 'Nothing to backfill.')}</p>"


def _last_turn(history: str) -> str:
    parts = [p for p in history.split("\n") if p.strip()]
    return parts[-1] if parts else ""


def _strip(argument: str) -> str:
    return argument.split(":", 1)[1].strip() if ":" in argument else argument


def _memory_context(regime, episodes, reflection):
    if not episodes and not reflection:
        return ""
    lines = [f"**Past episodes in {regime} regime:**"]
    for ep in episodes:
        m = ep["metadata"]
        outcome = (f", Outcome={m.get('outcome_label', 'N/A')} (P&L {m.get('pnl_pct', 'N/A')}%)"
                   if m.get("outcome_status") == "RESOLVED" else ", Outcome=PENDING")
        lines.append(f"- {m.get('trade_date')}: {m.get('final_signal')} · RSI {m.get('rsi')}{outcome}")
    ctx = "\n".join(lines)
    if reflection:
        ctx += f"\n\n**Regime-transition reflection:**\n{reflection}"
    return ctx


def _ep_view(ep):
    m = ep["metadata"]
    return {"trade_date": m.get("trade_date"), "regime": m.get("regime"),
            "signal": m.get("final_signal"), "rsi": m.get("rsi"),
            "outcome_status": m.get("outcome_status"), "pnl_pct": m.get("pnl_pct"),
            "outcome_label": m.get("outcome_label"), "distance": round(ep.get("distance", 0), 3)}
