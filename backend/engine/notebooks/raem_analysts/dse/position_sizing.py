# raem_analysts/dse/position_sizing.py -- shared by abbank-dse, batbc-dse, bpml-dse, waltonhil-dse
# The company-specific values come from each notebook's RAEM_PROFILE:
# @@TICKER@@ ticker, @@FILE@@ ticker as used in file names, @@LOG@@ trading-log name, @@SLOT:key@@ lines the profile supplies.
# >>> shared code
# ================================================================
# 📐 POSITION SIZING — (A) rule-based vol-target sizing + (B) gated PPO overlay
# ================================================================
# WHY THIS EXISTS (see the RL discussion): the LLM agents decide DIRECTION (BUY/HOLD/SELL).
# This cell decides HOW MUCH, and it runs AFTER the Decision Verifier so it uses the
# verified `effective_signal`. It does not change the agents' decision and it feeds nothing
# back into any LLM prompt (no circularity). The output is an ADVISORY PAPER position
# (fraction of capital in @@TICKER@@), not an order. Long-only: DSE retail cannot short, so
# SELL = go flat.
#
# (A) RULES (always on, explainable)   -- v7 changes marked [v7]:
#     risk cap = min(MAX_WEIGHT, TARGET_VOL / vol) x market_stress_mult x (verifier-FLAGGED haircut)
#       [v7] vol = max(EWMA20, 60-day) so a calm month cannot hide a regime change
#       [v7] market_stress_mult comes from an equal-weight index of the other graph-panel stocks
#            (the DSEX endpoint is dead): index 20d return <= -2.5% -> x0.75, <= -5% -> x0.5
#       [v7] defaults lowered (TARGET_VOL 10%, MAX_WEIGHT 80%) -- at 15%/100% the cap bound almost every
#            calm day, so "sizing" was just "all in"
#     BUY  -> target = risk cap (x RL scale if the overlay validated)
#     SELL -> 0 (exit)
#     HOLD -> [v7] keep the position but TRIM it if it is above the current risk cap (never adds)
#     NO-TRADE BAND: changes smaller than SIZING_BAND are skipped (turnover control, the one
#     thing the RL policy mainly learned in the BlindTrade ablation). Entries below
#     SIZING_MIN_WEIGHT are skipped.
# (B) RL OVERLAY (optional, needs torch): a tiny PPO policy with a Differential-Sharpe reward and
#     transaction costs, trained walk-forward on PRICE features only (no LLM history exists to
#     train on). It picks an exposure scale in {0, 0.5, 1} that multiplies the BUY size. It is
#     only used if it VALIDATES: on a held-out final SIZING_HOLDOUT days it must beat the plain
#     vol-target baseline (net of costs) with a positive Sharpe in most seeds. Otherwise scale = 1.
#     With ~60 holdout days the Sharpe is very noisy (SE ~ 2) -- the gate is a safety check,
#     not proof of skill, and it will often (correctly) say NOT_VALIDATED.
import os, json, warnings
import numpy as np
import pandas as pd
from datetime import datetime, timedelta

warnings.filterwarnings("ignore", message="Mean of empty slice")

# ── Config ───────────────────────────────────────────────────────
SIZING_TARGET_VOL_ANN = 0.10     # annualised vol the position is scaled to   [v7: was 0.15 -- TUNE with the price-only backtest]
SIZING_MAX_WEIGHT     = 0.80     # max fraction of capital in this stock       [v7: was 1.0  -- TUNE with the price-only backtest]
SIZING_VOL_LONG_SPAN  = 60       # [v7] slow vol window (days); vol used = max(EWMA20, this)
USE_MARKET_STRESS     = True     # [v7] ablation switch: False = no market-stress scaling
STRESS_WEAK_RET20     = -0.025   # [v7] equal-weight index 20-trading-day return at/below this -> WEAK
STRESS_RET20          = -0.05    # [v7] ... at/below this -> STRESS
STRESS_WEAK_MULT      = 0.75     # [v7] size multiplier in WEAK
STRESS_MULT           = 0.50     # [v7] size multiplier in STRESS
STRESS_MIN_PEERS      = 15       # [v7] need at least this many other stocks to trust the index
SIZING_REBALANCE_ON_HOLD = True  # [v7] HOLD may trim a position that exceeds the risk cap (never adds)
USE_RL_OVERLAY        = True     # [v7] False skips the PPO overlay entirely (faster; it rarely validates)
RUN_SIZING_PRICE_BACKTEST = False  # [v7] True = LLM-free backtest of old vs new sizing on the 51-stock panel
SIZING_MIN_WEIGHT     = 0.10     # don't open a position smaller than this
SIZING_BAND           = 0.20     # no-trade band (abs change in weight) for adjustments
SIZING_FLAGGED_HAIRCUT = 0.5     # multiply the risk cap if the Decision Verifier FLAGGED the decision [v7: also applies to HOLD trims]
SIZING_COST           = 0.005    # ONE-WAY cost fraction (PLACEHOLDER -- set to your broker's real one-way cost)
SIZING_LOOKBACK_DAYS  = 900      # calendar days of price history (~600 trading days)
SIZING_HOLDOUT        = 60       # last N trading days held out to validate the RL overlay
SIZING_RL_SEEDS       = 5
SIZING_RL_ITERS       = 60       # PPO updates per seed (each = one 120-day episode)
SIZING_RL_ACTIONS     = [0.0, 0.5, 1.0]
SIZING_STATE_FILE     = "position_state_@@TICKER@@.json"
_TD = 250                        # trading days per year (annualisation)


