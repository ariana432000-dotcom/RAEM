"""Graph Analyst: a DSE peer graph and a small graph-attention network (GAT), adapted from BlindTrade (Jeon & Lee 2026).

Ported from the research notebook (BATBC v10, "GRAPH ANALYST").  What it does, scaled down to what a single-stock
pipeline can support:
  1) a peer graph over a DSE universe: sector edges plus return-correlation edges;
  2) neighbour-aggregated features for the company (explainable, no training);
  3) a small dense GAT trained walk-forward to rank the stocks by their 5-day forward return;
  4) validate before deploying (the paper's rule): the GAT rank is passed to the debaters as evidence only if its holdout
     rank-IC beats GRAPH_IC_GATE and the no-graph ablation.  A shuffled negative control and a raw-momentum baseline are
     reported next to it, like the paper's leakage audit.
Not copied from the paper: PPO-DSR portfolio reinforcement learning (far too little data) and the LLM-reasoning embedding edges.

Output: `run_graph_analyst()` returns the `graph_context` sentence (debate_data adds it to every debater's HARD DATA block), the
metrics (peers, 20-day returns, GNN status) and the price panel (peer episodes and the news graph reuse it).

Only Dhaka Stock Exchange tickers have a peer universe here; any other ticker returns status SKIPPED.  The GAT needs torch
(in requirements.txt); without it the graph features are still produced.  Switches: RAEM_GRAPH_ANALYST=0 turns the whole step
off, RAEM_GRAPH_GNN=0 skips only the GAT.  Fetched prices are cached per symbol on disk (RAEM_GRAPH_CACHE_HOURS, default 12)
because DSE answers about one request a second; RAEM_GRAPH_FETCH_BUDGET (seconds, default 600) stops a slow first fetch and
lets the next run finish from the cache.
"""
from __future__ import annotations

import json
import os
import time
import warnings
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from . import config
from .debate_data import PEER_REL_BAND, peer_relation_facts  # noqa: F401  (the fact sheet and this module share them)

warnings.filterwarnings("ignore", message="Mean of empty slice")
warnings.filterwarnings("ignore", message="Degrees of freedom <= 0")

# Tickers are from the notebook and NOT verified against DSE: any that return fewer than 60 rows or cover under 70% of the
# dates are dropped automatically, so a wrong symbol only costs about one request.
GRAPH_PEERS = [
    "ABBANK", "BPML", "WALTONHIL",
    "GP", "ROBI", "SQURPHARMA", "RENATA", "BRACBANK", "CITYBANK", "ISLAMIBANK",
    "OLYMPIC", "UPGDCL", "BSRMLTD", "BERGERPBL", "MARICO", "LHB",
    "UNILEVERCL", "RAHIMAFOOD", "APEXFOODS", "BANGAS", "FUWANGFOOD", "MEGCONMILK", "RDFOOD", "ZEALBANGLA",
    "BXPHARMA", "BEACONPHAR", "ACIFORMULA", "ACMELAB", "IBNSINA", "KOHINOOR",
    "EBL", "PUBALIBANK", "UCB", "MTB", "PRIMEBANK", "SOUTHEASTB", "DUTCHBANGL", "EXIMBANK",
    "POWERGRID", "SUMITPOWER", "JAMUNAOIL", "MJLBD", "LINDEBD", "HEIDELBCEM", "SINGERBD",
    "BEXIMCO", "BATASHOE", "RAKCERAMIC", "TITASGAS", "DESCO",
]
GRAPH_LOOKBACK_DAYS = 450      # calendar days of history (about 300 trading days)
GRAPH_HORIZON = 5              # forward-return horizon (trading days) for the GAT target
GRAPH_HOLDOUT = 40             # last N usable dates held out for IC validation
GRAPH_CORR_TOPK = 3            # correlation neighbours per node
GRAPH_CORR_MIN = 0.15          # ignore weaker correlations than this
GRAPH_N_SEEDS = 5              # the paper used 20; 5 keeps the runtime small
GRAPH_EPOCHS = 80
GRAPH_IC_GATE = 0.03           # holdout mean rank-IC must exceed this AND the no-graph IC to be VALIDATED
GRAPH_FEATURES = ["ret1", "ret5", "ret20", "ret60", "vol20", "px_vs_sma50", "rsi14", "vol_ratio"]


