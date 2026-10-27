
/* ----- live backend: used only when this page is served by the FastAPI app (web/backend) ----- */
const LIVE = { ok: false, health: null, models: null, token: lsGet("raem:token", ""), fc: {}, ai: {}, busy: {}, err: {}, hist: {} };
X.lfc = { models: null, force: false };
X.ai.model = null;
X.ai.force = false;
const sleep = ms => new Promise(r => setTimeout(r, ms));
const liveId = key => key.replace(/[^\w-]/g, "_");
async function api(path, opts = {}) {
  const headers = Object.assign({ "Content-Type": "application/json" }, opts.headers || {});
  if (LIVE.token) headers.Authorization = "Bearer " + LIVE.token;
  const r = await fetch("api/" + path, Object.assign({}, opts, { headers }));
  let body = null;
  try { body = await r.json(); } catch (e) { body = null; }
  if (!r.ok) throw new Error((body && body.detail) || `The server answered ${r.status}.`);
  return body;
}
function applyForecastRun(t, run, onlyNewer) {
  if (!run || !run.result || !run.result.forecast || !DATA[t]) return;
  if (onlyNewer && run.result.forecast.last_date < DATA[t].forecast.last_date) return;  // the saved page already has newer prices
  DATA[t] = run.result;
  LIVE.fc[t] = { id: run.id, at: run.finished_at || "", date: run.run_date, models: (run.params || {}).models || [] };
}
function applyAnalysisRun(t, run, onlyNewer) {
  if (!run || !run.result || !run.result.entry) return;
  const cur = EXTRA.ai[t] && EXTRA.ai[t].latest, nxt = run.result.entry.latest || {};
  if (onlyNewer && cur && (cur.start || cur.date || "") > (nxt.start || nxt.date || "")) return;
  EXTRA.ai[t] = run.result.entry;
  LIVE.ai[t] = { id: run.id, at: run.finished_at || "", date: run.run_date, model: run.model_key };
  if (!AI_T.includes(t)) AI_T.push(t);
}
async function liveInit() {
  if (!/^https?:$/.test(location.protocol)) return;  // opened as a file: saved results only
  try {
    const h = await api("health");
    if (!h || h.status !== "ok" || !h.tickers) return;
    LIVE.health = h;
    LIVE.models = await api("models");
    LIVE.ok = true;
    try {  // companies added from the search box earlier: bring their saved forecast into the page (the page file may predate them)
      const cos = await api("companies");
      await Promise.all(cos.filter(c => c.ready && !DATA[c.ticker]).map(c => loadCompany(c.ticker).catch(() => null)));
    } catch (e) { /* an older server without the company routes */ }
    // the newest finished run of every company, on any date: a run's results stay on the page until a newer one
    const lt = await api("runs/latest?date=all");
    Object.entries(lt.forecast || {}).forEach(([t, run]) => applyForecastRun(t, run, true));
    Object.entries(lt.analysis || {}).forEach(([t, run]) => applyAnalysisRun(t, run, true));
    const s = $("#savedAt");
    if (s) s.textContent = `Live server · models run on request · saved data from ${dLong(ASOF)}`;
    render();
    // runs already queued or going (started before a reload, or from another browser): show their progress too
    const active = await api("runs?status=queued,running&limit=100");
    active.reverse().forEach(r => r.kind === "forecast" ? watchForecast(r.ticker, r.id) : r.kind === "analysis" ? watchAnalysis(r.ticker, r.id) : r.kind === "company" ? watchCompany(r.ticker, r.id) : null);
    if (active.length) render();
  } catch (e) {
    LIVE.ok = false;  // static copy (artifact, static host): saved results only
    if (LIVE.models) console.error("live mode off:", e);  // the server answered, so this is a page bug
  }
}
async function pollRun(id, key, onDone) {
  LIVE.busy[key] = { id, status: "queued", step: "", log: [], t0: Date.now() };
  for (;;) {
    let r;
    try {
      r = await api("jobs/" + id);
    } catch (e) {
      await sleep(4000);
      continue;  // a server restart or a network blip: keep asking
    }
    Object.assign(LIVE.busy[key], { status: r.status, step: r.step || "", log: r.log || [], ahead: r.ahead || 0 });
    if (r.elapsed != null) LIVE.busy[key].t0 = Date.now() - r.elapsed * 1000;  // the server's clock, right after a reload too
    if (key.startsWith("ai:") && r.status === "running" && (LIVE.busy[key].n = (LIVE.busy[key].n || 0) + 1) % 2 === 1) {
      try {  // the agents' outputs so far: the pipeline saves its log after every agent
        const lv = await api(`runs/${id}/live`);
        if (lv.entry && LIVE.busy[key]) LIVE.busy[key].entry = lv.entry;
      } catch (e) { /* asked again on the next poll */ }
    }
    const el = document.getElementById("live-" + liveId(key));
    if (el) el.innerHTML = liveStatus(key);
    if (r.status === "done") {
      const full = await api("runs/" + id);
      delete LIVE.busy[key];
      onDone(full);
      return;
    }
    if (r.status === "failed" || r.status === "held") {
      delete LIVE.busy[key];
      if (r.status === "failed") LIVE.err[key] = (r.error || "The run failed.") + " " + ((r.log || []).filter(l => l.startsWith("!!") && !/^!! (Executed copy saved|exited with code|RuntimeError)/.test(l) && !l.includes("Cause:")).slice(-2).join(" "));
      LIVE.hist[r.ticker] = undefined;  // the stored-runs table shows the held or failed run
      render();
      return;
    }
    await sleep(2500);
  }
}
function liveStatus(key) {
  const j = LIVE.busy[key];
  if (!j) return "";
  const m = /^\[(\d+)\/(\d+)\]\s*(.*)$/.exec(j.step || "");
  const pct = m ? Math.round(+m[1] / +m[2] * 100) : null;
  const lines = (j.log || []).filter(l => l.startsWith("== ") || l.startsWith("!!")).slice(-6).reverse();
  const mins = Math.floor((Date.now() - j.t0) / 60000), secs = Math.floor((Date.now() - j.t0) / 1000) % 60;
  return `<section class="card progress" style="margin-top:14px;box-shadow:none"><div class="top"><div class="ttl"><span class="dotc warn"></span>${j.status === "queued" ? `Waiting in the server queue${j.ahead ? ` (${j.ahead} ahead)` : ""}` : j.holding ? "Holding…" : "Running on the server"}</div><span class="mono" style="font-size:12px;color:var(--muted)">${mins}:${String(secs).padStart(2, "0")}${pct != null ? ` · ${pct}%` : ""}</span></div>
    ${pct != null ? `<div class="bar"><div style="width:${pct}%"></div></div>` : ""}
    <p class="msg" role="status"><b>${esc(m ? m[3] : (j.step || "Starting"))}</b>${m ? ` · step ${m[1]} of ${m[2]}` : ""}</p>
    ${key.startsWith("ai:") && j.status === "running" ? agentBoard(j) : `<ol class="events">${lines.map(l => `<li><span>${esc(l.replace(/^== /, ""))}</span></li>`).join("")}</ol>`}</section>`;
}
// The pipeline's agents in run order (sessions.STEP_LABELS); any other logged step is added where it appears
const AGENT_STAGES = ["Fundamentals", "Market", "News", "Sentiment", "Macro regime", "Bull vs bear debate",
  "Debate facilitator", "Post-mortem", "Trader", "Risk debate", "Risk facilitator", "Portfolio manager", "Verifier"];