# ── helpers ──────────────────────────────────────────────────────
def _sz_prices(company: str, as_of: str):
    start = (datetime.strptime(as_of, "%Y-%m-%d") - timedelta(days=SIZING_LOOKBACK_DAYS)).strftime("%Y-%m-%d")
    try:
        df = fetch_dse_ohlcv_range(company, start, as_of)
    except Exception as e:
        print(f"   ⚠️ sizing: price fetch failed: {str(e)[:120]}")
        return None
    if df is None or df.empty:
        return None
    c = df["Close"].where(df["Close"] > 0).dropna()
    return c if len(c) >= 150 else None


def _sz_load_state() -> list:
    try:
        with open(SIZING_STATE_FILE, "r", encoding="utf-8") as f:
            return list(json.load(f).get("history", []))
    except Exception:
        return []


def _sz_save_state(history: list):
    try:
        with open(SIZING_STATE_FILE, "w", encoding="utf-8") as f:
            json.dump({"note": "advisory PAPER position (fraction of capital) per trade date", "history": history}, f, indent=1)
    except Exception as e:
        print(f"   ⚠️ sizing: could not save state: {e}")


def _sz_recompute_equity(history: list) -> list:
    """[v7] Paper equity curve of the advisory positions: the weight held from one decision date to the next earns
    the stock's return between those dates, minus SIZING_COST on every weight change. Recomputed from scratch on each
    save, so re-running an older date never leaves stale numbers. Entries without a stored price (written by older
    versions) restart the chain. Decision-date-to-decision-date only -- NOT a daily marked equity."""
    eq, prev = 1.0, None
    for h in history:
        p, w = h.get("price"), float(h.get("weight", 0.0))
        if not p or not p > 0:
            h.pop("equity", None); eq, prev = 1.0, None
            continue
        if prev is None:
            eq *= (1 - SIZING_COST * w)
        else:
            eq *= (1 + prev["w"] * (p / prev["p"] - 1) - SIZING_COST * abs(w - prev["w"]))
        h["equity"] = round(eq, 5)
        prev = {"p": float(p), "w": w}
    return history


def _sz_current_weight(history: list, as_of: str) -> float:
    """Position BEFORE today's decision: last entry strictly before as_of (re-runs on the same day
    never compound on each other)."""
    prev = [h for h in history if h.get("date", "") < as_of]
    return float(prev[-1]["weight"]) if prev else 0.0


def _sz_vol_series(close: pd.Series) -> pd.Series:
    """[v7] annualised vol = max(EWMA20, 60-day std). np.fmax ignores NaN, so early rows fall back to EWMA20."""
    r = close.pct_change()
    fast = r.ewm(span=20, adjust=False).std()
    slow = r.rolling(SIZING_VOL_LONG_SPAN, min_periods=30).std()
    return pd.Series(np.fmax(fast.to_numpy(), slow.to_numpy()) * np.sqrt(_TD), index=close.index)


def _sz_realised_vol_ann(close: pd.Series) -> float:
    v = _sz_vol_series(close).iloc[-1]
    return float(v) if np.isfinite(v) and v > 0 else float("nan")


