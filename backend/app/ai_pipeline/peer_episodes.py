"""Peer episodes: price-based regime -> forward-return observations for the peers of a stock.

Ported from the research notebook (BATBC v10, "PEER EPISODES").  The news graph looks for past episodes of the company's
peers in episodic memory, but the pipeline only ever saves the analysed company's own episodes, so that hop was always
empty.  This fills it with honest data: for each sector / correlated peer we replay its own price history, tag the market
regime on past dates with the SAME classify_regime() used for the company, and store what the price did over the next
GRAPH_HORIZON trading days.
  * no model calls and NO invented trading decisions: these are observations, not "our" trades;
  * stored with source="PEER_PRICE", final_signal="N/A" and outcome_date = the day the outcome became known (the news graph
    ignores an episode until its outcome_date is before the as-of date, so a back-test has no look-ahead);
  * deterministic ids, so a re-run is idempotent (existing episodes are skipped, no extra embedding calls);
  * every query of the company's own memory filters on company=<ticker>, so peer rows never leak into its memory or graph memory.
Switch for the thesis ablation: RAEM_PEER_EPISODES=0 leaves the peer hop empty.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import config
from .memory import classify_regime, make_episode_id

PEER_EP_MAX_PEERS = 10          # peers per run (sector peers first, then correlated peers)
PEER_EP_PER_PEER = 8            # episodes stored per peer
PEER_EP_STEP = 10               # trading days between sampled dates (spreads them over different regimes)
PEER_EP_FLAT_BAND = 0.5         # |forward return| below this % is labelled FLAT (the band of the company's own outcomes)


def enabled() -> bool:
    return config.flag("RAEM_PEER_EPISODES", True)


def _indicator_frame(close: pd.Series) -> pd.DataFrame:
    """rsi / macd / Bollinger bands for every date: the inputs classify_regime() needs."""
    c = close.astype(float)
    d = c.diff()
    up, dn = d.clip(lower=0).ewm(alpha=1 / 14, adjust=False).mean(), (-d.clip(upper=0)).ewm(alpha=1 / 14, adjust=False).mean()
    rsi = 100 - 100 / (1 + up / dn.replace(0, np.nan))
    rsi = rsi.where(dn != 0, 100.0)
    macd = c.ewm(span=12, adjust=False).mean() - c.ewm(span=26, adjust=False).mean()
    ma, sd = c.rolling(20).mean(), c.rolling(20).std()
    return pd.DataFrame({"rsi": rsi, "macd": macd, "boll_ub": ma + 2 * sd, "boll_lb": ma - 2 * sd})


def build_peer_observations(close_df: pd.DataFrame, peer: str, horizon: int, n: int, step: int) -> list:
    """[{date, regime, fwd_ret, outcome_date}], newest first. Only dates whose outcome is already known."""
    if peer not in close_df.columns:
        return []
    s = close_df[peer].dropna()
    if len(s) < 60 + horizon:
        return []
    ind = _indicator_frame(s)
    out, last_i = [], len(s) - 1 - horizon                       # the last sampled date must have `horizon` days after it
    for k in range(n):
        i = last_i - k * step
        if i < 30:                                               # enough history for MACD / Bollinger
            break
        row = ind.iloc[i]
        if row.isna().any():
            continue
        fwd = (float(s.iloc[i + horizon]) / float(s.iloc[i]) - 1) * 100
        out.append({"date": s.index[i].strftime("%Y-%m-%d"), "regime": classify_regime(row.to_dict()),
                    "fwd_ret": fwd, "outcome_date": s.index[i + horizon].strftime("%Y-%m-%d")})
    return out


def store_peer_episodes(memory, company: str, as_of: str, peers, close_df: pd.DataFrame, horizon: int = 5, log=print) -> dict:
    """Store the peers' price observations in the episodic collection of `memory` (a connected RAEMMemory)."""
    stats = {"enabled": enabled(), "peers": [], "stored": 0, "already_there": 0}
    if not enabled():
        log("peer episodes are off (RAEM_PEER_EPISODES=0)")
        return stats
    peers = [p for p in dict.fromkeys(peers or []) if p != company][:PEER_EP_MAX_PEERS]
    if not peers or close_df is None or getattr(close_df, "empty", True):
        log("peer episodes: no peers or no price panel, nothing to store")
        return stats
    close_df = close_df[close_df.index <= as_of]                 # nothing after the as-of day, whatever the caller passes
    ids, docs, metas = [], [], []
    for p in peers:
        obs = build_peer_observations(close_df, p, horizon, PEER_EP_PER_PEER, PEER_EP_STEP)
        if obs:
            stats["peers"].append(p)
        for o in obs:
            if o["outcome_date"] >= as_of:                       # the outcome must be known strictly before as_of
                continue
            label = "UP" if o["fwd_ret"] > PEER_EP_FLAT_BAND else ("DOWN" if o["fwd_ret"] < -PEER_EP_FLAT_BAND else "FLAT")
            ep_id = make_episode_id(p, o["date"], run_key="PEER_PRICE")
            ids.append(ep_id)
            docs.append(f"Peer price observation: {p} on {o['date']}. Market regime: {o['regime']}. "
                        f"Forward {horizon}-trading-day return: {o['fwd_ret']:+.2f}% ({label}). Not a trading decision.")
            metas.append({"company": p, "trade_date": o["date"], "regime": o["regime"], "macro_regime": "N/A",
                          "final_signal": "N/A", "source": "PEER_PRICE", "outcome_status": "RESOLVED",
                          "pnl_pct": f"{o['fwd_ret']:.2f}", "outcome_label": label, "outcome_date": o["outcome_date"],
                          "horizon_days": str(horizon), "session_id": "PEER_PRICE", "news_topics": "NONE",
                          "verification_status": "N/A", "entry_price": "N/A", "exit_price": "N/A",
                          # the keys the Memory API reads (see memory.build_episode_document)
                          "memory_id": ep_id, "ticker": p, "timestamp": o["date"], "market_regime": o["regime"],
                          "agent_name": "peer_price_observation", "decision": "N/A", "outcome": label,
                          "summary": docs[-1][:200], "confidence": 0.0, "risk": "N/A", "version": "1.0", "experience_score": 0.0})
    if ids:
        have = set(memory.episodic.get(ids=ids).get("ids", []))
        keep = [i for i, x in enumerate(ids) if x not in have]
        stats["already_there"] = len(ids) - len(keep)
        if keep:
            memory.episodic.upsert(ids=[ids[i] for i in keep], documents=[docs[i] for i in keep], metadatas=[metas[i] for i in keep])
        stats["stored"] = len(keep)
    log(f"peer episodes: {len(stats['peers'])} peers {stats['peers']} | {stats['stored']} new stored, {stats['already_there']} already in memory")
    return stats
