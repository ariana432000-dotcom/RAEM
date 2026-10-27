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
- Runtime limits come from the Artifact host: external scripts only from cdnjs/jsDelivr/unpkg with pinned versions, stylesheets only from Google Fonts, no `fetch` of other hosts, no downloads, no `alert/confirm`, 16 MB page limit (current build is about 0.8 MB). The page currently loads **one external resource: the Google Fonts stylesheet** (Manrope, JetBrains Mono; see the `<link>` tags at the top of `template2.html`), with system-font fallbacks if it is blocked. Add nothing else.

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
│   ├── app/                  FastAPI, laid out like the DSE app: main.py (creates the app), core/config.py (env, model lists, paths),
│   │                         db/runs.py (SQLite), models/schemas.py, api/deps.py + api/routes/ (health, stocks, forecast, analysis, refresh, runs),
│   │                         services/ (tickers, live), workers/jobs.py (run queue + runners),
│   │                         pipeline/ (export_ticker.py, add_company.py, export_extra.py, patch_daily.py, sessions.py), ai_engine/run_pipeline.py (CLI runner),
│   │                         ai_pipeline/ (the AI analysis: runner, agents, llm, memory, embeddings, data_providers, logger)
│   ├── refresh.py            one command: top up prices, re-run the forecasts, rebuild the page
│   ├── requirements.txt
│   ├── engine/               forecasting code and its data (copied from the research project)
│   │                         predictor_lib.py, forecast_lib.py, advanced_forecast.py, forecast_agent.py,
│   │                         data/, daily_data/, forecast_log/, cache/, chroma_memory/, notebooks/ (only trading_log_*.json/csv and raem_chroma_db*)
│   ├── tradingagents/        the TradingAgents package used by the AI analysis (from the any_with_dse app; DSE vendor in dataflows/dse_*.py)
│   ├── storage/              raem.db, llm_cache/ (runtime, git-ignored)
│   ├── tests/                API tests with fake runners
│   ├── data/                 saved results the page is built from: tickers/*.json, extra.json
│   └── tools/sync_engine.py  re-copy engine/ from the research project
└── frontend/
    ├── src/                  template2.html, extra.css, extra.js, live.js (backend calls), handlers.js, merge.py, build.py
    └── dist/index.html       the built, self-contained page
```

Edit only `template2.html`, `extra.css`, `extra.js`, `handlers.js`. Never edit the generated files.

`lineChart()` accepts `null` gaps, `noline` series (tooltip only) and `opts.band`. The virtual Portfolio page (id `paper`, `localStorage` key `raem:pt`) was removed on request; do not re-add it.

Return Calculator notes: `renderInv()` answers "if I put in N, what do I have in 7 days" per company. Day-7 value comes from the chosen forecast (`invFc()`: Ensemble, Fusion, Fusion Top 3, Reviewed agent, or the daily model's expected return), with the reviewer's 10–90% band as the range. The saved record grades each LLM session (`sessOutcome()`): rating at the run date against the close 7 calendar days later; BUY goes long, SELL short, HOLD makes no trade. The "Past record" column comes from `EXTRA.backtest` (Ensemble day-7 calls followed vs buy and hold). Money is proportional, so the amount only scales results; the basis switch only changes the label (USD or the company's currency).

Layout: `.app` is a column of the sticky `.topnav` and the scrolling `#scroller`, which holds `<main class="content">` (max 1180px) and the footer. Home is `renderDash()` in `extra.js`: a hero with a featured-stock chart (`drawHome()`), a stock-card grid, a feature grid linking to the other pages, and the watchlist table. Ticker pages show a pill row to switch stock, and the options card collapses after a run.

Help for non-experts: `GLOSS` in `extra.js` holds every plain-language definition. `hint(GLOSS.key)` puts a focusable "?" with that text next to a heading, `plainSummary()` writes the "In plain words" box above a ticker's result tabs, and the Help page (`renderHelp()`) lists the same glossary. Add new jargon to `GLOSS` first, then reuse it; do not write a second explanation of the same term.

Model set: the site shows six models (N-BEATS, LASSO, Ridge, RandomForest, Theta, ARIMA), chosen on 2026-10-20 as the best average rank across the 8 tickers. `backend/app/pipeline/export_ticker.py` fits only those (`KEEP_MODELS`) plus the blends (`BLENDS`), so Ensemble, Fusion, Fusion Top 3 and the reviewer are built from the six. `BASE` in `template2.html` and the `shown` set in `build.py` must match that list. The Python project itself still trains all 14; only the website export is narrowed. The 14-model export was backed up in the research project under `.scratch/backup_20261020_ui14models/`.

AI Analysis notes: served by the backend, the page shows `aiLiveCard()` (the connected LLMs only, "Run again", Run / Hold / Resume) and `aiHistoryCard()` (stored runs, Resume on today's held runs), plus `aiEffectCard()` (how the rating moves the forecast), and a run executes the AI pipeline on the server (see Live runs; no notebooks). As a static copy (artifact, static host) it shows the latest saved analysis at once (an info card, `aiEffectCard()` and the results); by the user's choice there is no replay button there. The card title says "Today's AI analysis" with the server date; the pipeline takes the trade date from `datetime.now()`, so the server's time zone decides the day: the Dockerfile and compose set `TZ=Asia/Dhaka`, and `app/core/config.py` drops an IANA `TZ` on Windows (the CRT would read it as UTC) and calls `time.tzset()` elsewhere.

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

The exports read the engine's state (`backend/engine/forecast_log/`, `backend/engine/chroma_memory/`, `backend/engine/notebooks/` (trading logs only), `backend/engine/data/`, `backend/engine/daily_data/`; override the folder with `RAEM_ENGINE`) and write their output only under `web/data/`. `backend/refresh.py` also runs `predictor_lib.top_up_data_csv()` first unless `--no-top-up`, which appends final yfinance bars to `backend/engine/data/*.csv` (DSE files are skipped), as `daily.py` does. Two side effects come from the project code they call, not from the scripts: `forecast_lib.load_combined_history()` copies new closes from `backend/engine/notebooks/trading_log_*.csv` into `backend/engine/daily_data/`, and `predictor_lib.run_pipeline()` may refresh `backend/engine/cache/`. Neither writes to `backend/engine/chroma_memory/` or `backend/engine/forecast_log/` (the scripts skip `remember_episode`). They need the packages in `backend/requirements.txt`. `export_extra.py` fetches FX rates from the network and stores them under `fx` (paper trading needs it).

The published page is **not** updated by building. After `build.py`, publish `frontend/dist/index.html` with the Artifact tool and the existing artifact URL so the link stays the same; leave out its first six lines (the skeleton `build.py` adds), because the viewer adds its own.

## Live runs (backend)

- Three kinds of user runs (plus `refresh`), all queued in `app/workers/jobs.py` and stored in `app/db/runs.py` (SQLite, table `runs`):
  - `forecast`: `app/pipeline/export_ticker.py <T> --models a,b --out file`, on the engine's CSV price files. The key is the sorted model list. A run with exactly `config.DEFAULT_MODELS` also rewrites `backend/data/tickers/<T>.json` and rebuilds the page.
  - `analysis`: `app/ai_engine/run_pipeline.py --ticker <T> --provider <p> --model <m> --out file [--market --name]`. It runs `app/ai_pipeline/runner.py` (`PipelineRunner`, ported from the any_with_dse app) in one process for **any** ticker: no notebook, no per-company file, no preparation step. 16 steps: Outcome Backfill, Fundamentals, Market, News, Sentiment, Macro Regime, Bull vs Bear Debate, Investment Facilitator, Episodic Memory, Post-Mortem Review, Trader Proposal, Risk Debate, Risk Facilitator, Portfolio Manager, Decision Verifier, Save Episode. `SessionLog` (`logger.py`) writes `trading_log_<T>.json` in the format `sessions.py` reads, which then goes into `extra.json` via `sessions.ai_entry`. The run key is the bare model for Ollama and `provider:model` for the cloud (`config.llm_key`). `POST /api/analysis` takes any valid ticker, plus optional `market` and `name`; a ticker with no prices yet is fetched by the pipeline itself (yfinance, or bdshare/dsebd.org for DSE). The CLI prints `== [i/16] Label` per step and, on a failure, `!! Cause: <message>  (in step: ...)` (`failure_cause()`, with a hint for credit, key and rate-limit errors); the page shows those lines. A failed run leaves no partial session (`drop_session()`). `GET /api/models` lists every company on the site as `analysis_tickers`. The page's "Analyse any company" box (`aiAny` in live.js) calls `analysis` directly; it only chains `company` when the user also wants the forecast.
  - `company`: `app/pipeline/add_company.py --ticker <T> [--market auto|international|dse]`, started by the page's search box (`POST /api/companies`). Steps, each safe to repeat: prices CSV in `engine/data/` (yfinance, or bdshare/dsebd.org for DSE), `engine/raem_companies.py` registry (`backend/data/companies.json`, merged into `predictor_lib.TICKERS` by a 4-line hook that `tools/sync_engine.py` puts back after a sync), `export_ticker.py`, `export_extra.py --only <T>`. The page loads a finished company through `GET /api/companies/{t}` (`loadCompany()` in live.js: DATA, EXTRA.dataset/backtest/fx, ORDER); `ORDER` is the eight research companies first, then every other company in `DATA`. Never hard-code "8" in page text.
- **LLM providers:** `ollama` plus `app.core.config.CLOUD_PROVIDERS` (openai, anthropic, google, groq, kimi; kimi is Moonshot's OpenAI-compatible API at `RAEM_KIMI_BASE_URL`, key `MOONSHOT_API_KEY` or `KIMI_API_KEY`, temperature 1). The page lists only connected ones (`ok` and with models). A cloud provider is usable when its API key variable is set; `RAEM_<KEY>_MODELS` replaces its model list; `RAEM_OPENAI_BASE_URL` points openai at any OpenAI-compatible server. `app/core/config.py` loads `web/.env` (python-dotenv, existing variables win). Keys reach the runner through the inherited environment and are never returned by the API. `ai_pipeline/llm.py` builds the model (`make_llm()`; Anthropic gets no temperature) and retries transient errors (`invoke_llm_with_retry()`), but raises billing errors (`credit balance`, `insufficient_quota`) at once, so an empty account fails in seconds with a clear cause instead of retrying for minutes. `FMP_API_KEY` and `FINNHUB_API_KEY` are optional: without both, non-DSE tickers use the keyless Yahoo tools of `tradingagents` (`agents._ta_utils`).
- **Memory embeddings:** `ai_pipeline/embeddings.py`: `embed_mode()` picks `ollama` when the Ollama server lists `nomic-embed-text`; else `local` when chromadb's all-MiniLM-L6-v2 is on disk or downloads within `RAEM_EMBED_DOWNLOAD_TIMEOUT` (900 s, in a child process); else `hash` (`RAEMHashEmbedding`, signed word hashing, no model). `RAEM_EMBED` forces a mode. `store_path(mode)` gives the store: `raem_chroma_db` (ollama), `raem_chroma_db_local`, `raem_chroma_db_hash`; vectors of different sizes cannot share a store. In `local` and `hash` mode `copy_memory()` first copies every collection of the nomic store (`raem_episodic_memory`, `raem_news_corpus`) into the run's store by id, re-embedding the documents, keeping metadata, only missing ids; the source is not changed. `memory.connect()` opens the store. The Dockerfile downloads the MiniLM model at build time. The session records `llm_provider` and `memory_embeddings`. Trading logs and stores live under `RAEM_LOG_DIR` (default `engine/notebooks`).
- **Same-date reuse:** `jobs.submit()` returns a finished run with the same kind, company, `run_date` (server local date) and model key, unless `force`. An identical queued or running request is joined, not duplicated. A held run with that key goes back in the queue (`reused: "resumed"`, same id). Failed runs are never reused. One worker thread runs one job at a time.
- **Hold and resume:** `POST /api/runs/{id}/hold` (analysis only) marks a queued run `held`, or adds a running one to `jobs._held` and kills its process tree (`_kill_tree`, psutil, so the kernel's LLM request ends); `_execute` then stores `held`, not `failed`, keeping `step`. `db.init()` turns runs left `running` by a stopped server into `held`. Resuming works through the answer file `config.llm_cache_path(ticker, model_key)` (`storage/llm_cache/`), passed as `RAEM_LLM_CACHE`: `llm.enable_resume()` wraps the chat model's `_generate`, saves each answer under a hash of provider, model, temperature, stop, kwargs and the messages, and returns the saved answer for an identical prompt, so unchanged steps replay at once and steps whose inputs (prices, news) changed call the model. A failed run keeps the file too: `jobs.public()` adds `saved_answers` to held and failed analysis runs, and the page offers Continue for today's failed run when it has some. `_run_analysis` deletes the file after a finished run; `run_pipeline` logs `Resuming: N saved ...` and `Reused N ..., made M new ...` and returns `reused_answers` / `new_llm_calls`.
- **Debates and decision:** `sessions.entry_from_session()` adds `latest.signal` (verifier `effective_signal`, else `summary_table.final_signal`, else the rating mapped: Overweight→BUY, Underweight→SELL), `latest.verifier` and `latest.debate` (`turns` parsed by `debate_turns()` from "Bull Analyst: … Bear Analyst: …", `risk_turns`, `facilitator`, `risk_facilitator`, `trader`, `complete`). The session log keeps only excerpts, so `run_pipeline.debate_block(state)` builds the full debate (every turn, both facilitators, the trader) from the pipeline state into `latest.debate` with `complete: true`. `GET /api/runs/{id}/live` returns the session written so far (`ai_entry(..., since=started_at)`) while a run goes; `pollRun` asks it every second poll and `agentBoard()` draws the agents (done / running / waiting), the latest output and the debate so far. The AI page order: live card, `aiDecisionCard` (large BUY/HOLD/SELL like the any_with_dse RecommendationPanel), `aiEffectCard`, stats, `aiDebateCard`, `aiRiskCard`, final decision text, analyst pipeline, history, stored runs.
- **Forecast page runs on the server:** when `LIVE.ok`, `run()` in `template2.html` calls `liveForecast()`; the options card gets "All 14 (as in the forecast notebook)" / "Best 6" (`X.lfc.models`, all by default) and a live progress box; `showForecast()` opens the All models tab, where ★ marks `forecast.ensemble_members`. The static copy keeps the replay.
- **Hosting state:** `backend/tools/persist.py` (Railway, `RAEM_DATA_DIR`: folders linked onto one volume) and `backend/tools/hf_sync.py` (Hugging Face Space, `RAEM_HF_DATASET` + `HF_TOKEN`: `restore` at container start, `save_in_background()` from `jobs._execute` after every run, to a private dataset) share `persist.FOLDERS` and one rule: a file the image changed since the last save keeps the image's version, others keep the saved copy; `.py` files are never synced; SQLite files are copied with the backup API. `backend/tools/deploy_hf.py <user>/<space>` creates the Docker Space and the `<space>-data` dataset, sets `PORT`, `TZ`, `RAEM_HF_DATASET`, and uploads what git would push with a Space README header. The Dockerfile sets `HOME=/home/user` and makes `/app` writable because Spaces run as user 1000.
- **AI influence on the forecast:** frontend only, `aiBlend(t)` in `extra.js` (see `GLOSS.aiadj`): adjusted 7-day move = (1 − w)·Ensemble + w·rating×`typicalMove(t)`, w 0.2, or 0.1–0.35 from `invTrack()` once 5 calls are graded, none when the session is over 10 days older than the prices. Shown on Home, the Forecast summary, the Calculator (`INV_SRC` "AI") and the AI page (`aiEffectCard`).
- **The four analysts follow the research notebooks** (`ai_pipeline/agents.py`, `news_rag.py`, `dse_layer.py`; the reference is the BATBC v10 notebook): (1) **Fundamentals** hands the other agents a numbers-only block (`fundamentals_report` starts with `FUNDAMENTALS - NUMBERS ONLY`; the analyst's own report, verdict and facts stay in `fundamentals_report_llm` / `fund_verdict` and in the session log; `RAEM_FUND_NUMBERS_ONLY=0` restores the old behaviour, `RAEM_FUND_LLM_REPORT=0` skips the narrative call). (2) **Market** must not say bullish/bearish (prompt rule plus `_neutralise_bull_bear`) and keeps a `KEY POINTS` line. (3) **News** adds dated press coverage from web search (`news_rag.fetch_web_news`, directory pages dropped) and sector news (`SECTOR_NEWS_QUERIES`), returns the untruncated texts, and the runner stores them in the news corpus by scope (company / web / sector / market, exchange-wide notices about other firms tagged `DSE_MARKET`), de-duplicated by content hash, never returned for a date after the trade date, web results skipped in replay mode; `news_rag_context` (company items first, exchange notices labelled) is appended to the news evidence of the debates. (4) **Sentiment** is scored in code: a keyword lexicon over the stored company news, as-of filtered, recency weighted (half-life 10 days), score 1-10 → BULLISH ≥ 6 / BEARISH ≤ 4; the LLM only explains it and its own score is kept as `llm_score`. `sentiment_metrics.source` is `RULE_BASED` or `LLM_ONLY` (no stored company news to score); `RAEM_RULE_SENTIMENT=0` is the ablation switch. DSE companies use `dse_layer.py` (the notebook's data layer: old.dsebd.org prices, indicators, company page, disclosed EPS, corporate actions, notices; amounts in taka); other companies use the Yahoo/provider tools. Company names for press searches: `RAEM_COMPANY_NAME` (set from `--name`), `backend/data/companies.json`, `news_rag.RESEARCH_NAMES`, else the ticker. Not ported from the notebook (not part of the four analysts): Fact Guard, Graph Analyst, news graph, analyst cache.
- **No notebooks.** The AI analysis is plain Python (`app/ai_pipeline/`, `backend/tradingagents/`). Do not add per-company notebooks or files; a new company needs nothing but its ticker. Change an agent in `ai_pipeline/agents.py` once and it applies to every company. The reference is the any_with_dse app; keep its step names (`runner.STAGES`) because `sessions.STEP_LABELS` and the page's `AGENT_STAGES` read them.
- **Session archive:** the logger prunes `trading_log_*.json` to `MAX_DAYS`=10 days on every run. `sessions.archive_logs()` copies every logged session into the `sessions` table of the run database (and rewrites `backend/data/session_archive.json.gz`, which is committed). It runs at server start (after `import_snapshot()` seeds a new database), before and after each run, and in `tools/sync_engine.py` before logs are overwritten. `sessions_for()` merges logs and archive, so history never shrinks. Tests point `RAEM_DB` and `RAEM_SNAPSHOT` at temp files.
- Progress comes from the `== [i/16] Label` lines the CLI prints and from `GET /api/runs/{id}/live`.
- Packages the pipeline imports (`pdfplumber`, `markdown`, `bdshare`, `langgraph`, `ddgs`, ...) must be in `backend/requirements.txt`; the Docker image installs only that file.
- New pipeline steps reach the site automatically: `sessions.STEP_LABELS` only renames known step names; unknown steps keep their own name. Sessions saved before the move (from the notebooks) keep their old steps (Graph Analyst, Forecasting Agent, Prediction Engine, 7-day forecast, 10-day scenarios); new ones do not have them, because the forecast is a separate run.
- `config.FORECAST_MODELS` lists the 14 forecast model keys (best first); `config.DEFAULT_MODELS` must equal `KEEP_MODELS` in `export_ticker.py` (a test checks it). `BASE` in `template2.html` lists all 14 labels and the UI shows only the ones present in a run.
- Frontend: `live.js` calls the API only when the page is served by the backend (`liveInit()` asks `api/health`; a static copy or the artifact stays in replay mode; by the user's choice there is no "connect to a server" option). The token input saves on `input` and never re-renders on `change`, because that re-render would swallow the click that caused the blur. `applyForecastRun()` / `applyAnalysisRun()` swap `DATA[t]` / `EXTRA.ai[t]` for a run's result. On page load the newest finished run of each company on any date is applied (`GET /api/runs/latest?date=all`) unless the saved page already holds newer prices (forecast) or a newer session (analysis), and runs still queued or running (`GET /api/runs?status=queued,running`) are watched by `watchForecast()` / `watchAnalysis()`, so a reload or a second browser shows their progress. A running run's `elapsed` (seconds, server clock) drives the timer.
- Long runs: a cloud-model run takes a few minutes; a 7B Ollama run on a CPU can take hours. The worker calls `SetThreadExecutionState` on Windows while a job runs so the PC does not idle-sleep. Limit: `RAEM_ANALYSIS_TIMEOUT` (`config.ANALYSIS_TIMEOUT`, per run).
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
9. **Code to Figma.** To push the live page into Figma, capture it from the published artifact (desktop 1280, phone 390, dark and light) rather than from the template, because the DOM is generated at runtime. Name frames after pages: `Dashboard`, `Ticker`, `AI Analysis`, `Watchlist`, `Backtest`, `Dataset Explorer`, `News`, `Fundamentals`, `Memory`, `Settings`.
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
