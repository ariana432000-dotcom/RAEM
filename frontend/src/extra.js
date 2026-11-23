/* ---------- extra pages ---------- */
const NAVS = [["dash", "Home", "dash"], ["stocks", "Stocks", "chart"], ["ai", "AI Analysis", "line"], ["watch", "Watchlist", "star"], ["bt", "Backtest", "chart"], ["eval", "Evaluation", "check"], ["data", "Dataset Explorer", "db"], ["news", "News", "news"], ["fund", "Fundamentals Check", "check"], ["mem", "Memory", "brain"], ["set", "Settings", "gear"], ["help", "Help", "help"]];
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
const settings = Object.assign({ theme: "dark", win: 120, unit: "price", view: "Agent" }, lsGet("raem:set", {}));
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
const d0 = t => {
  if (DATA[t] && DATA[t].meta.name !== t) return `${DATA[t].meta.name} (${t})`;
  const r = X.stk && X.stk.list && X.stk.list.stocks.find(q => q.code === t);
  return r && r.name ? `${r.name} (${t})` : t;
};
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
  if (live) ensureStocks();
  const choices = live ? [...new Set([...LIVE.models.analysis_tickers, ...AI_T, ...(X.ai.extra || [])])] : AI_T;
  if (choices.length && !choices.includes(X.ai.t)) X.ai.t = choices[0];
  const t = X.ai.t, a = EXTRA.ai[t];
  const head = `<div class="page-h"><h1>AI Analysis</h1><p>TradingAgents with RAEM's episodic memory, run with the LLM you choose (a local Ollama model or a cloud model) for any company: analysts, bull vs bear debate, trader, risk debate, portfolio manager and verifier. Research output, not advice.</p></div>
    ${live ? "" : card("", "", "", `<div class="field"><span class="lbl">Company</span>${pillTabs(ORDER.concat(choices.filter(x => !ORDER.includes(x))).map(x => [x, x, !choices.includes(x)]), t, "data-ai")}</div>${!live && !ORDER.every(x => AI_T.includes(x)) ? `<p class="sub" style="margin-top:10px">No saved session for ${ORDER.filter(x => !AI_T.includes(x)).join(", ")} yet. Run it on the hosted site.</p>` : ""}`)}`;
  if (live) {
    if (!a) return head + aiLiveCard(t, a) + aiHistoryCard(t) + (LIVE.busy["ai:" + t] ? "" : nonEmpty("brain", "No session for this company yet", "Choose a model and run the analysis above to create the first one."));
    return aiResults(t, a, head + aiLiveCard(t, a), aiHistoryCard(t));
  }
  if (!a) return head + nonEmpty("inbox", "No analyst session", "This ticker has no saved LLM session.");
  // Static copy (no server): show the latest saved analysis at once. Running today's analysis needs the hosted site.
  const infoCard = card(`AI analysis: ${esc(d0(t))}`, `Latest saved analysis: ${dLong(a.latest.date)}, ${Object.keys(a.latest.steps).length} stages${a.latest.llm_model ? ", " + esc(a.latest.llm_model) : ""}.`, ratingBadge(a.latest.rating),
    `<p class="note">This copy has no server, so it cannot run the analysis and shows the latest saved one. On the hosted site this card runs ${esc(t)}'s analysis for today's date with the model you choose.</p>`);
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
/* "If I invest today, what could the next 3 trading days bring?" on the Decision card: one expected profit or loss on a fixed stake
   (100 USD, or 10,000 Tk for Dhaka stocks) and a likely range. Uses the AI-adjusted path when the AI analysis is fresh, else the Ensemble. */