def enabled() -> bool:
    return config.flag("RAEM_GRAPH_ANALYST", True)


def gnn_enabled() -> bool:
    return config.flag("RAEM_GRAPH_GNN", True)


def is_dse(company: str) -> bool:
    try:
        config.ensure_tradingagents_on_path()
        from tradingagents.dataflows.symbol_utils import is_dse_ticker
        return bool(is_dse_ticker(company))
    except Exception:  # noqa: BLE001
        return False


# ── 1) Price panel (cached per symbol) ───────────────────────────
def _cache_file() -> Path:
    return Path(os.environ.get("RAEM_GRAPH_CACHE_FILE") or config.DATA_DIR / "graph_panel_cache.json")


def _cache_hours() -> float:
    try:
        return float(os.environ.get("RAEM_GRAPH_CACHE_HOURS", "12"))
    except ValueError:
        return 12.0


def _cache_load() -> dict:
    try:
        with open(_cache_file(), "r", encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def _cache_save(d: dict) -> None:
    try:
        path = _cache_file()
        tmp = path.with_suffix(path.suffix + ".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(d, f)
        os.replace(tmp, path)
    except Exception as e:  # noqa: BLE001
        print(f"   graph panel cache not saved: {str(e)[:80]}")


def _default_fetchers():
    from . import dse_layer
    return dse_layer.fetch_dse_ohlcv_range, dse_layer.fetch_dse_company_snapshot


def load_graph_panel(company: str, as_of: str, peers=None, fetch_ohlcv=None, fetch_snapshot=None, log=print):
    """(close_df, volume_df, sectors) for the company and its candidate peers.

    A symbol fetched less than RAEM_GRAPH_CACHE_HOURS ago for the same as-of date comes from the disk cache, so a second
    company (or a re-run) only fetches what is missing.  Symbols with too little data are remembered and not asked again."""
    peers = list(GRAPH_PEERS if peers is None else peers)
    if fetch_ohlcv is None or fetch_snapshot is None:
        d_ohlcv, d_snap = _default_fetchers()
        fetch_ohlcv, fetch_snapshot = fetch_ohlcv or d_ohlcv, fetch_snapshot or d_snap
    end_dt = datetime.strptime(as_of, "%Y-%m-%d")
    start = (end_dt - timedelta(days=GRAPH_LOOKBACK_DAYS)).strftime("%Y-%m-%d")
    tickers = [company] + [t for t in peers if t != company]
    cache = _cache_load()
    syms, bad = cache.setdefault("symbols", {}), cache.setdefault("dropped", {})
    now, max_age = time.time(), _cache_hours() * 3600
    fresh = lambda e: isinstance(e, dict) and e.get("as_of") == as_of and now - e.get("fetched", 0) < max_age   # noqa: E731
    budget = float(os.environ.get("RAEM_GRAPH_FETCH_BUDGET", "600"))
    t0, fetched, cached, dirty, out_of_time = time.time(), 0, 0, False, False
    closes, vols, sectors = {}, {}, {}
    for sym in tickers:
        e = syms.get(sym)
        if fresh(e):
            cached += 1
        elif fresh(bad.get(sym)):
            continue
        elif time.time() - t0 > budget and sym != company:
            out_of_time = True
            continue
        else:
            df = fetch_ohlcv(sym, start, as_of)
            if df is None or df.empty or len(df) < 60:
                log(f"{sym}: no/insufficient DSE data ({0 if df is None else len(df)} rows), dropped from the graph")
                bad[sym] = {"as_of": as_of, "fetched": time.time()}
                dirty = True
                continue
            try:
                sector = (fetch_snapshot(sym).get("Sector") or "Unknown").strip() or "Unknown"
            except Exception:  # noqa: BLE001
                sector = "Unknown"
            e = {"as_of": as_of, "fetched": time.time(), "sector": sector,
                 "dates": [d.strftime("%Y-%m-%d") for d in df.index],
                 "close": [float(x) for x in df["Close"].where(df["Close"] > 0).fillna(0.0)],   # the parser writes 0.0 for a missing price
                 "volume": [float(x) for x in df["Volume"].fillna(0.0)]}
            syms[sym] = e
            fetched += 1
            dirty = True
            if fetched % 10 == 0:
                log(f"graph panel: {fetched} symbols fetched so far")
        idx = pd.to_datetime(e["dates"])
        closes[sym] = pd.Series(e["close"], index=idx).where(lambda s: s > 0)
        vols[sym] = pd.Series(e["volume"], index=idx)
        sectors[sym] = e.get("sector", "Unknown")
    if dirty:
        _cache_save(cache)
    if out_of_time:
        log(f"graph panel: the {budget:.0f}s fetch budget ran out; the rest comes from the cache on the next run")
    log(f"graph panel: {len(closes)} stocks ({cached} from cache, {fetched} fetched)")
    if not closes:
        return pd.DataFrame(), pd.DataFrame(), {}
    close = pd.DataFrame(closes).sort_index().ffill(limit=5)
    volume = pd.DataFrame(vols).sort_index().reindex(close.index).fillna(0.0)
    close = close.dropna(axis=1, thresh=int(0.7 * len(close)))      # drop very illiquid names
    volume = volume[close.columns]
    sectors = {k: v for k, v in sectors.items() if k in close.columns}
    return close, volume, sectors


# ── 2) Node features per (date, stock) ───────────────────────────
def build_feature_panel(close: pd.DataFrame, volume: pd.DataFrame) -> np.ndarray:
    """X [T, N, F], cross-sectionally z-scored per date, NaN -> 0."""
    ret1 = close.pct_change()
    feats = {
        "ret1": ret1,
        "ret5": close.pct_change(5),
        "ret20": close.pct_change(20),
        "ret60": close.pct_change(60),
        "vol20": ret1.rolling(20).std(),
        "px_vs_sma50": close / close.rolling(50, min_periods=20).mean() - 1,
    }
    delta = close.diff()
    gain = delta.clip(lower=0).rolling(14, min_periods=5).mean()
    loss = (-delta.clip(upper=0)).rolling(14, min_periods=5).mean()
    feats["rsi14"] = (100 - 100 / (1 + gain / loss.replace(0, np.nan))) / 100 - 0.5
    vol_ma = volume.rolling(20, min_periods=5).mean().replace(0, np.nan)
    feats["vol_ratio"] = np.log1p(volume / vol_ma - 1 + 1e-9).replace([np.inf, -np.inf], np.nan)
    cube = np.stack([feats[k].to_numpy(copy=True) for k in GRAPH_FEATURES], axis=-1).astype(float)   # [T,N,F]
    mu = np.nanmean(cube, axis=1, keepdims=True)
    sd = np.nanstd(cube, axis=1, keepdims=True)
    sd[sd < 1e-9] = 1.0
    z = (cube - mu) / sd
    return np.nan_to_num(z, nan=0.0, posinf=0.0, neginf=0.0)


# ── 3) Graph: sector edges + correlation edges ───────────────────
def build_graph(returns: pd.DataFrame, sectors: dict) -> np.ndarray:
    """Boolean adjacency [N,N] with self loops. Sector peers are fully connected; each node is also linked to its top-k most
    correlated stocks (|corr| >= GRAPH_CORR_MIN)."""
    cols = list(returns.columns)
    n = len(cols)
    adj = np.eye(n, dtype=bool)
    for i in range(n):
        for j in range(n):
            if i != j and sectors.get(cols[i], "?") == sectors.get(cols[j], "!") and sectors.get(cols[i]) != "Unknown":
                adj[i, j] = True
    corr = returns.corr(min_periods=40).fillna(0.0).to_numpy(copy=True)
    np.fill_diagonal(corr, 0.0)
    for i in range(n):
        order = np.argsort(-np.abs(corr[i]))[:GRAPH_CORR_TOPK]
        for j in order:
            if abs(corr[i, j]) >= GRAPH_CORR_MIN:
                adj[i, j] = adj[j, i] = True
    return adj


def neighbor_features(X_last: np.ndarray, adj: np.ndarray) -> np.ndarray:
    """One round of untrained mean message passing: the average of the neighbours' features (self excluded)."""
    a = adj.copy()
    np.fill_diagonal(a, False)
    deg = a.sum(1, keepdims=True)
    deg[deg == 0] = 1
    return (a.astype(float) @ X_last) / deg


# ── 4) Small dense GAT (needs torch) ─────────────────────────────
def _spearman(a, b) -> float:
    a, b = pd.Series(a), pd.Series(b)
    if a.nunique() < 3 or b.nunique() < 3:
        return np.nan
    return float(a.rank().corr(b.rank()))


def train_and_score_gat(X, fwd, adj, train_idx, test_idx, last_idx, seeds, epochs, use_graph=True):
    """Train on the dates in train_idx. Returns (holdout rank-IC mean, its sd over seeds, last-date scores [N], shuffled-control IC).
    Dense attention over the adjacency mask."""
    import torch
    import torch.nn as nn
    import torch.nn.functional as F
    n, f_in = X.shape[1], X.shape[2]
    A = torch.tensor(adj if use_graph else np.eye(n, dtype=bool))

    class GATLayer(nn.Module):
        def __init__(self, i, o, heads=2):
            super().__init__()
            self.h, self.o = heads, o
            self.W = nn.Linear(i, o * heads, bias=False)
            self.a_src = nn.Parameter(torch.randn(heads, o) * 0.1)
            self.a_dst = nn.Parameter(torch.randn(heads, o) * 0.1)

        def forward(self, x):                                # x [B,N,i]
            B, N, _ = x.shape
            h = self.W(x).view(B, N, self.h, self.o)
            es = (h * self.a_src).sum(-1)                    # [B,N,H]
            ed = (h * self.a_dst).sum(-1)
            e = F.leaky_relu(ed[:, :, None, :] + es[:, None, :, :], 0.2)   # [B,dst,src,H]
            e = e.masked_fill(~A[None, :, :, None], float("-inf"))
            alpha = torch.softmax(e, dim=2)
            out = torch.einsum("bijh,bjhf->bihf", alpha, h)
            return out.reshape(B, N, self.h * self.o)

    class Net(nn.Module):
        def __init__(self):
            super().__init__()
            self.g1, self.g2 = GATLayer(f_in, 16), GATLayer(32, 16)
            self.drop = nn.Dropout(0.2)
            self.head = nn.Linear(32 + f_in, 1)              # a skip connection keeps the raw signal

        def forward(self, x):
            h = F.elu(self.g1(x))
            h = F.elu(self.g2(self.drop(h)))
            return self.head(torch.cat([h, x], -1)).squeeze(-1)

    Xt = torch.tensor(X, dtype=torch.float32)
    y = fwd.copy()        # target: the cross-sectional z-score of the forward return per date
    y = (y - np.nanmean(y, 1, keepdims=True)) / np.where(np.nanstd(y, 1, keepdims=True) < 1e-9, 1, np.nanstd(y, 1, keepdims=True))
    yt = torch.tensor(np.nan_to_num(y, nan=0.0), dtype=torch.float32)

    ics, last_scores, shuf_ics = [], [], []
    for seed in seeds:
        torch.manual_seed(seed)
        np.random.seed(seed)
        net = Net()
        opt = torch.optim.Adam(net.parameters(), lr=5e-3, weight_decay=1e-3)
        tr = torch.tensor(train_idx)
        for _ in range(epochs):
            net.train()
            perm = tr[torch.randperm(len(tr))]
            for b in range(0, len(perm), 32):
                idx = perm[b:b + 32]
                loss = F.mse_loss(net(Xt[idx]), yt[idx])
                opt.zero_grad()
                loss.backward()
                opt.step()
        net.eval()
        with torch.no_grad():
            s_test = net(Xt[torch.tensor(test_idx)]).numpy()
            s_last = net(Xt[last_idx:last_idx + 1]).numpy()[0]
        seed_ic = [_spearman(s_test[k], fwd[t]) for k, t in enumerate(test_idx)]
        ics.append(np.nanmean(seed_ic))
        last_scores.append(s_last)
        rng = np.random.default_rng(seed)      # negative control: shuffle the scores across stocks within each date (paper 4.4)
        shuf_ics.append(np.nanmean([_spearman(rng.permutation(s_test[k]), fwd[t]) for k, t in enumerate(test_idx)]))
    return float(np.nanmean(ics)), float(np.nanstd(ics)), np.mean(last_scores, axis=0), float(np.nanmean(shuf_ics))


# ── 5) Orchestration ─────────────────────────────────────────────
def _skipped(msg: str) -> dict:
    return {"graph_context": "", "metrics": {"status": "SKIPPED", "reason": msg}, "report": msg}


def run_graph_analyst(company: str, as_of: str, panel=None, log=print) -> dict:
    """The Graph Analyst for `company` as of `as_of`. `panel` = (close, volume, sectors) skips the price fetch (tests)."""
    if not enabled():
        return _skipped("Graph analyst is off (RAEM_GRAPH_ANALYST=0).")
    if panel is None and not is_dse(company):
        return _skipped(f"Graph skipped: {company} is not a Dhaka Stock Exchange ticker, and the peer universe is DSE only.")
    log(f"building the DSE peer graph for {company} as of {as_of}")
    close, volume, sectors = panel if panel is not None else load_graph_panel(company, as_of, log=log)
    if company not in close.columns or close.shape[1] < 6:
        msg = f"Graph skipped: only {close.shape[1]} usable stocks (need >= 6 incl. {company})."
        log(msg)
        return {**_skipped(msg), "close": close}

    cols = list(close.columns)
    ci = cols.index(company)
    n = len(cols)
    rets = close.pct_change()
    X = build_feature_panel(close, volume)                          # [T,N,F]
    fwd = (close.shift(-GRAPH_HORIZON) / close - 1).to_numpy(copy=True)   # realised forward return (NaN on the last H rows)
    T = len(close)
    last_idx = T - 1
    usable = [t for t in range(60, T - GRAPH_HORIZON) if np.isfinite(fwd[t]).sum() >= max(5, n // 2)]

    adj_final = build_graph(rets, sectors)                          # final graph: all history up to as_of, inference only
    sec = sectors.get(company, "Unknown")
    sector_peers = [cols[j] for j in range(n) if j != ci and adj_final[ci, j] and sectors.get(cols[j]) == sec and sec != "Unknown"]
    corr_all = rets.corr(min_periods=40)[company].drop(company).dropna()
    corr_peers = [p for p in corr_all.abs().sort_values(ascending=False).index[:GRAPH_CORR_TOPK] if abs(corr_all[p]) >= GRAPH_CORR_MIN]

    nb = neighbor_features(X[last_idx], adj_final)
    fi = {k: i for i, k in enumerate(GRAPH_FEATURES)}

    def _ret(h, name):
        return close[name].iloc[-1] / close[name].iloc[-1 - h] - 1 if len(close) > h else np.nan

    sec_ret20 = np.nanmean([_ret(20, p) for p in sector_peers]) if sector_peers else np.nan
    cor_ret20 = np.nanmean([_ret(20, p) for p in corr_peers]) if corr_peers else np.nan
    uni_ret20 = np.nanmean([_ret(20, c) for c in cols if c != company])
    own_ret20 = _ret(20, company)
    metrics = {
        "status": "OK", "n_stocks": n, "sector": sec,
        "sector_peers": sector_peers, "corr_peers": corr_peers,
        "own_ret20_pct": round(100 * own_ret20, 2),
        "sector_peer_ret20_pct": None if np.isnan(sec_ret20) else round(100 * sec_ret20, 2),
        "corr_peer_ret20_pct": None if np.isnan(cor_ret20) else round(100 * cor_ret20, 2),
        "universe_ret20_pct": round(100 * uni_ret20, 2),
        "neighbor_mom5_z": round(float(nb[ci, fi["ret5"]]), 2),
        "neighbor_mom20_z": round(float(nb[ci, fi["ret20"]]), 2),
        "gnn_status": "NOT_RUN",
    }

    gnn_line = "GNN score: not run (torch not installed): graph features only."
    if not gnn_enabled():
        gnn_line = "GNN score: not run (RAEM_GRAPH_GNN=0): graph features only."
        metrics["gnn_status"] = "DISABLED"
    else:
        try:
            import torch  # noqa: F401
            if len(usable) >= GRAPH_HOLDOUT + 60:
                log("training the graph attention network (walk-forward, holdout-validated)")
                test_idx = usable[-GRAPH_HOLDOUT:]
                train_idx = [t for t in usable if t < test_idx[0] - GRAPH_HORIZON]    # purge: train targets must not overlap the holdout window
                adj_train = build_graph(rets.iloc[:test_idx[0]], sectors)              # graph from the training window only
                seeds = list(range(GRAPH_N_SEEDS))
                ic_g, sd_g, _, shuf_ic = train_and_score_gat(X, fwd, adj_train, train_idx, test_idx, last_idx, seeds, GRAPH_EPOCHS, use_graph=True)
                ic_n, sd_n, _, _ = train_and_score_gat(X, fwd, adj_train, train_idx, test_idx, last_idx, seeds, GRAPH_EPOCHS, use_graph=False)
                raw_ic = np.nanmean([_spearman(X[t, :, fi["ret20"]] - X[t, :, fi["vol20"]], fwd[t]) for t in test_idx])   # RAW baseline: momentum minus volatility
                _, _, last_scores, _ = train_and_score_gat(X, fwd, adj_final, list(usable), test_idx[-5:], last_idx, seeds, GRAPH_EPOCHS, use_graph=True)
                z = (last_scores - last_scores.mean()) / (last_scores.std() + 1e-9)
                rank = int((-last_scores).argsort().argsort()[ci]) + 1
                _icn = 0.0 if not np.isfinite(ic_n) else float(ic_n)
                validated = bool(np.isfinite(ic_g) and ic_g > GRAPH_IC_GATE and ic_g > _icn)
                metrics.update({
                    "gnn_status": "VALIDATED" if validated else "NOT_VALIDATED",
                    "gnn_rank": rank, "gnn_score_z": round(float(z[ci]), 2),
                    "holdout_ic_gat": round(ic_g, 4), "holdout_ic_gat_sd": round(sd_g, 4),
                    "holdout_ic_no_graph": round(ic_n, 4), "holdout_ic_raw_baseline": round(float(raw_ic), 4),
                    "holdout_ic_shuffled_control": round(shuf_ic, 4),
                    "holdout_dates": len(test_idx), "n_seeds": len(seeds),
                })
                tilt = "top third (bullish tilt)" if rank <= max(1, n // 3) else ("bottom third (bearish tilt)" if rank > n - max(1, n // 3) else "middle (no tilt)")
                if validated:
                    gnn_line = (f"GNN rank {rank}/{n} across ALL stocks in the graph, NOT a sector rank (z={z[ci]:+.2f}) -> {tilt} for the next {GRAPH_HORIZON} trading days. "
                                f"Holdout rank-IC={ic_g:+.3f} over {len(test_idx)} dates (no-graph {ic_n:+.3f}, raw momentum {raw_ic:+.3f}, "
                                f"shuffled {shuf_ic:+.3f}); IC is small/noisy, treat as weak supporting evidence only.")
                else:
                    gnn_line = (f"GNN score FAILED holdout validation (rank-IC={ic_g:+.3f}; needs > {GRAPH_IC_GATE} AND > the no-graph IC {ic_n:+.3f}); "
                                f"IGNORE it, do not cite the GNN rank as evidence.")
                if validated and raw_ic > ic_g:
                    gnn_line += " Raw momentum had a HIGHER holdout IC than the GNN, so give the momentum evidence more weight."
            else:
                gnn_line = f"GNN not trained: only {len(usable)} usable dates (need >= {GRAPH_HOLDOUT + 60})."
                metrics["gnn_status"] = "INSUFFICIENT_DATA"
        except ImportError:
            pass
        except Exception as e:  # noqa: BLE001
            gnn_line = f"GNN step failed ({str(e)[:120]}): graph features only."
            metrics["gnn_status"] = "ERROR"
            log(f"GNN error: {e}")

    def _fmt(v):
        return "n/a" if v is None else f"{v:+.2f}%"

    ctx = (f"{company} sits in the '{sec}' sector in a {n}-stock DSE peer graph (as of {as_of}). "
           f"Sector peers: {', '.join(sector_peers) or 'none in universe'}; most correlated peers: {', '.join(corr_peers) or 'none'}. "
           f"20-day return: {company} {metrics['own_ret20_pct']:+.2f}% vs sector peers {_fmt(metrics['sector_peer_ret20_pct'])}, "
           f"correlated peers {_fmt(metrics['corr_peer_ret20_pct'])}, whole universe {metrics['universe_ret20_pct']:+.2f}%. "
           f"{gnn_line}")
    log(ctx)
    return {"graph_context": ctx, "metrics": metrics, "report": ctx, "adjacency": adj_final, "nodes": cols, "close": close}