function agentBoard(j) {
  const L = j.entry && j.entry.latest, steps = (L && L.steps) || {}, done = Object.keys(steps);
  const all = AGENT_STAGES.concat(done.filter(s => !AGENT_STAGES.includes(s))), next = all.find(s => !done.includes(s)), last = done[done.length - 1];
  const turns = L && L.debate ? (L.debate.turns || []) : [];
  return `<ol class="agents" aria-label="Agents">${all.map(s => `<li class="${done.includes(s) ? "done" : s === next ? "now" : ""}"><span class="dot"></span><span>${esc(s)}${done.includes(s) && steps[s].time ? ` <span class="sub">${esc(steps[s].time)}</span>` : ""}</span></li>`).join("")}</ol>
    ${last ? `<div class="sect"><p class="l">Latest: ${esc(last)}</p><div class="pre" style="max-height:220px;overflow:auto">${md(String(steps[last].text || "").slice(0, 1800))}</div></div>` : `<p class="small" style="margin-top:10px">${esc(j.step || "Starting")} · agent outputs appear here as each one finishes.</p>`}
    ${turns.length ? `<div class="sect"><p class="l">Debate so far</p><ol class="debate">${turns.map(turnHtml).join("")}</ol></div>` : ""}`;
}
function tokenRow() {
  return LIVE.health && LIVE.health.run_token_required && !LIVE.token ? `<div class="field" style="margin-top:12px"><label for="run-token">Access token</label><input class="inp" type="password" id="run-token" data-bind="set-token" placeholder="This server asks for a token before it runs models" autocomplete="off"></div>` : "";
}