function aiThreeDay(t) {
  const d = DATA[t];
  if (!d) return "";   // a company that is not on the site has no forecast yet
  const f = d.forecast, last = f.last_price, cur = d.meta.currency, b = aiBlend(t), rb = fcBand(t), rp = fcPath(t);
  const path = b && !b.stale && b.path ? b.path : rp;
  if (!path) return "";
  const tk = cur === "BDT", A = tk ? 10000 : STAKE, k = 2, r = path[k] / last - 1;
  // the band holds the reviewed forecast's own error range; it is moved onto the path that is shown so both share one centre
  const lo = rb && rp ? (rb[k][0] - rp[k] + path[k]) / last - 1 : null, hi = rb && rp ? (rb[k][1] - rp[k] + path[k]) / last - 1 : null;
  const money = v => tk ? `${v >= 0 ? "+" : "−"}${Math.abs(v).toLocaleString("en-US", { maximumFractionDigits: 0 })} Tk` : stakeSigned(v);
  const stake = tk ? `${A.toLocaleString("en-US")} Tk` : stakeShow(A), old = f.dates[k] < refDate();
  return `<div class="sect"><p class="l">If you invest ${stake} today: after 3 trading days</p>
    <p class="v ${cls(r)}">${money(A * r)} <span class="vs">${fmtPct(r * 100)} expected</span></p>
    ${lo != null ? `<p class="small">Likely range: ${money(A * lo)} to ${money(A * hi)} (${fmtPct(lo * 100)} to ${fmtPct(hi * 100)}).</p>` : ""}
    <p class="small">${old ? `This forecast ends at the ${dShort(f.last_date)} close, so it is out of date; run the forecast again for today's estimate. ` : ""}An estimate from the forecast, before costs and tax; the real result can fall outside the range. Not investment advice.</p></div>`;
}
function aiDecisionCard(t, L) {
  const sig = aiSignal(L), d = DATA[t], seen = +(L.summary || {}).market_latest_close || null, cur = d ? d.meta.currency : "", entry = d ? d.forecast.last_price : seen;
  const fd = L.final_decision || "", summ = mdField(fd, "Executive Summary"), why = mdField(fd, "Investment Thesis"), hor = mdField(fd, "Time Horizon");
  const v = L.verifier || {}, b = aiBlend(t), band = fcBand(t);
  return card(`Decision ${hint(GLOSS.aidec)}`, `${esc(d0(t))} · ${dLong(L.date)}${L.llm_model ? " · " + esc(L.llm_model) : ""}`, sig ? sigBadge(sig) : ratingBadge(L.rating), `
    <div class="sigrow"><div class="bigsig ${esc(sig.toLowerCase())}">${esc(sig || "n/a")}</div>
      <div class="sigtext"><p class="conf">portfolio manager rating ${esc(L.rating || "n/a")}${v.status ? ` · verifier ${esc(String(v.status).toLowerCase())}${v.auto_overridden ? ", signal overridden" : ""}` : ""}</p>
        <p class="sum">${esc(summ || (fd ? fd.replace(/\*\*/g, "").slice(0, 400) : "No summary in this session."))}</p>
        ${hor && !/not provided/i.test(hor) ? `<p style="margin-top:6px;font-size:12px;color:var(--faint)">Time horizon: ${esc(hor)}</p>` : ""}</div></div>
    ${entry == null ? "" : d ? `<div class="sect"><p class="l">Entry (close of ${dShort(d.forecast.last_date)})</p><p class="v">${fmtP(entry)} ${esc(cur)}</p>
      ${seen && Math.abs(seen / entry - 1) > 0.01 ? `<p class="small">The analysis saw ${fmtP(seen)} ${esc(cur)} on ${dShort(L.date)}; the forecast uses closes to ${dShort(d.forecast.last_date)}. Run the forecast again for the newer prices.</p>` : ""}</div>`
      : `<div class="sect"><p class="l">Close seen by the analysis (${dShort(L.date)})</p><p class="v">${fmtP(entry)}</p><p class="small">${esc(t)} is not on the site yet, so it has no 7-day forecast. Add it from the search box to get one.</p></div>`}
    ${b && !b.stale ? `<div class="sect"><p class="l">7-day outlook with this decision</p><p class="v ${cls(b.adj)}">${fmtPct(b.adj * 100)} · ${fmtP(b.price)} ${esc(cur)}</p>${band ? `<p class="small">Range from past forecast errors: ${fmtP(band[6][0])} to ${fmtP(band[6][1])} ${esc(cur)}.</p>` : ""}</div>` : ""}
    ${aiThreeDay(t)}
    ${why ? `<div class="sect"><p class="l">Why ${esc(sig || L.rating || "")}</p><p class="why">${esc(why)}</p></div>` : ""}`);
}
const turnHtml = x => `<li class="turn ${SIDE_TONE[x.side] || "hold"}"><div class="who"><b>${esc(x.speaker)}</b><span class="badge ${SIDE_TONE[x.side] || "hold"}">${esc(x.side)}</span>${x.round ? `<span class="sub">round ${x.round}</span>` : ""}</div><div class="pre">${md(x.text)}</div></li>`;
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
    ${card("Analyst pipeline", "Each stage's saved output for this session.", "", `${pillTabs(stages.map(s => [s, s]), X.ai.stage, "data-stage")}<div class="scrollbox" style="margin-top:16px"><p class="sub" style="margin-bottom:8px">${esc(X.ai.stage)} · ${esc(st.time || "")}</p><div class="pre">${md(st.text)}</div></div><p class="src">Source: notebooks/trading_log_*.json (step outputs, shortened).</p>`)}
    ${aiSessionHistoryCard(t, a)}` + tail;
}
/* Session history: newest first. Interrupted runs (stopped before a final decision, so no rating) are hidden unless asked for;
   the first 15 are shown unless "show all" is on. Source: a notebook run (its logged model) or a website run (its model). */
function aiSessionHistoryCard(t, a) {
  const all = a.sessions.slice().reverse(), interrupted = all.filter(s => s.finished === false), finished = all.length - interrupted.length;
  const rows = X.ai.histInt ? all : all.filter(s => s.finished !== false), shown = X.ai.histAll ? rows : rows.slice(0, 15);
  const src = s => s.source === "notebook" ? `Notebook${s.model ? ` · ${esc(s.model)}` : ""}` : s.source === "website" ? `Website${s.model ? ` · ${esc(s.model)}` : ""}` : "–";
  const sub = `${finished} finished session${finished === 1 ? "" : "s"}${interrupted.length ? ` and ${interrupted.length} interrupted` : ""} for ${esc(t)} · showing ${shown.length}.`;
  const btns = `${rows.length > 15 ? `<button type="button" class="link" data-act="ai-hist-all">${X.ai.histAll ? "Show newest 15" : `Show all ${rows.length}`}</button>` : ""}${interrupted.length ? `<button type="button" class="link" data-act="ai-hist-int">${X.ai.histInt ? "Hide interrupted" : `Show interrupted (${interrupted.length})`}</button>` : ""}`;
  return card("Session history", sub, btns, `<div class="scroll"><table><thead><tr><th>Date</th><th>Started</th><th>Source</th><th>Signal</th><th>Rating</th></tr></thead><tbody>${shown.map(s => `<tr><td class="t">${dShort(s.date)}</td><td>${esc((s.start || "").slice(11, 16))}</td><td>${src(s)}</td><td>${s.signal && s.signal !== "nan" ? esc(s.signal) : "–"}</td><td>${s.finished === false ? `<span class="badge">interrupted</span>` : ratingBadge(s.rating)}</td></tr>`).join("")}</tbody></table></div><p class="src">An interrupted run stopped before its final decision, so it has no rating. Source: notebooks/trading_log_*.json, the session archive and website runs.</p>`);
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
    ${card("Add ticker", `This site has ${ORDER.length} companies. Add back any you removed; to add a new company use the search box at the top.`, "", `<form class="toolbar" id="wlform"><input class="inp grow" id="wlq" placeholder="Ticker (e.g. IBM)" aria-label="Ticker to add" style="text-transform:uppercase"><button class="btn primary" type="submit">Add</button>${missing.length ? `<span class="sub">Removed: ${missing.map(m => `<button type="button" class="link" data-act="wl-add" data-v="${esc(m)}">${esc(m)}</button>`).join(" ")}</span>` : ""}</form>${X.wlMsg ? `<p class="err" role="alert" style="margin-top:8px">${esc(X.wlMsg)}</p>` : ""}`)}
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
// the main forecast: the reviewer's checked average of the three lowest-error models, else the Ensemble
const fcPath = t => { const d = DATA[t], f = d.forecast, pl = d.agent && d.agent.plan; return pl && pl.path_ret ? pl.path_ret.map(r => f.last_price * (1 + r)) : f.paths.Ensemble || f.paths.Fusion || null; };
const fcName = t => DATA[t].agent && DATA[t].agent.plan && DATA[t].agent.plan.path_ret ? "Reviewed forecast" : "Ensemble";
function fcBand(t) {
  // band_ret holds the forecast-error quantiles around the forecast, so the price range is the reviewed path plus them
  // (as forecast_table() in forecast_agent.py does); without the path the range would sit around today's price instead
  const d = DATA[t];
  if (!d) return null;
  const last = d.forecast.last_price, pl = d.agent && d.agent.plan, p = pl && pl.band_ret, pr = pl && pl.path_ret;
  return p && pr ? p.map((b, i) => [last * (1 + pr[i] + b[0]), last * (1 + pr[i] + b[1])]) : null;
}
/* ----- outlook: what the main forecast means for a buyer (odds, trading costs, price steps, the stock's own past) ----- */
// Trading costs per side, assumed: DSE brokers charge about 0.3-0.5% a trade (commission, Howla and Laga, CDBL), online
// brokers abroad about 0.1%. Tax is left out.
const COST_SIDE = { dse: 0.004, other: 0.001 };
const MIN_ODDS = 0.6;  // BUY or SELL only when that outcome has at least these odds
const MIN_SKILL = 0.02;  // and only when the forecast's tested error was at least 2% below "no change" (less is noise)
const pc = v => Math.round(v * 100) + "%";
const atLeast = (v, min) => Math.round(v * 100) >= Math.round(min * 100);  // as shown, so 59.6% counts as the 60% it reads
function erf(x) {  // Abramowitz and Stegun 7.1.26, error below 1.5e-7
  const s = Math.sign(x), a = Math.abs(x), k = 1 / (1 + 0.3275911 * a);
  return s * (1 - ((((1.061405429 * k - 1.453152027) * k + 1.421413741) * k - 0.284496736) * k + 0.254829592) * k * Math.exp(-a * a));
}
const Phi = z => 0.5 * (1 + erf(z / Math.SQRT2));
function closesOf(t) {  // the two-year price table, then the forecast's own newer history
  const h = DATA[t].history || [], first = h.length ? h[0][0] : "9999";
  return [...((EXTRA.dataset || {})[t] || []).filter(r => r[0] < first).map(r => r[4]), ...h.map(r => r[1])].filter(v => v > 0);
}
function outlook(t) {
  const d = DATA[t], f = d && d.forecast, last = f && f.last_price, p = d && fcPath(t), pl = d && d.agent && d.agent.plan, band = d && fcBand(t);
  if (!p || !last) return null;
  const m = p[6] / last - 1, lo = band ? band[6][0] / last - 1 : null, hi = band ? band[6][1] / last - 1 : null;
  // the forecast is the middle outcome and the band's ends the 10% and 90% points; a half-normal on each side keeps the
  // band's lean (a stock whose past forecast errors were mostly upward keeps the longer upper tail)
  let cdf = null;
  if (band && lo < m && m < hi) { const sl = (m - lo) / 1.2816, sh = (hi - m) / 1.2816; cdf = x => Phi((x - m) / (x < m ? sl : sh)); }
  else if (band && lo < hi) { const c = (lo + hi) / 2, s = (hi - lo) / 2.5631; cdf = x => Phi((x - c) / s); }
  const side = d.meta.source === "dse" ? COST_SIDE.dse : COST_SIDE.other, cost = 2 * side;
  const pGain = cdf ? 1 - cdf(cost) : null, pFall = cdf ? cdf(0) : null;
  const err = pl && pl.holdout_mae_vs_naive != null ? pl.holdout_mae_vs_naive : ((f.scoreboard || []).find(r => r.model === "Ensemble") || {}).mae_vs_naive;
  const skill = err == null ? null : 1 - err, age = daysBetween(f.last_date, refDate());
  let sig = "HOLD", why;
  if (pGain == null || skill == null) why = "there is no tested error range for this forecast, so its odds are unknown";
  else if (age > 10) why = `the prices end on ${dLong(f.last_date)}, ${age} days ago; run the forecast again first`;
  else if (skill < MIN_SKILL) why = `in testing the forecast was ${skill > 0 ? "hardly" : "no"} more accurate than assuming no change (its error was ${err.toFixed(2)}× that of “no change”; at most 0.98× counts), so its direction is no reason to trade`;
  else if (atLeast(pGain, MIN_ODDS)) { sig = "BUY"; why = `the odds of a gain after trading costs are ${pc(pGain)}`; }
  else if (atLeast(pFall, MIN_ODDS)) { sig = "SELL"; why = `the odds of a fall are ${pc(pFall)}`; }
  else if (Math.abs(m) < cost) why = `the expected move (${fmtPct(m * 100)}) is smaller than the costs of a buy and a sell (${(cost * 100).toFixed(1)}%)`;
  else why = `the odds of a gain after costs (${pc(pGain)}) and of a fall (${pc(pFall)}) are both below ${pc(MIN_ODDS)}`;
  const cl = closesOf(t), steps = cl.slice(-250).map((v, i, a) => i ? Math.round(Math.abs(v - a[i - 1]) * 1e6) / 1e6 : 0).filter(v => v > 0);
  const tick = steps.length ? Math.min(...steps) : null;  // the smallest price step seen (0.10 tk on the DSE)
  const rets = []; for (let i = 0; i + 7 < cl.length; i++) rets.push(cl[i + 7] / cl[i] - 1);
  const srt = rets.slice().sort((a, b) => a - b), q = x => srt[Math.min(srt.length - 1, Math.floor(x * srt.length))];
  return { d, f, last, p, m, lo, hi, cdf, side, cost, pGain, pFall, err, skill, sig, why, age, tick,
    subTick: tick != null && tick / last > 0.002 && Math.abs(p[6] - last) < tick / 2, name: fcName(t), conf: pl ? pl.confidence : null,
    live: ((d.hitrate || {}).per_model || []).find(r => r.model === "Ensemble") || null,
    base: rets.length > 30 ? { n: rets.length, years: cl.length / 250, win: rets.filter(r => r > cost).length / rets.length, med: q(0.5) } : null };
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
  const first = [...X.bt.sel][0], hasPrice = !!(first && res[first].trades && res[first].trades.length && (EXTRA.dataset || {})[t]);
  return `<div class="page-h"><h1>Backtest</h1><p>How each model's saved day-7 calls would have done: long when it predicted up, short when down. Built from graded rows in the forecast log.</p></div>
    ${card("", "", "", `<div class="field"><span class="lbl">Ticker</span>${tkrPills(t, "data-bt")}</div>`)}
    ${hasPrice ? card("Trades on the price chart", `Every call of the ${esc(first)} model on the price. ▲ long entry, ▼ short entry; the line runs from entry to exit, green if the call made money and red if it lost. The dots above show each call's return.`, `<div class="pills nw"><button type="button" class="pillbtn" data-act="btp-zin" aria-label="Zoom in">+</button><button type="button" class="pillbtn" data-act="btp-zout" aria-label="Zoom out">−</button><button type="button" class="pillbtn" data-act="btp-reset">Reset</button></div>`, `<div class="ohlc" id="btp-ohlc"></div><div class="chartbox btpchart" id="btprice"></div><p class="src">Day-7 calls from non-overlapping entry dates, shown from May 21, 2026; the exit is the target date. Most rows are walk-forward replays, not live calls. No costs.${((DATA[t] || {}).meta || {}).source === "dse" ? " DSE trading is treated as long-only on this site, so the short calls are part of the test only." : ""}</p>`) : ""}
    ${models.length ? "" : nonEmpty("inbox", "Not enough graded calls", "This ticker has fewer than 8 non-overlapping graded day-7 calls for any model.")}`;
}
/* Trades of the first selected model on the price (Backtest page): a row of dots with each call's return above candles and volume,
   on one shared x axis. A triangle marks each entry (up = long, down = short); a line runs from entry to exit, green when the call
   made money and red when it lost. Candles are grey so the trade marks stand out; direction is shown by shape, outcome by colour
   and by the dot's position above or below zero. */
const BT_FROM = "2026-05-21";   // the trades chart starts on this day, nothing earlier is shown
function drawBTPrice() {
  const h = $("#btprice"); if (!h) return;
  const t = X.bt.t, res = EXTRA.backtest[t] || {}, m = [...X.bt.sel].filter(k => res[k])[0], rows = (EXTRA.dataset || {})[t];
  if (!m || !rows || !res[m].trades) return;
  const src = res[m].trades;
  /* a call can end past the last saved price bar: those dates get empty columns (no candle), so the call still shows */
  const dates0 = rows.map(r => r[0]), lastD = dates0[dates0.length - 1];
  const dates = dates0.concat([...new Set(src.flatMap(q => [q[0], q[1]]).filter(d => d > lastD))].sort());
  const at = d => { let a = 0, b = dates.length; while (a < b) { const c = (a + b) >> 1; if (dates[c] < d) a = c + 1; else b = c; } return a; };   // first row on or after d
  const trades = src.filter(q => q[0] >= BT_FROM).map(([d0, d1, s, p0, p1, rt]) => ({ d0, d1, s, p0, p1, rt, i0: at(d0), i1: at(d1) })).filter(x => x.i0 < dates.length && x.i1 < dates.length).sort((a, b) => a.i0 - b.i0);
  if (!trades.length) return;
  const fFrom = Math.min(at(BT_FROM), trades[0].i0), fTo = Math.min(dates.length - 1, Math.max(...trades.map(q => q.i1)) + 2);
  X.bt.full = [fFrom, fTo];                                    // the whole range; X.bt.win is the zoomed window inside it (null = whole)
  let [from, to] = X.bt.win || [fFrom, fTo];
  from = Math.max(fFrom, Math.min(from, fTo)); to = Math.min(fTo, Math.max(to, from));
  const n = to - from + 1, vis = trades.filter(q => q.i1 >= from && q.i0 <= to), pts = vis.length ? vis : trades;
  const cs = dates.slice(from, to + 1).map((d, j) => { const r = rows[from + j]; if (!r) return [d, null, null, null, null, 0]; const c = r[4]; return [r[0], r[1] ?? c, r[2] ?? c, r[3] ?? c, c, r[5] || 0]; });
  const cdl = cs.filter(c => c[4] != null);
  const W = Math.max(320, h.clientWidth || 700), padL = 8, padR = 56, padT = 8, plH = 90, gap1 = 14, priceH = 260, gap2 = 8, volH = 56, axisH = 22;
  const H = padT + plH + gap1 + priceH + gap2 + volH + axisH, step = (W - padL - padR) / n, x = i => padL + step * (i - from + 0.5), cw = Math.max(1, Math.min(8, step * 0.65));
  const rmax = Math.max(1, Math.ceil(Math.max(...pts.map(q => Math.abs(q.rt))))), yr = v => padT + plH / 2 - v / rmax * (plH / 2 - 8);
  const top = padT + plH + gap1, lo0 = Math.min(...cdl.map(c => c[3]), ...pts.map(q => Math.min(q.p0, q.p1))), hi0 = Math.max(...cdl.map(c => c[2]), ...pts.map(q => Math.max(q.p0, q.p1))), pad = (hi0 - lo0) * 0.06 || 0.05, lo = lo0 - pad, hi = hi0 + pad;
  const y = v => top + (hi - v) * priceH / (hi - lo), vtop = top + priceH + gap2, vmax = Math.max(...cs.map(c => c[5])) || 1;
  const col = q => q.rt > 0 ? "var(--buy)" : q.rt < 0 ? "var(--sell)" : "var(--faint)";
  let s = `<svg viewBox="0 0 ${W} ${H}" width="100%" role="img" aria-label="${esc(m)} calls for ${esc(t)}: returns above, candlestick price chart with entries and exits below">`;
  [rmax, 0, -rmax].forEach(v => { s += `<line x1="${padL}" x2="${W - padR}" y1="${yr(v).toFixed(1)}" y2="${yr(v).toFixed(1)}" stroke="${v === 0 ? "var(--faint)" : "var(--border-soft)"}" ${v === 0 ? 'stroke-dasharray="3 3"' : ""}/><text class="ax" x="${W - padR + 6}" y="${(yr(v) + 3).toFixed(1)}">${v > 0 ? "+" : v < 0 ? "−" : ""}${Math.abs(v)}%</text>`; });
  const CLIP = `<g clip-path="url(#btp-clip)">`;
  s += `<defs><clipPath id="btp-clip"><rect x="${padL}" y="0" width="${W - padL - padR}" height="${H}"/></clipPath></defs>${CLIP}`;
  vis.forEach(q => { s += `<circle cx="${x(q.i1).toFixed(1)}" cy="${yr(Math.max(-rmax, Math.min(rmax, q.rt))).toFixed(1)}" r="3.5" fill="${col(q)}"/>`; });
  s += "</g>";
  niceTicks(lo, hi, 5).filter(v => v >= lo && v <= hi).forEach(v => { s += `<line x1="${padL}" x2="${W - padR}" y1="${y(v).toFixed(1)}" y2="${y(v).toFixed(1)}" stroke="var(--border-soft)"/><text class="ax" x="${W - padR + 6}" y="${(y(v) + 3).toFixed(1)}">${fmtN(v, 2)}</text>`; });
  s += CLIP;
  cs.forEach((c, j) => {
    if (c[4] == null) return;
    const cx = x(from + j), up = c[4] >= c[1], bt = y(Math.max(c[1], c[4])), bb = y(Math.min(c[1], c[4]));
    s += `<line x1="${cx.toFixed(1)}" x2="${cx.toFixed(1)}" y1="${y(c[2]).toFixed(1)}" y2="${y(c[3]).toFixed(1)}" stroke="var(--faint)" opacity="0.7"/><rect x="${(cx - cw / 2).toFixed(1)}" y="${bt.toFixed(1)}" width="${cw.toFixed(1)}" height="${Math.max(1, bb - bt).toFixed(1)}" fill="${up ? "var(--panel)" : "var(--faint)"}" stroke="var(--faint)" opacity="0.8"/>`;
    s += `<rect x="${(cx - cw / 2).toFixed(1)}" y="${(vtop + volH - c[5] / vmax * volH).toFixed(1)}" width="${cw.toFixed(1)}" height="${(c[5] / vmax * volH).toFixed(1)}" fill="var(--faint)" opacity="0.35"/>`;
  });
  vis.forEach(q => {
    const x0 = x(q.i0), y0 = y(q.p0), x1 = x(q.i1), y1 = y(q.p1);
    s += `<line x1="${x0.toFixed(1)}" y1="${y0.toFixed(1)}" x2="${x1.toFixed(1)}" y2="${y1.toFixed(1)}" stroke="${col(q)}" stroke-width="1.8"/><circle cx="${x1.toFixed(1)}" cy="${y1.toFixed(1)}" r="2.8" fill="var(--panel)" stroke="${col(q)}" stroke-width="1.6"/>`;
    s += q.s > 0 ? `<polygon points="${x0.toFixed(1)},${(y0 - 6).toFixed(1)} ${(x0 - 5).toFixed(1)},${(y0 + 3).toFixed(1)} ${(x0 + 5).toFixed(1)},${(y0 + 3).toFixed(1)}" fill="var(--text)" stroke="var(--panel)"/>`
      : q.s < 0 ? `<polygon points="${x0.toFixed(1)},${(y0 + 6).toFixed(1)} ${(x0 - 5).toFixed(1)},${(y0 - 3).toFixed(1)} ${(x0 + 5).toFixed(1)},${(y0 - 3).toFixed(1)}" fill="var(--text)" stroke="var(--panel)"/>`
      : `<circle cx="${x0.toFixed(1)}" cy="${y0.toFixed(1)}" r="3" fill="none" stroke="var(--text)"/>`;
  });
  s += "</g>";
  [0, 0.25, 0.5, 0.75, 1].forEach((f, k) => { const j = Math.min(n - 1, Math.round(f * (n - 1))); s += `<text class="ax" x="${x(from + j).toFixed(1)}" y="${H - 6}" text-anchor="${k === 0 ? "start" : k === 4 ? "end" : "middle"}">${dShort(cs[j][0])}</text>`; });
  s += `<line id="btp-cross" x1="0" x2="0" y1="${padT}" y2="${vtop + volH}" stroke="var(--faint)" stroke-dasharray="3 3" opacity="0"/><rect x="${padL}" y="${padT}" width="${W - padL - padR}" height="${H - padT}" fill="transparent"/></svg>`;
  h.innerHTML = s;
  const out = $("#btp-ohlc"), wins = trades.filter(q => q.rt > 0).length, sgnTxt = q => q.s > 0 ? "long" : q.s < 0 ? "short" : "no call";
  const base = `<b>${esc(m)}</b> <span class="sub">${trades.length} calls, ${wins} made money. Hover over the chart for details. Scroll to zoom, drag to move, double-click to reset.</span>`;
  const show = j => {
    const c = cs[j], i = from + j, q = trades.find(z => i >= z.i0 && i <= z.i1);
    out.innerHTML = `<b>${esc(t)}</b> <span class="sub">${esc(c[0])}</span>${c[4] == null ? ` <span class="sub">no price bar saved</span>` : ` &nbsp; O <span>${fmtN(c[1], 2)}</span> H <span>${fmtN(c[2], 2)}</span> L <span>${fmtN(c[3], 2)}</span> C <span>${fmtN(c[4], 2)}</span> V <span>${fmtN(c[5])}</span>`}`
      + (q ? ` &nbsp; <b>${sgnTxt(q)}</b> <span class="sub">${esc(q.d0)} → ${esc(q.d1)}</span> <span>${fmtN(q.p0, 2)} → ${fmtN(q.p1, 2)}</span> <span class="${cls(q.rt)}">${fmtPct(q.rt)}</span>` : "");
  };
  out.innerHTML = base;
  const svg = h.querySelector("svg"), cross = $("#btp-cross");
  svg.addEventListener("pointermove", ev => { const r = svg.getBoundingClientRect(), px = (ev.clientX - r.left) * W / r.width, j = Math.max(0, Math.min(n - 1, Math.floor((px - padL) / step)));
    cross.setAttribute("x1", x(from + j)); cross.setAttribute("x2", x(from + j)); cross.setAttribute("opacity", "1"); show(j); });
  svg.addEventListener("pointerleave", () => { cross.setAttribute("opacity", "0"); out.innerHTML = base; });
  const frac = ev => { const r = svg.getBoundingClientRect(); return Math.max(0, Math.min(1, ((ev.clientX - r.left) * W / r.width - padL) / (W - padL - padR))); };
  svg.addEventListener("wheel", ev => { ev.preventDefault(); btpZoom(ev.deltaY < 0 ? 0.8 : 1.25, frac(ev)); }, { passive: false });
  svg.addEventListener("dblclick", () => btpReset());
  svg.addEventListener("pointerdown", ev => { if (ev.button === 0) btpDrag = { x0: ev.clientX, win: [from, to], cols: step * svg.getBoundingClientRect().width / W }; });
}
/* Zoom and pan of the trades chart. X.bt.win = [first, last] column shown inside X.bt.full (null = everything). */
let btpDrag = null;
function btpSet(a, b) {
  const [f0, f1] = X.bt.full || [0, 0], size = Math.min(b - a + 1, f1 - f0 + 1);
  a = Math.max(f0, Math.min(a, f1 - size + 1));
  X.bt.win = a === f0 && a + size - 1 === f1 ? null : [a, a + size - 1];
  drawBTPrice();
}
function btpZoom(fac, f = 0.5) {
  const [f0, f1] = X.bt.full || [0, 0], [a, b] = X.bt.win || [f0, f1], span = b - a + 1;
  const size = Math.max(Math.min(6, f1 - f0 + 1), Math.min(f1 - f0 + 1, Math.round(span * fac))), centre = a + f * span;
  btpSet(Math.round(centre - f * size), Math.round(centre - f * size) + size - 1);
}
function btpReset() { X.bt.win = null; drawBTPrice(); }
document.addEventListener("pointermove", ev => {
  if (!btpDrag || !X.bt.full) return;
  const d = Math.round((btpDrag.x0 - ev.clientX) / btpDrag.cols), [a, b] = btpDrag.win;
  if (d) btpSet(a + d, b + d);
});
document.addEventListener("pointerup", () => { btpDrag = null; });

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
  return `<div class="page-h"><h1>Dataset Explorer</h1><p>The daily price table the models train on: the data snapshot plus closes logged by the AI analysis.</p></div>
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
      ${statCard("News-flow sentiment", esc(se.overall || "–"), `score ${esc(se.score ?? "N/A")}/10 · ${se.source === "RULE_BASED" ? `scored in code from ${esc(se.n_items ?? "–")} stored news items` : "read by the LLM"}`, "", "brain")}
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
  const folders = [["data/", "2-year price snapshots", "python daily.py (tops up yfinance tickers)"], ["daily_data/", "closes logged by the AI analysis", "run an AI analysis"], ["forecast_log/", "7-day forecasts and their grades", "forecast_lib.record_forecast() / grade_forecasts()"], ["cache/", "tuned hyperparameters and model results", "python daily.py --retune"], ["chroma_memory/", "graded prediction episodes", "written by python daily.py"], ["notebooks/trading_log_*", "LLM analyst sessions", "run an AI analysis"]];
  return `<div class="page-h"><h1>Settings</h1><p>Preferences for this page, saved in this browser only.</p></div>
    ${card("Preferences", "", "", `
      ${row("Theme", "Follow your system, or force one.", pillTabs([["system", "System"], ["dark", "Dark"], ["light", "Light"]], settings.theme, "data-set-theme"))}
      ${row("Default forecast view", "Which forecast the ticker pages show first.", pillTabs(VIEWS.map(v => [v[0], v[1]]), settings.view, "data-set-view"))}
      ${row("Default history window", "Trading days of history on the price chart.", `<select data-bind="set-win" aria-label="History window">${[30, 60, 120, 180].map(n => `<option value="${n}" ${settings.win === n ? "selected" : ""}>${n} trading days</option>`).join("")}</select>`)}
      ${row("Default chart units", "Show price or change from the last close.", `<select data-bind="set-unit" aria-label="Chart units"><option value="price" ${settings.unit === "price" ? "selected" : ""}>Price</option><option value="pct" ${settings.unit === "pct" ? "selected" : ""}>Change from last close (%)</option></select>`)}`)}
    ${card("Saved data", "Stored in this browser only. Clearing site data removes it.", "", `
      ${row("Watchlist", `${X.wl.length} of ${ORDER.length} tickers pinned.`, `<button type="button" class="btn secondary sm" data-act="wl-reset">Pin all ${ORDER.length}</button>`)}
      ${row("All settings", "Back to defaults.", `<button type="button" class="btn secondary sm" data-act="set-reset">Reset settings</button>`)}`)}
    ${LIVE.ok ? card("Server", LIVE.health.run_token_required ? "This server asks for an access token before it runs models." : "This server runs models for anyone who opens the page.", "", `
      ${row("Connection", `Live server, date ${esc(LIVE.health.date)}. LLMs connected: ${esc(llmProviders().map(p => p.label).join(", ") || "none")}. Runs waiting: ${LIVE.health.queue}.`, `<span class="badge ${llmProviders().length ? "buy" : "hold"}">${llmProviders().length ? "AI ready" : "forecasts only"}</span>`)}
      ${LIVE.health.run_token_required ? row("Access token", "Saved in this browser and sent with each run request.", `<input class="inp" type="password" id="set-token" data-bind="set-token" value="${esc(LIVE.token)}" placeholder="token" aria-label="Server access token">`) : ""}`) : ""}
    ${card("Where the numbers come from", `Exported ${dLong(ASOF)} from the project's Python code. This page never recomputes them.`, "", `<div class="scroll"><table><thead><tr><th>Folder</th><th>Holds</th><th>Refresh with</th></tr></thead><tbody>${folders.map(f => `<tr><td class="t"><span class="mono">${esc(f[0])}</span></td><td class="t">${esc(f[1])}</td><td class="t">${esc(f[2])}</td></tr>`).join("")}</tbody></table></div>`)}`;
}

