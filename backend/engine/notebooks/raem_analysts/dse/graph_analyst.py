# raem_analysts/dse/graph_analyst.py -- shared by abbank-dse, batbc-dse, bpml-dse, waltonhil-dse
# The company-specific values come from each notebook's RAEM_PROFILE:
# @@TICKER@@ ticker, @@FILE@@ ticker as used in file names, @@LOG@@ trading-log name, @@SLOT:key@@ lines the profile supplies.
# >>> shared code
# ================================================================
# 🕸️ GRAPH ANALYST — DSE peer graph + small GAT (adapted from BlindTrade, Jeon & Lee 2026)
# ================================================================
# What this adds to RAEM (BlindTrade ideas, scaled down to what a single-stock
# pipeline can actually support):
#   1) Peer graph over a DSE universe: sector edges + return-correlation edges
#   2) Neighbor-aggregated features for COMPANY (explainable, no training)
#   3) A small dense GAT trained walk-forward to rank peers by 5-day forward return
#   4) VALIDATE BEFORE DEPLOYMENT (paper's rule): the GAT score is only passed to the
#      debaters if its holdout rank-IC is > 0. Also reports a no-graph ablation and a
#      shuffled negative control, like the paper's leakage audit.
#
# NOT copied from the paper: PPO-DSR portfolio RL (far too little data here) and the
# LLM-reasoning-embedding edges (only COMPANY has agent reasoning, peers do not).
#
# Output: `graph_result` (dict) and `graph_context` (short string). format_hard_indicators()
# below picks `graph_context` up automatically, so Bull/Bear/Trader/Risk/PM all see it.
#
# Needs only numpy/pandas. The GAT part needs torch (optional): without it the cell
# still runs and gives the graph-feature context only.

import os, pickle, warnings, time as _gtime
import numpy as np
import pandas as pd
from datetime import datetime, timedelta

warnings.filterwarnings("ignore", message="Mean of empty slice")
warnings.filterwarnings("ignore", message="Degrees of freedom <= 0")

# ── Config ───────────────────────────────────────────────────────
GRAPH_PEERS = [
    # the four thesis tickers (the notebook's own company is skipped automatically)
    "ABBANK", "BATBC", "BPML", "WALTONHIL",
    # large / liquid cross-sector names
    "GP", "ROBI", "SQURPHARMA", "RENATA", "BRACBANK", "CITYBANK", "ISLAMIBANK",
    "OLYMPIC", "UPGDCL", "BSRMLTD", "BERGERPBL", "MARICO", "LHB",      # LHB = Lafarge Holcim (LHBL returned 0 rows)
    # Food & Allied (sector peers of @@TICKER@@)
    "UNILEVERCL", "RAHIMAFOOD", "APEXFOODS", "BANGAS", "FUWANGFOOD", "MEGCONMILK", "RDFOOD", "ZEALBANGLA",
    # pharma / chemicals
    "BXPHARMA", "BEACONPHAR", "ACIFORMULA", "ACMELAB", "IBNSINA", "KOHINOOR",
    # banks
    "EBL", "PUBALIBANK", "UCB", "MTB", "PRIMEBANK", "SOUTHEASTB", "DUTCHBANGL", "EXIMBANK",
    # power / fuel / industrials / other
    "POWERGRID", "SUMITPOWER", "JAMUNAOIL", "MJLBD", "LINDEBD", "HEIDELBCEM", "SINGERBD",
    "BEXIMCO", "BATASHOE", "RAKCERAMIC", "TITASGAS", "DESCO",
]
# Tickers are from memory and NOT verified against DSE: any that return <60 rows or <70% coverage
# are dropped automatically (a warning is printed per drop), so a wrong symbol only costs ~1 request.
# The first run fetches ~55 stocks (about 1-2 minutes at 1 request/sec); reruns within
# GRAPH_CACHE_HOURS use the disk cache.
GRAPH_LOOKBACK_DAYS = 450      # calendar days of history (~300 trading days)
GRAPH_HORIZON       = 5        # forward-return horizon (trading days) for the GAT target
GRAPH_HOLDOUT       = 40       # last N usable dates held out for IC validation
GRAPH_CORR_TOPK     = 3        # correlation neighbors per node
GRAPH_CORR_MIN      = 0.15     # ignore weaker correlations than this
GRAPH_N_SEEDS       = 5        # paper used 20; 5 keeps runtime small
GRAPH_EPOCHS        = 80
GRAPH_IC_GATE       = 0.03     # holdout mean rank-IC must exceed this AND the no-graph IC to be VALIDATED (was > 0: IC +0.002 passed as 'bullish tilt')
GRAPH_CACHE_FILE    = "graph_panel_cache.pkl"
GRAPH_CACHE_HOURS   = 12       # reuse fetched prices if run again within this many hours