def _sz_vol_parts(close: pd.Series):
    """(EWMA20 vol, 60-day vol), annualised -- only for the printed report."""
    r = close.pct_change()
    f = r.ewm(span=20, adjust=False).std().iloc[-1] * np.sqrt(_TD)
    sl = r.rolling(SIZING_VOL_LONG_SPAN, min_periods=30).std().iloc[-1] * np.sqrt(_TD)
    return (float(f) if np.isfinite(f) else None), (float(sl) if np.isfinite(sl) else None)


def _stress_level(ret20: float):
    if ret20 <= STRESS_RET20:
        return "STRESS", STRESS_MULT
    if ret20 <= STRESS_WEAK_RET20:
        return "WEAK", STRESS_WEAK_MULT
    return "NORMAL", 1.0


def market_stress_from_panel(close_df, company: str, as_of: str) -> dict:
    """[v7] Market-stress thermometer from an equal-weight index of the OTHER panel stocks (company excluded, so
    the index does not contain the stock being sized). Uses rows <= as_of only. Pure function -> unit-testable."""
    out = {"status": "UNAVAILABLE", "level": "UNKNOWN", "mult": 1.0}
    try:
        if close_df is None or len(close_df) == 0:
            out["reason"] = "no panel"; return out
        px = close_df.drop(columns=[company], errors="ignore").copy()
        px.index = pd.to_datetime(px.index)
        px = px.loc[:pd.Timestamp(as_of)]
        px = px.dropna(axis=1, thresh=int(0.7 * max(len(px), 1)))
        if px.shape[1] < STRESS_MIN_PEERS or len(px) < 25:
            out["reason"] = f"only {px.shape[1]} stocks / {len(px)} days"; return out
        if (pd.Timestamp(as_of) - px.index[-1]).days > 7:
            out["reason"] = f"panel is stale (last row {px.index[-1].date()})"; return out
        r = px.pct_change(fill_method=None)
        lvl = (1 + r.mean(axis=1, skipna=True).fillna(0.0)).cumprod()
        ret20 = float(lvl.iloc[-1] / lvl.iloc[-21] - 1)
        breadth_down = float(((px.iloc[-1] / px.iloc[-21] - 1) < 0).mean())
        level, mult = _stress_level(ret20)
        out.update({"status": "OK", "level": level, "mult": mult, "index_ret20_pct": round(ret20 * 100, 2),
                    "breadth_down_pct": round(breadth_down * 100, 0), "n_stocks": int(px.shape[1]),
                    "last_row": str(px.index[-1].date())})
    except Exception as e:
        out["reason"] = str(e)[:100]
    return out


def _sz_features(close: pd.Series) -> np.ndarray:
    """Causal features only (rolling windows, no global statistics -> no look-ahead). [T, 6]"""
    r1 = close.pct_change()
    v20 = r1.rolling(20, min_periods=10).std().replace(0, np.nan)
    v60 = r1.rolling(60, min_periods=20).std().replace(0, np.nan)
    delta = close.diff()
    gain = delta.clip(lower=0).rolling(14, min_periods=5).mean()
    loss = (-delta.clip(upper=0)).rolling(14, min_periods=5).mean()
    rsi = (100 - 100 / (1 + gain / loss.replace(0, np.nan))) / 100 - 0.5
    f = pd.DataFrame({
        "r1": r1 / v20,
        "r5": close.pct_change(5) / (v20 * np.sqrt(5)),
        "r20": close.pct_change(20) / (v20 * np.sqrt(20)),
        "trend": (close / close.rolling(50, min_periods=20).mean() - 1) / (v20 * np.sqrt(50)),
        "rsi": rsi * 2,
        "volreg": (v20 / v60 - 1),
    }, index=close.index)
    return np.nan_to_num(f.clip(-3, 3).to_numpy(copy=True), nan=0.0)


def _sz_metrics(net: np.ndarray, turnover: float) -> dict:
    net = np.asarray(net, dtype=float)
    sd = net.std(ddof=1) if len(net) > 2 else np.nan
    sharpe = float(net.mean() / sd * np.sqrt(_TD)) if sd and np.isfinite(sd) and sd > 1e-12 else 0.0
    eq = np.cumprod(1 + net)
    mdd = float((eq / np.maximum.accumulate(eq) - 1).min()) if len(eq) else 0.0
    return {"sharpe": round(sharpe, 3), "total_return_pct": round(float(eq[-1] - 1) * 100, 2) if len(eq) else 0.0,
            "max_drawdown_pct": round(mdd * 100, 2), "turnover": round(float(turnover), 2)}