/* ----- fixed stake shown on the AI Decision card: 100 (USD), or 10,000 Tk for Dhaka stocks ----- */
const STAKE = 100;
const stakeShow = v => "$" + v.toFixed(2);
const stakeSigned = v => (v >= 0 ? "+" : "−") + stakeShow(Math.abs(v));
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

/* ----- AI influence: the latest AI rating tilts the model forecast a little ----- */
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
  const b = aiBlend(t);
  if (!b) return "";
  const cur = DATA[t].meta.currency;
  return card(`Effect on the forecast ${hint(GLOSS.aiadj)}`, `The latest AI rating (${dLong(b.date)}${b.model ? ", " + esc(b.model) : ""}) next to the model forecast.`, ratingBadge(b.rating),
    b.stale ? `<p class="sub">This AI session is more than 10 days older than the prices (${dLong(DATA[t].forecast.last_date)}), so it does not change the forecast. Run the analysis again to bring it in.</p>`
      : `<div class="kvs" style="margin:0"><div class="kv"><p class="k">Models (Ensemble)</p><p class="v ${cls(b.base)}">${fmtPct(b.base * 100)}</p><p class="s">${fmtP(b.last * (1 + b.base))} ${esc(cur)}</p></div>
        <div class="kv"><p class="k">AI view</p><p class="v ${cls(b.view)}">${fmtPct(b.view * 100)}</p><p class="s">${esc(b.rating)} × typical 7-day move</p></div>
        <div class="kv"><p class="k">AI-adjusted 7 days</p><p class="v ${cls(b.adj)}">${fmtPct(b.adj * 100)}</p><p class="s">${fmtP(b.price)} ${esc(cur)} · ${aiWhy(b)}</p></div></div>`);
}