# ── 1) Price panel ───────────────────────────────────────────────
def _load_graph_panel(company: str, as_of: str):
    """Returns (close_df, volume_df, sectors). Cached on disk because the pipeline is
    often run several times a day and DSE rate-limits (1 request/sec in _dse_get)."""
    if os.path.exists(GRAPH_CACHE_FILE):
        age_h = (_gtime.time() - os.path.getmtime(GRAPH_CACHE_FILE)) / 3600
        if age_h < GRAPH_CACHE_HOURS:
            try:
                with open(GRAPH_CACHE_FILE, "rb") as f:
                    cached = pickle.load(f)
                if (cached.get("as_of") == as_of and cached.get("company") == company
                        and cached.get("peers") == tuple(GRAPH_PEERS)):
                    print(f"   ♻️  Using cached graph panel ({age_h:.1f}h old)")
                    return cached["close"], cached["volume"], cached["sectors"]
            except Exception:
                pass

    end_dt = datetime.strptime(as_of, "%Y-%m-%d")
    start = (end_dt - timedelta(days=GRAPH_LOOKBACK_DAYS)).strftime("%Y-%m-%d")
    tickers = [company] + [t for t in GRAPH_PEERS if t != company]
    closes, vols, sectors = {}, {}, {}
    for sym in tickers:
        df = fetch_dse_ohlcv_range(sym, start, as_of)     # from the DSE data layer cell
        if df is None or df.empty or len(df) < 60:
            print(f"   ⚠️  {sym}: no/insufficient DSE data ({0 if df is None else len(df)} rows) — dropped from graph")
            continue
        closes[sym] = df["Close"].where(df["Close"] > 0)  # parser writes 0.0 for missing
        vols[sym] = df["Volume"]
        try:
            snap = fetch_dse_company_snapshot(sym)
            sectors[sym] = (snap.get("Sector") or "Unknown").strip() or "Unknown"
        except Exception:
            sectors[sym] = "Unknown"
    close = pd.DataFrame(closes).sort_index().ffill(limit=5)
    volume = pd.DataFrame(vols).sort_index().reindex(close.index).fillna(0.0)
    close = close.dropna(axis=1, thresh=int(0.7 * len(close)))   # drop very illiquid names
    volume = volume[close.columns]
    sectors = {k: v for k, v in sectors.items() if k in close.columns}
    try:
        with open(GRAPH_CACHE_FILE, "wb") as f:
            pickle.dump({"as_of": as_of, "company": company, "peers": tuple(GRAPH_PEERS), "close": close,
                         "volume": volume, "sectors": sectors}, f)
    except Exception:
        pass
    return close, volume, sectors


# ── 2) Node features per (date, stock) ───────────────────────────
GRAPH_FEATURES = ["ret1", "ret5", "ret20", "ret60", "vol20", "px_vs_sma50", "rsi14", "vol_ratio"]

def build_feature_panel(close: pd.DataFrame, volume: pd.DataFrame) -> np.ndarray:
    """Returns X [T, N, F], cross-sectionally z-scored per date, NaN -> 0."""
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
    cube = np.stack([feats[k].to_numpy(copy=True) for k in GRAPH_FEATURES], axis=-1).astype(float)  # [T,N,F]
    mu = np.nanmean(cube, axis=1, keepdims=True)
    sd = np.nanstd(cube, axis=1, keepdims=True)
    sd[sd < 1e-9] = 1.0
    z = (cube - mu) / sd
    return np.nan_to_num(z, nan=0.0, posinf=0.0, neginf=0.0)


# ── 3) Graph: sector edges + correlation edges ───────────────────
def build_graph(returns: pd.DataFrame, sectors: dict) -> np.ndarray:
    """Boolean adjacency [N,N] (with self loops). Sector peers fully connected; each node
    also linked to its top-k most correlated stocks (|corr| >= GRAPH_CORR_MIN)."""
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
    """One round of untrained mean message passing: average of neighbors' features
    (self excluded). This is the explainable 'GNN-lite' part."""
    a = adj.copy()
    np.fill_diagonal(a, False)
    deg = a.sum(1, keepdims=True)
    deg[deg == 0] = 1
    return (a.astype(float) @ X_last) / deg