def _sz_simulate(weights: np.ndarray, rn: np.ndarray):
    """weights[t] = position held from close t to close t+1; rn[t] = next-day return. Net of costs."""
    prev = np.concatenate([[0.0], weights[:-1]])
    turn = np.abs(weights - prev)
    net = weights * rn - SIZING_COST * turn
    return net, float(turn.sum())


def _sz_voltarget_weights(close: pd.Series, idx: np.ndarray) -> np.ndarray:
    """Always-long vol-target baseline WITH the same no-trade band, evaluated on dates idx."""
    r = close.pct_change()
    vol = _sz_vol_series(close).to_numpy()                      # [v7] same vol definition as the live rule
    w, cur = [], 0.0
    for t in idx:
        tgt = float(np.clip(SIZING_TARGET_VOL_ANN / vol[t], 0, SIZING_MAX_WEIGHT)) if np.isfinite(vol[t]) and vol[t] > 0 else cur
        if cur == 0.0 and tgt >= SIZING_MIN_WEIGHT:
            cur = tgt
        elif cur > 0.0 and abs(tgt - cur) >= SIZING_BAND:
            cur = tgt
        w.append(cur)
    return np.array(w)


# ── (B) PPO overlay ──────────────────────────────────────────────
def _sz_train_ppo(F: np.ndarray, rn: np.ndarray, t_lo: int, t_hi: int, seed: int, iters: int):
    """Trains on days [t_lo, t_hi) (rn must be valid there). Returns a policy callable
    probs(feature_row, prev_pos) -> action probabilities."""
    import torch, torch.nn as nn
    from torch.distributions import Categorical
    torch.manual_seed(seed); rng = np.random.default_rng(seed)
    nF, nA = F.shape[1] + 1, len(SIZING_RL_ACTIONS)
    pi = nn.Sequential(nn.Linear(nF, 16), nn.Tanh(), nn.Linear(16, nA))
    vf = nn.Sequential(nn.Linear(nF, 16), nn.Tanh(), nn.Linear(16, 1))
    opt = torch.optim.Adam(list(pi.parameters()) + list(vf.parameters()), lr=3e-3)
    EP, gamma, lam, eta = 120, 0.99, 0.95, 0.02

    for _ in range(iters):
        s0 = int(rng.integers(t_lo, max(t_lo + 1, t_hi - EP)))
        obs, acts, lps, rews, vals = [], [], [], [], []
        prev, A, B = 0.0, 0.0, 1e-4                        # DSR moments (B init = 1% daily vol)
        for t in range(s0, min(s0 + EP, t_hi)):
            o = torch.tensor(np.append(F[t], prev), dtype=torch.float32)
            with torch.no_grad():
                d = Categorical(logits=pi(o)); a = d.sample(); lp = d.log_prob(a); v = vf(o).squeeze()
            pos = SIZING_RL_ACTIONS[int(a)]
            net = pos * rn[t] - SIZING_COST * abs(pos - prev)
            dA, dB = net - A, net * net - B               # Differential Sharpe Ratio (Moody & Saffell)
            den = (B - A * A) ** 1.5
            r = float(np.clip((B * dA - 0.5 * A * dB) / den, -5, 5)) if den > 1e-12 else 0.0
            A, B = A + eta * dA, B + eta * dB
            obs.append(o); acts.append(a); lps.append(lp); rews.append(r); vals.append(v); prev = pos
        n = len(rews)
        if n < 10:
            continue
        vals_t = torch.stack(vals).detach().numpy(); last_v = 0.0
        adv, gae = np.zeros(n), 0.0
        for i in reversed(range(n)):
            nv = vals_t[i + 1] if i + 1 < n else last_v
            delta = rews[i] + gamma * nv - vals_t[i]
            gae = delta + gamma * lam * gae; adv[i] = gae
        ret = torch.tensor(adv + vals_t, dtype=torch.float32)
        advt = torch.tensor((adv - adv.mean()) / (adv.std() + 1e-8), dtype=torch.float32)
        O, Ac, LP = torch.stack(obs), torch.stack(acts), torch.stack(lps)
        for _k in range(4):                                # PPO epochs
            d = Categorical(logits=pi(O))
            ratio = torch.exp(d.log_prob(Ac) - LP)
            loss_pi = -torch.min(ratio * advt, torch.clamp(ratio, 0.8, 1.2) * advt).mean() - 0.01 * d.entropy().mean()
            loss_v = ((vf(O).squeeze(-1) - ret) ** 2).mean()
            opt.zero_grad(); (loss_pi + 0.5 * loss_v).backward(); opt.step()

    def probs(row, prev_pos):
        with torch.no_grad():
            return torch.softmax(pi(torch.tensor(np.append(row, prev_pos), dtype=torch.float32)), -1).numpy()
    return probs