/* ----- help: glossary, hints, plain-language summary ----- */
Object.assign(ICONS, { help: '<circle cx="12" cy="12" r="10"/><path d="M9.09 9a3 3 0 0 1 5.83 1c0 2-3 3-3 3"/><path d="M12 17h.01"/>' });
const GLOSS = {
  views: ["Which forecast", "Reviewed forecast (the main one): the three models with the smallest past error, averaged, after an automatic review that can shrink or clip the forecast; it comes with a 10–90% range. Ensemble: the average of the two models with the smallest past error. Fusion: a learned blend of all models. Fusion Top 3: the three most accurate models, weighted by accuracy. Best single model: the one model that called direction best in testing."],
  rec: ["Recommendation", "From the reviewed forecast and its 10–90% range, which comes from the stock's own past forecast errors. BUY when the odds of a gain after trading costs are at least 60%, SELL when the odds of a fall are at least 60%, otherwise HOLD. Always HOLD when the forecast's tested error was not at least 2% below that of 'no change', or the prices are old."],
  daily: ["Daily models (second opinion)", "An older, separate set of five models. Each bar is one model's predicted 7-day return; 'dir' is how often that model called up or down correctly on past data it had not seen. The one with the best direction record gives this panel's own signal, which does not set the recommendation."],
  fc: ["7-day forecast", "The 10–90% range means that in past tests the real price ended inside it about 8 times out of 10. 'Always-UP' is how often simply guessing 'up' was right, a baseline a useful model should beat."],
  models: ["Error vs naive", "Compares a model's average error with simply predicting 'no change'. Below 1.00 means the model beat that simple guess. Direction hit is how often it got up or down right on data it had not seen."],
  agent: ["Reviewer", "A rule-based check, not an AI. It flags old data, replaces weak models, shrinks the forecast toward 'no change' when past accuracy was poor, and limits unusually large moves."],
  track: ["Track record", "Every saved forecast is later checked against the real closing price. 'Within 1%' means the price landed within 1% of the forecast. The p-value says whether the direction record could be luck: below 0.05 is usually read as real skill."],
  votes: ["Votes", "Three separate views: the forecast's direction, the daily signal and the AI analyst's rating. ↑ up, ↓ down, – flat or hold."],
  past: ["Past record", "What your amount would be if you had followed the Ensemble's past 7-day calls (buy when it said up, sell short when it said down), next to simply buying and holding."],
  bt: ["Backtest terms", "Edge is the direction hit rate minus the 'always up' rate, in percentage points: positive means the model beat that simple guess. Max drawdown is the largest fall from a previous high."],
  dd: ["Drawdown", "How far your account was below its highest value so far."],
  runfc: ["Run the forecast", "Fits the chosen forecast models on the server, on the same CSV price files the engine uses, and draws the new 7-day path. Ensemble, Fusion and the reviewer range are rebuilt from the models you pick. Each run is stored; the same company, date and models give back the stored run."],
  ind: ["Indicators", "RSI above 70 is often called overbought and below 30 oversold. MACD above its signal line means upward momentum. %B shows where the price sits in its Bollinger band (0 = lower band, 1 = upper band). Volatility is the typical daily move over 30 days. They describe the recent past and are not a forecast."],
  aidec: ["Decision", "BUY, HOLD or SELL after the whole analysis has run: analysts, the bull vs bear debate, the trader, the risk debate and the portfolio manager. The decision verifier can override the signal when the text and the rating disagree. Overweight counts as BUY and Underweight as SELL."],
  aiadj: ["AI-adjusted forecast", "The 7-day forecast after the latest AI analysis (the AI analysis run with an LLM) has had its say. The AI rating (Buy +1, Overweight +½, Hold 0, Underweight −½, Sell −1) times the stock's typical 7-day move is its view; the adjusted move is 80% the models and 20% that view. Once the AI has five graded calls, its weight moves between 10% and 35% with how often it was right. A session more than 10 days older than the prices does not count."],
  runai: ["Run AI analysis", "Runs the whole AI analysis of the company on the server (analysts, debates, trader, risk, portfolio manager, verifier) with the LLM you choose, then shows the new session. Ollama models run on the server itself; OpenAI, Claude, Gemini and Groq models run with the server's API key, so no key is ever typed into this page. Each run is stored by company, date and model."],
  evwin: ["Window", "A call is judged on the closes of the next 1, 2 or 3 trading days. BUY is right if some close in that window is above the entry price, SELL if some close is below it, HOLD if some close is the same or higher. A longer window gives each call more chances, so accuracy rises with it. Only calls with three later closes are scored, the same calls for every window."],
  evacc: ["Accuracy", "The share of scored calls that were right under the window rule. With 30 to 40 calls per company it is uncertain: read it together with the 95% interval."],
  wilson: ["95% interval", "The range the true hit rate probably lies in, given how few calls there are (a Wilson interval). When two companies' intervals overlap a lot, the data do not show that one is better than the other."],
  evbase: ["Always HOLD", "What you would score by answering HOLD on every call. HOLD is right whenever some close in the window is not below the entry, which is most of the time, so this is the bar a BUY / HOLD / SELL agent has to clear."],
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
  const t = S.t, f = d.forecast, last = f.last_price, p = fcPath(t), band = fcBand(t), cur = d.meta.currency, o = outlook(t);
  if (!p) return "";
  const r = p[6] / last - 1, conf = d.agent && d.agent.plan ? d.agent.plan.confidence : null, age = daysBetween(f.last_date, refDate());
  return `<section class="card summary"><span class="fi">${ic("check")}</span><div><p class="l">In plain words</p>
    <p class="lead2">Over the next 7 trading days the ${o.name.toLowerCase()} expects ${esc(d.meta.name)} to <b>${moveWord(r)}</b> (${fmtPct(r * 100)}, to about ${fmtP(p[6])} ${esc(cur)}). The recommendation is ${sigBadge(o.sig)}: ${o.why}.${conf ? ` Confidence is <b>${esc(conf.toLowerCase())}</b>.` : ""}${band ? ` In past tests the price usually ended between ${fmtP(band[6][0])} and ${fmtP(band[6][1])} ${esc(cur)}.` : ""}</p>
    <p class="lead2" style="margin-top:6px">After 5 trading days: <b>${fmtPct((p[4] / last - 1) * 100)}</b> (about ${fmtP(p[4])} ${esc(cur)})${band ? `, usually between ${fmtP(band[4][0])} and ${fmtP(band[4][1])}` : ""}.${band ? ` As a profit or loss on the amount invested, that is about ${fmtPct((p[4] / last - 1) * 100)} with a range of ${fmtPct((band[4][0] / last - 1) * 100)} to ${fmtPct((band[4][1] / last - 1) * 100)}, ` : " "}before costs.</p>
    ${(b => b && !b.stale ? `<p class="lead2" style="margin-top:6px">The latest AI analysis rated it ${ratingBadge(b.rating)} on ${dShort(b.date)}; with that view the forecast becomes <b>${fmtPct(b.adj * 100)}</b> (about ${fmtP(b.price)} ${esc(cur)}; ${aiWhy(b)}).</p>` : "")(aiBlend(t))}
    ${age > 5 ? `<p class="sub" style="margin-top:6px;color:var(--hold)">These prices are from ${dLong(f.last_date)}, ${age} days old.</p>` : ""}</div></section>`;
}
function renderHelp() {
  const steps = [["Pick a stock", "Use the cards on Home, the Forecast menu or the search box."], ["Run the forecast", "Press Run forecast to see the 7-day path, the daily signal and every model's numbers."], ["Read the plain summary", "The 'In plain words' box says what the models expect and how sure they are."], ["Check the evidence", "Track record, Backtest and Memory show how past calls really did."]];
  return `<div class="page-h"><h1>Help and glossary</h1><p>How to use this site, and what each term means, in plain language.</p></div>
    ${card("How to use this site", "", "", `<ol class="steps4">${steps.map(([h, p], i) => `<li><span class="num">${i + 1}</span><div><h3>${h}</h3><p>${p}</p></div></li>`).join("")}</ol>`)}
    ${card("Glossary", "Hover or focus the ? next to a heading anywhere on the site to see the same text.", "", `<dl class="gloss">${Object.values(GLOSS).map(([h, p]) => `<div><dt>${esc(h)}</dt><dd>${esc(p)}</dd></div>`).join("")}</dl>`)}
    ${card("About the data", "", "", `<ul class="plain"><li>All numbers were exported from the project's Python code on ${dLong(ASOF)}. This site replays them; it does not train models or fetch live prices.</li><li>Saved prices end between ${dLong(ORDER.map(t => DATA[t].forecast.last_date).sort()[0])} and ${dLong(ORDER.map(t => DATA[t].forecast.last_date).sort().slice(-1)[0])}, so every saved forecast starts from those closes, not from today's prices.</li><li>The watchlist and your settings are saved only in this browser.</li><li>This is a research tool. Nothing here is investment advice.</li></ul>`)}`;
}