/* forecast runs (Forecast page) */
function lfcModels() {  // all models by default, as the forecast notebook runs them (the Ensemble then picks the best two of all)
  if (!X.lfc.models) X.lfc.models = new Set(LIVE.models.forecast.map(m => m.key));
  return X.lfc.models;
}
function showForecast(t) {  // the Forecast page opens its results on the run's models table
  S.ran[t] = true;
  if (S.page === "tk" && S.t === t) S.tab = "models";
}
async function liveForecast(t) {
  const key = "fc:" + t;
  LIVE.err[key] = "";
  try {
    const res = await api("forecast", { method: "POST", body: JSON.stringify({ ticker: t, models: [...lfcModels()], force: !!X.lfc.force }) });
    if (res.reused === "stored") {
      applyForecastRun(t, res.run);
      showForecast(t);
      render();
      return;
    }
    render();
    await watchForecast(t, res.run.id);
  } catch (e) {
    LIVE.err[key] = e.message;
    render();
  }
}
function watchForecast(t, id) {
  const key = "fc:" + t;
  if (LIVE.busy[key]) return;
  return pollRun(id, key, full => {
    applyForecastRun(t, full);
    showForecast(t);
    S.log.unshift({ t, msg: `forecast run with ${(full.params || {}).models.length} models`, ts: nowT() });
    render();
  });
}
/* AI analysis runs */
async function liveAnalysis(t) {
  const key = "ai:" + t;
  LIVE.err[key] = "";
  try {
    const sel = llmSel();
    if (!sel) throw new Error("No LLM is connected on the server.");
    const res = await api("analysis", { method: "POST", body: JSON.stringify({ ticker: t, llm_provider: sel.provider, llm_model: sel.model, force: !!X.ai.force }) });
    LIVE.hist[t] = undefined;
    if (res.reused === "stored") {
      applyAnalysisRun(t, res.run);
      render();
      return;
    }
    render();
    await watchAnalysis(t, res.run.id);
  } catch (e) {
    LIVE.err[key] = e.message;
    render();
  }
}
function watchAnalysis(t, id) {
  const key = "ai:" + t;
  if (LIVE.busy[key]) return;
  return pollRun(id, key, full => {
    applyAnalysisRun(t, full);
    LIVE.hist[t] = undefined;
    S.log.unshift({ t, msg: `AI analysis finished (${full.model_key})`, ts: nowT() });
    render();
  });
}
async function liveHold(t) {
  const key = "ai:" + t, j = LIVE.busy[key];
  if (!j) return;
  j.holding = true;
  render();
  try {
    await api(`runs/${j.id}/hold`, { method: "POST" });  // pollRun sees 'held' and refreshes the card
  } catch (e) {
    j.holding = false;
    LIVE.err[key] = e.message;
    render();
  }
}
async function loadAiHistory(t) {
  try {
    LIVE.hist[t] = await api(`runs?kind=analysis&ticker=${encodeURIComponent(t)}&limit=20`);
  } catch (e) {
    LIVE.hist[t] = [];
  }
  if (S.page === "ai") render();
}
async function loadAiRun(id) {
  try {
    const r = await api("runs/" + id);
    applyAnalysisRun(r.ticker, r);
    X.ai.t = r.ticker;
    render();
  } catch (e) {
    LIVE.err["ai:" + X.ai.t] = e.message;
    render();
  }
}
/* LLM choice: only what the server has connected (Ollama models it can reach, cloud providers with an API key).
   X.ai.model holds "provider|model". */