def _sz_eval_policy(probs, F, rn, idx):
    w, prev = [], 0.0
    for t in idx:
        prev = SIZING_RL_ACTIONS[int(np.argmax(probs(F[t], prev)))]
        w.append(prev)
    return np.array(w)


def _sz_run_rl(close: pd.Series, cur_w: float) -> dict:
    out = {"status": "NOT_RUN", "scale": 1.0}
    try:
        import torch  # noqa: F401
    except ImportError:
        out["reason"] = "torch not installed"; return out
    try:
        F = _sz_features(close)
        rn = close.pct_change().shift(-1).to_numpy(copy=True)          # next-day return (NaN on last row)
        T = len(close)
        t_hold0 = T - 1 - SIZING_HOLDOUT                                # holdout days: [t_hold0, T-1)
        t_lo, t_hi = 60, t_hold0 - 1                                    # purge: last train day's next-day return < holdout start
        if t_hi - t_lo < 200:
            out["status"] = "INSUFFICIENT_DATA"; out["reason"] = f"only {t_hi - t_lo} training days (need >= 200)"; return out
        hold_idx = np.arange(t_hold0, T - 1)
        base_w = _sz_voltarget_weights(close, hold_idx)
        bh_net, bh_turn = _sz_simulate(np.ones(len(hold_idx)), rn[hold_idx])
        base_net, base_turn = _sz_simulate(base_w, rn[hold_idx])
        base_m, bh_m = _sz_metrics(base_net, base_turn), _sz_metrics(bh_net, bh_turn)

        seed_m = []
        for s in range(SIZING_RL_SEEDS):
            pr = _sz_train_ppo(F, rn, t_lo, t_hi, s, SIZING_RL_ITERS)
            w = _sz_eval_policy(pr, F, rn, hold_idx)
            net, turn = _sz_simulate(w, rn[hold_idx])
            seed_m.append(_sz_metrics(net, turn))
        rl_sh = [m["sharpe"] for m in seed_m]
        mean_sh, beat = float(np.mean(rl_sh)), float(np.mean([x > base_m["sharpe"] for x in rl_sh]))
        validated = mean_sh > base_m["sharpe"] and mean_sh > 0 and beat >= 0.6
        se = float(np.sqrt(_TD / SIZING_HOLDOUT))
        out.update({
            "status": "VALIDATED" if validated else "NOT_VALIDATED",
            "holdout_days": len(hold_idx), "sharpe_se_approx": round(se, 2),
            "rl_sharpe_mean": round(mean_sh, 3), "rl_sharpe_per_seed": rl_sh, "rl_seeds_beating_baseline": round(beat, 2),
            "rl_turnover_mean": round(float(np.mean([m["turnover"] for m in seed_m])), 2),
            "voltarget_baseline": base_m, "buy_and_hold": bh_m,
        })
        if validated:                                                  # final policies: retrain on ALL history
            ps = [_sz_train_ppo(F, rn, t_lo, T - 1, s, SIZING_RL_ITERS) for s in range(SIZING_RL_SEEDS)]
            p = np.mean([pp(F[-1], float(np.clip(cur_w, 0, 1))) for pp in ps], axis=0)
            out["scale"] = float(SIZING_RL_ACTIONS[int(np.argmax(p))])
            out["action_probs"] = [round(float(x), 2) for x in p]
    except Exception as e:
        out.update({"status": "ERROR", "scale": 1.0, "reason": str(e)[:150]})
        print(f"   ❌ RL overlay error: {e}")
    return out