/* ----- evaluation (accuracy of the AI analysis' BUY / HOLD / SELL calls; data: backend/data/evaluation.json) ----- */
X.ev = { w: 3, t: "", n: 25 };
const EV = () => EXTRA.evaluation || null;
const EV_W = [[1, "1 day"], [2, "2 days"], [3, "3 days"]];
const evA = v => v == null ? "–" : v.toFixed(1) + "%";
const evI = o => o && o.lo != null ? `${o.lo.toFixed(1)}–${o.hi.toFixed(1)}%` : "–";
const evRight = (sig, r) => sig === "BUY" ? Math.max(...r) > 0 : sig === "SELL" ? Math.min(...r) < 0 : Math.max(...r) >= 0;
const evName = t => DATA[t] ? DATA[t].meta.name : ((EV().companies.find(c => c.ticker === t) || {}).name || t);
/* A bar list: label | SVG track (percent coordinates, so text never scales) | value. items: {label, sub, k, n, acc, lo, hi, base, color} */
function evBars(items, avg, baseLabel) {
  const grid = [0, 25, 50, 75, 100];
  const axis = `<div class="evrow" aria-hidden="true"><span></span><svg class="evt" height="16">${grid.map(g => `<text class="ax" x="${g}%" y="12" text-anchor="${g === 0 ? "start" : g === 100 ? "end" : "middle"}">${g}%</text>`).join("")}</svg><span></span></div>`;
  const rows = items.map(it => {
    const left = `<div class="evl"><b class="mono">${esc(it.label)}</b><span title="${esc(it.sub)}">${esc(it.sub)}</span></div>`;
    if (it.acc == null) return `<div class="evrow">${left}<div class="evt evnone">no scored calls yet</div><div class="evv mono">–</div></div>`;
    const lab = `${it.label}: ${it.acc.toFixed(1)}% right (${it.k} of ${it.n}), 95% interval ${it.lo.toFixed(1)} to ${it.hi.toFixed(1)}%${it.base != null ? `, ${baseLabel} ${it.base.toFixed(1)}%` : ""}`;
    return `<div class="evrow">${left}<svg class="evt" height="28" role="img" aria-label="${esc(lab)}">
      ${grid.slice(1, 4).map(g => `<line x1="${g}%" x2="${g}%" y1="0" y2="28" stroke="var(--border-soft)"/>`).join("")}
      <rect x="0" y="6" width="${it.acc}%" height="16" rx="3" fill="${it.color || "var(--accent)"}"/>
      <line x1="${it.lo}%" x2="${it.hi}%" y1="14" y2="14" stroke="var(--text)" stroke-width="1.5"/>
      <line x1="${it.lo}%" x2="${it.lo}%" y1="9" y2="19" stroke="var(--text)" stroke-width="1.5"/>
      <line x1="${it.hi}%" x2="${it.hi}%" y1="9" y2="19" stroke="var(--text)" stroke-width="1.5"/>
      ${it.base != null ? `<line x1="${it.base}%" x2="${it.base}%" y1="2" y2="26" stroke="var(--hold)" stroke-width="2.5"/>` : ""}
      ${avg != null ? `<line x1="${avg}%" x2="${avg}%" y1="0" y2="28" stroke="var(--faint)" stroke-dasharray="3 3"/>` : ""}
    </svg><div class="evv mono">${it.acc.toFixed(1)}%</div></div>`;
  }).join("");
  return `<div class="evchart">${axis}${rows}</div>`;
}
function renderEval() {
  const ev = EV();
  const head = `<div class="page-h"><h1>Evaluation</h1><p>How often the AI analysis' BUY, HOLD and SELL calls were right, judged on the closes of the next trading days. Every refresh merges the trading logs, the session archive and the files in <span class="mono">backend/data/evaluation_inputs</span> and scores them again.</p></div>`;
  if (!ev || !ev.scored) return head + card("", "", "", emptyState("chart", "No evaluation saved yet", "Run <span class='mono'>python backend/app/pipeline/export_evaluation.py</span> (or <span class='mono'>python backend/refresh.py</span>), then rebuild the page."));
  const w = X.ev.w, W = String(w), o = ev.overall[W], diff = o.acc - o.base, pend = ev.pending;
  const co = ev.companies.map(c => ({ c, s: c.w[W] })), sg = ev.signals[W];
  const byCo = co.slice().sort((p, q) => (q.s.acc == null ? -1 : q.s.acc) - (p.s.acc == null ? -1 : p.s.acc));
  const coBars = evBars(byCo.map(({ c, s }) => ({ label: c.ticker, sub: `${evName(c.ticker)} · ${s.k}/${s.n}`, k: s.k, n: s.n, acc: s.acc, lo: s.lo, hi: s.hi, base: s.base })), o.acc, "always HOLD");
  const sigColor = { BUY: "var(--buy)", HOLD: "var(--hold)", SELL: "var(--sell)" };
  const sgBars = evBars(["BUY", "HOLD", "SELL"].map(k => ({ label: k, sub: `${sg[k].n} calls · ${sg[k].k} right`, k: sg[k].k, n: sg[k].n, acc: sg[k].acc, lo: sg[k].lo, hi: sg[k].hi, base: sg[k].base, color: sigColor[k] })), o.acc, "if always this signal");
  const windowPills = `<div class="pills" role="group" aria-label="Window">${EV_W.map(([v, l]) => `<button type="button" class="pillbtn" data-act="ev-w" data-v="${v}" aria-pressed="${w === v}"><i></i>${l}</button>`).join("")}</div>`;
  const dv = d => d == null ? "–" : (d > 0 ? "+" : d < 0 ? "−" : "") + Math.abs(d).toFixed(1);
  const coRows = byCo.map(({ c, s }) => { const d = s.acc == null ? null : s.acc - s.base; return `<tr><td class="t"><span class="tkr">${esc(c.ticker)}</span>${evName(c.ticker) === c.ticker ? "" : " " + esc(evName(c.ticker))}</td><td>${c.calls}</td><td>${s.n}</td><td>${s.k}</td><td>${evA(s.acc)}</td><td>${evI(s)}</td><td>${evA(s.base)}</td><td class="${cls(d)}">${dv(d)}</td><td>${c.pending}</td></tr>`; }).join("");
  const sgRows = ["BUY", "HOLD", "SELL"].map(k => `<tr><td class="t">${sigBadge(k)}</td><td>${sg[k].n}</td><td>${sg[k].k}</td><td>${evA(sg[k].acc)}</td><td>${evI(sg[k])}</td><td>${evA(sg[k].base)}</td></tr>`).join("");
  const tick = ev.companies.map(c => c.ticker), rowsAll = ev.rows.filter(r => !X.ev.t || r[1] === X.ev.t).slice().reverse(), shown = rowsAll.slice(0, X.ev.n);
  const filt = `<div class="pills" role="group" aria-label="Company">${[["", "All"], ...tick.map(t => [t, t])].map(([v, l]) => `<button type="button" class="pillbtn" data-act="ev-t" data-v="${esc(v)}" aria-pressed="${X.ev.t === v}"><i></i>${esc(l)}</button>`).join("")}</div>`;
  const callRows = shown.map(r => {
    const [d, t, sig, entry, rets, carried, filled, scored] = r;
    const res = scored ? (evRight(sig, rets.slice(0, w)) ? `<span class="up">Right</span>` : `<span class="down">Wrong</span>`) : `<span class="flat">Waiting</span>`;
    return `<tr><td class="t"><span class="mono">${dShort(d)}</span>${carried ? " †" : ""}${filled ? " ‡" : ""}</td><td class="t"><span class="tkr">${esc(t)}</span></td><td class="t">${sigBadge(sig)}</td><td>${entry == null ? "–" : fmtP(entry)}</td>${[0, 1, 2].map(i => `<td class="${cls(rets[i])}">${rets[i] == null ? "–" : fmtPct(rets[i])}</td>`).join("")}<td class="t">${res}</td></tr>`;
  }).join("");
  const sc = ev.sources || {}, ss = sc.sessions || {}, cl = sc.closes || {};
  const srcRows = [["Trading logs", "backend/engine/notebooks", `${fmtN(ss.logs || 0)} sessions`], ["Session archive", "backend/data/session_archive.json.gz", `${fmtN(ss.archive || 0)} sessions`],
    ["Input files", (sc.files || []).join(", ") || "backend/data/evaluation_inputs", `${fmtN(ss.input_files || 0)} sessions, ${fmtN(sc.signal_rows_from_files || 0)} signal rows`],
    ["Closes logged by sessions", "price window of each session", `${fmtN(cl.logged_windows || 0)} closes`], ["Closes from input files", "ticker, date, close", `${fmtN(cl.input_files || 0)} closes`],
    ["Closes from the engine CSVs", "only days after the last logged close", `${fmtN(cl.engine_csv || 0)} closes`]];
  return `${head}
    ${card("", "", "", `<div class="field"><span class="lbl">Window ${hint(GLOSS.evwin)}</span>${windowPills}</div>`)}
    <div class="stats">${statCard(`Accuracy ${hint(GLOSS.evacc)}`, evA(o.acc), `${o.k} of ${o.n} · ${evI(o)}`, "accent", "check")}${statCard(`Always HOLD ${hint(GLOSS.evbase)}`, evA(o.base), `agent is ${Math.abs(diff).toFixed(1)} pts ${diff >= 0 ? "above" : "below"} this bar`, "hold", "pulse")}${statCard("Calls scored", fmtN(ev.scored), `${fmtN(ev.calls)} calls · ${fmtN(pend)} waiting`, "", "chart")}${statCard("Companies", fmtN(ev.companies.length), `${dShort(ev.first)} to ${dLong(ev.asof)}`, "", "db")}</div>
    ${card("Accuracy by company", `Share of calls judged right within the next ${w} trading day${w > 1 ? "s" : ""}, best first. The whisker is the 95% interval ${hint(GLOSS.wilson)}.`, "", `${coBars}<p class="evleg"><span><i class="b"></i>Accuracy</span><span><i class="w"></i>95% interval</span><span><i class="h"></i>Always HOLD</span><span><i class="a"></i>All stocks ${evA(o.acc)}</span></p><p class="src">Source: backend/data/evaluation.json, written by backend/app/pipeline/export_evaluation.py.</p>`)}
    <div class="two">
      ${card("Accuracy by signal", "How each kind of call did. The amber mark is what answering the same signal on every call would have scored.", "", `${sgBars}<p class="evleg"><span><i class="h"></i>If always this signal</span><span><i class="a"></i>All calls ${evA(o.acc)}</span></p>`)}
      ${card("Accuracy over time", "Running accuracy of all scored calls by the day they were made, from the 20th call on.", "", `<div class="chartbox" id="evtrend"></div><div class="legend"><span><i style="background:var(--accent)"></i>Agent</span><span><i style="background:var(--hold)"></i>Always HOLD</span></div>`)}
    </div>
    ${card("Companies", `Calls scored within ${w} day${w > 1 ? "s" : ""}. Vs HOLD is the agent's accuracy minus always-HOLD, in points.`, "", `<div class="scroll"><table><thead><tr><th>Company</th><th>Calls</th><th>Scored</th><th>Right</th><th>Accuracy</th><th>95% interval</th><th>Always HOLD</th><th>Vs HOLD</th><th>Waiting</th></tr></thead><tbody>${coRows}<tr class="tot"><td class="t">All stocks</td><td>${ev.calls}</td><td>${o.n}</td><td>${o.k}</td><td>${evA(o.acc)}</td><td>${evI(o)}</td><td>${evA(o.base)}</td><td class="${cls(diff)}">${dv(diff)}</td><td>${pend}</td></tr></tbody></table></div>`)}
    ${card("Signals", "", "", `<div class="scroll"><table><thead><tr><th>Signal</th><th>Scored</th><th>Right</th><th>Accuracy</th><th>95% interval</th><th>If always this signal</th></tr></thead><tbody>${sgRows}</tbody></table></div>`)}
    ${card("Calls", `Newest first. Right or Wrong uses the ${w}-day window above; the +1d to +3d columns are the change of the next three closes against the entry close.`, "", `<div class="field"><span class="lbl">Company</span>${filt}</div><div class="scroll"><table><thead><tr><th>Date</th><th>Company</th><th>Signal</th><th>Entry</th><th>+1d</th><th>+2d</th><th>+3d</th><th>Result</th></tr></thead><tbody>${callRows}</tbody></table></div>
      ${rowsAll.length > shown.length ? `<p class="evmore"><button type="button" class="btn secondary sm" data-act="ev-more">Show more (${rowsAll.length - shown.length} left)</button></p>` : ""}
      <p class="src">† no price bar that day (weekend, holiday or no data yet): the entry is the last close before it. ‡ the run stopped after the Trader step, so its own Trader decision is used. Waiting: fewer than three later closes exist so far.</p>`)}
    ${card("How a call is scored", "", "", `<ul class="plain"><li><b>BUY</b> is right if some close within the window is above the entry price, <b>SELL</b> if some close is below it, <b>HOLD</b> if some close is the same or higher. Longer windows give a call more chances, so accuracy rises with the window.</li><li>The entry price is the close of the day of the call, or the last close before it when the market had no bar that day. Calls on such days keep their own date.</li><li>A call is scored only when three later closes exist, the same calls for every window. The others say Waiting and are scored by a later refresh.</li><li>One call per company and day: the most complete finished run. A run that stopped after the Trader step uses its Trader decision (${fmtN(ev.filled)} scored calls).</li><li>Always HOLD is the bar to beat: HOLD is right whenever the price does not fall inside the window, which is most of the time.</li><li>This page replays saved results; it never fetches prices itself. Research output, not investment advice.</li></ul>`)}
    ${card("Where the numbers come from", "Files are merged by company and day; a later source wins. Drop new files into backend/data/evaluation_inputs and run python backend/refresh.py.", "", `<div class="scroll"><table class="lt"><thead><tr><th>Source</th><th>Where</th><th>Amount</th></tr></thead><tbody>${srcRows.map(r => `<tr><td class="t">${esc(r[0])}</td><td class="t"><span class="mono">${esc(r[1])}</span></td><td class="t">${esc(r[2])}</td></tr>`).join("")}</tbody></table></div>${(sc.skipped || []).length ? `<p class="src">Skipped: ${esc(sc.skipped.join("; "))}</p>` : ""}`)}`;
}
function drawEval() {
  const h = $("#evtrend"), ev = EV(); if (!h || !ev) return;
  const pts = ev.trend[String(X.ev.w)] || [], MIN = 20;
  if (pts.length < 2) { h.innerHTML = ""; return; }
  const pct = (a, b) => b >= MIN ? 100 * a / b : null;
  lineChart(h, pts.map(p => p[0]), [{ name: "Agent", v: pts.map(p => pct(p[2], p[1])), color: "var(--accent)", w: 2.4 }, { name: "Always HOLD", v: pts.map(p => pct(p[3], p[1])), color: "var(--hold)", dash: true, w: 1.8 }], { label: "Running accuracy of all scored calls", fmt: v => v.toFixed(0) + "%" });
}

/* ----- router ----- */
function goPage(p) { if (S.running) return; S.more = false; S.page = p; $("#app").classList.remove("open"); render(false); $("#scroller").scrollTop = 0; }
function renderExtra() {
  return ({ ai: renderAI, watch: renderWatch, bt: renderBT, data: renderData, news: renderNews, fund: renderFund, mem: renderMem, set: renderSet, help: renderHelp, stocks: renderStocks, stock: renderStock, eval: renderEval })[S.page]();
}
function afterRender() { if (S.page === "bt") drawBTPrice(); if (S.page === "eval") drawEval(); if (S.page === "stocks") ensureStocks(); if (S.page === "stock") { ensureStock(X.stk.code); drawStockChart(); } markRows(); }

/* ---------- top nav ---------- */
const NAV_MAIN = [["dash", "Market"], ["stocks", "Stocks"], ["tk", "Forecast"], ["ai", "AI Analysis"], ["bt", "Backtest"], ["eval", "Evaluation"]];
const CHEV = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" style="width:14px;height:14px"><path d="m6 9 6 6 6-6"/></svg>';
function renderNav() {
  const cur = id => (S.page === id || (id === "stocks" && S.page === "stock") ? ' aria-current="page"' : "");
  const more = NAVS.filter(([id]) => !NAV_MAIN.some(m => m[0] === id));
  const moreOn = more.some(([id]) => S.page === id);
  $("#nav").innerHTML = NAV_MAIN.map(([id, l]) => `<button type="button" class="nl" data-page="${id}"${cur(id)}>${l}</button>`).join("") +
    `<span class="morewrap"><button type="button" class="nl" data-act="more" aria-haspopup="true" aria-expanded="${!!S.more}"${moreOn ? ' aria-current="true"' : ""}>More ${CHEV}</button>` +
    (S.more ? `<div class="pop">${more.map(([id, l, i]) => `<button type="button" class="nl" data-page="${id}"${cur(id)}>${ic(i)}${l}</button>`).join("")}</div>` : "") + `</span>`;
  $("#sheetnav").innerHTML = NAVS.map(([id, l, i]) => `<button type="button" class="nl" data-page="${id}"${cur(id)}>${ic(i)}${id === "dash" ? "Market" : l}</button>`).join("") +
    `<div class="navh">Forecast by stock</div>` +
    ORDER.map(t => { const sig = S.ran[t] ? (outlook(t) || {}).sig || "" : ""; return `<button type="button" class="nl" data-t="${esc(t)}"${S.page === "tk" && S.t === t ? ' aria-current="page"' : ""}><span class="sym">${esc(t)}</span><span class="nm">${esc(DATA[t].meta.name)}</span><span class="dot ${sig}" role="img" aria-label="${sig ? "signal " + sig : "not run"}"></span></button>`; }).join("");
}

/* ---------- home ---------- */
X.mkt = { data: null, state: "idle", err: "", sort: "value_mn", newsN: 10 };
const fmtN = (v, d = 0) => v == null ? "–" : v.toLocaleString("en-US", { minimumFractionDigits: d, maximumFractionDigits: d });
const arrow = v => v > 0.0005 ? "▲" : v < -0.0005 ? "▼" : "■";
const MKT_SORTS = [["value_mn", "Value"], ["volume", "Volume"], ["trade", "Trade"]];
function mktHead() {
  const m = X.mkt, d = m.data, live = LIVE.ok;
  const sub = !live ? "Live market data comes from the server; this copy has the saved forecasts only."
    : m.state === "loading" && !d ? "Loading the exchange's latest day…"
    : d && d.ok ? `Trading day ${dLong(d.date)} · fetched ${esc(d.fetched_at.replace("T", " ").slice(0, 16))} (Dhaka time) · ${esc(d.scope || "DSE day-end archive, all instruments")}${d.provisional ? " · provisional: the day-end archive may not be final yet, so totals can still change" : ""}${d.stale ? " · exchange not reachable, showing the last saved copy" : ""}`
    : "Market data not available right now.";
  return `<div class="pagehead"><div><h1>DSE Market Overview</h1><p class="sub">${sub}</p></div>
    ${live ? `<button type="button" class="btn secondary sm" data-act="mkt-refresh"${m.state === "loading" ? " disabled" : ""}>${m.state === "loading" ? "Refreshing…" : "Refresh"}</button>` : ""}</div>`;
}
function mktIndices(d) {
  return `<div class="idxgrid">${["DSEX", "DSES", "DS30"].map(n => {
    const i = d && (d.indices || []).find(x => x.name === n);
    return i ? `<div class="card idx"><p class="k">${n}</p><p class="v mono">${fmtN(i.value, 2)}</p><p class="chg mono ${cls(i.change)}">${arrow(i.change)} ${fmtN(Math.abs(i.change), 2)} <span>(${fmtPct(i.change_pct)})</span></p></div>`
      : `<div class="card idx"><p class="k">${n}</p><p class="v mono">–</p><p class="chg flat">not available</p></div>`;
  }).join("")}</div>`;
}
function mktSummary(s, d) {
  return `<div class="stats">${statCard("Total trade", fmtN(s.total_trade), `${fmtN(s.traded_issues)} issues traded`, "", "pulse")}${statCard("Total volume", fmtN(s.total_volume), "shares", "", "chart")}${statCard("Value (mn)", fmtN(s.value_mn, 2), "BDT", "", "wallet")}
    <div class="card stat"><div style="min-width:0"><p class="k">Advanced / declined</p><p class="v breadth"><span class="up">▲ ${s.advanced}</span> <span class="down">▼ ${s.declined}</span> <span class="flat">■ ${s.unchanged}</span></p><p class="h">against the previous close</p></div><div class="ico">${ic("check")}</div></div></div>
    <p class="src">These totals add up every issue traded in the exchange's day-end archive${d && d.date ? ` for ${dLong(d.date)}` : ""}${d && d.fetched_at ? `, fetched ${esc(d.fetched_at.replace("T", " ").slice(0, 16))} (Dhaka time)` : ""}${d && d.provisional ? ", which may not be final yet" : ""}. ${(() => { const ex = d && d.exchange_totals; return ex && ex.value_mn != null ? `The exchange's own front page shows ${fmtN(ex.value_mn, 2)} mn BDT from ${fmtN(ex.total_trade)} trades${ex.as_of ? ` (last update ${esc(ex.as_of)})` : ""}. The two totals can differ, and the exchange does not say why, so each is shown with its own time.` : "The exchange's own notices can show a different turnover. Compare each figure's time before comparing them."; })()}</p>`;
}
function mktSectors(s) {
  if (!s.sectors.length) return emptyState("db", "Sector breadth needs the sector list", "Add backend/data/dse_sectors.json (trading code to sector) and it fills in on the next refresh.");
  const tot = k => s.sectors.reduce((q, x) => q + x[k], 0);
  // Only issues whose sector is known are in s.sectors. The rest are shown as "Unclassified" so the last row adds up to the
  // advanced / declined / unchanged counts of the market summary instead of looking like the whole market.
  const all = s.advanced + s.declined + s.unchanged, known = tot("total"), un = { advanced: s.advanced - tot("advanced"), declined: s.declined - tot("declined"), unchanged: s.unchanged - tot("unchanged") };
  const unTotal = all - known;
  return `<div class="scroll"><table class="dense"><thead><tr><th>Sector</th><th>Advanced</th><th>Declined</th><th>Unchanged</th><th>Total</th></tr></thead><tbody>${s.sectors.map(x => `<tr><td class="t">${esc(x.sector)}</td><td class="up">${x.advanced}</td><td class="down">${x.declined}</td><td class="flat">${x.unchanged}</td><td>${x.total}</td></tr>`).join("")}
    ${unTotal > 0 ? `<tr><td class="t">Unclassified (sector not known yet)</td><td class="up">${un.advanced}</td><td class="down">${un.declined}</td><td class="flat">${un.unchanged}</td><td>${unTotal}</td></tr>` : ""}
    <tr class="tot"><td class="t">All traded issues</td><td class="up">${s.advanced}</td><td class="down">${s.declined}</td><td class="flat">${s.unchanged}</td><td>${all}</td></tr></tbody></table></div>
    <p class="src">${fmtN(known)} of ${fmtN(all)} issues have a sector${unTotal > 0 ? "; the rest are listed as Unclassified. Run backend/tools/build_dse_sectors.py to fill in the sector list" : ""}.${all < s.traded_issues ? ` ${fmtN(s.traded_issues - all)} traded issues are left out of the counts because the archive has no previous close for them.` : ""}</p>`;
}
function mktTop(s) {
  const key = X.mkt.sort, rows = (s.stocks || s.top).slice().sort((p, q) => (q[key] || 0) - (p[key] || 0)).slice(0, 20);
  return `<div class="scroll"><table class="dense top"><thead><tr><th>#</th><th>Code</th><th>LTP</th><th>Close</th><th>Change</th><th>Trade</th><th>Value (mn)</th><th>Volume</th></tr></thead><tbody>${rows.map((r, i) =>
    `<tr class="click" data-stk="${esc(r.code)}"><td class="flat">${i + 1}</td><td class="t"><span class="tkr">${esc(r.code)}</span></td><td>${fmtN(r.ltp, 1)}</td><td>${fmtN(r.close, 1)}</td><td class="${cls(r.change_pct)}">${fmtPct(r.change_pct)}</td><td>${fmtN(r.trade)}</td><td>${fmtN(r.value_mn, 2)}</td><td>${fmtN(r.volume)}</td></tr>`).join("")}</tbody></table></div>`;
}
function mktNews(d) {
  const items = d.news || [], n = X.mkt.newsN;
  if (!items.length) return emptyState("news", "No notices fetched", "The exchange's news feed did not answer; Refresh tries again.");
  return `<ul class="newsfeed">${items.slice(0, n).map(i => `<li><div class="nh">${i.code ? `<span class="tkr">${esc(i.code)}</span>` : ""}<span class="sub">${esc(i.date)}</span></div><h4>${esc(i.headline)}</h4>${i.text ? `<p>${esc(i.text)}</p>` : ""}</li>`).join("")}</ul>
    ${items.length > n ? `<button type="button" class="btn secondary sm" data-act="mkt-news-more">Show more (${items.length - n})</button>` : ""}`;
}
function renderDash() {
  const m = X.mkt, d = m.data, s = d && d.summary;
  const rows = ORDER.map(t => {
    const dd = DATA[t], f = dd.forecast, ran = S.ran[t], cl = dd.history.slice(-30).map(h => h[1]);
    const ch = (cl[cl.length - 1] / cl[0] - 1) * 100, p = fcPath(t), ens = p ? (p[6] / f.last_price - 1) * 100 : null, age = daysBetween(f.last_date, refDate());
    return `<tr class="click" data-t="${esc(t)}"><td><span class="tkr">${esc(t)}</span><div class="sub">${esc(dd.meta.name)}</div></td><td>${fmtP(f.last_price)} <span class="sub">${esc(dd.meta.currency)}</span></td><td>${spark(cl)}</td><td class="${cls(ch)}">${fmtPct(ch)}</td><td style="${age > 5 ? "color:var(--hold)" : ""}">${dShort(f.last_date)}</td><td>${ran ? sigBadge((outlook(t) || {}).sig) : `<span class="badge">not run</span>`}</td><td class="${cls(ens)}">${fmtPct(ens)}</td><td>${(b => b && !b.stale ? `${ratingBadge(b.rating)} <span class="${cls(b.adj)}">${fmtPct(b.adj * 100)}</span>` : b ? `${ratingBadge(b.rating)} <span class="sub">old</span>` : "–")(aiBlend(t))}</td></tr>`;
  }).join("");
  const dse = ORDER.filter(t => DATA[t].meta.source === "dse");
  const none = LIVE.ok ? (m.state === "loading" ? "Loading…" : "The exchange did not answer. Refresh tries again.") : "Run the app's server to see the live market here (python -m uvicorn app.main:app --app-dir backend).";
  return `${mktHead()}
  ${mktIndices(d)}
  <section class="mksec"><div class="sec-h"><div><h2>Market summary</h2><p>The latest trading day on the Dhaka Stock Exchange.</p></div></div>${s ? mktSummary(s, d) : card("", "", "", emptyState("db", "No market summary yet", none), "dashed")}</section>
  <section class="mksec mk2"><div>${card("Sector breadth", "Advanced, declined and unchanged issues per sector.", "", s ? mktSectors(s) : emptyState("db", "No data yet", none))}</div>
    <div>${card("Top 20 shares", "Ranked by the column you pick.", `<div class="pills">${MKT_SORTS.map(([k, l]) => `<button type="button" class="pillbtn" data-mkt="${k}" aria-pressed="${m.sort === k}">${l}</button>`).join("")}</div>`, s ? mktTop(s) : emptyState("db", "No data yet", none))}</div></section>
  <section class="mksec"><div class="sec-h"><div><h2>RAEM forecast signals</h2><p>Seven-day model forecasts for the stocks on this site. Click a row for the full forecast.</p></div><button type="button" class="btn primary sm" id="runall">Show all signals</button></div>
    ${card("Watchlist", "Last close, 30-day move, the recommendation and the reviewed forecast's call.", "", `<div class="scroll"><table><thead><tr><th>Stock</th><th>Last close</th><th>30 days</th><th>30d change</th><th>Price data to</th><th>Signal</th><th>Forecast 7d</th><th>With AI ${hint(GLOSS.aiadj)}</th></tr></thead><tbody>${rows}</tbody></table></div><p class="src">Source: data/ and daily_data/ price files; signals from predictor_lib; Ensemble from forecast_lib; With AI = the Ensemble tilted by the latest AI analysis.</p>`)}</section>
  <section class="mksec">${card("DSE news", "Latest notices from the exchange.", "", d ? mktNews(d) : emptyState("news", "No news yet", none))}</section>
  ${dse.length && DATA[dse[0]].daily.data_end_date < DATA[dse[0]].forecast.last_date ? `<section class="card warn"><div style="display:flex;gap:10px;align-items:flex-start;flex-wrap:wrap"><span class="badge hold">Data note</span><p style="font-size:13px;color:var(--muted);flex:1 1 300px">The second-opinion daily models for ${dse.map(esc).join(", ")} use prices up to ${dLong(DATA[dse[0]].daily.data_end_date)}, because daily.py reads only the data snapshot. The 7-day forecasts and the recommendation use prices up to ${dLong(DATA[dse[0]].forecast.last_date)}.</p></div></section>` : ""}`;
}