function llmProviders() {
  const m = LIVE.models;
  const all = m.llm_providers || [{ key: "ollama", label: "Ollama (this server)", ok: m.llm.ok, models: m.llm.models, default: m.llm.default }];
  return all.filter(p => p.ok && p.models.length);
}
function llmSel() {
  const provs = llmProviders();
  if (!provs.length) return null;
  const has = v => { const i = (v || "").indexOf("|"); if (i < 0) return false; const p = provs.find(x => x.key === v.slice(0, i)); return !!(p && p.models.includes(v.slice(i + 1))); };
  if (!has(X.ai.model)) {
    const first = provs.find(p => p.key === "ollama") || provs[0];
    X.ai.model = `${first.key}|${first.models.includes(first.default) ? first.default : first.models[0]}`;
  }
  const i = X.ai.model.indexOf("|"), provider = X.ai.model.slice(0, i), model = X.ai.model.slice(i + 1);
  return { provider, model, p: provs.find(x => x.key === provider), key: provider === "ollama" ? model : `${provider}:${model}` };
}
function selFromKey(key) {  // a run's model key back to "provider|model"
  const i = key.indexOf(":"), pre = key.slice(0, i);
  return (LIVE.models.llm_providers || []).some(p => p.key === pre && p.key !== "ollama") ? `${pre}|${key.slice(i + 1)}` : `ollama|${key}`;
}
function unfinishedRunFor(t, key) {  // today's held run, or today's newest run if it failed after saving answers
  const rows = (LIVE.hist[t] || []).filter(r => r.run_date === LIVE.health.date && r.model_key === key);
  return rows.find(r => r.status === "held") || (rows[0] && rows[0].status === "failed" && rows[0].saved_answers ? rows[0] : null);
}
/* analyse any company: the pipeline needs no preparation, so any ticker runs at once; it is also listed on the page */
function aiAnyCard() {
  const a = X.ai;
  return `<div class="field" style="margin-top:16px"><label for="ai-any">Analyse any company</label>
    <div class="toolbar"><input class="inp grow" id="ai-any" placeholder="Any ticker: TSLA, 7203.T, 0700.HK, SQURPHARMA" value="${esc(a.any || "")}" aria-label="Ticker" autocomplete="off">
      <button type="button" class="btn primary" data-act="ai-any">Analyse</button></div>
    <p class="sub">Any company works, on the site or not: the prices, fundamentals and news are fetched when the analysis runs, with the model chosen below. A 7-day forecast needs the company added from the search box.</p>
    ${a.anyMsg ? `<p class="err" role="alert">${esc(a.anyMsg)}</p>` : ""}</div>`;
}
async function aiAny() {
  const el = $("#ai-any"), raw = ((el && el.value) || X.ai.any || "").trim().toUpperCase();
  X.ai.any = raw; X.ai.anyMsg = "";
  if (!raw) { X.ai.anyMsg = "Type a ticker first."; render(); return; }
  if (!/^[A-Z0-9.\-]{1,20}$/.test(raw)) { X.ai.anyMsg = "A ticker has letters, digits, dots and dashes only."; render(); return; }
  X.ai.extra = X.ai.extra || [];
  if (!DATA[raw] && !X.ai.extra.includes(raw)) X.ai.extra.push(raw);
  X.ai.t = raw; X.ai.any = "";
  render();
  liveAnalysis(raw);
}
function aiLiveCard(t, a) {
  const key = "ai:" + t, li = LIVE.ai[t], j = LIVE.busy[key], sel = llmSel();
  const badge = li ? `<span class="badge buy">${esc(li.model)} · ${esc(li.date)}</span>` : `<span class="badge">${a ? "saved session" : "no session yet"}</span>`;
  if (!sel) {
    return card(`Run AI analysis ${hint(GLOSS.runai)}`, `${esc(d0(t))}`, badge,
      `<p class="err" role="alert">No LLM is connected on this server. Start Ollama with a model pulled, or add an API key (ANTHROPIC_API_KEY for Claude, MOONSHOT_API_KEY for Kimi) to the server's .env and restart it.</p><p class="note" style="margin-top:8px">Stored runs below still open.</p>`);
  }
  const groups = llmProviders().map(p => `<optgroup label="${esc(p.label)}">${p.models.map(m => `<option value="${esc(p.key + "|" + m)}" ${p.key + "|" + m === X.ai.model ? "selected" : ""}>${esc(m)}</option>`).join("")}</optgroup>`).join("");
  const storedToday = li && li.date === LIVE.health.date && li.model === sel.key, held = !j && unfinishedRunFor(t, sel.key);
  const heldStep = held && /^\[(\d+)\/(\d+)\]/.exec(held.step || "");
  const main = j ? `<button type="button" class="btn secondary" data-act="ai-hold" ${j.holding ? "disabled" : ""}>${j.holding ? "Holding…" : "Hold"}</button>`
    : held && held.status === "failed" ? `<button type="button" class="btn primary" data-act="ai-live">Continue (${held.saved_answers} saved answers)</button>`
    : held ? `<button type="button" class="btn primary" data-act="ai-live">Resume${heldStep ? ` from step ${heldStep[1]} of ${heldStep[2]}` : ""}</button>`
    : `<button type="button" class="btn primary" data-act="ai-live">${storedToday && !X.ai.force ? "Open today's stored run" : `Run AI analysis for ${dShort(LIVE.health.date)}`}</button>`;
  const how = sel.provider === "ollama" ? "Runs the whole analysis (analysts, debates, trader, risk, portfolio manager, verifier) on this server's Ollama. A 7B model on a computer without a GPU takes about 1 to 3 hours; smaller models are faster."
    : `Runs the whole analysis with ${esc(sel.p.label)} through the server's API key: minutes instead of hours, and the provider bills each run.`;
  const holdNote = held ? ` The last run ${held.status === "failed" ? "stopped with an error" : "was held"} at ${esc(held.step || "an early step")}. Running it again reuses every saved answer whose inputs (prices, news, earlier answers) are unchanged, so it continues where it stopped; steps whose inputs changed run again.`
    : " Hold stops a run and keeps what it has done; running it again later continues from there while the market data is unchanged.";
  const mem = LIVE.models.embeddings_ok === false ? " Memory: Ollama's nomic-embed-text is not reachable, so the memory uses a separate local store." : "";
  return card(`Today's AI analysis ${hint(GLOSS.runai)}`, `${esc(d0(t))} · runs for ${dLong(LIVE.health.date)}`, badge, `
    <div class="formgrid" style="grid-template-columns:repeat(auto-fit,minmax(220px,1fr))">
      <div class="field"><label for="ai-model">LLM model (connected on this server)</label><select id="ai-model" data-bind="ai-model" ${j ? "disabled" : ""}>${groups}</select></div>
      <div class="field"><span class="lbl">Options</span><div class="checks"><label><input type="checkbox" id="ai-force" data-bind="ai-force" ${X.ai.force ? "checked" : ""}> Run again even if stored today</label></div></div>
    </div>${tokenRow()}
    <div class="runbar">${main}
      <p class="note">${how} Runs are stored by company, date and model, so asking again today opens the stored result at once.${holdNote}${mem}</p></div>
    ${LIVE.err[key] ? `<p class="err" role="alert">${esc(LIVE.err[key])}</p>` : ""}
    <div id="live-${liveId(key)}">${liveStatus(key)}</div>`);
}
function aiHistoryCard(t) {
  const h = LIVE.hist[t];
  if (h === undefined) {
    LIVE.hist[t] = null;
    loadAiHistory(t);
  }
  const rows = (h || []).filter(r => r.kind === "analysis");
  const cell = r => r.status === "done" ? `<button type="button" class="link" data-act="ai-load" data-v="${esc(r.id)}">Open</button>`
    : r.status === "held" && r.run_date === LIVE.health.date && !LIVE.busy["ai:" + t] ? `<button type="button" class="link" data-act="ai-resume" data-v="${esc(r.model_key)}">Resume</button>`
    : r.status === "failed" && r.saved_answers && r.run_date === LIVE.health.date && !LIVE.busy["ai:" + t] ? `<button type="button" class="link" data-act="ai-resume" data-v="${esc(r.model_key)}" title="${esc(r.error || "")}">Continue</button>`
    : r.status === "failed" ? `<span class="sub" title="${esc(r.error || "")}">see log</span>` : "";
  const body = !h ? `<p class="sub">Loading…</p>` : !rows.length ? `<p class="sub">No AI analysis has run on this server for ${esc(t)} yet.</p>` :
    `<div class="scroll"><table><thead><tr><th>Date</th><th>Model</th><th>Status</th><th>Rating</th><th>Took</th><th></th></tr></thead><tbody>${rows.map(r => `<tr><td class="t">${esc(r.run_date)}</td><td class="t">${esc(r.model_key)}</td><td><span class="badge ${r.status === "done" ? "buy" : r.status === "failed" ? "sell" : "hold"}" title="${esc(r.status === "held" ? r.step || "" : "")}">${esc(r.status)}</span></td><td>${r.summary && r.summary.rating ? ratingBadge(r.summary.rating) : "–"}</td><td>${r.summary && r.summary.seconds ? Math.max(1, Math.round(r.summary.seconds / 60)) + " min" : "–"}</td><td>${cell(r)}</td></tr>`).join("")}</tbody></table></div>`;
  return card("Stored runs on this server", "Every AI analysis run for this company, newest first. A held run resumes where it stopped.", "", body);
}