# ── main ─────────────────────────────────────────────────────────
def run_position_sizing(company: str, as_of: str, verification: dict) -> dict:
    sig = str((verification or {}).get("effective_signal") or (verification or {}).get("final_signal") or "HOLD").upper()
    sig = sig if sig in ("BUY", "SELL", "HOLD") else "HOLD"
    flagged = str((verification or {}).get("status", "")).upper() == "FLAGGED"
    history = _sz_load_state()
    cur_w = _sz_current_weight(history, as_of)
    res = {"company": company, "as_of": as_of, "signal": sig, "verifier_flagged": flagged, "current_weight": round(cur_w, 3),
           "disclaimer": "advisory PAPER position (fraction of capital), long-only -- not an order"}

    close = _sz_prices(company, as_of)       # last close on or before as_of (decision assumed to be taken after that close)
    if close is None:
        res.update({"status": "SKIPPED", "reason": "not enough DSE price history", "final_weight": cur_w, "action": "NO_CHANGE", "rl": {"status": "NOT_RUN"}})
        return res
    vol = _sz_realised_vol_ann(close)
    res["realised_vol_ann_pct"] = round(vol * 100, 1) if np.isfinite(vol) else None
    f_vol, s_vol = _sz_vol_parts(close)
    res["vol_ewma20_pct"] = round(f_vol * 100, 1) if f_vol is not None else None
    res["vol_60d_pct"] = round(s_vol * 100, 1) if s_vol is not None else None

    # [v7] market-stress thermometer (equal-weight index of the other graph-panel stocks)
    stress = {"status": "OFF", "level": "OFF", "mult": 1.0}
    if USE_MARKET_STRESS:
        try:
            panel_close, _v, _s = _load_graph_panel(company, as_of)
            stress = market_stress_from_panel(panel_close, company, as_of)
        except Exception as e:
            stress = {"status": "UNAVAILABLE", "level": "UNKNOWN", "mult": 1.0, "reason": str(e)[:100]}
    res["market_stress"] = stress

    use_rl = USE_RL_OVERLAY and sig == "BUY"
    rl = _sz_run_rl(close, cur_w) if use_rl else {"status": "NOT_RUN", "scale": 1.0,
                                                  "reason": f"signal is {sig}; overlay only scales BUY" if USE_RL_OVERLAY else "USE_RL_OVERLAY=False"}
    res["rl"] = rl

    cap_w = None                                                      # [v7] risk cap, computed on EVERY run
    if np.isfinite(vol):
        cap_w = min(SIZING_MAX_WEIGHT, SIZING_TARGET_VOL_ANN / vol) * float(stress.get("mult", 1.0))
        if flagged:
            cap_w *= SIZING_FLAGGED_HAIRCUT
    res["risk_cap_weight"] = round(float(cap_w), 3) if cap_w is not None else None

    if sig == "BUY" and cap_w is not None:
        rule_w = cap_w
        target = rule_w * (rl["scale"] if rl.get("status") == "VALIDATED" else 1.0)
    elif sig == "SELL":
        rule_w, target = 0.0, 0.0
    elif sig == "HOLD" and SIZING_REBALANCE_ON_HOLD and cur_w > 0 and cap_w is not None:
        rule_w, target = cap_w, min(cur_w, cap_w)                    # [v7] HOLD can only trim
    else:
        rule_w, target = cur_w, cur_w
    res["rule_weight"] = round(float(rule_w), 3)
    res["target_before_band"] = round(float(target), 3)

    # no-trade band
    final = cur_w
    if sig == "SELL":
        final = 0.0
    elif cur_w == 0.0:
        final = target if target >= SIZING_MIN_WEIGHT else 0.0
    elif abs(target - cur_w) >= SIZING_BAND or target == 0.0:
        final = target
    if sig != "BUY" and 0.0 < final < SIZING_MIN_WEIGHT:             # [v7] a trim must not leave a dust position
        final = 0.0
    if final > cur_w + 1e-9:
        action = "ENTER" if cur_w == 0.0 else "ADD"
    elif final < cur_w - 1e-9:
        action = "EXIT" if final == 0.0 else "TRIM"
    else:
        action = "HOLD_POSITION" if cur_w > 0 else "STAY_FLAT"
    final = float(min(max(final, 0.0), SIZING_MAX_WEIGHT))
    res.update({"status": "OK", "final_weight": round(final, 3), "action": action})

    entry = {"date": as_of, "weight": round(final, 4), "signal": sig, "action": action, "price": round(float(close.iloc[-1]), 4)}
    history = _sz_recompute_equity(sorted([h for h in history if h.get("date") != as_of] + [entry], key=lambda h: h["date"]))
    _sz_save_state(history)
    res["paper_equity"] = next((h.get("equity") for h in history if h.get("date") == as_of), None)
    return res