/* ---------- stocks: the DSE list and one company's analysis page ---------- */
X.stk = { list: null, lstate: "idle", lerr: "", q: "", sector: "", n: 100, code: "", det: {}, dstate: {}, derr: {}, range: 126 };
const cr = mn => mn == null ? null : mn / 10;   // BDT millions to crore
const taka = (v, d = 2) => v == null ? "–" : "৳" + fmtN(v, d);
const takaCr = mn => mn == null ? "–" : "৳" + fmtN(cr(mn), 2) + " Cr";
function ensureStocks() { if (LIVE.ok && !X.stk.list && X.stk.lstate !== "loading" && X.stk.lstate !== "err") loadStocks(false); }
function ensureStock(code) { if (LIVE.ok && code && !X.stk.det[code] && X.stk.dstate[code] !== "loading" && X.stk.dstate[code] !== "err") loadStock(code, false); }
function openStock(code) {
  if (S.running) return;
  X.stk.code = String(code).toUpperCase(); S.more = false; S.page = "stock"; $("#app").classList.remove("open"); render(false); $("#scroller").scrollTop = 0;
}
function resolveStock(v) {
  v = String(v || "").trim(); if (!v) return null;
  const L = X.stk.list ? X.stk.list.stocks : [], up = v.toUpperCase(), low = v.toLowerCase();
  const hit = L.find(r => r.code === up) || L.find(r => (r.name || "").toLowerCase().includes(low)) || L.find(r => r.code.startsWith(up));
  return hit ? hit.code : (/^[A-Z0-9._&-]{1,20}$/.test(up) ? up : null);
}
const crumb = (...parts) => `<nav class="crumb" aria-label="Breadcrumb">${parts.map((p, i) => (i ? "<span>/</span>" : "") + (Array.isArray(p) ? `<button type="button" class="link" data-page="${p[0]}">${p[1]}</button>` : `<b>${esc(p)}</b>`)).join("")}</nav>`;
const needServer = what => card("", "", "", emptyState("db", `${what} needs the server`, "Run the app's server to load live DSE data (python -m uvicorn app.main:app --app-dir backend)."), "dashed");

