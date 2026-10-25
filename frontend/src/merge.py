"""Merge extra.css / extra.js into template2.html -> template3.html, then point build.py at it."""
import os

HERE = os.path.dirname(os.path.abspath(__file__))
rd = lambda n: open(os.path.join(HERE, n), encoding="utf-8").read()
t, js, css = rd("template2.html"), rd("extra.js") + rd("live.js"), rd("extra.css")


def rep(a, b, cnt=1):
    global t
    assert t.count(a) == cnt, (a[:60], t.count(a))
    t = t.replace(a, b)


js = js.replace(", F = k => (tk => ((EXTRA.ai[tk] || {}).latest || { summary: {} }).summary.fundamentals_analyst || {})", "")
js = js.replace('wl: lsGet("raem:wl", null) || ORDER.slice(),', 'wl: (lsGet("raem:wl", null) || ORDER.slice()).filter(x => ORDER.includes(x)),')

rep('const ASOF = "2026-10-20";', 'const ASOF = "2026-10-20";\nconst EXTRA = /*__EXTRA__*/null;')
rep("@media (max-width: 767px) {", css + "\n@media (max-width: 767px) {")

rep("/* ---------- render & run ---------- */", js + "\n/* ---------- render & run ---------- */")
rep('$("#main").innerHTML = S.page === "dash" ? renderDash() : renderTicker();\n  if (S.page === "tk") drawChart($("#chart"));',
    '$("#main").innerHTML = S.page === "dash" ? renderDash() : S.page === "tk" ? renderTicker() : renderExtra();\n  if (S.page === "tk") drawChart($("#chart"));\n  afterRender();')
rep('if (b.dataset.page === "dash") { if (S.running) return; S.page = "dash"; $("#app").classList.remove("open"); render(false); $("#scroller").scrollTop = 0; return; }',
    'if (b.dataset.page) { goPage(b.dataset.page); return; }')
rep('r.dataset.theme = dark ? "light" : "dark"; render(); return; }',
    'r.dataset.theme = dark ? "light" : "dark"; settings.theme = r.dataset.theme; saveSettings(); render(); return; }')
rep('if (c && S.page === "tk") drawChart(c); }, 120); });', 'if (c && S.page === "tk") drawChart(c); if (S.page === "bt") drawBT(); if (S.page === "paper") drawPT(); if (S.page === "inv") drawInv(); if (S.page === "dash") drawHome(); }, 120); });')
rep("let rz; addEventListener", rd("handlers.js") + "let rz; addEventListener")
rep('$("#menu").innerHTML = ic("menu");', '$("#menu").innerHTML = ic("menu");\napplySettings(true);')
open(os.path.join(HERE, "template3.html"), "w", encoding="utf-8", newline="\n").write(t)

print("merged", len(t))