def _sz_format(res: dict) -> str:
    rl = res.get("rl", {})
    lines = [f"📐 POSITION SIZING — {res['company']} | {res['as_of']}  (advisory paper position, long-only)",
             f"   Signal (verified): {res['signal']}{'  [verifier FLAGGED -> cap x' + str(SIZING_FLAGGED_HAIRCUT) + ']' if res.get('verifier_flagged') else ''}"]
    if res.get("status") != "OK":
        lines.append(f"   ⚠️ {res.get('status')}: {res.get('reason', '')}")
        return "\n".join(lines)
    st = res.get("market_stress", {})
    if st.get("status") == "OK":
        mline = (f"   Market stress: {st['level']} (equal-weight index of {st['n_stocks']} other stocks: 20d {st['index_ret20_pct']:+.1f}%, "
                 f"{st['breadth_down_pct']:.0f}% of them down) -> size x{st['mult']}")
    elif st.get("status") == "OFF":
        mline = "   Market stress: OFF (USE_MARKET_STRESS=False)"
    else:
        mline = f"   Market stress: UNAVAILABLE ({st.get('reason', '')}) -> no scaling applied"
    lines += [f"   Vol used (max of EWMA20 {res.get('vol_ewma20_pct')}% / 60d {res.get('vol_60d_pct')}%): {res.get('realised_vol_ann_pct')}%",
              mline,
              f"   Risk cap {res.get('risk_cap_weight') if res.get('risk_cap_weight') is None else format(res['risk_cap_weight'], '.0%')}  ->  rule weight {res['rule_weight']:.0%}",
              f"   RL overlay: {rl.get('status')}" + (f" (scale x{rl['scale']})" if rl.get("status") == "VALIDATED" else ""),
              f"   Current {res['current_weight']:.0%}  ->  target {res['target_before_band']:.0%}  ->  after no-trade band {res['final_weight']:.0%}   => {res['action']}"]
    if res.get("paper_equity") is not None:
        lines.append(f"   Paper equity of the advisory positions (decision-date to decision-date, after cost): {res['paper_equity']:.3f}")
    if rl.get("rl_sharpe_mean") is not None:
        b = rl["voltarget_baseline"]
        lines.append(f"   RL holdout ({rl['holdout_days']}d, Sharpe SE ~{rl['sharpe_se_approx']}): RL {rl['rl_sharpe_mean']:+.2f} (beat baseline in {rl['rl_seeds_beating_baseline']:.0%} of seeds) "
                     f"vs vol-target {b['sharpe']:+.2f} vs buy&hold {rl['buy_and_hold']['sharpe']:+.2f}")
    return "\n".join(lines)