function renderStocks() {
  const x = X.stk, L = x.list;
  const sub = L ? `${fmtN(L.count)} actively traded companies on the Dhaka Stock Exchange · Prices as of ${L.date ? dLong(L.date) : "–"}${L.stale ? " (exchange not reachable, last saved copy)" : ""}` : "Every company traded on the Dhaka Stock Exchange, with its latest price and change.";
  const head = `${crumb(["dash", "Market"], "DSE Stocks")}<div class="pagehead"><div><h1>All DSE Listed Companies</h1><p class="sub">${esc(sub)}</p></div></div>`;
  if (!LIVE.ok) return head + needServer("The stock list");
  if (!L) return head + card("", "", "", emptyState("db", x.lstate === "err" ? "The stock list could not be loaded" : "Loading the stock list…", x.lstate === "err" ? esc(x.lerr) : "Fetching the latest trading day from the exchange.", x.lstate === "err" ? `<button type="button" class="btn secondary sm" data-act="stk-reload">Try again</button>` : ""), "dashed");
  const q = x.q.trim().toLowerCase();
  const rows = L.stocks.filter(r => (!x.sector || r.sector === x.sector) && (!q || r.code.toLowerCase().includes(q) || (r.name || "").toLowerCase().includes(q)));
  const chips = L.sectors.length ? `<div class="field"><label>Browse by sector</label><div class="pills">${L.sectors.map(s => `<button type="button" class="pillbtn" data-act="stk-sector" data-v="${esc(s.sector)}" aria-pressed="${x.sector === s.sector}">${esc(s.sector)} <span class="sub">${s.count}</span></button>`).join("")}</div></div>` : "";
  const note = L.sectors_known < L.count ? `<p class="sub">Sector is known for ${fmtN(L.sectors_known)} of ${fmtN(L.count)} companies. It is added when you open a stock, or all at once with <span class="mono">python backend/tools/build_dse_sectors.py</span>.</p>` : "";
  const body = rows.slice(0, x.n).map(r => `<tr class="click" data-stk="${esc(r.code)}"><td class="t"><span class="tkr">${esc(r.code)}</span></td><td class="t nm">${esc(r.name || "–")}</td><td class="t">${r.sector ? `<span class="badge">${esc(r.sector)}</span>` : `<span class="sub">–</span>`}</td><td>${fmtN(r.close || r.ltp, 2)}</td><td class="${cls(r.change_pct)}">${fmtPct(r.change_pct)}</td><td class="t"><button type="button" class="btn secondary sm" data-stk="${esc(r.code)}">Analyze →</button></td></tr>`).join("");
  return `${head}<div class="field stkq"><input class="inp" id="stk-q" data-bind="stk-q" placeholder="Search symbol or company…" value="${esc(x.q)}" aria-label="Search symbol or company" autocomplete="off" spellcheck="false"></div>${chips}${note}
    <p class="sub" style="margin:12px 0">${fmtN(rows.length)} results</p>
    ${card("", "", "", rows.length ? `<div class="scroll"><table class="dense stktable"><thead><tr><th>Symbol</th><th>Company</th><th>Sector</th><th>LTP ${L.date ? `<span class="sub">${esc(L.date)}</span>` : ""}</th><th>Change</th><th>Analysis</th></tr></thead><tbody>${body}</tbody></table></div>
      ${rows.length > x.n ? `<p style="margin-top:14px"><button type="button" class="btn secondary sm" data-act="stk-more">Show more (${rows.length - x.n})</button></p>` : ""}` : emptyState("search", "No company matches", "Try another symbol or company name, or clear the sector filter."))}`;
}

function stockStats(d) {
  const t = (k, v, c) => `<div class="statx"><p class="k">${k}</p><p class="v mono ${c || ""}">${v}</p></div>`;
  const chg = d.change == null ? "–" : `${d.change >= 0 ? "+" : "−"}${fmtN(Math.abs(d.change), 2)} <span>(${fmtPct(d.change_pct)})</span>`;
  return `<div class="card statone"><div class="statcells">${t("Last close", taka(d.last_close))}${t("Change", chg, cls(d.change))}${t("Day high", taka(d.day_high))}${t("Day low", taka(d.day_low))}${t("52W high", taka(d.week52_high))}${t("52W low", taka(d.week52_low))}${t("Volume", fmtN(d.volume))}${t("Market cap", takaCr(d.market_cap_mn))}</div></div>`;
}
function stockLastSession(code) {
  const a = EXTRA.ai[code], L = a && a.latest; if (!L) return "";
  const sig = aiSignal(L), d = DATA[code], cur = d ? d.meta.currency : "", fd = L.final_decision || "", v = L.verifier || {}, open = !!(X.stk.detail && X.stk.detail[code]);
  const summ = mdField(fd, "Executive Summary") || (fd ? fd.replace(/\*\*/g, "").slice(0, 400) : ""), b = d ? aiBlend(code) : null;
  const out = b && !b.stale ? `<p class="sub">7-day outlook with this decision: <b class="mono ${cls(b.adj)}">${fmtPct(b.adj * 100)} · ${fmtP(b.price)} ${esc(cur)}</b></p>` : "";
  return `<div class="raemai"><p class="sigline">AI analysis, last saved session: ${sig ? sigBadge(sig) : ratingBadge(L.rating)}</p>
    <p class="sub">${dLong(L.date)}${L.llm_model ? " · " + esc(L.llm_model) : ""} · portfolio manager rating ${esc(L.rating || "n/a")}${v.status ? ` · verifier ${esc(String(v.status).toLowerCase())}` : ""}</p>
    ${summ ? `<p class="rsum">${esc(summ)}</p>` : ""}${out}
    <div class="cta"><button type="button" class="btn secondary sm" data-act="stk-ai" data-v="${esc(code)}">Open AI analysis</button>
      <button type="button" class="btn secondary sm" data-act="stk-detail" data-v="${esc(code)}" aria-expanded="${open}">${open ? "Hide details" : "Details"}</button></div>${open ? stockProcess(L) : ""}</div>`;
}
/* The full process behind a saved decision: every stage of the session in the order it ran, each one openable. The
   debates use the complete turns when the session kept them (website runs), else the stage's saved text. */
