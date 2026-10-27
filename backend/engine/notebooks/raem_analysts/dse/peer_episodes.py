# raem_analysts/dse/peer_episodes.py -- shared by abbank-dse, batbc-dse, bpml-dse, waltonhil-dse
# The company-specific values come from each notebook's RAEM_PROFILE:
# @@TICKER@@ ticker, @@FILE@@ ticker as used in file names, @@LOG@@ trading-log name, @@SLOT:key@@ lines the profile supplies.
# >>> shared code
# ================================================================
# 👥 PEER EPISODES — price-based regime -> forward-return observations for peer stocks
# ================================================================
# peer_episode_lines() (news-graph cell) looks for past episodes of peer stocks in `episodic_memory`, but the
# pipeline only ever saves @@TICKER@@ episodes, so that hop was always empty. This cell fills it with HONEST data:
# for each sector / correlated peer we replay its own price history, tag the market regime on past dates with the
# SAME classify_regime() used for @@TICKER@@, and store what the price did over the next GRAPH_HORIZON trading days.
#   * no LLM calls and NO invented trading decisions -- these are observations, not "our" trades
#   * stored with source="PEER_PRICE", final_signal="N/A", outcome_date = the day the outcome became known
#     (peer_episode_lines ignores an episode until outcome_date < as_of -> no look-ahead when you backtest)
#   * deterministic ids -> re-running is idempotent (existing episodes are skipped, no extra embedding calls)
#   * every @@TICKER@@ query filters on company="@@TICKER@@", so peer rows never leak into @@TICKER@@'s own memory / graph memory
# Ablation switch for the thesis: USE_PEER_EPISODES = False leaves the peer hop empty (old behaviour).
import numpy as np
import pandas as pd

USE_PEER_EPISODES = True
PEER_EP_MAX_PEERS = 10          # peers per run (sector peers first, then correlated peers)
PEER_EP_PER_PEER = 8            # episodes stored per peer
PEER_EP_STEP = 10               # trading days between sampled dates (spreads them over different regimes)
PEER_EP_FLAT_BAND = 0.5         # |forward return| below this % is labelled FLAT (same band as @@TICKER@@ outcomes)


def _peer_indicator_frame(close: pd.Series) -> pd.DataFrame:
    """rsi / macd / Bollinger bands for every date -- the inputs classify_regime() needs."""
    c = close.astype(float)
    d = c.diff()
    up, dn = d.clip(lower=0).ewm(alpha=1 / 14, adjust=False).mean(), (-d.clip(upper=0)).ewm(alpha=1 / 14, adjust=False).mean()
    rsi = 100 - 100 / (1 + up / dn.replace(0, np.nan))
    rsi = rsi.where(dn != 0, 100.0)
    macd = c.ewm(span=12, adjust=False).mean() - c.ewm(span=26, adjust=False).mean()
    ma, sd = c.rolling(20).mean(), c.rolling(20).std()
    return pd.DataFrame({"rsi": rsi, "macd": macd, "boll_ub": ma + 2 * sd, "boll_lb": ma - 2 * sd})


def build_peer_observations(close_df: pd.DataFrame, peer: str, horizon: int, n: int, step: int) -> list:
    """Returns [{date, regime, fwd_ret, outcome_date}], newest first. Only dates whose outcome is already known."""
    if peer not in close_df.columns:
        return []
    s = close_df[peer].dropna()
    if len(s) < 60 + horizon:
        return []
    ind = _peer_indicator_frame(s)
    out, last_i = [], len(s) - 1 - horizon                       # last sampled date must have `horizon` days after it
    for k in range(n):
        i = last_i - k * step
        if i < 30:                                               # need enough history for MACD / Bollinger
            break
        row = ind.iloc[i]
        if row.isna().any():
            continue
        fwd = (float(s.iloc[i + horizon]) / float(s.iloc[i]) - 1) * 100
        out.append({"date": s.index[i].strftime("%Y-%m-%d"), "regime": classify_regime(row.to_dict()),
                    "fwd_ret": fwd, "outcome_date": s.index[i + horizon].strftime("%Y-%m-%d")})
    return out


def store_peer_episodes(company: str, as_of: str) -> dict:
    stats = {"enabled": USE_PEER_EPISODES, "peers": [], "stored": 0, "already_there": 0}
    if not USE_PEER_EPISODES:
        print("👥 Peer episodes disabled (USE_PEER_EPISODES=False).")
        return stats
    gm = (globals().get("graph_result") or {}).get("metrics") or {}
    peers = list(dict.fromkeys(list(gm.get("sector_peers") or []) + list(gm.get("corr_peers") or [])))
    peers = [p for p in peers if p != company][:PEER_EP_MAX_PEERS]
    if not peers:
        print("👥 Peer episodes: graph_result has no peers -- nothing to store.")
        return stats
    try:
        close_df, _vol, _sec = _load_graph_panel(company, as_of)         # disk-cached panel from the Graph Analyst cell
    except Exception as e:
        print(f"⚠️ Peer episodes skipped (price panel unavailable): {str(e)[:100]}")
        return stats
    horizon = int(globals().get("GRAPH_HORIZON", 5))
    ids, docs, metas = [], [], []
    for p in peers:
        obs = build_peer_observations(close_df, p, horizon, PEER_EP_PER_PEER, PEER_EP_STEP)
        if obs:
            stats["peers"].append(p)
        for o in obs:
            if o["outcome_date"] >= as_of:                               # outcome must be known strictly before as_of
                continue
            label = "UP" if o["fwd_ret"] > PEER_EP_FLAT_BAND else ("DOWN" if o["fwd_ret"] < -PEER_EP_FLAT_BAND else "FLAT")
            ids.append(make_episode_id(p, o["date"], run_id="PEER_PRICE"))
            docs.append(f"Peer price observation: {p} on {o['date']}. Market regime: {o['regime']}. "
                        f"Forward {horizon}-trading-day return: {o['fwd_ret']:+.2f}% ({label}). Not a trading decision.")
            metas.append({"company": p, "trade_date": o["date"], "regime": o["regime"], "macro_regime": "N/A",
                          "final_signal": "N/A", "source": "PEER_PRICE", "outcome_status": "RESOLVED",
                          "pnl_pct": f"{o['fwd_ret']:.2f}", "outcome_label": label, "outcome_date": o["outcome_date"],
                          "horizon_days": str(horizon), "session_id": "PEER_PRICE", "news_topics": "NONE",
                          "verification_status": "N/A", "entry_price": "N/A", "exit_price": "N/A"})
    if ids:
        have = set(episodic_memory.get(ids=ids).get("ids", []))
        keep = [i for i, x in enumerate(ids) if x not in have]
        stats["already_there"] = len(ids) - len(keep)
        if keep:
            episodic_memory.upsert(ids=[ids[i] for i in keep], documents=[docs[i] for i in keep], metadatas=[metas[i] for i in keep])
        stats["stored"] = len(keep)
    print(f"👥 Peer episodes: {len(stats['peers'])} peers {stats['peers']} | {stats['stored']} new stored, "
          f"{stats['already_there']} already in memory | episodic_memory total: {episodic_memory.count()}")
    return stats


peer_episode_result = store_peer_episodes(COMPANY, TRADE_DATE)
try:
    log_step("Peer Episodes", {"company": COMPANY, "date": TRADE_DATE}, peer_episode_result)
except Exception:
    pass