# ── [v7] LLM-free backtest: old sizing vs new sizing vs buy&hold, split by market regime ──
def sizing_price_only_backtest(close_df, company: str, start_after: int = 120, up_thr: float = 0.03, down_thr: float = -0.03,
                               verbose: bool = True) -> dict:
    """Stand-in signal = 'always BUY'. Compares (old) 15%/100%/EWMA20 sizing, (new) the v7 rule with and without
    the market-stress multiplier, and buy&hold. Segments days by the equal-weight index's trailing 60-day return
    (UP >= +3%, DOWN <= -3%, else FLAT). Needs only prices -> can use the full 2-year panel. Shows whether the
    thermometer helps in DOWN markets and what it costs in UP markets -- the FINSABER-style split."""
    px = close_df.copy(); px.index = pd.to_datetime(px.index)
    c = px[company].dropna()
    others = px.drop(columns=[company], errors="ignore")
    lvl = (1 + others.pct_change(fill_method=None).mean(axis=1, skipna=True).fillna(0.0)).cumprod().reindex(c.index).ffill()
    ret20, ret60 = (lvl / lvl.shift(20) - 1).to_numpy(), (lvl / lvl.shift(60) - 1).to_numpy()
    rn = c.pct_change().shift(-1).to_numpy(copy=True)
    vol_old = (c.pct_change().ewm(span=20, adjust=False).std() * np.sqrt(_TD)).to_numpy()
    vol_new = _sz_vol_series(c).to_numpy()
    idx = np.arange(start_after, len(c) - 1)

    def banded(target_fn):
        w, cur = [], 0.0
        for t in idx:
            tgt = target_fn(t)
            if cur == 0.0 and tgt >= SIZING_MIN_WEIGHT:
                cur = tgt
            elif cur > 0.0 and abs(tgt - cur) >= SIZING_BAND:
                cur = tgt
            w.append(cur)
        return np.array(w)

    def mult(t):
        return _stress_level(ret20[t])[1] if np.isfinite(ret20[t]) else 1.0
    strat = {
        "buy&hold": np.ones(len(idx)),
        "old (15%/100%/EWMA20)": banded(lambda t: float(np.clip(0.15 / vol_old[t], 0, 1.0)) if np.isfinite(vol_old[t]) and vol_old[t] > 0 else 0.0),
        "new, no stress": banded(lambda t: min(SIZING_MAX_WEIGHT, SIZING_TARGET_VOL_ANN / vol_new[t]) if np.isfinite(vol_new[t]) and vol_new[t] > 0 else 0.0),
        "new + stress": banded(lambda t: min(SIZING_MAX_WEIGHT, SIZING_TARGET_VOL_ANN / vol_new[t]) * mult(t) if np.isfinite(vol_new[t]) and vol_new[t] > 0 else 0.0),
    }
    seg = np.where(ret60[idx] >= up_thr, "UP", np.where(ret60[idx] <= down_thr, "DOWN", "FLAT"))
    seg = np.where(np.isfinite(ret60[idx]), seg, "FLAT")
    rows = []
    for name, w in strat.items():
        net, turn = _sz_simulate(w, rn[idx])
        for label in ("ALL", "UP", "FLAT", "DOWN"):
            m = np.ones(len(idx), bool) if label == "ALL" else (seg == label)
            if m.sum() < 5:
                rows.append({"strategy": name, "market": label, "days": int(m.sum()), "return_pct": np.nan, "max_dd_pct": np.nan, "avg_weight": np.nan}); continue
            eq = np.cumprod(1 + net[m])
            rows.append({"strategy": name, "market": label, "days": int(m.sum()), "return_pct": round(float(eq[-1] - 1) * 100, 2),
                         "max_dd_pct": round(float((eq / np.maximum.accumulate(eq) - 1).min()) * 100, 2), "avg_weight": round(float(w[m].mean()), 2)})
    df = pd.DataFrame(rows)
    if verbose:
        print("📊 Sizing price-only backtest (stand-in signal: always BUY; cost applied; segments = index 60d return)")
        print(df.pivot(index="strategy", columns="market", values="return_pct")[["ALL", "UP", "FLAT", "DOWN"]].to_string())
        print("   (segment days: " + ", ".join(f"{k}={int((seg == k).sum())}" for k in ("UP", "FLAT", "DOWN")) + "; returns compound only the days inside each segment)")
    return {"table": df, "segment_days": {k: int((seg == k).sum()) for k in ("UP", "FLAT", "DOWN")}}


# ── RUN ──
print("=" * 60)
sizing_result = run_position_sizing(COMPANY, TRADE_DATE, verification_result if "verification_result" in dir() else {})
print(_sz_format(sizing_result))
print("=" * 60 + "\n")

if RUN_SIZING_PRICE_BACKTEST:
    try:
        _pc, _pv, _ps = _load_graph_panel(COMPANY, TRADE_DATE)
        sizing_backtest_result = sizing_price_only_backtest(_pc, COMPANY)
    except Exception as _e:
        print(f"   ⚠️ sizing price-only backtest skipped: {str(_e)[:120]}")

try:
    session_log["summary_table"]["position_sizing"] = {
        k: sizing_result.get(k) for k in ("signal", "current_weight", "risk_cap_weight", "rule_weight", "final_weight", "action", "status", "paper_equity")
    } | {"market_stress": sizing_result.get("market_stress", {}).get("level"), "stress_mult": sizing_result.get("market_stress", {}).get("mult"),
        "rl_status": sizing_result.get("rl", {}).get("status"), "rl_scale": sizing_result.get("rl", {}).get("scale")}
except Exception:
    pass
log_step("Position Sizing (rule + RL overlay)",
         {"effective_signal": sizing_result.get("signal"), "verifier_flagged": sizing_result.get("verifier_flagged"),
          "current_weight": sizing_result.get("current_weight")},
         {"sizing": sizing_result, "report": _sz_format(sizing_result)})
