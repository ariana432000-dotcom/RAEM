/* ---------- extra pages ---------- */
const NAVS = [["dash", "Home", "dash"], ["ai", "AI Analysis", "line"], ["watch", "Watchlist", "star"], ["inv", "Return Calculator", "pulse"], ["bt", "Backtest", "chart"], ["data", "Dataset Explorer", "db"], ["news", "News", "news"], ["fund", "Fundamentals Check", "check"], ["papers", "Research Papers", "file"], ["mem", "Memory", "brain"], ["set", "Settings", "gear"], ["help", "Help", "help"]];
Object.assign(ICONS, {
  star: '<path d="M12 2l3.09 6.26L22 9.27l-5 4.87 1.18 6.88L12 17.77l-6.18 3.25L7 14.14 2 9.27l6.91-1.01z"/>',
  wallet: '<path d="M19 7V4a1 1 0 0 0-1-1H5a2 2 0 0 0 0 4h15a1 1 0 0 1 1 1v4h-3a2 2 0 0 0 0 4h3a1 1 0 0 0 1-1v-2a1 1 0 0 0-1-1"/><path d="M3 5v14a2 2 0 0 0 2 2h15a1 1 0 0 0 1-1v-4"/>',
  news: '<path d="M4 22h16a2 2 0 0 0 2-2V4a2 2 0 0 0-2-2H8a2 2 0 0 0-2 2v16a2 2 0 0 1-2 2Zm0 0a2 2 0 0 1-2-2v-9c0-1.1.9-2 2-2h2"/><path d="M18 14h-8M15 18h-5M10 6h8v4h-8z"/>',
  check: '<path d="M16 4h2a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h2"/><rect x="8" y="2" width="8" height="4" rx="1"/><path d="m9 14 2 2 4-4"/>',
  file: '<path d="M15 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7Z"/><path d="M14 2v4a2 2 0 0 0 2 2h4M10 13h4M10 17h4"/>',
  gear: '<path d="M12.22 2h-.44a2 2 0 0 0-2 2v.18a2 2 0 0 1-1 1.73l-.43.25a2 2 0 0 1-2 0l-.15-.08a2 2 0 0 0-2.73.73l-.22.38a2 2 0 0 0 .73 2.73l.15.1a2 2 0 0 1 1 1.72v.51a2 2 0 0 1-1 1.74l-.15.09a2 2 0 0 0-.73 2.73l.22.38a2 2 0 0 0 2.73.73l.15-.08a2 2 0 0 1 2 0l.43.25a2 2 0 0 1 1 1.73V20a2 2 0 0 0 2 2h.44a2 2 0 0 0 2-2v-.18a2 2 0 0 1 1-1.73l.43-.25a2 2 0 0 1 2 0l.15.08a2 2 0 0 0 2.73-.73l.22-.39a2 2 0 0 0-.73-2.73l-.15-.08a2 2 0 0 1-1-1.74v-.5a2 2 0 0 1 1-1.74l.15-.09a2 2 0 0 0 .73-2.73l-.22-.38a2 2 0 0 0-2.73-.73l-.15.08a2 2 0 0 1-2 0l-.43-.25a2 2 0 0 1-1-1.73V4a2 2 0 0 0-2-2z"/><circle cx="12" cy="12" r="3"/>',
});
const lsGet = (k, d) => { try { const v = localStorage.getItem(k); return v ? JSON.parse(v) : d; } catch (e) { return d; } };
const lsSet = (k, v) => { try { localStorage.setItem(k, JSON.stringify(v)); } catch (e) { /* storage unavailable */ } };
const lsDel = k => { try { localStorage.removeItem(k); } catch (e) { /* storage unavailable */ } };
const cleanTxt = s => { let x = String(s || ""); const i = x.indexOf("'report': \""); if (x.startsWith("{") && i >= 0) x = x.slice(i + 11).replace(/"\}\s*(…\[truncated\])?\s*$/, "$1"); return x.replace(/\\n/g, "\n").replace(/\\'/g, "'"); };
const md = s => esc(cleanTxt(s)).replace(/^#{1,6}\s*(.+)$/gm, "<b>$1</b>").replace(/\*\*(.+?)\*\*/g, "<b>$1</b>");
const num = v => { const n = parseFloat(String(v ?? "").replace(/[^0-9.\-]/g, "")); return isFinite(n) ? n : null; };
const AI_T = ORDER.filter(t => EXTRA.ai[t]);
const ratingTone = r => ({ buy: "buy", hold: "hold", sell: "sell", overweight: "buy", underweight: "sell" }[String(r || "").toLowerCase()] || "");
const ratingBadge = r => r ? `<span class="badge sig ${ratingTone(r)}"><i></i>${esc(r)}</span>` : `<span class="badge">n/a</span>`;
const pillTabs = (items, cur, attr) => `<div class="pills">${items.map(([id, label, dis]) => `<button type="button" class="pillbtn" ${attr}="${esc(id)}" aria-pressed="${cur === id}" ${dis ? "disabled style=\"opacity:.45;cursor:not-allowed\"" : ""}><i></i>${esc(label)}</button>`).join("")}</div>`;
const tkrPills = (cur, attr, only) => pillTabs(ORDER.map(t => [t, t, only && !only.includes(t)]), cur, attr);

const X = {
  ai: { t: AI_T[0], stage: "Fundamentals" },
  wl: lsGet("raem:wl", null) || ORDER.slice(),
  bt: { t: ORDER[0], sel: new Set(["Ensemble"]) },
  ds: { t: ORDER[0], page: 0, from: "", to: "" },
  news: { t: AI_T[0] },
  fund: { t: AI_T[0] },
  mem: { tab: "episodes", q: "", t: "" },
  wlMsg: "",
};
const settings = Object.assign({ theme: "system", win: 120, unit: "price", view: "Ensemble" }, lsGet("raem:set", {}));
const saveSettings = () => lsSet("raem:set", settings);
function applySettings(initial) {
  if (settings.theme === "system") delete document.documentElement.dataset.theme; else document.documentElement.dataset.theme = settings.theme;
  if (initial) { S.win = settings.win; S.unit = settings.unit; S.view = settings.view; }
}
const lastClose = t => DATA[t].forecast.last_price;
const fxOf = t => EXTRA.fx[DATA[t].meta.currency] || 1;
const usd = (t, v) => v / fxOf(t);
const money = v => (v < 0 ? "−" : "") + "$" + Math.abs(v).toLocaleString("en-US", { maximumFractionDigits: 0 });
const nonEmpty = (icon, title, desc) => card("", "", "", emptyState(icon, title, desc), "dashed");

/* ----- AI analysis ----- */
const d0 = t => (DATA[t] && DATA[t].meta.name !== t ? `${DATA[t].meta.name} (${t})` : t);
function aiProgress() {
  const p = X.ai.prog, n = p.stages.length, pct = Math.round(p.i / n * 100), cur = p.stages[Math.min(p.i, n - 1)];
  return `<section class="card progress" style="margin-top:16px;box-shadow:none"><div class="top"><div class="ttl"><span class="dotc warn"></span>Analysing ${esc(p.t)}${p.of > 1 ? ` (${p.k} of ${p.of})` : ""}</div><span class="mono" style="font-size:12px;color:var(--muted)">${pct}%</span></div>
    <div class="bar"><div style="width:${pct}%"></div></div><p class="msg" role="status"><b>${esc(cur)}</b> · stage ${Math.min(p.i + 1, n)} of ${n}</p>
    <ol class="events">${p.events.map(e => `<li><span class="mono">${esc(e.ts)}</span><span>${esc(e.name)}</span></li>`).join("")}</ol></section>`;
}
async function aiRun(list) {
  if (X.ai.running || !list.length) return;
  X.ai.running = true; const back = X.ai.t;
  for (let k = 0; k < list.length; k++) {
    const t = list[k], L = EXTRA.ai[t].latest, stages = Object.keys(L.steps);
    X.ai.t = t; X.ai.prog = { t, k: k + 1, of: list.length, i: 0, stages, events: [] }; render();
    for (let i = 0; i < stages.length; i++) {
      X.ai.prog.i = i; X.ai.prog.events.unshift({ ts: L.steps[stages[i]].time || "", name: stages[i] });
      const el = $("#aiprog"); if (el) el.innerHTML = aiProgress();
      await wait(list.length > 1 ? 110 : 240);
    }
    X.ai.ran[t] = true; S.log.unshift({ t, msg: "AI analysis shown", ts: nowT() });
  }
  X.ai.running = false; X.ai.prog = null; X.ai.t = list.length > 1 ? back : list[0]; render();
}
function renderAI() {
  X.ai.ran = X.ai.ran || {};
  const live = LIVE.ok && LIVE.models;
  const choices = live ? LIVE.models.analysis_tickers : AI_T;
  if (choices.length && !choices.includes(X.ai.t)) X.ai.t = choices[0];
  const t = X.ai.t, a = EXTRA.ai[t];
  const head = `<div class="page-h"><h1>AI Analysis</h1><p>The company notebooks' LLM analyst panel (TradingAgents with the LLM you choose: a local Ollama model or a cloud model): analysts, Graph Analyst, Forecasting Agent, debate, trader, risk, portfolio manager, verifier and position sizing. Research output, not advice.</p></div>
    ${card("", "", "", `<div class="field"><span class="lbl">Company</span>${tkrPills(t, "data-ai", choices)}</div>${!live && !ORDER.every(x => AI_T.includes(x)) ? `<p class="sub" style="margin-top:10px">No saved session for ${ORDER.filter(x => !AI_T.includes(x)).join(", ")} yet. Run it on the hosted site.</p>` : ""}`)}`;
  if (live) {
    if (!a) return head + aiLiveCard(t, a) + aiHistoryCard(t) + (LIVE.busy["ai:" + t] ? "" : nonEmpty("brain", "No session for this company yet", "Choose a model and run the analysis above to create the first one."));
    return aiResults(t, a, head + aiLiveCard(t, a), aiHistoryCard(t));
  }
  if (!a) return head + nonEmpty("inbox", "No analyst session", "This ticker has no saved LLM session.");
  // Static copy (no server): show the latest saved analysis at once. Running today's analysis needs the hosted site.
  const infoCard = card(`AI analysis: ${esc(d0(t))}`, `Latest saved analysis: ${dLong(a.latest.date)}, ${Object.keys(a.latest.steps).length} stages${a.latest.llm_model ? ", " + esc(a.latest.llm_model) : ""}.`, ratingBadge(a.latest.rating),
    `<p class="note">This copy has no server, so it cannot run the notebook and shows the latest saved analysis. On the hosted site this card runs ${esc(t)}'s whole notebook for today's date with the model you choose.</p>`);
  return aiResults(t, a, head + infoCard);
}
/* ----- AI analysis: decision and debates (laid out like the any_with_dse analysis page) ----- */
const SIDE_TONE = { bull: "buy", bear: "sell", aggressive: "sell", conservative: "accent", neutral: "hold" };
const SIGNAL_OF = { BUY: "BUY", OVERWEIGHT: "BUY", HOLD: "HOLD", UNDERWEIGHT: "SELL", SELL: "SELL" };
function mdField(text, label) {  // the text after "**Label**:" up to the next "**Label**:" line
  const m = new RegExp("\\*\\*" + label + "\\*\\*\\s*:\\s*([\\s\\S]*?)(?=\\n\\s*\\*\\*[A-Z][^*\\n]{1,40}\\*\\*\\s*:|$)", "i").exec(String(text || ""));
  return m ? m[1].trim() : "";
}
const aiSignal = L => L.signal || SIGNAL_OF[String(L.rating || "").toUpperCase()] || "";
/* "If I buy now, what could the next 5 trading days bring?" on the Decision card. Uses the amount and currency set on the
   Return calculator page (default $100), the AI-adjusted path when the AI analysis is fresh, else the Ensemble. */
function aiFiveDay(t) {
  const d = DATA[t], f = d.forecast, last = f.last_price, cur = d.meta.currency, b = aiBlend(t), band = fcBand(t);
  const useAI = b && !b.stale && b.path, path = useAI ? b.path : fcPath(t);
  if (!path) return "";
  // Dhaka stocks are shown in taka on a 10,000 Tk stake; the others use the Return calculator's amount and currency
  const tk = cur === "BDT", A = tk ? 10000 : (invAmt() || 100), k = 4, r = path[k] / last - 1, age = daysBetween(f.last_date, ASOF);
  const lo = band ? band[k][0] / last - 1 : null, hi = band ? band[k][1] / last - 1 : null;
  const money2 = v => tk ? `${v >= 0 ? "+" : "−"}${Math.abs(v).toLocaleString("en-US", { maximumFractionDigits: 0 })} Tk` : invSigned(t, v);
  const pl = x => money2(A * x), stake = tk ? `${A.toLocaleString("en-US")} Tk` : invShow(t, A);
  return `<div class="sect"><p class="l">If you buy now: next 5 trading days (to ${dShort(f.dates[k])})</p>
    <p class="v ${cls(r)}">${pl(r)} <span class="vs">${fmtPct(r * 100)} on ${stake}</span></p>
    ${band ? `<div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-top:10px">
      <div><p class="l" style="margin:0">Likely worst (10%)</p><p class="v ${cls(lo)}">${pl(lo)}</p><p class="vs ${cls(lo)}">${fmtPct(lo * 100)} · price ${fmtP(band[k][0])} ${esc(cur)}</p></div>
      <div><p class="l" style="margin:0">Likely best (90%)</p><p class="v ${cls(hi)}">${pl(hi)}</p><p class="vs ${cls(hi)}">${fmtPct(hi * 100)} · price ${fmtP(band[k][1])} ${esc(cur)}</p></div></div>` : ""}
    <p class="small">Based on the ${useAI ? "AI-adjusted forecast" : "Ensemble forecast"} from the ${dShort(f.last_date)} close of ${fmtP(last)} ${esc(cur)}${age > 5 ? ` (${age} days before export, so the real start price may differ)` : ""}. The range comes from this stock's own past forecast errors; the price can end outside it. Before costs and tax. ${tk ? "" : "Change the amount on the Return calculator page. "}Not investment advice.</p></div>`;
}
function aiDecisionCard(t, L) {
  const sig = aiSignal(L), d = DATA[t], cur = d.meta.currency, entry = d.forecast.last_price, seen = +(L.summary || {}).market_latest_close || null;
  const fd = L.final_decision || "", summ = mdField(fd, "Executive Summary"), why = mdField(fd, "Investment Thesis"), hor = mdField(fd, "Time Horizon");
  const v = L.verifier || {}, b = aiBlend(t), band = fcBand(t);
  return card(`Decision ${hint(GLOSS.aidec)}`, `${esc(d0(t))} · ${dLong(L.date)}${L.llm_model ? " · " + esc(L.llm_model) : ""}`, sig ? sigBadge(sig) : ratingBadge(L.rating), `
    <div class="sigrow"><div class="bigsig ${esc(sig.toLowerCase())}">${esc(sig || "n/a")}</div>
      <div class="sigtext"><p class="conf">portfolio manager rating ${esc(L.rating || "n/a")}${v.status ? ` · verifier ${esc(String(v.status).toLowerCase())}${v.auto_overridden ? ", signal overridden" : ""}` : ""}</p>
        <p class="sum">${esc(summ || (fd ? fd.replace(/\*\*/g, "").slice(0, 400) : "No summary in this session."))}</p>
        ${hor && !/not provided/i.test(hor) ? `<p style="margin-top:6px;font-size:12px;color:var(--faint)">Time horizon: ${esc(hor)}</p>` : ""}</div></div>
    <div class="sect"><p class="l">Entry (close of ${dShort(d.forecast.last_date)})</p><p class="v">${fmtP(entry)} ${esc(cur)}</p>
      ${seen && Math.abs(seen / entry - 1) > 0.01 ? `<p class="small">The notebook saw ${fmtP(seen)} ${esc(cur)} on ${dShort(L.date)}; the forecast uses closes to ${dShort(d.forecast.last_date)}. Run the forecast again for the newer prices.</p>` : ""}</div>
    ${b && !b.stale ? `<div class="sect"><p class="l">7-day outlook with this decision</p><p class="v ${cls(b.adj)}">${fmtPct(b.adj * 100)} · ${fmtP(b.price)} ${esc(cur)}</p>${band ? `<p class="small">Range from past forecast errors: ${fmtP(band[6][0])} to ${fmtP(band[6][1])} ${esc(cur)}.</p>` : ""}</div>` : ""}
    ${aiFiveDay(t)}
    ${why ? `<div class="sect"><p class="l">Why ${esc(sig || L.rating || "")}</p><p class="why">${esc(why)}</p></div>` : ""}`);
}
const turnHtml = x => `<li class="turn ${SIDE_TONE[x.side] || "hold"}"><div class="who"><b>${esc(x.speaker)}</b><span class="badge ${SIDE_TONE[x.side] || "hold"}">${esc(x.side)}</span>${x.round ? `<span class="sub">round ${x.round}</span>` : ""}</div><div class="pre">${md(x.text)}</div></li>`;
function aiDebateCard(L) {
  const db = L.debate;
  if (!db || (!(db.turns || []).length && !db.facilitator)) return "";
  return card(`AI debate ${hint(GLOSS.debate)}`, db.complete ? "Bull vs bear, round by round, then the facilitator's verdict." : "Excerpt the notebook logged; runs made on this site keep the whole debate.", "", `
    <ol class="debate">${(db.turns || []).map(turnHtml).join("")}</ol>
    ${db.facilitator ? `<div class="sect"><p class="l">Facilitator's verdict</p><div class="pre">${md(db.facilitator)}</div></div>` : ""}`);
}
function aiRiskCard(L) {
  const db = L.debate;
  if (!db || (!(db.risk_turns || []).length && !db.risk_facilitator && !db.trader)) return "";
  return card("Trader and risk debate", "The trader's plan, then aggressive, conservative and neutral risk analysts argue over it.", "", `
    ${db.trader ? `<div class="sect" style="margin-top:0;padding-top:0;border-top:0"><p class="l">Trader's plan</p><div class="pre">${md(db.trader)}</div></div>` : ""}
    <ol class="debate" style="margin-top:12px">${(db.risk_turns || []).map(turnHtml).join("")}</ol>
    ${db.risk_facilitator ? `<div class="sect"><p class="l">Risk facilitator</p><div class="pre">${md(db.risk_facilitator)}</div></div>` : ""}`);
}
function aiResults(t, a, top, tail = "") {
  const L = a.latest, sm = L.summary || {}, mi = sm.market_indicators || {}, na = sm.news_analyst || {};
  const stages = Object.keys(L.steps);
  if (!stages.includes(X.ai.stage)) X.ai.stage = stages[0];
  const st = L.steps[X.ai.stage] || { text: "", time: "" };
  return top + aiDecisionCard(t, L) + aiEffectCard(t) + `
    <div class="stats">
      ${statCard("Decision", sigBadge(aiSignal(L) || "HOLD"), `rating ${esc(L.rating || "n/a")}`, "", "brain")}
      ${statCard("Session", dShort(L.date), `${esc((L.start || "").slice(11, 16))} to ${esc((L.end || "").slice(11, 16))}${L.llm_model ? ` · ${esc(L.llm_model)}` : ""}`, "", "clock")}
      ${statCard("RSI", mi.rsi == null ? "–" : mi.rsi.toFixed(1), mi.macd == null ? "" : `MACD ${mi.macd.toFixed(2)}`, "", "line")}
      ${statCard("News sentiment", esc(na.overall_sentiment || "–"), `+${esc(na.positive_count ?? "–")} / −${esc(na.negative_count ?? "–")} / ${esc(na.neutral_count ?? "–")} neutral`, "", "news")}
    </div>
    ${aiDebateCard(L)}${aiRiskCard(L)}
    ${card("Final decision text", `${esc(t)} · ${dLong(L.date)}`, ratingBadge(L.rating), `<div class="pre">${md(L.final_decision)}</div>`)}
    ${card("Analyst pipeline", "Each stage's saved output for this session.", "", `${pillTabs(stages.map(s => [s, s]), X.ai.stage, "data-stage")}<div class="scrollbox" style="margin-top:16px"><p class="sub" style="margin-bottom:8px">${esc(X.ai.stage)} · ${esc(st.time || "")}</p><div class="pre">${md(st.text)}</div></div><p class="src">Source: notebooks/trading_log_*.json (step outputs, shortened).</p>`)}
    ${card("Session history", `${a.sessions.length} saved sessions for ${esc(t)}.`, "", `<div class="scroll"><table><thead><tr><th>Date</th><th>Started</th><th>Signal</th><th>Rating</th></tr></thead><tbody>${a.sessions.slice().reverse().slice(0, 15).map(s => `<tr><td class="t">${dShort(s.date)}</td><td>${esc((s.start || "").slice(11, 16))}</td><td>${s.signal && s.signal !== "nan" ? esc(s.signal) : "–"}</td><td>${ratingBadge(s.rating)}</td></tr>`).join("")}</tbody></table></div>`)}` + tail;
}

/* ----- watchlist ----- */
function renderWatch() {
  const rows = X.wl.map(t => {
    const rowsAll = EXTRA.dataset[t], c = rowsAll.map(r => r[4]), last = c[c.length - 1], prev = c[c.length - 2];
    const win = c.slice(-250), lo = Math.min(...win), hi = Math.max(...win), vol = rowsAll[rowsAll.length - 1][5];
    const ch1 = (last / prev - 1) * 100, ch30 = (last / c[c.length - 31] - 1) * 100;
    return `<tr class="click" data-t="${esc(t)}"><td><span class="tkr">${esc(t)}</span><div class="sub">${esc(DATA[t].meta.name)}</div></td><td>${fmtP(last)} <span class="sub">${esc(DATA[t].meta.currency)}</span></td><td class="${cls(ch1)}">${fmtPct(ch1)}</td><td class="${cls(ch30)}">${fmtPct(ch30)}</td>
      <td>${vol ? vol.toLocaleString() : "–"}</td><td><span class="rangebar"><u style="left:${Math.max(0, Math.min(96, (last - lo) / ((hi - lo) || 1) * 100))}%"></u></span><div class="sub" style="font-size:10px">${fmtP(lo)} – ${fmtP(hi)}</div></td>
      <td><button type="button" class="x" data-act="wl-del" data-v="${esc(t)}" aria-label="Remove ${esc(t)}">×</button></td></tr>`;
  }).join("");
  const missing = ORDER.filter(t => !X.wl.includes(t));
  return `<div class="page-h"><h1>Watchlist</h1><p>Your pinned tickers with the latest close. Saved in this browser.</p></div>
    ${card("Add ticker", "This export has 8 tickers. Add back any you removed.", "", `<form class="toolbar" id="wlform"><input class="inp grow" id="wlq" placeholder="Ticker (e.g. IBM)" aria-label="Ticker to add" style="text-transform:uppercase"><button class="btn primary" type="submit">Add</button>${missing.length ? `<span class="sub">Removed: ${missing.map(m => `<button type="button" class="link" data-act="wl-add" data-v="${esc(m)}">${esc(m)}</button>`).join(" ")}</span>` : ""}</form>${X.wlMsg ? `<p class="err" role="alert" style="margin-top:8px">${esc(X.wlMsg)}</p>` : ""}`)}
    ${X.wl.length ? card("Quotes", "Last close per ticker. Click a row to open the forecast.", "", `<div class="scroll"><table><thead><tr><th>Ticker</th><th>Last close</th><th>1 day</th><th>30 days</th><th>Volume</th><th>1-year range</th><th></th></tr></thead><tbody>${rows}</tbody></table></div><p class="src">Source: data/ and daily_data/ price files. Closes are as of the last saved bar, not live quotes.</p>`) : nonEmpty("inbox", "Your watchlist is empty", "Add a ticker above to see its quote here.")}`;
}

/* ----- price helpers ----- */
const rowsOf = t => EXTRA.dataset[t];
function closeOn(t, date) {
  const r = rowsOf(t); let lo = 0, hi = r.length - 1, ans = -1;
  while (lo <= hi) { const m = (lo + hi) >> 1; if (r[m][0] <= date) { ans = m; lo = m + 1; } else hi = m - 1; }
  return ans < 0 ? null : { date: r[ans][0], px: r[ans][4] };
}
const nextDays = (iso, n) => { const out = []; const d = new Date(iso + "T00:00:00Z"); while (out.length < n) { d.setUTCDate(d.getUTCDate() + 1); if (d.getUTCDay() % 6) out.push(d.toISOString().slice(0, 10)); } return out; };
const fcPath = t => { const f = DATA[t].forecast; return f.paths.Ensemble || f.paths.Fusion || null; };
function fcBand(t) {
  // band_ret holds the forecast-error quantiles around the forecast, so the price range is the reviewed path plus them
  // (as forecast_table() in forecast_agent.py does); without the path the range would sit around today's price instead
  const d = DATA[t], last = d.forecast.last_price, pl = d.agent && d.agent.plan, p = pl && pl.band_ret, pr = pl && pl.path_ret;
  return p && pr ? p.map((b, i) => [last * (1 + pr[i] + b[0]), last * (1 + pr[i] + b[1])]) : null;
}
/* ----- generic line chart ----- */
function lineChart(host, labels, series, opts = {}) {
  const W = Math.max(300, host.clientWidth || 700), H = 280, padL = 52, padR = 16, padT = 12, padB = 24;
  const all = series.flatMap(s => s.v).concat(opts.band ? [...opts.band.lo, ...opts.band.hi] : []).filter(v => v != null), lo0 = Math.min(...all), hi0 = Math.max(...all), pad = (hi0 - lo0) * 0.08 || 0.05;
  const lo = lo0 - pad, hi = hi0 + pad, n = labels.length;
  const x = i => padL + i * (W - padL - padR) / Math.max(1, n - 1), y = v => padT + (hi - v) * (H - padT - padB) / (hi - lo);
  let s = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="${esc(opts.label || "chart")}">`;
  niceTicks(lo, hi, 5).filter(v => v >= lo && v <= hi).forEach(v => { s += `<line x1="${padL}" x2="${W - padR}" y1="${y(v)}" y2="${y(v)}" stroke="var(--border-soft)"/><text class="ax" x="${padL - 8}" y="${y(v) + 3}" text-anchor="end">${opts.fmt ? opts.fmt(v) : v.toFixed(2)}</text>`; });
  if (opts.base != null && opts.base > lo && opts.base < hi) s += `<line x1="${padL}" x2="${W - padR}" y1="${y(opts.base)}" y2="${y(opts.base)}" stroke="var(--faint)" stroke-dasharray="4 4"/>`;
  [0, Math.floor((n - 1) / 2), n - 1].forEach((i, k) => { s += `<text class="ax" x="${x(i)}" y="${H - 6}" text-anchor="${k === 0 ? "start" : k === 2 ? "end" : "middle"}">${esc(opts.lab ? opts.lab(labels[i]) : dShort(labels[i]))}</text>`; });
  if (opts.band) {
    const ix = opts.band.lo.map((v, i) => (v != null && opts.band.hi[i] != null) ? i : -1).filter(i => i >= 0);
    if (ix.length) { const first = ix[0] > 0 ? ix[0] - 1 : ix[0], pts = [...(first < ix[0] ? [first] : []), ...ix], bandLo = i => (opts.band.lo[i] != null ? opts.band.lo[i] : series[1].v[i]), bandHi = i => (opts.band.hi[i] != null ? opts.band.hi[i] : series[1].v[i]);
      s += `<polygon points="${pts.map(i => `${x(i).toFixed(1)},${y(bandHi(i)).toFixed(1)}`).join(" ")} ${pts.slice().reverse().map(i => `${x(i).toFixed(1)},${y(bandLo(i)).toFixed(1)}`).join(" ")}" fill="var(--text)" opacity="0.12"/>`; }
  }
  series.forEach(se => {
    if (se.noline) return;
    let run = [];
    const flush = () => { if (run.length > 1) s += `<polyline points="${run.join(" ")}" fill="none" stroke="${se.color}" stroke-width="${se.w || 2}" ${se.dash ? 'stroke-dasharray="5 4"' : ""} stroke-linejoin="round"/>`; run = []; };
    se.v.forEach((v, i) => { if (v == null) flush(); else run.push(`${x(i).toFixed(1)},${y(v).toFixed(1)}`); });
    flush();
  });
  s += `<line id="lxh" x1="0" x2="0" y1="${padT}" y2="${H - padB}" stroke="var(--faint)" opacity="0"/><rect id="lhit" x="${padL}" y="${padT}" width="${W - padL - padR}" height="${H - padT - padB}" fill="transparent"/></svg><div class="tip" hidden></div>`;
  host.innerHTML = s;
  const svg = host.querySelector("svg"), tip = host.querySelector(".tip"), xh = host.querySelector("#lxh"), hit = host.querySelector("#lhit");
  const mv = ev => {
    const r = svg.getBoundingClientRect(), px = (ev.clientX - r.left) * W / r.width, i = Math.max(0, Math.min(n - 1, Math.round((px - padL) / ((W - padL - padR) / Math.max(1, n - 1)))));
    xh.setAttribute("x1", x(i)); xh.setAttribute("x2", x(i)); xh.setAttribute("opacity", "0.6");
    tip.innerHTML = `<div class="d">${opts.lab ? opts.lab(labels[i]) : dLong(labels[i])}</div>${series.filter(se => se.v[i] != null).map(se => `<div class="r" style="color:${se.color}"><span>${esc(se.name)}</span><span>${opts.fmt ? opts.fmt(se.v[i]) : se.v[i].toFixed(2)}</span></div>`).join("")}`;
    tip.hidden = false; tip.style.left = Math.min(r.width - tip.offsetWidth - 4, Math.max(4, x(i) * r.width / W + 12)) + "px"; tip.style.top = "8px";
  };
  hit.addEventListener("pointermove", mv); hit.addEventListener("pointerdown", mv);
  hit.addEventListener("pointerleave", () => { tip.hidden = true; xh.setAttribute("opacity", "0"); });
}

/* ----- backtest ----- */
function renderBT() {
  const t = X.bt.t, res = EXTRA.backtest[t] || {}, models = Object.keys(res);
  X.bt.sel = new Set([...X.bt.sel].filter(m => res[m]));
  if (!X.bt.sel.size && models.length) X.bt.sel.add(models.includes("Ensemble") ? "Ensemble" : models[0]);
  const rows = models.map(m => { const r = res[m], edge = r.hit == null ? null : r.hit - r.always_up; return `<tr class="click ${X.bt.sel.has(m) ? "sel" : ""}" data-act="bt-m" data-v="${esc(m)}"><td class="t"><span class="mname"><i style="${X.bt.sel.has(m) ? `background:var(${COLORS[[...X.bt.sel].indexOf(m) % 8]})` : ""}"></i>${esc(m)}</span></td><td>${r.n}</td><td>${r.hit == null ? "–" : r.hit.toFixed(0) + "%"}</td><td>${r.always_up.toFixed(0)}%</td><td class="${cls(edge)}">${edge == null ? "–" : (edge > 0 ? "+" : "") + edge.toFixed(0)}</td><td class="${cls(r.strategy_pct)}">${fmtPct(r.strategy_pct, 1)}</td><td class="${cls(r.buyhold_pct)}">${fmtPct(r.buyhold_pct, 1)}</td><td class="${cls(r.avg_trade_pct)}">${fmtPct(r.avg_trade_pct)}</td><td class="down">${r.max_dd_pct.toFixed(1)}%</td></tr>`; }).join("");
  const sum = ORDER.map(tk => { const e = (EXTRA.backtest[tk] || {}).Ensemble; return e ? `<tr class="click" data-act="bt-t" data-v="${esc(tk)}"><td><span class="tkr">${esc(tk)}</span></td><td>${e.n}</td><td>${e.hit == null ? "–" : e.hit.toFixed(0) + "%"}</td><td>${e.always_up.toFixed(0)}%</td><td class="${cls(e.strategy_pct)}">${fmtPct(e.strategy_pct, 1)}</td><td class="${cls(e.buyhold_pct)}">${fmtPct(e.buyhold_pct, 1)}</td></tr>` : ""; }).join("");
  return `<div class="page-h"><h1>Backtest</h1><p>How each model's saved day-7 calls would have done: long when it predicted up, short when down. Built from graded rows in the forecast log.</p></div>
    ${card("", "", "", `<div class="field"><span class="lbl">Ticker</span>${tkrPills(t, "data-bt")}</div>`)}
    ${models.length ? card("Equity curve", "Growth of 1.00 following each selected model's calls, one non-overlapping 7-day trade at a time. Dashed grey = buy and hold on the same entry dates.", "", `<div class="chartbox" id="btchart"></div><div class="legend" id="btlegend"></div><p class="src">Source: forecast_log/${esc(t)}_forecasts.csv (graded day-7 rows).</p>`) : ""}
    ${models.length ? card(`Models ${hint(GLOSS.bt)}`, "Click a row to add or remove its curve.", "", `<div class="scroll"><table><thead><tr><th>Model</th><th>Trades</th><th>Direction hit</th><th>Always-UP</th><th>Edge (pts)</th><th>Strategy</th><th>Buy &amp; hold</th><th>Avg trade</th><th>Max drawdown</th></tr></thead><tbody>${rows}</tbody></table></div><p class="src">No costs, no slippage. Most rows are walk-forward replays from Ridge, ARIMA and Kalman, so of the six models on this site only Ridge, ARIMA and the Ensemble have enough history. Past Ensemble rows were made from the full model set. A strategy that trails buy and hold in a rising market is common and not a bug.</p>`) : nonEmpty("inbox", "Not enough graded calls", "This ticker has fewer than 8 non-overlapping graded day-7 calls for any model.")}
    ${card("Ensemble across tickers", "Click a row to open that ticker.", "", `<div class="scroll"><table><thead><tr><th>Ticker</th><th>Trades</th><th>Direction hit</th><th>Always-UP</th><th>Strategy</th><th>Buy &amp; hold</th></tr></thead><tbody>${sum}</tbody></table></div>`)}`;
}
function drawBT() {
  const h = $("#btchart"); if (!h) return;
  const res = EXTRA.backtest[X.bt.t] || {}, sel = [...X.bt.sel].filter(m => res[m]);
  if (!sel.length) return;
  const base = res[sel[0]], series = [{ name: "Buy & hold", v: base.buyhold_eq, color: "var(--faint)", dash: true, w: 1.5 }];
  sel.forEach((m, i) => series.push({ name: m, v: res[m].equity, color: `var(${COLORS[i % 8]})`, w: 2.2 }));
  const n = Math.min(...series.map(s => s.v.length));
  lineChart(h, base.dates.slice(0, n), series.map(s => ({ ...s, v: s.v.slice(0, n) })), { base: 1, label: "Equity curve", fmt: v => v.toFixed(2) });
  $("#btlegend").innerHTML = series.map(s => `<span><i style="background:${s.color}"></i>${esc(s.name)}</span>`).join("");
}

/* ----- dataset explorer ----- */
const PAGE_N = 20;
function renderData() {
  const t = X.ds.t, all = EXTRA.dataset[t];
  let rows = all.map((r, i) => ({ r, ch: i ? (r[4] / all[i - 1][4] - 1) * 100 : null }));
  if (X.ds.from) rows = rows.filter(x => x.r[0] >= X.ds.from);
  if (X.ds.to) rows = rows.filter(x => x.r[0] <= X.ds.to);
  rows.reverse();
  const pages = Math.max(1, Math.ceil(rows.length / PAGE_N)); X.ds.page = Math.min(X.ds.page, pages - 1);
  const cl = rows.map(x => x.r[4]), vols = rows.map(x => x.r[5]).filter(v => v), view = rows.slice(X.ds.page * PAGE_N, X.ds.page * PAGE_N + PAGE_N);
  return `<div class="page-h"><h1>Dataset Explorer</h1><p>The daily price table the models train on: the data snapshot plus closes logged by the notebooks.</p></div>
    ${card("", "", "", `<div class="field"><span class="lbl">Ticker</span>${tkrPills(t, "data-ds")}</div><div class="toolbar" style="margin-top:16px"><div class="field"><label for="ds-from">From</label><input class="inp" type="date" id="ds-from" data-bind="ds-from" value="${esc(X.ds.from)}"></div><div class="field"><label for="ds-to">To</label><input class="inp" type="date" id="ds-to" data-bind="ds-to" value="${esc(X.ds.to)}"></div><button type="button" class="btn secondary sm" data-act="ds-clear" style="align-self:flex-end">Clear dates</button></div>`)}
    <div class="stats">
      ${statCard("Rows", rows.length.toLocaleString(), `${all.length} in full file`, "", "db")}
      ${statCard("Range", rows.length ? dShort(rows[rows.length - 1].r[0]) : "–", rows.length ? "to " + dShort(rows[0].r[0]) : "", "", "clock")}
      ${statCard("Close low / high", rows.length ? fmtP(Math.min(...cl)) : "–", rows.length ? "high " + fmtP(Math.max(...cl)) : "", "", "chart")}
      ${statCard("Avg volume", vols.length ? Math.round(vols.reduce((a, b) => a + b, 0) / vols.length).toLocaleString() : "–", `${esc(DATA[t].meta.currency)} prices`, "", "pulse")}
    </div>
    ${card("Daily prices", "Newest first.", "", rows.length ? `<div class="scroll"><table><thead><tr><th>Date</th><th>Open</th><th>High</th><th>Low</th><th>Close</th><th>Change</th><th>Volume</th><th>Source</th></tr></thead><tbody>${view.map(({ r, ch }) => `<tr><td class="t">${esc(r[0])}</td><td>${fmtP(r[1])}</td><td>${fmtP(r[2])}</td><td>${fmtP(r[3])}</td><td>${fmtP(r[4])}</td><td class="${cls(ch)}">${ch == null ? "–" : fmtPct(ch)}</td><td>${r[5] ? r[5].toLocaleString() : "–"}</td><td><span class="badge ${r[6] === "snapshot" ? "" : "accent"}">${esc(r[6] || "–")}</span></td></tr>`).join("")}</tbody></table></div>
      <div class="pager"><button type="button" class="btn secondary sm" data-act="ds-prev" ${X.ds.page === 0 ? "disabled" : ""}>Prev</button><span class="mono">page ${X.ds.page + 1} / ${pages}</span><button type="button" class="btn secondary sm" data-act="ds-next" ${X.ds.page >= pages - 1 ? "disabled" : ""}>Next</button></div>
      <p class="src">Source: data/*_2y.csv plus daily_data/*_daily.csv through forecast_lib.load_combined_history(). Change is the close-to-close return.</p>` : emptyState("inbox", "No rows in this range", "Clear the dates to see the full table."))}`;
}

/* ----- news ----- */
function renderNews() {
  const t = X.news.t, a = EXTRA.ai[t];
  const head = `<div class="page-h"><h1>News</h1><p>What the news and sentiment analysts saw in their latest session, plus the news text stored for retrieval.</p></div>${card("", "", "", `<div class="field"><span class="lbl">Ticker</span>${tkrPills(t, "data-news", AI_T)}</div>`)}`;
  if (!a) return head + nonEmpty("inbox", "No news session", "This ticker has no saved LLM session, so there is no news report.");
  const L = a.latest, sm = L.summary || {}, na = sm.news_analyst || {}, se = sm.sentiment_analyst || {};
  const chunks = EXTRA.news_corpus.filter(c => c.meta.company === t);
  const byDate = {}; chunks.forEach(c => { (byDate[c.meta.trade_date] = byDate[c.meta.trade_date] || []).push(c); });
  const dates = Object.keys(byDate).sort().reverse();
  return head + `
    <div class="stats">
      ${statCard("Overall sentiment", esc(na.overall_sentiment || "–"), "news analyst", na.overall_sentiment === "POSITIVE" ? "buy" : na.overall_sentiment === "NEGATIVE" ? "sell" : "hold", "news")}
      ${statCard("Positive", esc(na.positive_count ?? "–"), "as counted by the analyst", "buy", "line")}
      ${statCard("Negative", esc(na.negative_count ?? "–"), "as counted by the analyst", "sell", "line")}
      ${statCard("Social sentiment", esc(se.overall || "–"), `score ${esc(se.score ?? "N/A")}`, "", "brain")}
    </div>
    ${card("News analyst report", `${esc(t)} · session of ${dLong(L.date)}`, "", `<div class="scrollbox"><div class="pre">${md((L.steps.News || {}).text || "No news report.")}</div></div><p class="src">Source: notebooks/trading_log_*.json, News Analyst step (shortened).</p>`)}
    ${card("Stored news text", `${chunks.length} chunks for ${esc(t)} in the retrieval store.`, "", dates.length ? dates.slice(0, 4).map(d => `<div class="memcard" style="margin-bottom:10px"><div class="hd"><span class="badge accent">${esc(d)}</span><span class="sub">${byDate[d].length} chunks</span></div><p>${esc(byDate[d][0].doc)}</p></div>`).join("") : emptyState("inbox", "No stored news", "Nothing for this ticker in raem_news_corpus."))}`;
}

/* ----- fundamentals ----- */
function renderFund() {
  const t = X.fund.t, F = k => (tk => ((EXTRA.ai[tk] || {}).latest || { summary: {} }).summary.fundamentals_analyst || {});
  const rows = ORDER.map(tk => {
    const f = ((EXTRA.ai[tk] || {}).latest || { summary: {} }).summary.fundamentals_analyst;
    if (!f) return `<tr><td><span class="tkr">${esc(tk)}</span></td><td colspan="8" class="sub" style="text-align:left">No analyst session</td></tr>`;
    const lo = num(f["52w_low"]), hi = num(f["52w_high"]), last = lastClose(tk), pos = lo != null && hi != null && hi > lo ? Math.max(0, Math.min(96, (last - lo) / (hi - lo) * 100)) : null;
    const v = k => esc(f[k] ?? "N/A");
    return `<tr class="click ${tk === t ? "sel" : ""}" data-act="fund-t" data-v="${esc(tk)}"><td><span class="tkr">${esc(tk)}</span></td><td>${v("market_cap")}</td><td>${v("pe_ratio")}</td><td>${v("eps_ttm")}</td><td>${v("revenue_ttm")}</td><td>${v("beta")}</td><td>${v("dividend_yield")}</td><td>${pos == null ? "–" : `<span class="rangebar"><u style="left:${pos}%"></u></span><div class="sub" style="font-size:10px">${fmtP(lo)} – ${fmtP(hi)}</div>`}</td><td>${v("net_debt")}</td></tr>`;
  }).join("");
  const a = EXTRA.ai[t], ft = a && a.latest.steps.Fundamentals;
  return `<div class="page-h"><h1>Fundamentals Check</h1><p>Company figures the fundamentals analyst pulled in its latest session. "N/A" means the data source had no value.</p></div>
    ${card("All tickers", "Click a row to read that analyst's write-up below. The marker shows the last close inside its 52-week range.", "", `<div class="scroll"><table class="fund-grid"><thead><tr><th>Ticker</th><th>Market cap</th><th>P/E</th><th>EPS (TTM)</th><th>Revenue (TTM)</th><th>Beta</th><th>Div. yield</th><th>52-week range</th><th>Net debt</th></tr></thead><tbody>${rows}</tbody></table></div><p class="src">Source: notebooks/trading_log_*.json, summary_table.fundamentals_analyst. Values are shown as the source wrote them, in each company's currency.</p>`)}
    ${a ? card(`${esc(t)} analyst write-up`, `Session of ${dLong(a.latest.date)}.`, "", `<div class="scrollbox"><div class="pre">${md(ft ? ft.text : "No fundamentals text.")}</div></div>`) : ""}`;
}

/* ----- papers ----- */
function renderPapers() {
  return `<div class="page-h"><h1>Research Papers</h1><p>The papers the model designs come from. This project has no annual-report PDFs.</p></div>
    ${card("paper/ folder", `${EXTRA.papers.length} PDF files.`, "", `<div class="scroll"><table><thead><tr><th>File</th><th>Size</th></tr></thead><tbody>${EXTRA.papers.map(p => `<tr><td class="t"><span class="mname"><i style="background:var(--accent)"></i>${esc(p.file)}</span></td><td>${p.mb} MB</td></tr>`).join("")}</tbody></table></div><p class="src">PDFs cannot be opened from this page. Open them from the project's paper/ folder. See CLAUDE.md for which model each paper inspired (LASSO, RandomForest, DNN, WSAE-LSTM).</p>`)}`;
}

/* ----- memory ----- */
function renderMem() {
  const m = EXTRA.memory, eps = m.episodes, tr2 = m.raem_episodic_memory || [], q = X.mem.q.toLowerCase();
  const tabs = [["episodes", `Prediction episodes (${eps.length})`], ["trades", `Trading episodes (${tr2.length})`], ["news", `News chunks (${EXTRA.news_corpus.length})`]];
  let body = "";
  if (X.mem.tab === "episodes") {
    const list = eps.filter(e => (!X.mem.t || e.ticker === X.mem.t) && (!q || (e.doc + e.ticker + e.winner_model).toLowerCase().includes(q)));
    body = `<div class="toolbar" style="margin-bottom:14px"><select id="mem-t" data-bind="mem-t" aria-label="Filter by ticker"><option value="">All tickers</option>${ORDER.map(x => `<option ${X.mem.t === x ? "selected" : ""}>${esc(x)}</option>`).join("")}</select><input class="inp grow" id="mem-q" data-bind="mem-q" placeholder="Search text…" value="${esc(X.mem.q)}" aria-label="Search memory"></div>
      <div class="scroll"><table><thead><tr><th>Ticker</th><th>Data to</th><th>Model</th><th>Predicted</th><th>Actual</th><th>Status</th><th>Direction</th></tr></thead><tbody>${list.slice(0, 40).map(e => `<tr><td><span class="tkr">${esc(e.ticker)}</span></td><td class="t">${esc(e.data_end_date)}</td><td class="t">${esc(e.winner_model)}</td><td class="${cls(e.pred_return)}">${fmtPct(e.pred_return * 100)}</td><td class="${cls(e.actual_return)}">${e.actual_return == null ? "–" : fmtPct(e.actual_return * 100)}</td><td><span class="badge ${e.outcome_status === "GRADED" ? "buy" : ""}">${esc(e.outcome_status)}</span></td><td>${e.direction_correct == null ? "–" : e.direction_correct ? '<span class="up">right</span>' : '<span class="down">wrong</span>'}</td></tr>`).join("")}</tbody></table></div><p class="src">${list.length} matching of ${eps.length}. Source: chroma_memory, collection prediction_episodes. Used to shrink-correct new daily predictions.</p>`;
  } else if (X.mem.tab === "trades") {
    body = tr2.length ? `<div class="grid3">${tr2.map(e => `<div class="memcard"><div class="hd"><span class="tkr">${esc(e.meta.company || "")}</span><span class="badge">${esc(e.meta.trade_date || "")}</span>${ratingBadge(e.meta.final_signal)}${e.meta.outcome_label ? `<span class="badge accent">${esc(e.meta.outcome_label)}</span>` : ""}</div><p>${esc(e.doc)}</p></div>`).join("")}</div><p class="src">Source: notebooks/raem_chroma_db, collection raem_episodic_memory (the LLM agents' own memory, separate from chroma_memory).</p>` : emptyState("inbox", "No trading episodes");
  } else {
    const cs = EXTRA.news_corpus.filter(c => !q || c.doc.toLowerCase().includes(q) || (c.meta.company || "").toLowerCase().includes(q));
    body = `<div class="toolbar" style="margin-bottom:14px"><input class="inp grow" id="mem-q" data-bind="mem-q" placeholder="Search news text or ticker…" value="${esc(X.mem.q)}" aria-label="Search news chunks"></div><div class="scrollbox"><div class="grid3">${cs.slice(0, 24).map(c => `<div class="memcard"><div class="hd"><span class="tkr">${esc(c.meta.company || "")}</span><span class="badge">${esc(c.meta.trade_date || "")}</span><span class="badge accent">${esc(c.meta.source || "")}</span></div><p>${esc(c.doc)}</p></div>`).join("")}</div></div><p class="src">${cs.length} chunks match. Source: notebooks/raem_chroma_db, collection raem_news_corpus.</p>`;
  }
  return `<div class="page-h"><h1>Memory</h1><p>What the system remembers: past predictions graded against reality, past LLM trading sessions and stored news.</p></div>
    <div class="stats" style="grid-template-columns:repeat(3,minmax(0,1fr))">
      ${statCard("Prediction episodes", eps.length, "chroma_memory", "accent", "brain")}
      ${statCard("Trading episodes", tr2.length, "raem_episodic_memory", "", "db")}
      ${statCard("News chunks", EXTRA.news_corpus.length, "raem_news_corpus", "", "news")}
    </div>
    ${card("", "", "", `${pillTabs(tabs, X.mem.tab, "data-mem")}<div style="margin-top:18px">${body}</div>`)}`;
}

/* ----- settings ----- */
function renderSet() {
  const row = (n, d, ctl) => `<div class="setrow"><div><p class="n">${n}</p><p class="d">${d}</p></div><div>${ctl}</div></div>`;
  const folders = [["data/", "2-year price snapshots", "python daily.py (tops up yfinance tickers)"], ["daily_data/", "closes logged by the notebooks", "run a per-ticker notebook"], ["forecast_log/", "7-day forecasts and their grades", "forecast_lib.record_forecast() / grade_forecasts()"], ["cache/", "tuned hyperparameters and model results", "python daily.py --retune"], ["chroma_memory/", "graded prediction episodes", "written by python daily.py"], ["notebooks/trading_log_*", "LLM analyst sessions", "run a per-ticker notebook with Ollama"]];
  return `<div class="page-h"><h1>Settings</h1><p>Preferences for this page, saved in this browser only.</p></div>
    ${card("Preferences", "", "", `
      ${row("Theme", "Follow your system, or force one.", pillTabs([["system", "System"], ["dark", "Dark"], ["light", "Light"]], settings.theme, "data-set-theme"))}
      ${row("Default forecast view", "Which forecast the ticker pages show first.", pillTabs(VIEWS.map(v => [v[0], v[1]]), settings.view, "data-set-view"))}
      ${row("Default history window", "Trading days of history on the price chart.", `<select data-bind="set-win" aria-label="History window">${[30, 60, 120, 180].map(n => `<option value="${n}" ${settings.win === n ? "selected" : ""}>${n} trading days</option>`).join("")}</select>`)}
      ${row("Default chart units", "Show price or change from the last close.", `<select data-bind="set-unit" aria-label="Chart units"><option value="price" ${settings.unit === "price" ? "selected" : ""}>Price</option><option value="pct" ${settings.unit === "pct" ? "selected" : ""}>Change from last close (%)</option></select>`)}`)}
    ${card("Saved data", "Stored in this browser only. Clearing site data removes it.", "", `
      ${row("Watchlist", `${X.wl.length} of ${ORDER.length} tickers pinned.`, `<button type="button" class="btn secondary sm" data-act="wl-reset">Pin all 8</button>`)}
      ${row("All settings", "Back to defaults.", `<button type="button" class="btn secondary sm" data-act="set-reset">Reset settings</button>`)}`)}
    ${LIVE.ok ? card("Server", LIVE.health.run_token_required ? "This server asks for an access token before it runs models." : "This server runs models for anyone who opens the page.", "", `
      ${row("Connection", `Live server, date ${esc(LIVE.health.date)}. LLMs connected: ${esc(llmProviders().map(p => p.label).join(", ") || "none")}. Runs waiting: ${LIVE.health.queue}.`, `<span class="badge ${llmProviders().length ? "buy" : "hold"}">${llmProviders().length ? "AI ready" : "forecasts only"}</span>`)}
      ${LIVE.health.run_token_required ? row("Access token", "Saved in this browser and sent with each run request.", `<input class="inp" type="password" id="set-token" data-bind="set-token" value="${esc(LIVE.token)}" placeholder="token" aria-label="Server access token">`) : ""}`) : ""}
    ${card("Where the numbers come from", `Exported ${dLong(ASOF)} from the project's Python code. This page never recomputes them.`, "", `<div class="scroll"><table><thead><tr><th>Folder</th><th>Holds</th><th>Refresh with</th></tr></thead><tbody>${folders.map(f => `<tr><td class="t"><span class="mono">${esc(f[0])}</span></td><td class="t">${esc(f[1])}</td><td class="t">${esc(f[2])}</td></tr>`).join("")}</tbody></table></div>`)}`;
}

/* ----- return calculator: "if I put in 100" per company ----- */
X.inv = { amt: 100, basis: "usd", src: "Ensemble", t: AI_T[0], h: 5 };  // h = days ahead the calculator shows (5 or 7)
const INV_SRC = [["Ensemble", "Ensemble"], ["AI", "AI-adjusted"], ["Fusion", "Fusion"], ["Fusion-Top3", "Fusion Top 3"], ["Agent", "Reviewed agent"], ["Daily", "Daily 5-model signal"]];
function invFc(t, src) {
  const d = DATA[t], f = d.forecast, last = f.last_price, band = fcBand(t);
  let path;
  if (src === "AI") path = (aiBlend(t) || {}).path || f.paths.Ensemble;
  else if (src === "Agent" && d.agent && d.agent.plan && d.agent.plan.path_ret) path = d.agent.plan.path_ret.map(r => last * (1 + r));
  else if (src === "Daily") { const r = d.daily.final_expected_return; path = Array.from({ length: 7 }, (_, i) => last * (1 + r * (i + 1) / 7)); }
  else path = f.paths[src] || f.paths.Ensemble;
  const hb = band && src !== "Daily", k = (X.inv.h === 7 ? 7 : 5) - 1;  // day-5 or day-7 point of the 7-day path and of its error band
  return { last, path, end: path[k], lo: hb ? band[k][0] : null, hi: hb ? band[k][1] : null };
}
const invAmt = () => Math.max(0, +X.inv.amt || 0);
const invShow = (t, v) => X.inv.basis === "usd" ? "$" + v.toFixed(2) : v.toFixed(2) + " " + DATA[t].meta.currency;
const invSigned = (t, v) => (v >= 0 ? "+" : "−") + invShow(t, Math.abs(v));
const vote3 = v => v > 0.0005 ? 1 : v < -0.0005 ? -1 : 0;
const ratingVote = r => ({ buy: 1, overweight: 1, sell: -1, underweight: -1 }[String(r || "").toLowerCase()] || 0);
const voteTxt = v => v > 0 ? '<span class="up">↑</span>' : v < 0 ? '<span class="down">↓</span>' : '<span class="flat">–</span>';
function sessOutcome(t, s) {
  const rows = rowsOf(t), lastDate = rows[rows.length - 1][0], e = closeOn(t, s.date);
  const rating = String(s.rating || (s.signal && s.signal !== "nan" ? s.signal : "")).toUpperCase();
  if (!e || !["BUY", "SELL", "HOLD", "OVERWEIGHT", "UNDERWEIGHT"].includes(rating)) return null;
  const tg = new Date(s.date + "T00:00:00Z"); tg.setUTCDate(tg.getUTCDate() + 7); const td = tg.toISOString().slice(0, 10);
  if (td > lastDate) return { rating, entry: e.px, pending: true };
  const x = closeOn(t, td);
  return { rating, entry: e.px, exit: x.px, r: x.px / e.px - 1, pending: false };
}
const followFactor = (rating, r) => (rating === "BUY" || rating === "OVERWEIGHT") ? 1 + r : (rating === "SELL" || rating === "UNDERWEIGHT") ? 1 - r : 1;
function invTrack(t) {
  const a = EXTRA.ai[t]; if (!a) return null;
  const list = a.sessions.map(s => ({ s, o: sessOutcome(t, s) })).filter(x => x.o);
  const done = list.filter(x => !x.o.pending), trades = done.filter(x => x.o.rating !== "HOLD");
  const hits = trades.filter(x => followFactor(x.o.rating, x.o.r) > 1).length;
  const avgFollow = trades.length ? trades.reduce((q, x) => q + followFactor(x.o.rating, x.o.r) - 1, 0) / trades.length : null;
  const holds = done.filter(x => x.o.rating === "HOLD");
  return { list, done, trades, hits, avgFollow, holds, avgHold: holds.length ? holds.reduce((q, x) => q + x.o.r, 0) / holds.length : null };
}

/* ----- AI influence: the notebook's latest rating tilts the model forecast a little ----- */
// adjusted 7-day move = (1 - w) * model move + w * AI view, where the AI view is the rating (Buy +1 ... Sell -1) times the stock's
// typical 7-day move. w is 20%, or 10-35% from the AI's own graded calls once it has 5; a session over 10 days older than
// the prices has no weight.
const AI_VIEW = { BUY: 1, OVERWEIGHT: 0.5, HOLD: 0, UNDERWEIGHT: -0.5, SELL: -1 };
function typicalMove(t) {  // median absolute 5-trading-day move over the last ~120 closes
  const cl = DATA[t].history.slice(-125).map(h => h[1]), m = [];
  for (let i = 0; i + 5 < cl.length; i++) m.push(Math.abs(cl[i + 5] / cl[i] - 1));
  m.sort((a, b) => a - b);
  return m.length ? m[Math.floor(m.length / 2)] : 0.02;
}
function aiBlend(t) {
  const d = DATA[t], p = d && fcPath(t), L = EXTRA.ai[t] && EXTRA.ai[t].latest;
  const code = L && String(L.rating || "").toUpperCase(), sig = L && L.signal;
  if (!p || !L || !(code in AI_VIEW || sig in AI_VIEW)) return null;
  // the final decision counts: the rating's shade (Overweight +½) only while it agrees with it (the verifier can override)
  const useSig = sig in AI_VIEW && SIGNAL_OF[code] !== sig, score = useSig ? AI_VIEW[sig] : AI_VIEW[code];
  const last = d.forecast.last_price, base = p[6] / last - 1, info = { rating: useSig ? sig[0] + sig.slice(1).toLowerCase() : L.rating, date: L.date, model: L.llm_model, base, last };
  if (daysBetween(L.date, d.forecast.last_date) > 10) return Object.assign(info, { stale: true, w: 0, adj: base, price: p[6], path: p });
  const tr = invTrack(t), n = tr ? tr.trades.length : 0;
  const w = n >= 5 ? Math.min(0.35, Math.max(0.1, 0.2 + (tr.hits / n - 0.5) * 0.6)) : 0.2;
  const view = score * typicalMove(t);
  const path = p.map((v, i) => last * (1 + (1 - w) * (v / last - 1) + w * view * (i + 1) / 7));
  return Object.assign(info, { stale: false, w, view, adj: path[6] / last - 1, price: path[6], path, n, hits: tr ? tr.hits : 0 });
}
const aiWhy = b => `${Math.round(b.w * 100)}% weight${b.n >= 5 ? `, ${b.hits} of ${b.n} past AI calls right` : ""}`;
function aiEffectCard(t) {
  const b = aiBlend(t), cur = DATA[t].meta.currency;
  if (!b) return "";
  return card(`Effect on the forecast ${hint(GLOSS.aiadj)}`, `The latest AI rating (${dLong(b.date)}${b.model ? ", " + esc(b.model) : ""}) next to the model forecast.`, ratingBadge(b.rating),
    b.stale ? `<p class="sub">This AI session is more than 10 days older than the prices (${dLong(DATA[t].forecast.last_date)}), so it does not change the forecast. Run the analysis again to bring it in.</p>`
      : `<div class="kvs" style="margin:0"><div class="kv"><p class="k">Models (Ensemble)</p><p class="v ${cls(b.base)}">${fmtPct(b.base * 100)}</p><p class="s">${fmtP(b.last * (1 + b.base))} ${esc(cur)}</p></div>
        <div class="kv"><p class="k">AI view</p><p class="v ${cls(b.view)}">${fmtPct(b.view * 100)}</p><p class="s">${esc(b.rating)} × typical 7-day move</p></div>
        <div class="kv"><p class="k">AI-adjusted 7 days</p><p class="v ${cls(b.adj)}">${fmtPct(b.adj * 100)}</p><p class="s">${fmtP(b.price)} ${esc(cur)} · ${aiWhy(b)}</p></div></div>`);
}
function renderInv() {
  const A = invAmt(), x = X.inv, H = x.h === 7 ? 7 : 5;
  const rows = ORDER.map(t => {
    const fc = invFc(t, x.src), ret = fc.end / fc.last - 1, dly = DATA[t].daily, dRet = dly.final_expected_return;
    const llm = EXTRA.ai[t] ? EXTRA.ai[t].latest : null, bt = (EXTRA.backtest[t] || {}).Ensemble;
    return { t, fc, ret, val: A * (1 + ret), lo: fc.lo == null ? null : A * fc.lo / fc.last, hi: fc.hi == null ? null : A * fc.hi / fc.last, dly, dRet, dVal: A * (1 + dRet), llm,
      votes: [vote3(ret), dly.final_signal === "BUY" ? 1 : dly.final_signal === "SELL" ? -1 : 0, llm ? ratingVote(llm.rating) : 0], bt,
      age: daysBetween(DATA[t].forecast.last_date, ASOF) };
  });
  rows.sort((a, b) => b.ret - a.ret);
  const mx = Math.max(...rows.flatMap(r => [Math.abs(r.ret), r.lo == null ? 0 : Math.abs(r.lo / A - 1), r.hi == null ? 0 : Math.abs(r.hi / A - 1)]), 0.001) * 1.1, pos = v => Math.max(1, Math.min(99, 50 + v / mx * 50));
  const srcNote = x.src === "Daily" ? "the daily 5-model signal's expected 7-day return, spread evenly over the 7 days" : x.src === "Agent" ? "the reviewed agent path" : x.src === "AI" ? "the Ensemble path tilted by each company's latest AI rating (the Ensemble alone where there is no recent rating)" : `the ${x.src} path from forecast_lib`;
  const tk = x.t, trk = invTrack(tk), bt = EXTRA.backtest[tk] || {};
  const best = rows[0], worst = rows[rows.length - 1];
  const table = rows.map(r => `<tr class="click" data-t="${esc(r.t)}"><td><span class="tkr">${esc(r.t)}</span><div class="sub">${esc(DATA[r.t].meta.name)}</div></td>
    <td class="${r.age > 5 ? "" : ""}" style="${r.age > 5 ? "color:var(--hold)" : ""}">${dShort(DATA[r.t].forecast.last_date)}</td>
    <td>${invShow(r.t, r.val)}</td><td class="${cls(r.ret)}">${invSigned(r.t, r.val - A)}</td><td class="${cls(r.ret)}">${fmtPct(r.ret * 100)}</td>
    <td>${r.lo == null ? "–" : `${invSigned(r.t, r.lo - A)} to ${invSigned(r.t, r.hi - A)}`}</td>
    <td>${sigBadge(r.dly.final_signal)} <span class="sub">${invShow(r.t, r.dVal)}</span></td>
    <td>${r.llm ? `${ratingBadge(r.llm.rating)} <span class="sub">${dShort(r.llm.date)}</span>` : '<span class="sub">no session</span>'}</td>
    <td class="votes" title="forecast / daily model / LLM rating">${r.votes.map(voteTxt).join(" ")}</td>
    <td>${r.bt ? `${invShow(r.t, A * r.bt.equity[r.bt.equity.length - 1])} <span class="sub">vs ${invShow(r.t, A * r.bt.buyhold_eq[r.bt.buyhold_eq.length - 1])}</span>` : "–"}</td></tr>`).join("");
  const bars = rows.map(r => `<div class="row wide"><span class="mono">${esc(r.t)}</span><div class="dv"><b class="${r.ret >= 0 ? "pos" : "neg"}" style="${r.ret >= 0 ? `left:50%;width:${pos(r.ret) - 50}%` : `right:50%;width:${50 - pos(r.ret)}%`}"></b>${r.lo == null ? "" : `<u style="left:${pos(r.lo / A - 1)}%;width:${pos(r.hi / A - 1) - pos(r.lo / A - 1)}%"></u>`}</div><span class="val"><span class="${cls(r.ret)}">${invSigned(r.t, r.val - A)}</span> · ${fmtPct(r.ret * 100)}</span></div>`).join("");
  const sessRows = trk ? trk.list.slice().reverse().slice(0, 14).map(({ s, o }) => {
    const f = followFactor(o.rating, o.r || 0), tone = o.pending ? "" : o.rating === "HOLD" ? "hold" : f > 1 ? "buy" : "sell";
    return `<tr><td class="t">${dShort(s.date)}</td><td>${ratingBadge(o.rating[0] + o.rating.slice(1).toLowerCase())}</td><td>${fmtP(o.entry)}</td><td>${o.pending ? "–" : fmtP(o.exit)}</td><td class="${o.pending ? "flat" : cls(o.r)}">${o.pending ? "pending" : fmtPct(o.r * 100)}</td>
      <td>${o.pending ? "–" : invShow(tk, A * f)}</td><td>${o.pending ? "–" : invShow(tk, A * (1 + o.r))}</td><td><span class="badge ${tone}">${o.pending ? "pending" : o.rating === "HOLD" ? "no trade" : f > 1 ? "right" : "wrong"}</span></td></tr>`;
  }).join("") : "";
  const btRows = Object.entries(bt).map(([m, r]) => `<tr><td class="t">${esc(m)}</td><td>${r.n}</td><td>${invShow(tk, A * r.equity[r.equity.length - 1])}</td><td>${invShow(tk, A * r.buyhold_eq[r.buyhold_eq.length - 1])}</td><td class="${cls(r.strategy_pct)}">${fmtPct(r.strategy_pct, 1)}</td></tr>`).join("");
  return `<div class="page-h"><h1>Return Calculator</h1><p>If you put in ${A} today, what each company's forecast, daily model, notebook LLM and past record say you would have in ${H} days.</p></div>
    ${card("Your investment", "Change the amount, the money basis or the forecast used.", "", `<div class="formgrid" style="grid-template-columns:repeat(auto-fit,minmax(170px,1fr))">
      <div class="field"><label for="inv-a">Amount per company</label><input class="inp w-sm" id="inv-a" data-bind="inv-amt" type="number" min="1" step="any" value="${esc(x.amt)}"></div>
      <div class="field"><label for="inv-b">Amount is in</label><select id="inv-b" data-bind="inv-basis"><option value="usd" ${x.basis === "usd" ? "selected" : ""}>US dollars (converted to each currency)</option><option value="local" ${x.basis === "local" ? "selected" : ""}>Each company's own currency</option></select></div></div>
      <div class="field" style="margin-top:16px"><span class="lbl">Days ahead</span>${pillTabs([["5", "5 days"], ["7", "7 days"]], String(H), "data-inv-h")}</div>
      <div class="field" style="margin-top:16px"><span class="lbl">Forecast used</span>${pillTabs(INV_SRC.map(([id, l]) => [id, l]), x.src, "data-inv-src")}</div>`)}
    <div class="stats">
      ${statCard("Best expected", esc(best.t), `${fmtPct(best.ret * 100)} · ${invSigned(best.t, best.val - A)}`, best.ret > 0 ? "buy" : "sell", "line")}
      ${statCard("Worst expected", esc(worst.t), `${fmtPct(worst.ret * 100)} · ${invSigned(worst.t, worst.val - A)}`, worst.ret > 0 ? "buy" : "sell", "chart")}
      ${statCard("All 8 together", invAmt() ? fmtPct(rows.reduce((q, r) => q + r.ret, 0) / rows.length * 100) : "–", `average ${H}-day return`, "accent", "wallet")}
      ${statCard("Amount in", x.basis === "usd" ? "$" + A : A + " each", x.basis === "usd" ? "converted at saved FX" : "own currency", "", "db")}
    </div>
    ${card(`Company by company ${hint(GLOSS.votes)}${hint(GLOSS.past)}`, `Value of ${A} after ${H} days using ${esc(srcNote)}. Click a row to open that ticker.`, "", `<div class="scroll"><table><thead><tr><th>Company</th><th>Price as of</th><th>Value in ${H} days</th><th>Profit / loss</th><th>Return</th><th>10–90% range (profit / loss)</th><th>Daily model</th><th>Notebook LLM</th><th>Votes</th><th>Past record</th></tr></thead><tbody>${table}</tbody></table></div>
      <p class="src">Votes show forecast, daily model and LLM rating in that order (↑ up, ↓ down, – flat or hold). "Past record" is what the amount would be if you had followed the Ensemble's saved day-7 calls since the log began, then (smaller) what buying and holding on the same dates would give. Forecasts start from each company's own last close, so DSE companies start from Oct 4. The range comes from the reviewer's past forecast errors on a small test set, so a result outside it is possible. Costs and tax are not included. Research output, not investment advice.</p>`)}
    <div class="two">
      ${card("Expected gain per company", `On ${A}. Bar = expected return, line = 10–90% range.`, "", `<div class="rows">${bars}</div>`)}
      ${card(`Growth of ${A} over 7 days`, "Each company's forecast path, day by day.", "", `<div class="chartbox" id="invgrow"></div><div class="legend" id="invgrow-l"></div>`)}
    </div>
    ${card("Notebook record by company", "What the LLM analyst notebook said in past runs, and what 100 would have done if you followed it for 7 days.", "", `<div class="field"><span class="lbl">Company</span>${pillTabs(ORDER.map(t => [t, t, !EXTRA.ai[t]]), tk, "data-inv-t")}</div>
      ${!trk ? `<p class="sub" style="margin-top:14px">No notebook session for ${esc(tk)}.</p>` : `
      <div class="stats" style="margin-top:16px">
        ${statCard("Notebook runs", trk.list.length, `${trk.done.length} graded, ${trk.list.length - trk.done.length} pending`, "", "brain")}
        ${statCard("Direction right", trk.trades.length ? `${trk.hits}/${trk.trades.length}` : "–", trk.trades.length ? `${(trk.hits / trk.trades.length * 100).toFixed(0)}% of BUY/SELL calls` : "no BUY/SELL calls yet", trk.trades.length && trk.hits / trk.trades.length > 0.5 ? "buy" : "hold", "check")}
        ${statCard(`${A} following BUY/SELL`, trk.avgFollow == null ? "–" : invShow(tk, A * (1 + trk.avgFollow)), trk.avgFollow == null ? "no trades" : `${fmtPct(trk.avgFollow * 100)} average per call`, trk.avgFollow > 0 ? "buy" : trk.avgFollow < 0 ? "sell" : "", "wallet")}
        ${statCard(`${A} on HOLD days`, trk.avgHold == null ? "–" : invShow(tk, A * (1 + trk.avgHold)), trk.avgHold == null ? "" : `if you had held instead: ${fmtPct(trk.avgHold * 100)} average`, "", "clock")}
      </div>
      <div class="scroll"><table><thead><tr><th>Run date</th><th>Rating</th><th>Entry</th><th>Close +7d</th><th>Move</th><th>${A} following it</th><th>${A} if held</th><th>Result</th></tr></thead><tbody>${sessRows}</tbody></table></div>
      <p class="src">Source: notebooks/trading_log_*.json (rating) with the saved closes. BUY goes long, SELL goes short, HOLD makes no trade. Calls are one run each and overlap in time, so the average per call is not a compounded return.</p>`}`)}
    ${Object.keys(bt).length ? card(`Forecast record for ${esc(tk)}`, `Value of ${A} if you followed each model's saved day-7 calls, from the first graded call to the last.`, "", `<div class="chartbox" id="invbt"></div><div class="legend" id="invbt-l"></div>
      <div class="scroll" style="margin-top:14px"><table><thead><tr><th>Model</th><th>Trades</th><th>${A} followed</th><th>${A} buy and hold</th><th>Strategy return</th></tr></thead><tbody>${btRows}</tbody></table></div><p class="src">Source: forecast_log graded rows. Same method as the Backtest page: long when the model predicted up, short when down, no costs.</p>`) : ""}`;
}
function drawInv() {
  const A = invAmt(), g = $("#invgrow"); if (!g) return;
  const days = ["d0", "d1", "d2", "d3", "d4", "d5", "d6", "d7"];
  const ser = ORDER.map((t, i) => { const fc = invFc(t, X.inv.src); return { name: t, v: [A, ...fc.path.map(p => A * p / fc.last)], color: `var(${COLORS[i % 8]})`, w: 2 }; });
  lineChart(g, days, ser, { label: "Growth of the invested amount", base: A, fmt: v => v.toFixed(2), lab: s => s === "d0" ? "now" : "day " + s.slice(1) });
  $("#invgrow-l").innerHTML = ser.map(s => `<span><i style="background:${s.color}"></i>${esc(s.name)}</span>`).join("");
  const b = $("#invbt"), bt = EXTRA.backtest[X.inv.t] || {}, e = bt.Ensemble; if (!b || !e) return;
  const n = e.equity.length;
  lineChart(b, e.dates, [{ name: "Ensemble followed", v: e.equity.map(v => A * v), color: "var(--accent)", w: 2.4 }, { name: "Buy and hold", v: e.buyhold_eq.map(v => A * v), color: "var(--faint)", dash: true, w: 1.6 }], { label: "Followed versus buy and hold", base: A, fmt: v => v.toFixed(0) });
  $("#invbt-l").innerHTML = `<span><i style="background:var(--accent)"></i>Ensemble followed</span><span><i style="background:var(--faint)"></i>Buy and hold</span>`;
}


/* ----- help: glossary, hints, plain-language summary ----- */
Object.assign(ICONS, { help: '<circle cx="12" cy="12" r="10"/><path d="M9.09 9a3 3 0 0 1 5.83 1c0 2-3 3-3 3"/><path d="M12 17h.01"/>' });
const GLOSS = {
  views: ["Which forecast", "Ensemble: the average of the two models with the smallest past error. Fusion: a learned blend of all models. Fusion Top 3: the three most accurate models, weighted by accuracy. Reviewed agent: the three best models after an automatic review that can shrink or clip the forecast. Best single model: the one model that called direction best in testing."],
  rec: ["Daily signal", "Five models each predict the 7-day return and the one that was best in testing is used. Above the threshold the signal is BUY, below minus the threshold it is SELL, otherwise HOLD. The threshold grows when the stock has been more volatile."],
  daily: ["Five daily models", "Each bar is one model's predicted 7-day return. 'dir' is how often that model called up or down correctly on past data it had not seen."],
  fc: ["7-day forecast", "The 10–90% range means that in past tests the real price ended inside it about 8 times out of 10. 'Always-UP' is how often simply guessing 'up' was right, a baseline a useful model should beat."],
  models: ["Error vs naive", "Compares a model's average error with simply predicting 'no change'. Below 1.00 means the model beat that simple guess. Direction hit is how often it got up or down right on data it had not seen."],
  agent: ["Reviewer", "A rule-based check, not an AI. It flags old data, replaces weak models, shrinks the forecast toward 'no change' when past accuracy was poor, and limits unusually large moves."],
  track: ["Track record", "Every saved forecast is later checked against the real closing price. 'Within 1%' means the price landed within 1% of the forecast. The p-value says whether the direction record could be luck: below 0.05 is usually read as real skill."],
  votes: ["Votes", "Three separate views: the forecast's direction, the daily signal and the AI analyst's rating. ↑ up, ↓ down, – flat or hold."],
  past: ["Past record", "What your amount would be if you had followed the Ensemble's past 7-day calls (buy when it said up, sell short when it said down), next to simply buying and holding."],
  bt: ["Backtest terms", "Edge is the direction hit rate minus the 'always up' rate, in percentage points: positive means the model beat that simple guess. Max drawdown is the largest fall from a previous high."],
  dd: ["Drawdown", "How far your account was below its highest value so far."],
  runfc: ["Run the forecast", "Fits the chosen forecast models on the server, on the same CSV price files the notebooks use, and draws the new 7-day path. Ensemble, Fusion and the reviewer range are rebuilt from the models you pick. Each run is stored; the same company, date and models give back the stored run."],
  ind: ["Indicators", "RSI above 70 is often called overbought and below 30 oversold. MACD above its signal line means upward momentum. %B shows where the price sits in its Bollinger band (0 = lower band, 1 = upper band). Volatility is the typical daily move over 30 days. They describe the recent past and are not a forecast."],
  aidec: ["Decision", "BUY, HOLD or SELL after the whole notebook has run: analysts, the bull vs bear debate, the trader, the risk debate and the portfolio manager. The decision verifier can override the signal when the text and the rating disagree. Overweight counts as BUY and Underweight as SELL."],
  debate: ["AI debate", "Two LLM agents argue for (bull) and against (bear) the stock over several rounds using the analysts' reports; a facilitator weighs the evidence. A second debate between aggressive, conservative and neutral risk analysts tests the trader's plan."],
  aiadj: ["AI-adjusted forecast", "The 7-day forecast after the latest AI analysis (the company notebook run with an LLM) has had its say. The AI rating (Buy +1, Overweight +½, Hold 0, Underweight −½, Sell −1) times the stock's typical 7-day move is its view; the adjusted move is 80% the models and 20% that view. Once the AI has five graded calls, its weight moves between 10% and 35% with how often it was right. A session more than 10 days older than the prices does not count."],
  runai: ["Run AI analysis", "Runs the company notebook from top to bottom on the server (the same cells as Run All in Jupyter) with the LLM you choose, then shows the new session. Ollama models run on the server itself; OpenAI, Claude, Gemini and Groq models run with the server's API key, so no key is ever typed into this page. Each run is stored by company, date and model, and the notebook also caches its analysts for the same date."],
};
let hintN = 0;
const hint = g => `<span class="help" tabindex="0" role="note" aria-label="${esc(g[0])}: ${esc(g[1])}"><span aria-hidden="true">?</span><span class="tipbox" aria-hidden="true"><b>${esc(g[0])}</b>${esc(g[1])}</span></span>`;
function moveWord(r) {
  const a = Math.abs(r);
  if (a < 0.005) return "stay roughly flat";
  const dir = r > 0 ? "rise" : "fall";
  return a < 0.02 ? `${dir} slightly` : a < 0.05 ? `${dir} moderately` : `${dir} sharply`;
}
function plainSummary(d) {
  const t = S.t, f = d.forecast, last = f.last_price, p = fcPath(t), band = fcBand(t), cur = d.meta.currency;
  if (!p) return "";
  const r = p[6] / last - 1, conf = d.agent && d.agent.plan ? d.agent.plan.confidence : null, age = daysBetween(f.last_date, ASOF);
  return `<section class="card summary"><span class="fi">${ic("check")}</span><div><p class="l">In plain words</p>
    <p class="lead2">Over the next 7 trading days the Ensemble expects ${esc(d.meta.name)} to <b>${moveWord(r)}</b> (${fmtPct(r * 100)}, to about ${fmtP(p[6])} ${esc(cur)}). The daily signal is ${sigBadge(d.daily.final_signal)}.${conf ? ` Confidence is <b>${esc(conf.toLowerCase())}</b>.` : ""}${band ? ` In past tests the price usually ended between ${fmtP(band[6][0])} and ${fmtP(band[6][1])} ${esc(cur)}.` : ""}</p>
    <p class="lead2" style="margin-top:6px">After 5 trading days: <b>${fmtPct((p[4] / last - 1) * 100)}</b> (about ${fmtP(p[4])} ${esc(cur)})${band ? `, usually between ${fmtP(band[4][0])} and ${fmtP(band[4][1])}` : ""}.${band ? ` As a profit or loss on the amount invested, that is about ${fmtPct((p[4] / last - 1) * 100)} with a range of ${fmtPct((band[4][0] / last - 1) * 100)} to ${fmtPct((band[4][1] / last - 1) * 100)}, ` : " "}before costs.</p>
    ${(b => b && !b.stale ? `<p class="lead2" style="margin-top:6px">The latest AI analysis rated it ${ratingBadge(b.rating)} on ${dShort(b.date)}; with that view the forecast becomes <b>${fmtPct(b.adj * 100)}</b> (about ${fmtP(b.price)} ${esc(cur)}; ${aiWhy(b)}).</p>` : "")(aiBlend(t))}
    ${age > 5 ? `<p class="sub" style="margin-top:6px;color:var(--hold)">These prices are from ${dLong(f.last_date)}, ${age} days before the export.</p>` : ""}</div></section>`;
}
function renderHelp() {
  const steps = [["Pick a stock", "Use the cards on Home, the Forecast menu or the search box."], ["Run the forecast", "Press Run forecast to see the 7-day path, the daily signal and every model's numbers."], ["Read the plain summary", "The 'In plain words' box says what the models expect and how sure they are."], ["Check the evidence", "Track record, Backtest and Memory show how past calls really did."]];
  return `<div class="page-h"><h1>Help and glossary</h1><p>How to use this site, and what each term means, in plain language.</p></div>
    ${card("How to use this site", "", "", `<ol class="steps4">${steps.map(([h, p], i) => `<li><span class="num">${i + 1}</span><div><h3>${h}</h3><p>${p}</p></div></li>`).join("")}</ol>`)}
    ${card("Glossary", "Hover or focus the ? next to a heading anywhere on the site to see the same text.", "", `<dl class="gloss">${Object.values(GLOSS).map(([h, p]) => `<div><dt>${esc(h)}</dt><dd>${esc(p)}</dd></div>`).join("")}</dl>`)}
    ${card("About the data", "", "", `<ul class="plain"><li>All numbers were exported from the project's Python code on ${dLong(ASOF)}. This site replays them; it does not train models or fetch live prices.</li><li>US, Saudi and Hong Kong prices run to mid-October. The four Dhaka Stock Exchange stocks stop at ${dLong(DATA.ABBANK ? DATA.ABBANK.forecast.last_date : ASOF)}, so their forecasts start from older prices.</li><li>The watchlist and your settings are saved only in this browser.</li><li>This is a research tool. Nothing here is investment advice.</li></ul>`)}`;
}

/* ----- router ----- */
function goPage(p) { if (S.running) return; S.more = false; S.page = p; $("#app").classList.remove("open"); render(false); $("#scroller").scrollTop = 0; }
function renderExtra() {
  return ({ ai: renderAI, watch: renderWatch, inv: renderInv, bt: renderBT, data: renderData, news: renderNews, fund: renderFund, papers: renderPapers, mem: renderMem, set: renderSet, help: renderHelp })[S.page]();
}
function afterRender() { if (S.page === "bt") drawBT(); if (S.page === "inv") drawInv(); if (S.page === "dash") drawHome(); markRows(); }

/* ---------- top nav ---------- */
const NAV_MAIN = [["dash", "Home"], ["tk", "Forecast"], ["inv", "Calculator"], ["bt", "Backtest"], ["ai", "AI Analysis"]];
const CHEV = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" style="width:14px;height:14px"><path d="m6 9 6 6 6-6"/></svg>';
function renderNav() {
  const cur = id => (S.page === id ? ' aria-current="page"' : "");
  const more = NAVS.filter(([id]) => !NAV_MAIN.some(m => m[0] === id));
  const moreOn = more.some(([id]) => S.page === id);
  $("#nav").innerHTML = NAV_MAIN.map(([id, l]) => `<button type="button" class="nl" data-page="${id}"${cur(id)}>${l}</button>`).join("") +
    `<span class="morewrap"><button type="button" class="nl" data-act="more" aria-haspopup="true" aria-expanded="${!!S.more}"${moreOn ? ' aria-current="true"' : ""}>More ${CHEV}</button>` +
    (S.more ? `<div class="pop">${more.map(([id, l, i]) => `<button type="button" class="nl" data-page="${id}"${cur(id)}>${ic(i)}${l}</button>`).join("")}</div>` : "") + `</span>`;
  $("#sheetnav").innerHTML = NAVS.map(([id, l, i]) => `<button type="button" class="nl" data-page="${id}"${cur(id)}>${ic(i)}${id === "dash" ? "Home" : l}</button>`).join("") +
    `<div class="navh">Forecast by stock</div>` +
    ORDER.map(t => { const sig = S.ran[t] ? DATA[t].daily.final_signal : ""; return `<button type="button" class="nl" data-t="${esc(t)}"${S.page === "tk" && S.t === t ? ' aria-current="page"' : ""}><span class="sym">${esc(t)}</span><span class="nm">${esc(DATA[t].meta.name)}</span><span class="dot ${sig}" role="img" aria-label="${sig ? "signal " + sig : "not run"}"></span></button>`; }).join("");
}

/* ---------- home ---------- */
X.home = { t: ORDER[0] };
function renderDash() {
  const ht = X.home.t, hd = DATA[ht], hf = hd.forecast, hp = fcPath(ht), hr = hp ? hp[6] / hf.last_price - 1 : null;
  const graded = ORDER.reduce((q, t) => q + Math.max(0, ...((DATA[t].hitrate.per_model || []).map(r => r.graded_calls || 0))), 0);
  const cards = ORDER.map(t => {
    const d = DATA[t], f = d.forecast, p = fcPath(t), r = p ? p[6] / f.last_price - 1 : null, cl = d.history.slice(-60).map(h => h[1]), age = daysBetween(f.last_date, ASOF);
    return `<button type="button" class="tcard" data-t="${esc(t)}"><div class="top"><span class="tkr">${esc(t)}</span><span class="chip ${r == null ? "flat" : r > 0.0005 ? "up" : r < -0.0005 ? "down" : "flat"}">${r == null ? "–" : fmtPct(r * 100)} in 7d</span></div>
      <div><div class="nm">${esc(d.meta.name)}</div><div class="px mono">${fmtP(f.last_price)} <span class="sub">${esc(d.meta.currency)}</span></div></div>${spark(cl, 220, 38)}
      <div class="sub" style="${age > 5 ? "color:var(--hold)" : ""}">price as of ${dShort(f.last_date)}${age > 5 ? ` (${age} days old)` : ""}</div>
      ${(b => b ? `<div class="sub ailine">AI ${ratingBadge(b.rating)} ${b.stale ? "too old to count" : `→ <b class="${cls(b.adj)}">${fmtPct(b.adj * 100)}</b> with AI`}</div>` : "")(aiBlend(t))}</button>`;
  }).join("");
  const feats = [["inv", "pulse", "Return Calculator", "Put in 100 and see what each company's forecast, model and notebook say you would have in a week."], ["bt", "chart", "Backtest", "How each model's past calls would have done against buy and hold."], ["ai", "line", "AI Analysis", "Read what the LLM analyst panel decided, stage by stage."], ["news", "news", "News and fundamentals", "News sentiment, P/E, EPS and 52-week ranges per company."], ["mem", "brain", "Memory", "Past predictions graded against what really happened."]];
  const rows = ORDER.map(t => {
    const d = DATA[t], f = d.forecast, ran = S.ran[t], cl = d.history.slice(-30).map(h => h[1]);
    const ch = (cl[cl.length - 1] / cl[0] - 1) * 100, p = fcPath(t), ens = p ? (p[6] / f.last_price - 1) * 100 : null, age = daysBetween(f.last_date, ASOF);
    return `<tr class="click" data-t="${esc(t)}"><td><span class="tkr">${esc(t)}</span><div class="sub">${esc(d.meta.name)}</div></td><td>${fmtP(f.last_price)} <span class="sub">${esc(d.meta.currency)}</span></td><td>${spark(cl)}</td><td class="${cls(ch)}">${fmtPct(ch)}</td><td style="${age > 5 ? "color:var(--hold)" : ""}">${dShort(f.last_date)}</td><td>${ran ? sigBadge(d.daily.final_signal) : `<span class="badge">not run</span>`}</td><td class="${cls(ens)}">${fmtPct(ens)}</td><td>${(b => b && !b.stale ? `${ratingBadge(b.rating)} <span class="${cls(b.adj)}">${fmtPct(b.adj * 100)}</span>` : b ? `${ratingBadge(b.rating)} <span class="sub">old</span>` : "–")(aiBlend(t))}</td></tr>`;
  }).join("");
  const act = S.log.length ? `<ul class="actlist">${S.log.slice(0, 8).map(e => `<li>${ic("pulse")}<div><p><span class="tk">${esc(e.t)}</span> <span style="color:var(--muted)">${esc(e.msg)}</span></p><p class="tm">${esc(e.ts)}</p></div></li>`).join("")}</ul>` : emptyState("pulse", "No activity yet", "Open a stock and press Run forecast to see pipeline events here.");
  const dse = ORDER.filter(t => DATA[t].meta.source === "dse");
  return `<section class="hero">
    <div><span class="eyebrow"><span class="dotc"></span>Data as of ${dLong(ASOF)}</span>
      <h1>Seven-day stock forecasts, <em>clearly explained</em>.</h1>
      <p class="lead">See what six forecasting models expect for eight stocks across four markets, the daily BUY, HOLD or SELL signal, and a graded record of how past calls turned out.</p>
      <div class="cta"><button type="button" class="btn primary" id="runall">Show all signals</button><button type="button" class="btn secondary" data-page="help">How it works</button></div>
      <div class="herostats"><div><b>8</b><span>stocks</span></div><div><b>6</b><span>models and 3 blends</span></div><div><b>${graded.toLocaleString()}</b><span>past calls graded</span></div></div></div>
    <div class="card herochart"><div class="field" style="margin-bottom:14px">${pillTabs(ORDER.map(t => [t, t]), ht, "data-home")}</div>
      <div class="hc-top"><div><div class="sub">${esc(hd.meta.name)}</div><div class="hc-px mono">${fmtP(hf.last_price)} <span class="sub">${esc(hd.meta.currency)}</span></div></div>
        <span class="chip ${hr == null ? "flat" : hr > 0.0005 ? "up" : hr < -0.0005 ? "down" : "flat"}">${hr == null ? "–" : fmtPct(hr * 100)} expected in 7 days</span></div>
      <div class="chartbox" id="homechart"></div><div class="legend" id="homechart-l"></div>
      <p class="src">Ensemble forecast with the reviewer's 10–90% range.${(b => b && !b.stale ? ` With the latest AI rating (${esc(b.rating)}): ${fmtPct(b.adj * 100)}.` : "")(aiBlend(ht))} <button type="button" class="link" data-t="${esc(ht)}">Open ${esc(ht)}</button></p></div>
  </section>
  <section><div class="sec-h"><div><h2>How it works</h2><p>Four steps from a stock to the evidence behind its forecast.</p></div><button type="button" class="link" data-page="help">Read the glossary</button></div><ol class="steps4">${[["Pick a stock", "Choose one of the eight cards below."], ["Run the forecast", "See the 7-day path and the daily signal."], ["Read the summary", "A plain-language box says what to expect."], ["Check the evidence", "Track record and backtest show past accuracy."]].map(([h, p], i) => `<li><span class="num">${i + 1}</span><div><h3>${h}</h3><p>${p}</p></div></li>`).join("")}</ol></section>
  <section><div class="sec-h"><div><h2>Pick a stock</h2><p>Each card shows the Ensemble's 7-day call. Open one for the full forecast.</p></div></div><div class="tgrid">${cards}</div></section>
  <section><div class="sec-h"><div><h2>Everything in one place</h2><p>Beyond the forecast, you can test ideas and read the evidence behind them.</p></div></div>
    <div class="feats">${feats.map(([pg, i, t, d]) => `<button type="button" class="feat" data-page="${pg}"><span class="fi">${ic(i)}</span><h3>${t}</h3><p>${d}</p></button>`).join("")}</div></section>
  <section><div class="sec-h"><div><h2>All stocks at a glance</h2><p>Click a row to open it.</p></div></div>
    <div class="dashgrid">${card("Watchlist", "Last close, 30-day move and the Ensemble's call.", "", `<div class="scroll"><table><thead><tr><th>Stock</th><th>Last close</th><th>30 days</th><th>30d change</th><th>Price data to</th><th>Signal</th><th>Ensemble 7d</th><th>With AI ${hint(GLOSS.aiadj)}</th></tr></thead><tbody>${rows}</tbody></table></div><p class="src">Source: data/ and daily_data/ price files; signals from predictor_lib; Ensemble from forecast_lib; With AI = the Ensemble tilted by the latest AI analysis.</p>`)}
    ${card("Session activity", "Every run in this browser session.", "", act)}</div></section>
  ${dse.length ? `<section class="card warn"><div style="display:flex;gap:10px;align-items:flex-start;flex-wrap:wrap"><span class="badge hold">Data note</span><p style="font-size:13px;color:var(--muted);flex:1 1 300px">The daily BUY, HOLD or SELL for ${dse.map(esc).join(", ")} uses prices up to ${dLong(DATA[dse[0]].daily.data_end_date)}, because daily.py reads only the data snapshot. The 7-day forecasts also use the notebook logs, up to ${dLong(DATA[dse[0]].forecast.last_date)}.</p></div></section>` : ""}`;
}
function drawHome() {
  const h = $("#homechart"); if (!h) return;
  const t = X.home.t, d = DATA[t], f = d.forecast, path = fcPath(t), band = fcBand(t);
  const hist = d.history.slice(-90), n = hist.length, labels = [...hist.map(r => r[0]), ...(path ? f.dates : [])], N = labels.length;
  const series = [{ name: "Close", v: [...hist.map(r => r[1]), ...Array(N - n).fill(null)], color: "var(--accent)", w: 2.4 }];
  if (path) series.push({ name: "Ensemble", v: [...Array(n - 1).fill(null), hist[n - 1][1], ...path], color: "var(--text)", w: 2.8 });
  const opts = { label: `${t} price and Ensemble forecast`, fmt: v => fmtP(v) };
  if (path && band) {
    const lo = [...Array(n).fill(null), ...band.map(b => b[0])], hi = [...Array(n).fill(null), ...band.map(b => b[1])];
    opts.band = { lo, hi }; series.push({ name: "Low 10%", v: lo, color: "var(--sell)", noline: true }, { name: "High 90%", v: hi, color: "var(--buy)", noline: true });
  }
  lineChart(h, labels, series, opts);
  $("#homechart-l").innerHTML = series.filter(s => !s.noline).map(s => `<span><i style="background:${s.color}"></i>${esc(s.name)}</span>`).join("") + (opts.band ? `<span><i style="background:var(--text);opacity:.25;height:8px"></i>10–90% range</span>` : "");
}