# ── 4) Small dense GAT (optional, needs torch) ───────────────────
def _spearman(a, b) -> float:
    a, b = pd.Series(a), pd.Series(b)
    if a.nunique() < 3 or b.nunique() < 3:
        return np.nan
    return float(a.rank().corr(b.rank()))


def train_and_score_gat(X, fwd, adj, train_idx, test_idx, last_idx, seeds, epochs, use_graph=True):
    """Trains on dates in train_idx; returns (holdout rank-IC per date [array],
    last-date scores [N], shuffled-control IC mean). Dense attention over adj mask."""
    import torch, torch.nn as nn, torch.nn.functional as F
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
            self.head = nn.Linear(32 + f_in, 1)              # skip connection keeps raw signal
        def forward(self, x):
            h = F.elu(self.g1(x))
            h = F.elu(self.g2(self.drop(h)))
            return self.head(torch.cat([h, x], -1)).squeeze(-1)

    Xt = torch.tensor(X, dtype=torch.float32)
    # target: cross-sectional z-score of forward return per date
    y = fwd.copy()
    y = (y - np.nanmean(y, 1, keepdims=True)) / np.where(np.nanstd(y, 1, keepdims=True) < 1e-9, 1, np.nanstd(y, 1, keepdims=True))
    yt = torch.tensor(np.nan_to_num(y, nan=0.0), dtype=torch.float32)

    ics, last_scores, shuf_ics = [], [], []
    for seed in seeds:
        torch.manual_seed(seed); np.random.seed(seed)
        net = Net()
        opt = torch.optim.Adam(net.parameters(), lr=5e-3, weight_decay=1e-3)
        tr = torch.tensor(train_idx)
        for _ in range(epochs):
            net.train()
            perm = tr[torch.randperm(len(tr))]
            for b in range(0, len(perm), 32):
                idx = perm[b:b + 32]
                loss = F.mse_loss(net(Xt[idx]), yt[idx])
                opt.zero_grad(); loss.backward(); opt.step()
        net.eval()
        with torch.no_grad():
            s_test = net(Xt[torch.tensor(test_idx)]).numpy()
            s_last = net(Xt[last_idx:last_idx + 1]).numpy()[0]
        seed_ic = [_spearman(s_test[k], fwd[t]) for k, t in enumerate(test_idx)]
        ics.append(np.nanmean(seed_ic))
        last_scores.append(s_last)
        # negative control: shuffle scores across stocks within each date (paper §4.4)
        rng = np.random.default_rng(seed)
        shuf_ics.append(np.nanmean([_spearman(rng.permutation(s_test[k]), fwd[t])
                                    for k, t in enumerate(test_idx)]))
    return float(np.nanmean(ics)), float(np.nanstd(ics)), np.mean(last_scores, axis=0), float(np.nanmean(shuf_ics))


