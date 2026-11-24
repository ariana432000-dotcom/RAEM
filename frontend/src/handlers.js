document.addEventListener("click", e => {
  const el = e.target.closest("[data-act],[data-ai],[data-stage],[data-bt],[data-ds],[data-news],[data-mem],[data-set-theme],[data-set-view],[data-mkt],[data-stk]");
  if (!el || el.disabled) return;
  const d = el.dataset, a = d.act, v = d.v;
  if (d.ai) { if (X.ai.running) return; X.ai.t = d.ai; }
  else if (a === "ai-run") { aiRun([X.ai.t]); return; }
  else if (a === "ai-live") { liveAnalysis(X.ai.t); return; }
  else if (a === "ai-hold") { liveHold(X.ai.t); return; }
  else if (a === "ai-resume") { X.ai.model = selFromKey(v); liveAnalysis(X.ai.t); return; }
  else if (a === "ai-load") { loadAiRun(v); return; }
  else if (a === "lfc-m") { const m = lfcModels(); m.has(v) ? m.delete(v) : m.add(v); }
  else if (a === "ai-runall") { aiRun(AI_T.slice()); return; }
  else if (d.stage) X.ai.stage = d.stage;
  else if (d.bt) { X.bt.t = d.bt; X.bt.sel = new Set(["Ensemble"]); X.bt.win = null; }
  else if (d.mkt) X.mkt.sort = d.mkt;
  else if (d.stk) { openStock(d.stk); return; }
  else if (a === "stk-sector") { X.stk.sector = X.stk.sector === v ? "" : v; X.stk.n = 100; }
  else if (a === "stk-more") X.stk.n += 100;
  else if (a === "stk-back") { goPage(X.stk.from && X.stk.from !== "stock" ? X.stk.from : "stocks"); return; }
  else if (a === "stk-range") { X.stk.range = +v; saveStk(); }
  else if (a === "stk-frange") { X.stk.frange = +v; saveStk(); }
  else if (a === "stk-panel") { const c = X.stk.code; X.stk.panel[c] = X.stk.panel[c] === v ? "" : v; saveStk(); }
  else if (a === "stk-fc-reload") { delete X.stk.fcstate[v]; delete X.stk.fcr[v]; }
  else if (a === "stk-reload") { if (v) { delete X.stk.dstate[v]; delete X.stk.det[v]; } else { X.stk.lstate = "idle"; } }
  else if (a === "stk-ai") { X.ai.extra = X.ai.extra || []; if (!DATA[v] && !X.ai.extra.includes(v)) X.ai.extra.push(v); X.ai.t = v; goPage("ai"); return; }
  else if (a === "mkt-refresh") { loadMarket(true); return; }
  else if (a === "mkt-news-more") X.mkt.newsN += 20;
  else if (a === "ai-hist-all") X.ai.histAll = !X.ai.histAll;
  else if (a === "ai-hist-int") X.ai.histInt = !X.ai.histInt;
  else if (d.mem) X.mem.tab = d.mem;
  else if (d.setTheme) { settings.theme = d.setTheme; saveSettings(); applySettings(false); }
  else if (d.setView) { settings.view = d.setView; S.view = d.setView; saveSettings(); }
  else if (a === "wl-del") { X.wl = X.wl.filter(t => t !== v); lsSet("raem:wl", X.wl); X.wlMsg = ""; }
  else if (a === "wl-add") { if (!X.wl.includes(v)) X.wl.push(v); lsSet("raem:wl", X.wl); X.wlMsg = ""; }
  else if (a === "wl-reset") { X.wl = ORDER.slice(); lsDel("raem:wl"); X.wlMsg = ""; }
  else if (a === "bt-m") { X.bt.sel.has(v) ? X.bt.sel.delete(v) : X.bt.sel.add(v); }
  else if (a === "bt-t") { X.bt.t = v; X.bt.sel = new Set(["Ensemble"]); X.bt.win = null; }
  else if (a === "btp-zin") { btpZoom(0.6); return; }
  else if (a === "btp-zout") { btpZoom(1 / 0.6); return; }
  else if (a === "btp-reset") { btpReset(); return; }
  else if (a === "ev-w") X.ev.w = +v;
  else if (a === "ev-t") { X.ev.t = v; X.ev.n = 25; }
  else if (a === "ev-more") X.ev.n += 25;
  else if (a === "more") S.more = !S.more;
  else if (a === "opt-toggle") S.optOpen = !S.optOpen;
  else if (a === "win") S.win = +v;
  else if (a === "set-reset") { Object.assign(settings, { theme: "dark", win: 126, unit: "price", view: "Agent" }); lsDel("raem:set"); applySettings(true); }
  else return;
  render();
});
document.addEventListener("change", e => {
  const b = e.target.dataset.bind; if (!b) return;
  const v = e.target.value;
  if (b === "mem-t") X.mem.t = v;
  else if (b === "ai-model") X.ai.model = v;
  else if (b === "ai-force") X.ai.force = e.target.checked;
  else if (b === "set-token") return;  // saved on input; a re-render here would swallow the click that caused the blur
  else if (b === "set-win") { settings.win = +v; S.win = +v; saveSettings(); }
  else if (b === "set-unit") { settings.unit = v; S.unit = v; saveSettings(); }
  else return;
  render();
});
document.addEventListener("input", e => {
  const b = e.target.dataset.bind;
  if (b === "set-token") { LIVE.token = e.target.value.trim(); lsSet("raem:token", LIVE.token); }
  else if (b === "stk-q") { X.stk.q = e.target.value; X.stk.n = 100; render(); const i = $("#stk-q"); if (i) { i.focus(); i.setSelectionRange(i.value.length, i.value.length); } }
  else if (b === "mem-q") { X.mem.q = e.target.value; render(); const i = $("#mem-q"); if (i) { i.focus(); i.setSelectionRange(i.value.length, i.value.length); } }
});

document.addEventListener("keydown", e => {
  if (e.key === "Escape" && S.more) { S.more = false; render(); return; }
  if (e.key === "Escape" && $("#app").classList.contains("open")) { $("#app").classList.remove("open"); $("#menu").setAttribute("aria-expanded", "false"); $("#menu").focus(); return; }
  const row =e.target.closest && e.target.closest("tr.click");
  if (row && e.target === row && (e.key === "Enter" || e.key === " ")) { e.preventDefault(); row.click(); return; }
  const tab = e.target.closest && e.target.closest(".tab[role='tab']");
  if (tab && ["ArrowLeft", "ArrowRight", "Home", "End"].includes(e.key)) {
    e.preventDefault();
    const ids = TABS.map(t => t[0]), i = ids.indexOf(S.tab);
    S.tab = ids[e.key === "Home" ? 0 : e.key === "End" ? ids.length - 1 : (i + (e.key === "ArrowRight" ? 1 : -1) + ids.length) % ids.length];
    render(); const nt = $("#tab-" + S.tab); if (nt) nt.focus();
  }
});
$("#skip").addEventListener("click", e => { e.preventDefault(); $("#main").focus(); });
function markRows() { document.querySelectorAll("tr.click").forEach(r => { r.tabIndex = 0; }); }
document.addEventListener("click", e => { if (S.more && !e.target.closest(".morewrap")) { S.more = false; renderNav(); } });
