document.addEventListener("click", e => {
  const el = e.target.closest("[data-act],[data-ai],[data-stage],[data-bt],[data-ds],[data-news],[data-mem],[data-set-theme],[data-set-view],[data-inv-t],[data-inv-src],[data-home]");
  if (!el || el.disabled) return;
  const d = el.dataset, a = d.act, v = d.v;
  if (d.ai) { if (X.ai.running) return; X.ai.t = d.ai; }
  else if (a === "ai-run") { aiRun([X.ai.t]); return; }
  else if (a === "ai-live") { liveAnalysis(X.ai.t); return; }
  else if (a === "ai-hold") { liveHold(X.ai.t); return; }
  else if (a === "ai-resume") { X.ai.model = selFromKey(v); liveAnalysis(X.ai.t); return; }
  else if (a === "ai-load") { loadAiRun(v); return; }
  else if (a === "lfc-run") { liveForecast(X.ptForm.t); return; }
  else if (a === "lfc-m") { const m = lfcModels(); m.has(v) ? m.delete(v) : m.add(v); }
  else if (a === "lfc-best") { X.lfc.models = new Set(LIVE.models.forecast.filter(x => x.default).map(x => x.key)); }
  else if (a === "lfc-all") { X.lfc.models = new Set(LIVE.models.forecast.map(x => x.key)); }
  else if (a === "ai-runall") { aiRun(AI_T.slice()); return; }
  else if (d.stage) X.ai.stage = d.stage;
  else if (d.bt) { X.bt.t = d.bt; X.bt.sel = new Set(["Ensemble"]); }
  else if (d.ds) { X.ds.t = d.ds; X.ds.page = 0; }
  else if (d.news) X.news.t = d.news;
  else if (d.home) X.home.t = d.home;
  else if (d.invT) X.inv.t = d.invT;
  else if (d.invSrc) X.inv.src = d.invSrc;
  else if (d.mem) X.mem.tab = d.mem;
  else if (d.setTheme) { settings.theme = d.setTheme; saveSettings(); applySettings(false); }
  else if (d.setView) { settings.view = d.setView; S.view = d.setView; saveSettings(); }
  else if (a === "wl-del") { X.wl = X.wl.filter(t => t !== v); lsSet("raem:wl", X.wl); X.wlMsg = ""; }
  else if (a === "wl-add") { if (!X.wl.includes(v)) X.wl.push(v); lsSet("raem:wl", X.wl); X.wlMsg = ""; }
  else if (a === "wl-reset") { X.wl = ORDER.slice(); lsDel("raem:wl"); X.wlMsg = ""; }
  else if (a === "pt-side") { X.ptForm.side = v; X.ptForm.msg = ""; }
  else if (a === "pt-go") placeOrder();
  else if (a === "pt-view") X.ptView = v;
  else if (a === "pt-reset") { X.pt = { cash: 100000, pos: {}, orders: [], realized: 0 }; lsDel("raem:pt"); X.ptForm.msg = "Account reset."; X.ptForm.ok = true; }
  else if (a === "bt-m") { X.bt.sel.has(v) ? X.bt.sel.delete(v) : X.bt.sel.add(v); }
  else if (a === "bt-t") { X.bt.t = v; X.bt.sel = new Set(["Ensemble"]); }
  else if (a === "fund-t") X.fund.t = v;
  else if (a === "more") S.more = !S.more;
  else if (a === "opt-toggle") S.optOpen = !S.optOpen;
  else if (a === "ds-clear") { X.ds.from = ""; X.ds.to = ""; X.ds.page = 0; }
  else if (a === "ds-prev") X.ds.page = Math.max(0, X.ds.page - 1);
  else if (a === "ds-next") X.ds.page++;
  else if (a === "set-reset") { Object.assign(settings, { theme: "system", win: 120, unit: "price", view: "Ensemble" }); lsDel("raem:set"); applySettings(true); }
  else return;
  render();
});
document.addEventListener("change", e => {
  const b = e.target.dataset.bind; if (!b) return;
  const v = e.target.value;
  if (b === "pt-t") { X.ptForm.t = v; X.ptForm.date = ""; X.ptForm.msg = ""; } else if (b === "pt-d") { X.ptForm.date = v; X.ptForm.msg = ""; } else if (b === "pt-q") { X.ptForm.qty = v; X.ptForm.msg = ""; }
  else if (b === "ds-from") { X.ds.from = v; X.ds.page = 0; } else if (b === "ds-to") { X.ds.to = v; X.ds.page = 0; }
  else if (b === "mem-t") X.mem.t = v;
  else if (b === "lfc-force") X.lfc.force = e.target.checked;
  else if (b === "ai-model") X.ai.model = v;
  else if (b === "ai-force") X.ai.force = e.target.checked;
  else if (b === "set-token") return;  // saved on input; a re-render here would swallow the click that caused the blur
  else if (b === "inv-basis") X.inv.basis = v;
  else if (b === "inv-amt") X.inv.amt = v;
  else if (b === "set-win") { settings.win = +v; S.win = +v; saveSettings(); }
  else if (b === "set-unit") { settings.unit = v; S.unit = v; saveSettings(); }
  else return;
  render();
});
document.addEventListener("input", e => {
  const b = e.target.dataset.bind;
  if (b === "pt-q") X.ptForm.qty = e.target.value;
  else if (b === "set-token") { LIVE.token = e.target.value.trim(); lsSet("raem:token", LIVE.token); }
  else if (b === "inv-amt") { X.inv.amt = e.target.value; render(); const i = $("#inv-a"); if (i) { i.focus(); try { i.setSelectionRange(i.value.length, i.value.length); } catch (err) { /* number inputs have no caret */ } } }
  else if (b === "mem-q") { X.mem.q = e.target.value; render(); const i = $("#mem-q"); if (i) { i.focus(); i.setSelectionRange(i.value.length, i.value.length); } }
});
document.addEventListener("submit", e => {
  if (e.target.id !== "wlform") return;
  e.preventDefault();
  const q = $("#wlq").value.trim().toUpperCase(); if (!q) return;
  const hit = ORDER.find(x => x === q);
  if (!hit) X.wlMsg = `${q} is not in this export. Available: ${ORDER.join(", ")}.`;
  else if (X.wl.includes(hit)) X.wlMsg = `${hit} is already on your watchlist.`;
  else { X.wl.push(hit); lsSet("raem:wl", X.wl); X.wlMsg = ""; }
  render();
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
