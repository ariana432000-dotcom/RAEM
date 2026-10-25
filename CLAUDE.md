# CLAUDE.md (web/)

Rules for turning Figma designs into code, and code into Figma, for the **RAEM Forecast Console** in this folder. This folder is self-contained: `backend/engine/` holds a copy of the forecasting code and its data (refresh it with `backend/tools/sync_engine.py` from the research project, whose own rules are in that project's CLAUDE.md). This file covers the UI and the web app.

## What this UI is

A single self-contained HTML page (published as a claude.ai Artifact) that replays results exported from the project's Python code: daily BUY/HOLD/SELL signals, 7-day forecasts from 14 models, backtest, memory, LLM analyst sessions and more. It has **no framework, no bundler and no backend**. Everything is one `<style>` block, plain JavaScript that builds HTML strings, and inline SVG. Its look is a soft-glass website: a sticky glass top nav, a hero on Home, and translucent cards over a pastel gradient field (light) or a deep navy one (dark). It began as a hand re-implementation of the AInvest terminal in the `any_with_dse-main` demo (Next.js + Tailwind) and was restyled on 2026-10-20 at the user's request; the Tailwind map in section 2 still describes that demo's vocabulary.

Because it is not a React/Tailwind project, **do not paste Figma MCP's React + Tailwind output into it**. Treat that output as a visual reference and rewrite it with the tokens and helpers below.

## 1. Design system structure

### Token definitions

All tokens are CSS custom properties at the top of `src/template2.html`, in one theme triplet (light-first):

```css
:root { --bg:#eef0fb; --panel:#ffffffb8; --text:#141a33; --accent:#4350f0; ... color-scheme: light; }
@media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) { ...dark values... } }
:root[data-theme="dark"] { ...dark values again, so the toggle wins both ways... }
```

Rules:
- Every token is first defined on bare `:root` (light). The media block and the `[data-theme="dark"]` block only **redefine** tokens, and they must stay identical to each other.
- `body` keeps `background: var(--bg)`. The gradient field is a fixed `.bgfx` layer built from `--g1` to `--g4` over `--bg`.
- Panels are translucent (`--panel` has alpha) so they pick up the field behind them. Anything that must stay readable over charts or content (tooltips, the "More" menu, the mobile sheet, chart dot outlines) uses the opaque `--solid`.
- No colour literals in components. The only literals allowed are inside the token blocks.
- Theme state: `data-theme` on `<html>` is `dark`, `light` or absent (system). `settings.theme` in `src/extra.js` keeps it in `localStorage` (`raem:set`).

| Token | Light | Dark | Use |
|---|---|---|---|
| `--bg` | `#eef0fb` | `#0a0e1f` | page background under the gradient field |
| `--g1` to `--g4` | `#c9d3ff #e5d3ff #c8f1e6 #ffe0cf` | `#1a1f55 #2a1a55 #0d2f3d #12245a` | the four soft glows of the field |
| `--panel` / `--panel-hover` | `#ffffffb8` / `#ffffffe6` | `#ffffff0f` / `#ffffff1c` | glass cards, nav, hover |
| `--bg-raised` | `#ffffffa6` | `#ffffff12` | inputs, pills, inset areas |
| `--solid` | `#ffffff` | `#171c33` | opaque surfaces (tooltip, menu, sheet) |
| `--border` / `--border-soft` | `#505aa029` / `#505aa01a` | `#ffffff22` / `#ffffff14` | card and divider lines |
| `--ctl` | `#7d88b0` | `#7480ad` | borders of inputs, selects, secondary buttons (3:1 needed) |
| `--text` / `--muted` / `--faint` | `#141a33` / `#4d5880` / `#5c6689` | `#eef0ff` / `#b0b8da` / `#a0a9cf` | text levels |
| `--accent`, `--accent2`, `--accent-strong`, `--accent-soft` | `#4350f0`, `#8a4ff0`, `#2f3ac4`, `#4350f01a` | `#8b97ff`, `#c08bff`, `#aab3ff`, `#8b97ff26` | actions (gradient from accent to accent2) and selected state; use `--accent-strong` for accent-coloured **text** |
| `--on-accent` | `#ffffff` | `#0a0e1f` | text on accent or signal fills |
| `--buy`, `--sell`, `--hold` (+ `-soft`) | `#0a6f43`, `#c4254f`, `#8a5304` | `#3ddc97`, `#ff8aa0`, `#fbbf24` | **signal colours only** (BUY, SELL, HOLD, up, down, status) |
| `--c1` to `--c8` | 8 chart hues | 8 lighter hues | model overlay lines, in selection order |
| `--base-line` | `#b2badb` | `#3b4468` | de-emphasised chart lines |
| `--shadow` | soft indigo | black | card elevation |
| `--font-sans`, `--font-mono` | Manrope, JetBrains Mono (Google Fonts) with system fallbacks | same | see typography |

Contrast was checked on 2026-10-20 for text, accent and signal colours against the translucent panel over the lightest and darkest parts of the field (4.5:1 for text, 3:1 for control borders). Re-check after changing any token, and add new colours to **all three blocks**.

Typography: Manrope (500 to 800) for text and JetBrains Mono for every figure, ticker and date (`font-variant-numeric: tabular-nums`), loaded from Google Fonts, the only allowed font host, each with a system fallback stack. Headings are weight 800 with tight tracking; the hero title is `clamp(36px, 5.2vw, 62px)` with a gradient on the key phrase. Sizes in use: 11, 12, 13, 14 (body), 16, 18, 24 (stat value), 28 to 40 (page title), up to 62 (hero). Uppercase labels use `letter-spacing: 0.06–0.1em`.

Spacing: 4/6/8/10/12/14/16/20/24px steps; card padding 24px; gaps 12px (stat row), 16 to 28px (stacks). Radius: 22px cards, 12px selects and inputs, 999px buttons, tabs, pills, badges and the search field. There is no token transformation step (no Style Dictionary, no Figma variable sync). Values are hand-copied.

### Component library

No component files. Components are **helper functions that return HTML strings**, in `src/template2.html` (shared) and `src/extra.js` (page-specific). Class names carry the styling.

| Component | Helper / class | Notes |
|---|---|---|
| Card | `card(title, sub, action, body, extra)` / `.card` (`.warn`, `.dashed`) | glass: translucent, blur, 22px radius, soft shadow; header is `.ch` |
| Hero, stock card, feature card | `.hero`, `.tcard`, `.feat` (built in `renderDash()`) | Home only; cards lift 3px on hover |
| Top nav | `renderNav()` / `.topnav`, `.nl`, `.pop` | main links, a "More" popover, search, theme toggle; below 860px a full-screen `.sheet` menu |
| Stat card | `statCard(label, value, hint, tone, icon)` / `.stat` | tones `buy sell hold accent`; value is mono |
| Badge | `.badge` (`buy sell hold accent`) | pill, 12px |
| Signal badge | `sigBadge(sig)` / `.badge.sig` | mono, uppercase, dot |
| Signal ring | `ring(sig, pct, label, size)` | the signature visual; SVG, 7px stroke |
| Button | `.btn` (`primary secondary sm`) | pill, 44px (34px for `sm`); primary has the accent gradient |
| Pill toggle | `.pillbtn[aria-pressed]` (`.mdl` for model chips) | filters and view switches |
| Segmented tabs | `.tabs` / `.tab[role=tab]` | glass pill group with a gradient selected tab, arrow-key support |
| Table | plain `<table>`; `tr.click` rows are focusable | numbers right-aligned and mono |
| Progress card | `progressCard()` / `.progress` | bar, current stage, timestamped events |
| Empty state | `emptyState(icon, title, desc, action)` / `.empty` | wrapped in a `.card.dashed` |
| Charts | `drawChart()` (price + forecast), `lineChart()` (equity), `spark()` | hand-written SVG, no chart library |
| Tooltip | `.tip` | pointer-driven, positioned by JS |

There is no Storybook. The live page is the only documentation. Code Connect does not apply: there is no component file to map a Figma component to. Map by **name** instead (see the Figma section).

### Frameworks and libraries

- UI: none. Vanilla JavaScript, template literals, event delegation on `document` (`src/handlers.js` and the first click handler in the template).
- Styling: hand-written CSS, one `<style>` block, custom properties.
- Build: `src/merge.py` joins `template2.html`, `extra.css`, `extra.js`, `live.js`, `handlers.js` into `template3.html`; `src/build.py` injects the data and writes `frontend/dist/index.html`. Plain string replacement, no bundler, no npm. The template is a page fragment (the artifact viewer adds the document skeleton); `build.py` prepends the skeleton (`<!doctype html>`, charset, viewport, an inline icon) for hosting, because without it a hosted page renders in quirks mode and phones show the desktop layout. Both scripts write LF line endings on every OS (`.gitattributes` keeps the repo LF too).
- Runtime limits come from the Artifact host: external scripts only from cdnjs/jsDelivr/unpkg with pinned versions, stylesheets only from Google Fonts, no `fetch` of other hosts, no downloads, no `alert/confirm`, 16 MB page limit (current build is about 0.8 MB). The page currently loads **no external resource at all**. Keep it that way unless a design truly needs a font.

### Asset management

- No image, video or font files. Everything is inline.
- Data is embedded as two JSON objects replacing `/*__DATA__*/null` and `/*__EXTRA__*/null` in the template. They come from `backend/data/tickers/*.json` (per ticker) and `backend/data/extra.json`.
- No CDN, no image optimisation. If a design needs a raster image, embed it as a `data:` URI and keep it small.

### Icon system

Icons are an inline-SVG path table, `ICONS` in `src/template2.html` (extended in `src/extra.js`), in the Lucide style: 24×24 viewBox, `stroke="currentColor"`, `stroke-width="2"`, round caps and joins. Use them through `ic("name")`, which returns the `<svg>`. Names in use: `dash line chart db clock brain pulse inbox alert moon sun menu star wallet news check file gear`.

To add an icon from Figma: export the SVG, keep only the inner paths, add them to `ICONS` under a short lowercase name, size it with CSS (`width/height: 16px` for nav and stat icons, 20px in the empty state). Do not add an icon font or per-icon files.

### Styling approach

- Plain CSS with class names, no methodology (no BEM, no CSS Modules). Components are single-purpose classes; modifiers are a second class (`.badge.buy`, `.btn.primary`).
- Layout uses flex and grid with `gap`, not per-element margins.
- Global styles: reset, `[hidden]{display:none!important}`, focus ring (`:focus-visible` 2px accent), reduced-motion override, selection colour.
- Responsive: one mobile breakpoint `@media (max-width: 767px)`: the sidebar becomes a slide-over drawer, stat grid goes to 2 columns, rows tighten. `@media (pointer: coarse)` raises touch targets to 44px. Wide tables and code scroll inside `.scroll` (`overflow-x: auto`), never the page. The page must not scroll horizontally at 390px.
- Safe-area insets: side gutters use `max(16px, env(safe-area-inset-left, 0px))`. The `<meta name="claude:viewport-fit-sides">` tag at the top of the template is only valid together with those paddings.

### Project structure

```
web/
├── README.md                 how to run and host
├── CLAUDE.md                 design and code rules for the UI and the web app
├── Dockerfile                container image (build from this folder)
├── docker-compose.yml        run the container with data kept on disk
├── .env.example              settings to copy into .env (run token, Ollama URL, default LLM, refresh token, CORS, port)
├── .github/workflows/tests.yml  API tests + page build on every push
├── .dockerignore  .gitignore  .gitattributes (LF everywhere)
├── backend/
│   ├── app/                  FastAPI: main.py (routes), jobs.py (run queue + runners), db.py (SQLite), config.py (env, model lists)
│   ├── refresh.py            one command: top up prices, re-run the forecasts, rebuild the page
│   ├── requirements.txt
│   ├── engine/               forecasting code, per-company notebooks, TradingAgents package and their data (copied from the research project)
│   │                         predictor_lib.py, forecast_lib.py, advanced_forecast.py, forecast_agent.py,
│   │                         data/, daily_data/, forecast_log/, cache/, chroma_memory/, notebooks/, paper/
│   ├── exporter/             export_ticker.py (models + --out), run_notebook.py (notebook Run All), sessions.py (trading logs -> JSON), export_extra.py, patch_daily.py
│   ├── storage/              raem.db + executed notebook copies (runtime, git-ignored)
│   ├── tests/                API tests with fake runners
│   ├── data/                 saved results the page is built from: tickers/*.json, extra.json
│   └── tools/sync_engine.py  re-copy engine/ from the research project
└── frontend/
    ├── src/                  template2.html, extra.css, extra.js, live.js (backend calls), handlers.js, merge.py, build.py
    └── dist/index.html       the built, self-contained page
```

Edit only `template2.html`, `extra.css`, `extra.js`, `handlers.js`. Never edit the generated files.

Portfolio notes (the page id stays `paper`; it was called Portfolio): orders carry a fill `date` (any date in the price data, in date order), fill at that day's close, and store `realized` P&L on sells. The portfolio curve is replayed from the order list (`ptCurve()`); the 7-day projection (`ptProject()`) adds each holding's Ensemble path and sums the agent 10–90% bands. State lives in `localStorage` key `raem:pt`. `lineChart()` accepts `null` gaps, `noline` series (tooltip only) and `opts.band`.

Return Calculator notes: `renderInv()` answers "if I put in N, what do I have in 7 days" per company. Day-7 value comes from the chosen forecast (`invFc()`: Ensemble, Fusion, Fusion Top 3, Reviewed agent, or the daily model's expected return), with the reviewer's 10–90% band as the range. The notebook record grades each LLM session (`sessOutcome()`): rating at the run date against the close 7 calendar days later; BUY goes long, SELL short, HOLD makes no trade. The "Past record" column comes from `EXTRA.backtest` (Ensemble day-7 calls followed vs buy and hold). Money is proportional, so the amount only scales results; the basis switch only changes the label (USD or the company's currency).

Layout: `.app` is a column of the sticky `.topnav` and the scrolling `#scroller`, which holds `<main class="content">` (max 1180px) and the footer. Home is `renderDash()` in `extra.js`: a hero with a featured-stock chart (`drawHome()`), a stock-card grid, a feature grid linking to the other pages, and the watchlist table. Ticker pages show a pill row to switch stock, and the options card collapses after a run.

Help for non-experts: `GLOSS` in `extra.js` holds every plain-language definition. `hint(GLOSS.key)` puts a focusable "?" with that text next to a heading, `plainSummary()` writes the "In plain words" box above a ticker's result tabs, and the Help page (`renderHelp()`) lists the same glossary. Add new jargon to `GLOSS` first, then reuse it; do not write a second explanation of the same term.

Model set: the site shows six models (N-BEATS, LASSO, Ridge, RandomForest, Theta, ARIMA), chosen on 2026-10-20 as the best average rank across the 8 tickers. `backend/exporter/export_ticker.py` fits only those (`KEEP_MODELS`) plus the blends (`BLENDS`), so Ensemble, Fusion, Fusion Top 3 and the reviewer are built from the six. `BASE` in `template2.html` and the `shown` set in `build.py` must match that list. The Python project itself still trains all 14; only the website export is narrowed. The 14-model export was backed up in the research project under `.scratch/backup_20261020_ui14models/`.

AI Analysis notes: served by the backend, the page shows `aiLiveCard()` (the connected LLMs only, "Run again", Run / Hold / Resume) and `aiHistoryCard()` (stored runs, Resume on today's held runs), plus `aiEffectCard()` (how the rating moves the forecast), and a run executes the company notebook on the server (see Live runs). As a static copy (artifact, static host) it shows the latest saved analysis at once (an info card, `aiEffectCard()` and the results); by the user's choice there is no replay button there. The card title says "Today's AI analysis" with the server date; the notebooks take `TRADE_DATE` from `datetime.now()`, so the server's time zone decides the day: the Dockerfile and compose set `TZ=Asia/Dhaka`, and `config.py` drops an IANA `TZ` on Windows (the CRT would read it as UTC) and calls `time.tzset()` elsewhere.

Page pattern: each page is `renderXxx()` returning an HTML string, registered in `renderExtra()` (or handled in `render()` for `dash` and `tk`). State lives in `S` (ticker pages) and `X` (extra pages). Clicks use `data-*` attributes (`data-t`, `data-act`, `data-ai`, ...) handled by delegation. A page that draws a chart gets a hook in `afterRender()`.

### Commands

Run from this folder (`web/`):

```bash
python backend/refresh.py                      # everything: price top-up, all tickers, extra pages, build
python backend/refresh.py AAPL IBM --no-top-up # some tickers only (about 1-2 min each with 6 models)
python backend/refresh.py --build-only         # rebuild the page from the data already exported
python -m uvicorn app.main:app --app-dir backend --port 8000   # serve the page and the API
python backend/tools/sync_engine.py /path/to/research/project   # bring engine/ up to date
```

The exports read the engine's state (`backend/engine/forecast_log/`, `backend/engine/chroma_memory/`, `backend/engine/notebooks/`, `backend/engine/data/`, `backend/engine/daily_data/`; override the folder with `RAEM_ENGINE`) and write their output only under `web/data/`. `backend/refresh.py` also runs `predictor_lib.top_up_data_csv()` first unless `--no-top-up`, which appends final yfinance bars to `backend/engine/data/*.csv` (DSE files are skipped), as `daily.py` does. Two side effects come from the project code they call, not from the scripts: `forecast_lib.load_combined_history()` copies new closes from `backend/engine/notebooks/trading_log_*.csv` into `backend/engine/daily_data/`, and `predictor_lib.run_pipeline()` may refresh `backend/engine/cache/`. Neither writes to `backend/engine/chroma_memory/` or `backend/engine/forecast_log/` (the scripts skip `remember_episode`). They need the packages in `backend/requirements.txt`. `export_extra.py` fetches FX rates from the network and stores them under `fx` (paper trading needs it).

The published page is **not** updated by building. After `build.py`, publish `frontend/dist/index.html` with the Artifact tool and the existing artifact URL so the link stays the same; leave out its first six lines (the skeleton `build.py` adds), because the viewer adds its own.

## Live runs (backend)

- Two kinds of user runs, both queued in `app/jobs.py` and stored in `app/db.py` (SQLite, table `runs`):
  - `forecast`: `exporter/export_ticker.py <T> --models a,b --out file`, on the engine's CSV price files. The key is the sorted model list. A run with exactly `config.DEFAULT_MODELS` also rewrites `backend/data/tickers/<T>.json` and rebuilds the page.
  - `analysis`: `exporter/run_notebook.py --ticker <T> --provider <p> --model <m> --out file`. It runs the company notebook (found by its `COMPANY = "..."` line) with nbclient, cell by cell, and stores the new trading-log session (`sessions.ai_entry`), which also goes into `extra.json`. The run key is the bare model for Ollama and `provider:model` for the cloud (`config.llm_key`).
- **LLM providers:** `ollama` plus `config.CLOUD_PROVIDERS` (openai, anthropic, google, groq, kimi; kimi is Moonshot's OpenAI-compatible API at `RAEM_KIMI_BASE_URL`, key `MOONSHOT_API_KEY` or `KIMI_API_KEY`). The page lists only connected ones (`ok` and with models). A cloud provider is usable when its API key variable is set; `RAEM_<KEY>_MODELS` replaces its model list; `RAEM_OPENAI_BASE_URL` points openai at any OpenAI-compatible server. `config.py` loads `web/.env` (python-dotenv, existing variables win). Keys reach the notebook kernel through the inherited environment and are never returned by the API. In cloud mode `run_notebook.rewrite()` turns every `ChatOllama(` constructor into `RAEMCloudChat(` (defined in `CONFIG_CELL`): a ChatOllama subclass that overrides `_generate` / `_stream` / `_agenerate` to call the cloud model, so the Fact Guard's patch on `ChatOllama.invoke`, the analyst cache key (`llm.model`, `temperature`, `num_predict`) and every cell work unchanged. `num_predict` becomes `max_tokens` (Gemini gets no cap, since its thinking counts against it); a "temperature" error retries once without a temperature. A test (`test_cloud_adapter_keeps_the_fact_guard`) checks the patch still applies.
- **Memory embeddings:** `run_notebook.embed_mode()` picks `ollama` when the Ollama server lists `nomic-embed-text`; else `local` when chromadb's all-MiniLM-L6-v2 is on disk or downloads within `RAEM_EMBED_DOWNLOAD_TIMEOUT` (900 s, in a child process: its S3 host gave 3 KB/s from Bangladesh and the first test run died in the News RAG upsert); else `hash` (`RAEMHashEmbedding` in `CONFIG_CELL`, signed word hashing, no model). `RAEM_EMBED` forces a mode. The rewrite sends `embedding_functions.OllamaEmbeddingFunction(` through `RAEM_embedding_function(` and `CHROMA_DB_PATH` through `RAEM_CHROMA_PATH`; `local` and `hash` each have their own store (`raem_chroma_db_local`, `raem_chroma_db_hash`), because 384-dimension vectors cannot join the 768-dimension nomic collections. The Dockerfile downloads the MiniLM model at build time. In `local` and `hash` mode `CONFIG_CELL` runs `_raem_copy_memory()` first: every collection of `./raem_chroma_db` (the notebooks' nomic store: `raem_episodic_memory`, `raem_news_corpus`) is copied into `RAEM_CHROMA_PATH` by id, documents re-embedded with the run's function, metadata kept, only missing ids; the source's contents are not changed (a test checks ids, text, metadata and vectors). The session records `llm_provider` and `memory_embeddings`.
- **Same-date reuse:** `jobs.submit()` returns a finished run with the same kind, company, `run_date` (server local date) and model key, unless `force`. An identical queued or running request is joined, not duplicated. A held run with that key goes back in the queue (`reused: "resumed"`, same id). Failed runs are never reused. One worker thread runs one job at a time.
- **Hold and resume:** `POST /api/runs/{id}/hold` (analysis only) marks a queued run `held`, or adds a running one to `jobs._held` and kills its process tree (`_kill_tree`, psutil, so the kernel's LLM request ends); `_execute` then stores `held`, not `failed`, keeping `step`. `db.init()` turns runs left `running` by a stopped server into `held`. Resuming works through the answer file `config.llm_cache_path(ticker, model_key)` (`storage/llm_cache/`), passed as `RAEM_LLM_CACHE`: `CONFIG_CELL` wraps `ChatOllama._generate` and `RAEMCloudChat._generate`, saves each answer under a hash of provider, model, temperature, num_predict, stop and the messages, and returns the saved answer for an identical prompt, so unchanged steps replay at once and steps whose inputs (prices, news) changed call the model. A failed run keeps the file too: `jobs.public()` adds `saved_answers` to held and failed analysis runs, and the page offers Continue for today's failed run when it has some. `_run_analysis` deletes the file after a finished run; `run_notebook` logs `Resuming: N saved ...` and `Reused N ..., made M new ...` and returns `reused_answers` / `new_llm_calls`.
- **Debates and decision:** `sessions.entry_from_session()` adds `latest.signal` (verifier `effective_signal`, else `summary_table.final_signal`, else the rating mapped: Overweight→BUY, Underweight→SELL), `latest.verifier` and `latest.debate` (`turns` parsed by `debate_turns()` from "Bull Analyst: … Bear Analyst: …", `risk_turns`, `facilitator`, `risk_facilitator`, `trader`, `complete`). The notebooks log only excerpts (1500 characters of the bull/bear history, the end of each risk case), so `run_notebook.py` appends `CAPTURE_CELL` (in-memory only, after the last cell) that dumps `final_debate_state`, `final_risk_debate_state`, the facilitator decisions and `trader_result` to `RAEM_FULL_OUT`; `full_debate()` turns that into `latest.debate` with `complete: true`. `GET /api/runs/{id}/live` returns the session written so far (`ai_entry(..., since=started_at)`) while a run goes; `pollRun` asks it every second poll and `agentBoard()` draws the agents (done / running / waiting), the latest output and the debate so far. The AI page order: live card, `aiDecisionCard` (large BUY/HOLD/SELL like the any_with_dse RecommendationPanel), `aiEffectCard`, stats, `aiDebateCard`, `aiRiskCard`, final decision text, analyst pipeline, history, stored runs.
- **Forecast page runs on the server:** when `LIVE.ok`, `run()` in `template2.html` calls `liveForecast()`; the options card gets "All 14 (as in the forecast notebook)" / "Best 6" (`X.lfc.models`, all by default) and a live progress box; `showForecast()` opens the All models tab, where ★ marks `forecast.ensemble_members`. The static copy keeps the replay.
- **Hosting state:** `backend/tools/persist.py` (Railway, `RAEM_DATA_DIR`: folders linked onto one volume) and `backend/tools/hf_sync.py` (Hugging Face Space, `RAEM_HF_DATASET` + `HF_TOKEN`: `restore` at container start, `save_in_background()` from `jobs._execute` after every run, to a private dataset) share `persist.FOLDERS` and one rule: a file the image changed since the last save keeps the image's version, others keep the saved copy; `.ipynb`/`.py` and `storage/notebook_runs` are never synced; SQLite files are copied with the backup API. `backend/tools/deploy_hf.py <user>/<space>` creates the Docker Space and the `<space>-data` dataset, sets `PORT`, `TZ`, `RAEM_HF_DATASET`, and uploads what git would push with a Space README header. The Dockerfile sets `HOME=/home/user` and makes `/app` writable because Spaces run as user 1000.
- **Portfolio indicators:** `techIndicators(t)` computes RSI 14 (Wilder), MACD 12/26/9, 20/50-day averages, Bollinger %B, 30-day volatility and the 52-week range from `DATA[t].history`; `ptMetrics()` gives return, max drawdown (from `ptCurve()`), win rate (sells with `rl > 0`) and exposure.
- **AI influence on the forecast:** frontend only, `aiBlend(t)` in `extra.js` (see `GLOSS.aiadj`): adjusted 7-day move = (1 − w)·Ensemble + w·rating×`typicalMove(t)`, w 0.2, or 0.1–0.35 from `invTrack()` once 5 calls are graded, none when the session is over 10 days older than the prices. Shown on Home, the Forecast summary, Portfolio (`ptModelOutputs`, chart line), the Calculator (`INV_SRC` "AI") and the AI page (`aiEffectCard`).
- **Never edit the notebooks for the website.** `run_notebook.py` adapts an in-memory copy only: it inserts a first cell reading `RAEM_LLM_MODEL` / `OLLAMA_BASE_URL`, replaces `model="qwen2.5:7b"` and `"http://localhost:11434` with those, and turns `%pip` lines into `pass`. Every other cell runs as written, so the notebook stays the source of truth. Keep `repeat_penalty=1.15` and `num_predict=2048` in the notebooks (see the research project's CLAUDE.md).
- **Session archive:** the notebooks prune `trading_log_*.json` to `MAX_DAYS`=10 days on every run. `sessions.archive_logs()` copies every logged session into the `sessions` table of the run database (and rewrites `backend/data/session_archive.json.gz`, which is committed). It runs at server start (after `import_snapshot()` seeds a new database), before and after each notebook run, and in `tools/sync_engine.py` before logs are overwritten. `sessions_for()` merges logs and archive, so history never shrinks. Tests point `RAEM_DB` and `RAEM_SNAPSHOT` at temp files.
- Progress labels for notebook cells come from the cell's `log_step("...")` name, else the markdown heading above it, else `def create_...` names, else its first comment.
- Packages that notebooks `%pip install` must be in `backend/requirements.txt`, because the runner skips those lines (e.g. `ddgs` for the DSE news search).
- New notebook steps reach the site automatically: `sessions.STEP_LABELS` only renames known `log_step()` names; unknown steps keep their own name. Only "LLM Setup" is dropped.
- `config.FORECAST_MODELS` lists the 14 forecast model keys (best first); `config.DEFAULT_MODELS` must equal `KEEP_MODELS` in `export_ticker.py` (a test checks it). `BASE` in `template2.html` lists all 14 labels and the UI shows only the ones present in a run.
- Frontend: `live.js` calls the API only when the page is served by the backend (`liveInit()` asks `api/health`; a static copy or the artifact stays in replay mode; by the user's choice there is no "connect to a server" option). The token input saves on `input` and never re-renders on `change`, because that re-render would swallow the click that caused the blur. `applyForecastRun()` / `applyAnalysisRun()` swap `DATA[t]` / `EXTRA.ai[t]` for a run's result. On page load the newest finished run of each company on any date is applied (`GET /api/runs/latest?date=all`) unless the saved page already holds newer prices (forecast) or a newer session (analysis), and runs still queued or running (`GET /api/runs?status=queued,running`) are watched by `watchForecast()` / `watchAnalysis()`, so a reload or a second browser shows their progress. A running run's `elapsed` (seconds, server clock) drives the timer.
- Long runs: one 7B notebook run takes 2 to 3 hours on a CPU. The worker calls `SetThreadExecutionState` (system and display required) on Windows while a job runs so the PC does not idle-sleep or drop into Modern Standby when the display times out (a closed laptop lid still sleeps; on wake nbclient's cell time limit fires at once). Limits: `RAEM_CELL_TIMEOUT` (7200 s per cell), `RAEM_NOTEBOOK_TIMEOUT` (6 h per run).
- Tests: `python -m pytest -q backend/tests` (fake runners, offline). Add a test for every new endpoint or reuse rule.

## 2. Figma integration rules

1. **Start from tokens.** Read the Figma file's variables and map them to the table above. Figma variable names should mirror token names: `color/bg`, `color/panel`, `color/border`, `color/text`, `color/muted`, `color/faint`, `color/accent`, `color/buy`, `color/sell`, `color/hold`, with `Dark` and `Light` modes. If a variable has no token, add one in all three blocks, do not inline the hex.
2. **Translate, do not copy.** `get_design_context` returns React + Tailwind. Rebuild the result with `card()`, `statCard()`, `.pillbtn`, `.badge`, `.tab` and the other helpers. If a design needs a new pattern, add one helper and one class, then reuse it.
3. **Tailwind to token map** for reading Figma output: `bg-bg`→`--bg`, `bg-panel`→`--panel`, `bg-bg-raised`→`--bg-raised`, `border-border`→`--border`, `border-border-soft`→`--border-soft`, `text-text`→`--text`, `text-text-muted`→`--muted`, `text-text-faint`→`--faint`, `text-accent`/`bg-accent`→`--accent`, `text-buy/sell/hold`→`--buy/--sell/--hold`, `font-mono`→`--font-mono`. This is the same vocabulary the AInvest demo uses.
4. **Numbers are mono.** Prices, percentages, dates and tickers use `--font-mono` with tabular numbers. Signal words (BUY, HOLD, SELL) are mono uppercase.
5. **Signal colours mean something.** Green, red and amber are only for BUY, SELL, HOLD, positive, negative and warning. Never use them as decoration. Direction is also shown by a sign (`+`/`−`) and text, never by colour alone.
6. **Both themes.** A design is not done until it exists in dark and light. Check both after building (set `data-theme` on `<html>`).
7. **Accessibility is part of the spec.** Text 4.5:1, control borders and focus ring 3:1, touch targets 44px on coarse pointers, visible `:focus-visible`, every control with a label or `aria-label`, buttons that toggle use `aria-pressed`, tabs use `role="tab"` with arrow keys, status messages use `role="status"`/`"alert"`. New clickable table rows need `tr.click` (it gets `tabindex` and Enter/Space handling from `markRows()`).
8. **Charts are SVG.** Do not look for a chart component to map. Match the Figma chart's colours, axes and tooltip by editing `drawChart()` / `lineChart()`. The current style: area gradient under the close line, forecast zone shaded with `--accent-soft`, main forecast in `--text`, overlays in `--c1..--c8`, grey base models, tooltip card with `--panel` and `--border`.
9. **Code to Figma.** To push the live page into Figma, capture it from the published artifact (desktop 1280, phone 390, dark and light) rather than from the template, because the DOM is generated at runtime. Name frames after pages: `Dashboard`, `Ticker`, `AI Analysis`, `Watchlist`, `Portfolio`, `Backtest`, `Dataset Explorer`, `News`, `Fundamentals`, `Papers`, `Memory`, `Settings`.
10. **Copy and units.** Plain, specific English; sentence case; no emoji. Prices keep each ticker's own currency (USD, SAR, HKD, BDT). Only paper-trading equity converts to USD.

## 3. Content and honesty rules

- The page replays saved results. It never trains models. Keep the "Replays results exported from the project's Python code" note and the source line under each panel (`Source: forecast_lib...`).
- It is research output, not investment advice. Keep the disclaimer in the sidebar footer and the AI Analysis intro.
- Daily signals for the DSE tickers (ABBANK, BATBC, BPML, WALTONHIL) are computed on the data snapshot only (to 2026-09-17) while forecasts also use the notebook logs (to 2026-10-04). The page shows a warning about this. Keep it until `predictor_lib` merges the daily logs.
- 0300.HK has no LLM analyst session. Its Midea log records `000333.SZ`, a different listing. Do not merge them.

## 4. Do not

- Do not add npm, React, Tailwind, a chart library or web fonts without asking: they change how the page is hosted and its size.
- Do not hard-code machine paths. The scripts find `backend/engine/` from `__file__` (or `RAEM_ENGINE`).
- Do not edit `template3.html` or `raem-forecast-console.html` by hand.
- Do not rewrite the files under `backend/engine/data`, `backend/engine/cache`, `backend/engine/forecast_log` or `backend/engine/chroma_memory` from UI work. There is no git history: back up first before any destructive edit.