function stockProcess(L) {
  const steps = Object.entries(L.steps || {}), dbg = L.debate && L.debate.complete ? L.debate : null, sig = aiSignal(L);
  const turns = (list, fallback) => list && list.length ? `<ul class="turns">${list.map(turnHtml).join("")}</ul>` : `<div class="pre">${md(fallback)}</div>`;
  const body = (name, st) => name === "Bull vs bear debate" && dbg ? turns(dbg.turns, st.text)
    : name === "Risk debate" && dbg ? turns(dbg.risk_turns, st.text) : `<div class="pre">${md(st.text)}</div>`;
  if (!steps.length) return `<p class="sub" style="margin-top:12px">This session did not keep its stage outputs.</p>`;
  const last = steps.length - 1;
  return `<div class="aiproc"><p class="sub">${steps.length} stages ran in this order; open any stage to read what it produced. Outputs are shortened where the session saved only an excerpt.</p>
    <ol class="flow">${steps.map(([n], i) => `<li>${esc(n)}</li>`).join("")}<li class="end">${esc(sig || L.rating || "Decision")}</li></ol>
    ${steps.map(([n, st], i) => `<details${i === last ? " open" : ""}><summary><span class="no mono">${i + 1}</span><span class="nm">${esc(n)}</span><span class="tm mono">${esc(st.time || "")}</span></summary><div class="scrollbox">${body(n, st)}</div></details>`).join("")}</div>`;
}
function stockRaem(d) {
  const code = d.code, inData = !!DATA[code], busy = LIVE.busy["co:" + code], o = inData ? outlook(code) : null;
  const sess = stockLastSession(code);
  let left, right;
  if (o) {
    left = `${ring(o.sig, Math.max(o.pGain || 0, o.pFall || 0), o.sig, 96)}`;
    right = `<p class="sigline">RAEM signal: ${sigBadge(o.sig)}</p><p class="sub">Expected 7-day move <b class="mono ${cls(o.m)}">${fmtPct(o.m * 100)}</b> from the reviewed forecast. ${esc(o.why ? o.why.charAt(0).toUpperCase() + o.why.slice(1) + "." : "")}</p>
      <div class="cta"><button type="button" class="btn primary sm" data-t="${esc(code)}">Open full forecast</button>${sess ? "" : `<button type="button" class="btn secondary sm" data-act="stk-ai" data-v="${esc(code)}">AI analysis</button>`}</div>`;
  } else {
    left = `<div class="ring0" aria-hidden="true">–</div>`;
    right = `<p class="sigline">No RAEM analysis for ${esc(code)} yet</p><p class="sub">Adding the company fetches its prices and runs the seven-day forecast with the models; the signal and the AI analysis then appear here.${busy ? " Adding it now…" : ""}</p>
      <div class="cta"><button type="button" class="btn primary sm" data-add="${esc(code)}" data-market="dse" data-name="${esc(d.name || "")}"${busy ? " disabled" : ""}>${busy ? "Adding…" : "Run RAEM analysis"}</button>${sess ? "" : `<button type="button" class="btn secondary sm" data-act="stk-ai" data-v="${esc(code)}">AI analysis only</button>`}</div>`;
  }
  return card("RAEM analysis", "Seven-day model forecast and the BUY, HOLD or SELL signal.", "", `<div class="raemrow"><div>${left}</div><div>${right}</div></div>${sess}`);
}
const HOLD_COL = { "Sponsor/Director": "var(--c7)", Institute: "var(--buy)", Public: "var(--faint)", Govt: "var(--hold)", Foreign: "var(--c3)" };
function donut(sh) {
  const R = 52, C = 2 * Math.PI * R, tot = sh.reduce((q, s) => q + s.pct, 0) || 1; let off = 0;
  const top = sh.slice().sort((a, b) => b.pct - a.pct)[0];
  const arcs = sh.map(s => { const len = s.pct / tot * C, a = `<circle cx="70" cy="70" r="${R}" fill="none" stroke="${HOLD_COL[s.label] || "var(--faint)"}" stroke-width="16" stroke-dasharray="${len.toFixed(2)} ${(C - len).toFixed(2)}" stroke-dashoffset="${(-off).toFixed(2)}" transform="rotate(-90 70 70)"/>`; off += len; return a; }).join("");
  return `<svg viewBox="0 0 140 140" class="donut" role="img" aria-label="Shareholding pattern">${arcs}<text x="70" y="68" text-anchor="middle" class="dn">${top.pct.toFixed(1)}%</text><text x="70" y="84" text-anchor="middle" class="dl">${esc(top.label)}</text></svg>`;
}
function epsBars(rows) {
  const W = 360, H = 170, padB = 22, padT = 22, hi = Math.max(...rows.map(r => r.eps), 0), lo = Math.min(...rows.map(r => r.eps), 0), span = hi - lo || 1;
  const y = v => padT + (hi - v) * (H - padT - padB) / span, bw = Math.min(38, (W - 20) / rows.length - 14), step = (W - 20) / rows.length;
  return `<svg viewBox="0 0 ${W} ${H}" class="epsbars" role="img" aria-label="Annual EPS">${rows.map((r, i) => {
    const prev = i ? rows[i - 1].eps : null, col = prev == null ? "var(--faint)" : r.eps >= prev ? "var(--buy)" : "var(--sell)", x = 10 + step * i + (step - bw) / 2, y0 = y(Math.max(r.eps, 0)), h = Math.max(2, Math.abs(y(r.eps) - y(0)));
    return `<rect x="${x.toFixed(1)}" y="${y0.toFixed(1)}" width="${bw}" height="${h.toFixed(1)}" rx="3" fill="${col}"/><text x="${(x + bw / 2).toFixed(1)}" y="${(y0 - 5).toFixed(1)}" text-anchor="middle" class="ev">${r.eps.toFixed(2)}</text><text x="${(x + bw / 2).toFixed(1)}" y="${H - 6}" text-anchor="middle" class="ex">${r.year}</text>`;
  }).join("")}</svg>`;
}
function stockFundamentals(d) {
  const sh = d.shareholding || [], ae = d.annual_eps || [], qe = d.quarterly_eps || [];
  const tile = (k, v) => `<div class="card statx"><p class="k">${k}</p><p class="v mono">${v}</p></div>`;
  const tiles = `<div class="fundtiles">${tile("Category", esc(d.category || "–"))}${tile("Paid-up capital", takaCr(d.paid_up_mn))}${tile("Cash dividend", d.dividend ? `${d.dividend.pct}% (${d.dividend.year})` : "–")}${tile("Outstanding shares", fmtN(d.shares_out))}</div>`;
  const hold = sh.length ? `<div class="subcard"><p class="k">Shareholding pattern</p><div class="holdrow">${donut(sh)}<ul class="legend2">${sh.map(s => `<li><i style="background:${HOLD_COL[s.label] || "var(--faint)"}"></i><span>${esc(s.label)}</span><b class="mono">${s.pct.toFixed(2)}%</b></li>`).join("")}</ul></div></div>` : `<div class="subcard"><p class="k">Shareholding pattern</p><p class="sub">Not available from the company page.</p></div>`;
  const eps = ae.length ? `<div class="subcard"><p class="k">Annual EPS trend</p>${epsBars(ae)}</div>` : `<div class="subcard"><p class="k">Annual EPS trend</p><p class="sub">Not available from the company page.</p></div>`;
  const q = `<div class="subcard"><p class="k">Quarterly EPS</p>${qe.length ? `<div class="qeps">${qe.map(r => `<div><b class="mono ${r.eps < 0 ? "down" : ""}">${r.eps < 0 ? "−" : ""}${Math.abs(r.eps).toFixed(2)}</b><span>${esc(r.period)}</span></div>`).join("")}</div>` : `<p class="sub">No quarterly EPS disclosure found in the exchange's recent notices.</p>`}</div>`;
  return card("Company fundamentals", "", "", `${tiles}<div class="fundgrid">${hold}${eps}</div>${q}<p class="src">Fundamental data sourced from dsebd.org${d.as_of ? `, last updated ${esc(d.as_of)}` : ""}.</p>`);
}
function renderStock() {
  const x = X.stk, code = x.code, d = x.det[code];
  const bc = crumb(["dash", "Market"], ["stocks", "DSE Stocks"], code);
  if (!LIVE.ok) return bc + needServer("The stock analysis");
  if (!d) return bc + card("", "", "", emptyState("chart", x.dstate[code] === "err" ? `${esc(code)} could not be loaded` : `Loading ${esc(code)}…`, x.dstate[code] === "err" ? esc(x.derr[code] || "") : "Fetching the company page and its price history.", x.dstate[code] === "err" ? `<button type="button" class="btn secondary sm" data-act="stk-reload" data-v="${esc(code)}">Try again</button>` : ""), "dashed");
  if (!d.ok) return bc + card("", "", "", emptyState("alert", `No price data for ${esc(code)}`, `The exchange returned no daily prices for this code. ${esc((d.errors || []).join(" · "))}`, `<button type="button" class="btn secondary sm" data-page="stocks">Back to the list</button>`), "dashed");
  return `${bc}
  <div class="pagehead"><div><h1>${esc(d.name || code)} <span class="stkcode">(${esc(code)})</span></h1><p class="sub">Dhaka Stock Exchange${d.sector ? ` · ${esc(d.sector)}` : ""}${d.category ? ` · Category ${esc(d.category)}` : ""} · As of ${dLong(d.as_of)}</p></div></div>
  <form class="stkjump" id="stk-jump" autocomplete="off"><input class="inp" id="stk-jump-q" placeholder="Search another DSE stock, e.g. IPDC, Renata, Robi" aria-label="Search another DSE stock" spellcheck="false"><button type="submit" class="btn secondary sm">Search</button></form>
  <section class="mksec stkmain"><div class="stkside"><h2 class="stkh">${esc(code)} key statistics</h2>${stockStats(d)}</div>${card(`${esc(code)} daily price and volume`, "Dhaka Stock Exchange", `<div class="pills">${[[63, "3M"], [126, "6M"], [252, "1Y"]].map(([n, l]) => `<button type="button" class="pillbtn" data-act="stk-range" data-v="${n}" aria-pressed="${x.range === n}">${l}</button>`).join("")}</div>`, `<div class="ohlc mono" id="stk-ohlc"></div><div class="chartbox candles" id="stk-chart"></div><p class="src" id="stk-fcnote"></p>`)}</section>
  <section class="mksec">${stockRaem(d)}</section>
  <section class="mksec">${stockFundamentals(d)}</section>
  ${d.errors && d.errors.length ? `<p class="sub">Some parts could not be fetched: ${esc(d.errors.join(" · "))}</p>` : ""}`;
}
function drawStockChart() {
  const h = $("#stk-chart"); if (!h) return;
  const d = X.stk.det[X.stk.code]; if (!d || !d.candles || !d.candles.length) return;
  const cs = d.candles.slice(-X.stk.range), n = cs.length;
  // the site's 7-day forecast for this company (when it has one), drawn after its own last close
  const ax = cs.map(c => c[0]), fT = DATA[d.code] && fcPath(d.code) ? d.code : null;
  let fc = null;
  if (fT) {
    const f = DATA[fT].forecast, fp = fcPath(fT), bd = fcBand(fT);
    f.dates.forEach(ds => { if (ds > ax[ax.length - 1]) ax.push(ds); });
    const ix = ds => ax.findIndex(a => a >= ds), a0 = ix(f.last_date);
    if (a0 >= 0 && ax[0] <= f.last_date) {
      fc = { name: fcName(fT), from: f.last_date, pts: [[a0, f.last_price, f.last_price, f.last_price]] };
      f.dates.forEach((ds, k) => fc.pts.push([ix(ds), fp[k], bd ? bd[k][0] : fp[k], bd ? bd[k][1] : fp[k]]));
    }
  }
  const nAx = ax.length;
  const W = Math.max(320, h.clientWidth || 700), H = 380, padL = 8, padR = 56, padT = 10, volH = 70, axisH = 22, gap = 12, priceH = H - padT - volH - gap - axisH;
  const fcv = fc ? fc.pts.flatMap(q => [q[1], q[2], q[3]]) : [], lo0 = Math.min(...cs.map(c => c[3]), ...fcv), hi0 = Math.max(...cs.map(c => c[2]), ...fcv), pad = (hi0 - lo0) * 0.06 || 0.05, lo = lo0 - pad, hi = hi0 + pad;
  const y = v => padT + (hi - v) * priceH / (hi - lo), vmax = Math.max(...cs.map(c => c[5])) || 1, step = (W - padL - padR) / nAx, cw = Math.max(1, Math.min(10, step * 0.7)), x = i => padL + step * (i + 0.5);
  const volTop = padT + priceH + gap, last = cs[n - 1];
  let s = `<svg viewBox="0 0 ${W} ${H}" width="100%" role="img" aria-label="${esc(d.code)} candlestick chart with volume">`;
  niceTicks(lo, hi, 5).filter(v => v >= lo && v <= hi).forEach(v => { s += `<line x1="${padL}" x2="${W - padR}" y1="${y(v).toFixed(1)}" y2="${y(v).toFixed(1)}" stroke="var(--border-soft)"/><text class="ax" x="${W - padR + 6}" y="${(y(v) + 3).toFixed(1)}">${fmtN(v, 2)}</text>`; });
  cs.forEach((c, i) => {
    const up = c[4] >= c[1], col = up ? "var(--buy)" : "var(--sell)", top = y(Math.max(c[1], c[4])), bot = y(Math.min(c[1], c[4]));
    s += `<line x1="${x(i).toFixed(1)}" x2="${x(i).toFixed(1)}" y1="${y(c[2]).toFixed(1)}" y2="${y(c[3]).toFixed(1)}" stroke="${col}"/><rect x="${(x(i) - cw / 2).toFixed(1)}" y="${top.toFixed(1)}" width="${cw.toFixed(1)}" height="${Math.max(1, bot - top).toFixed(1)}" fill="${col}"/>`;
    s += `<rect x="${(x(i) - cw / 2).toFixed(1)}" y="${(volTop + volH - c[5] / vmax * volH).toFixed(1)}" width="${cw.toFixed(1)}" height="${(c[5] / vmax * volH).toFixed(1)}" fill="${col}" opacity="0.45"/>`;
  });
  if (fc) {
    const P = (q, k) => `${x(q[0]).toFixed(1)},${y(q[k]).toFixed(1)}`;
    s += `<polygon points="${[...fc.pts.map(q => P(q, 3)), ...fc.pts.slice().reverse().map(q => P(q, 2))].join(" ")}" fill="var(--accent-soft)"/>`;
    s += `<polyline points="${fc.pts.map(q => P(q, 1)).join(" ")}" fill="none" stroke="var(--accent-strong)" stroke-width="2" stroke-dasharray="5 3"/>`;
    fc.pts.slice(1).forEach(q => { s += `<circle cx="${x(q[0]).toFixed(1)}" cy="${y(q[1]).toFixed(1)}" r="2.5" fill="var(--accent-strong)"/>`; });
  }
  s += `<line x1="${padL}" x2="${W - padR}" y1="${y(last[4]).toFixed(1)}" y2="${y(last[4]).toFixed(1)}" stroke="${last[4] >= last[1] ? "var(--buy)" : "var(--sell)"}" stroke-dasharray="2 3"/><rect x="${W - padR + 2}" y="${(y(last[4]) - 9).toFixed(1)}" width="${padR - 4}" height="18" rx="3" fill="${last[4] >= last[1] ? "var(--buy)" : "var(--sell)"}"/><text x="${W - padR / 2}" y="${(y(last[4]) + 4).toFixed(1)}" text-anchor="middle" style="font:600 11px var(--font-mono);fill:var(--on-accent)">${fmtN(last[4], 2)}</text>`;
  [0, 0.25, 0.5, 0.75, 1].forEach((f, k) => { const i = Math.min(nAx - 1, Math.round(f * (nAx - 1))); s += `<text class="ax" x="${x(i).toFixed(1)}" y="${H - 6}" text-anchor="${k === 0 ? "start" : k === 4 ? "end" : "middle"}">${dShort(ax[i])}</text>`; });
  s += `<line id="stk-cross" x1="0" x2="0" y1="${padT}" y2="${volTop + volH}" stroke="var(--faint)" stroke-dasharray="3 3" opacity="0"/><rect id="stk-hit" x="${padL}" y="${padT}" width="${W - padL - padR}" height="${H - padT}" fill="transparent"/></svg>`;
  h.innerHTML = s;
  const out = $("#stk-ohlc"), show = i => {
    const c = cs[i], p = c && i ? cs[i - 1][4] : null, ch = p ? (c[4] / p - 1) * 100 : null, q = fc && fc.pts.slice(1).find(z => z[0] === i);
    const fcTxt = q ? ` &nbsp; <span class="fcv">Forecast ${fmtN(q[1], 2)}</span> <span class="sub">(${fmtN(q[2], 2)} to ${fmtN(q[3], 2)})</span>` : "";
    out.innerHTML = c ? `<b>${esc(d.code)}</b> <span class="sub">${esc(c[0])}</span> &nbsp; O <span>${fmtN(c[1], 2)}</span> H <span>${fmtN(c[2], 2)}</span> L <span>${fmtN(c[3], 2)}</span> C <span>${fmtN(c[4], 2)}</span> V <span>${fmtN(c[5])}</span> <span class="${cls(ch)}">${fmtPct(ch)}</span>${fcTxt}`
      : `<b>${esc(d.code)}</b> <span class="sub">${esc(ax[i])}</span>${fcTxt}`; };
  const note = $("#stk-fcnote");
  if (note) note.innerHTML = fc ? `Dashed blue line: ${esc(fc.name.toLowerCase())} for the next 7 trading days after the ${dLong(fc.from)} close, with its likely range shaded. ${fc.from < last[0] ? "It was made on older prices, so you can compare it with what actually happened. " : ""}Source: forecast_lib. Research output, not investment advice.` : "";
  show(n - 1);
  const svg = h.querySelector("svg"), cross = $("#stk-cross");
  svg.addEventListener("pointermove", ev => { const r = svg.getBoundingClientRect(), px = (ev.clientX - r.left) * W / r.width, i = Math.max(0, Math.min(nAx - 1, Math.floor((px - padL) / step)));
    cross.setAttribute("x1", x(i)); cross.setAttribute("x2", x(i)); cross.setAttribute("opacity", "1"); show(i); });
  svg.addEventListener("pointerleave", () => { cross.setAttribute("opacity", "0"); show(n - 1); });
}