# ── 5) Orchestration ─────────────────────────────────────────────
def run_graph_analyst(company: str, as_of: str) -> dict:
    print(f"🕸️  Building DSE peer graph for {company} as of {as_of}...")
    close, volume, sectors = _load_graph_panel(company, as_of)
    if company not in close.columns or close.shape[1] < 6:
        msg = f"Graph skipped: only {close.shape[1]} usable stocks (need >= 6 incl. {company})."
        print("   ⚠️ ", msg)
        return {"graph_context": "", "metrics": {"status": "SKIPPED", "reason": msg}, "report": msg}

    cols = list(close.columns)
    ci = cols.index(company)
    n = len(cols)
    rets = close.pct_change()
    X = build_feature_panel(close, volume)                          # [T,N,F]
    fwd = (close.shift(-GRAPH_HORIZON) / close - 1).to_numpy(copy=True)          # realized fwd return (NaN on last H rows)
    T = len(close)
    last_idx = T - 1
    usable = [t for t in range(60, T - GRAPH_HORIZON) if np.isfinite(fwd[t]).sum() >= max(5, n // 2)]

    # final graph (all history up to as_of -- inference only)
    adj_final = build_graph(rets, sectors)
    sec = sectors.get(company, "Unknown")
    sector_peers = [cols[j] for j in range(n) if j != ci and adj_final[ci, j] and sectors.get(cols[j]) == sec and sec != "Unknown"]
    corr_all = rets.corr(min_periods=40)[company].drop(company).dropna()
    corr_peers = [p for p in corr_all.abs().sort_values(ascending=False).index[:GRAPH_CORR_TOPK] if abs(corr_all[p]) >= GRAPH_CORR_MIN]

    # explainable graph features
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

    # GAT with walk-forward holdout validation (needs torch)
    gnn_line = "GNN score: not run (torch not installed) — graph features only."
    try:
        import torch  # noqa: F401
        if len(usable) >= GRAPH_HOLDOUT + 60:
            test_idx = usable[-GRAPH_HOLDOUT:]
            # purge: train targets must not overlap the holdout window (fwd return looks H days ahead)
            train_idx = [t for t in usable if t < test_idx[0] - GRAPH_HORIZON]
            adj_train = build_graph(rets.iloc[:test_idx[0]], sectors)    # graph from training window only
            seeds = list(range(GRAPH_N_SEEDS))
            ic_g, sd_g, _, shuf_ic = train_and_score_gat(X, fwd, adj_train, train_idx, test_idx, last_idx, seeds, GRAPH_EPOCHS, use_graph=True)
            ic_n, sd_n, _, _ = train_and_score_gat(X, fwd, adj_train, train_idx, test_idx, last_idx, seeds, GRAPH_EPOCHS, use_graph=False)
            # RAW baseline: simple momentum-minus-volatility, as in the paper's RAW Top-20
            raw_ic = np.nanmean([_spearman(X[t, :, fi["ret20"]] - X[t, :, fi["vol20"]], fwd[t]) for t in test_idx])
            # final model: retrain on everything usable up to as_of, graph from all history
            full_train = [t for t in usable]
            _, _, last_scores, _ = train_and_score_gat(X, fwd, adj_final, full_train, test_idx[-5:], last_idx, seeds, GRAPH_EPOCHS, use_graph=True)
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
                            f"shuffled {shuf_ic:+.3f}); IC is small/noisy — treat as weak supporting evidence only.")
            else:
                gnn_line = (f"GNN score FAILED holdout validation (rank-IC={ic_g:+.3f}; needs > {GRAPH_IC_GATE} AND > the no-graph IC {ic_n:+.3f}); "
                            f"IGNORE it — do not cite the GNN rank as evidence.")
            if validated and raw_ic > ic_g:
                gnn_line += " Raw momentum had a HIGHER holdout IC than the GNN, so give the momentum evidence more weight."
        else:
            gnn_line = f"GNN not trained: only {len(usable)} usable dates (need >= {GRAPH_HOLDOUT + 60})."
            metrics["gnn_status"] = "INSUFFICIENT_DATA"
    except ImportError:
        pass
    except Exception as e:
        gnn_line = f"GNN step failed ({str(e)[:120]}) — graph features only."
        metrics["gnn_status"] = "ERROR"
        print(f"   ❌ GNN error: {e}")

    def _fmt(v): return "n/a" if v is None else f"{v:+.2f}%"
    ctx = (f"{company} sits in the '{sec}' sector in a {n}-stock DSE peer graph (as of {as_of}). "
           f"Sector peers: {', '.join(sector_peers) or 'none in universe'}; most correlated peers: {', '.join(corr_peers) or 'none'}. "
           f"20-day return: {company} {metrics['own_ret20_pct']:+.2f}% vs sector peers {_fmt(metrics['sector_peer_ret20_pct'])}, "
           f"correlated peers {_fmt(metrics['corr_peer_ret20_pct'])}, whole universe {metrics['universe_ret20_pct']:+.2f}%. "
           f"{gnn_line}")
    report = ctx
    print(f"   {ctx}")
    return {"graph_context": ctx, "metrics": metrics, "report": report,
            "adjacency": adj_final, "nodes": cols}


# ── RUN ──────────────────────────────────────────────────────────
graph_result = run_graph_analyst(COMPANY, TRADE_DATE)
graph_context = graph_result.get("graph_context", "")

log_step(
    "Graph Analyst",
    {"company": COMPANY, "date": TRADE_DATE, "universe": GRAPH_PEERS[:6] + ["..."]},
    {"metrics": graph_result.get("metrics", {}), "report": graph_result.get("report", "")},
)