/* ----- add any company: the search box finds it, the server downloads its prices, and forecasts it ----- */
X.add = { q: "", results: null, market: "auto", msg: "" };
const ADD_MARKETS = [["auto", "Find it automatically"], ["international", "Yahoo Finance (US, Hong Kong, Saudi, ...)"], ["dse", "Dhaka Stock Exchange"]];
function hideAddBox() { const b = $("#addbox"); if (b) b.hidden = true; }
async function addPrompt(q) {
  X.add.q = q; X.add.results = null; X.add.msg = "";
  renderAddBox();
  try { X.add.results = await api("search?q=" + encodeURIComponent(q)); } catch (e) { X.add.results = []; }
  if (X.add.q === q) renderAddBox();
}
function renderAddBox() {
  const b = $("#addbox"); if (!b) return;
  const a = X.add, q = a.q;
  const local = ORDER.filter(t => t.startsWith(q) || DATA[t].meta.name.toUpperCase().includes(q));
  const web = (a.results || []).filter(r => !r.on_site && !DATA[r.symbol]);
  const li = (sym, name, btn) => `<li><div><b class="mono">${esc(sym)}</b><span>${esc(name)}</span></div>${btn}</li>`;
  const open = local.map(t => li(t, DATA[t].meta.name, `<button type="button" class="btn secondary sm" data-open="${esc(t)}">Open</button>`)).join("");
  const add = web.map(r => li(r.symbol, r.name + (r.exchange ? " · " + r.exchange : ""), `<button type="button" class="btn sm" data-add="${esc(r.symbol)}" data-market="international" data-name="${esc(r.name)}">Add</button>`)).join("");
  b.innerHTML = `<p class="addh">${local.length ? "On this site" : `No <b>${esc(q)}</b> on this site yet`}</p>
    ${open ? `<ul class="addlist">${open}</ul>` : ""}
    <p class="addh">Add a company from the web</p>
    ${a.results === null ? `<p class="sub">Searching…</p>` : add ? `<ul class="addlist">${add}</ul>` : `<p class="sub">No web suggestions. You can still add the text as a ticker below.</p>`}
    <div class="addrow"><select id="add-market" aria-label="Market">${ADD_MARKETS.map(([v, l]) => `<option value="${v}"${a.market === v ? " selected" : ""}>${esc(l)}</option>`).join("")}</select>
      <button type="button" class="btn sm" data-add="${esc(q)}" data-name="">Add ${esc(q)}</button></div>
    ${a.msg ? `<p class="sub" style="color:var(--sell)" role="alert">${esc(a.msg)}</p>` : ""}
    <p class="sub">A new company takes several minutes: its prices and 7-day forecast are made on the server.</p>`;
  b.hidden = false;
}
async function loadCompany(t) {  // the saved forecast and extra data of a company, into the page
  const b = await api("companies/" + encodeURIComponent(t));
  DATA[t] = b.data;
  EXTRA.dataset[t] = b.dataset || [];
  EXTRA.backtest[t] = b.backtest || {};
  if (b.ai) { EXTRA.ai[t] = b.ai; if (!AI_T.includes(t)) AI_T.push(t); }
  Object.entries(b.fx || {}).forEach(([c, v]) => { if (v) EXTRA.fx[c] = v; });
  if (!ORDER.includes(t)) ORDER.push(t);
  if (!X.wl.includes(t)) { X.wl.push(t); if (lsGet("raem:wl", null)) lsSet("raem:wl", X.wl); }
  try { if (LIVE.ok) LIVE.models = await api("models"); } catch (e) { /* the AI Analysis page then lists the new company after a reload */ }
  render();
}
async function addCompany(t, market, name) {
  const key = "co:" + t;
  if (LIVE.busy[key]) return;
  LIVE.err[key] = "";
  try {
    const res = await api("companies", { method: "POST", body: JSON.stringify({ ticker: t, market: market || "auto", name: name || null }) });
    if (res.reused === "exists") {
      await loadCompany(res.ticker || t); hideAddBox(); $("#q").value = "";
      openTicker(res.ticker || t);
      return;
    }
    hideAddBox();
    $("#q").value = "";
    watchCompany(t, res.run.id);
  } catch (e) {
    X.add.msg = e.message;
    renderAddBox();
  }
}
function watchCompany(t, id) {
  const key = "co:" + t;
  LIVE.err[key] = "";
  const p = pollRun(id, key, async () => {
    try { await loadCompany(t); openTicker(t); } catch (e) { LIVE.err[key] = "It was added, but the page could not load it: " + e.message; }
  });
  renderAddProg();
  p.then(renderAddProg);
}
function renderAddProg() {  // progress of companies being added, bottom right, so it stays while you browse
  let p = $("#addprog");
  const busy = Object.keys(LIVE.busy).filter(k => k.startsWith("co:")), errs = Object.keys(LIVE.err).filter(k => k.startsWith("co:") && LIVE.err[k]);
  if (!busy.length && !errs.length) { if (p) p.remove(); return; }
  if (!p) { p = document.createElement("div"); p.id = "addprog"; document.body.appendChild(p); }
  p.innerHTML = busy.map(k => `<div class="addprog-i"><p class="addprog-t">Adding <b class="mono">${esc(k.slice(3))}</b></p><div id="live-${liveId(k)}">${liveStatus(k)}</div></div>`).join("")
    + errs.map(k => `<div class="addprog-i"><p class="addprog-t"><b class="mono">${esc(k.slice(3))}</b> could not be added</p><p class="sub">${esc(LIVE.err[k])}</p>
      <button type="button" class="btn sm" data-add="${esc(k.slice(3))}" data-market="auto" data-name="">Try again</button> <button type="button" class="btn secondary sm" data-add-dismiss="${esc(k)}">Dismiss</button></div>`).join("");
}
document.addEventListener("click", e => {
  const add = e.target.closest("[data-add]"), open = e.target.closest("[data-open]"), dis = e.target.closest("[data-add-dismiss]");
  if (add) {
    const sel = $("#add-market");
    addCompany(add.dataset.add, add.dataset.market || (sel ? sel.value : "auto"), add.dataset.name || "");
    delete LIVE.err["co:" + add.dataset.add];
    return;
  }
  if (open) { hideAddBox(); $("#q").value = ""; openTicker(open.dataset.open); return; }
  if (dis) { delete LIVE.err[dis.dataset.addDismiss]; renderAddProg(); return; }
  if (!e.target.closest("#search")) hideAddBox();
});
document.addEventListener("change", e => {
  if (e.target.id === "add-market") X.add.market = e.target.value;
});
document.addEventListener("input", e => { if (e.target.id === "ai-any") X.ai.any = e.target.value; });
document.addEventListener("keydown", e => { if (e.key === "Enter" && e.target.id === "ai-any") { e.preventDefault(); aiAny(); } });
document.addEventListener("keydown", e => { if (e.key === "Escape") hideAddBox(); });
