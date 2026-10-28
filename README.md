# RAEM Forecast Console

Seven-day stock forecasts and an AI analysis (TradingAgents agents + RAEM episodic memory) for any company (eight come with saved results: AAPL, IBM, 2222.SR, 0300.HK and four Dhaka Stock Exchange stocks), as a website you can host. Research output, not investment advice.

This folder is self-contained: put it on GitHub, clone it anywhere and run it. It does not need the research project next to it.

## What it does

| Page | What you can do | Where it comes from |
|---|---|---|
| Home | featured chart, a card per stock, how it works | saved results |
| Forecast | 7-day path, daily BUY/HOLD/SELL, plain-language summary, every model's prediction, reviewer, track record; **Run** runs the best 5 models on the server; the page shows what 100 tk could become, and the Ensemble averages the best two, as in the forecast notebook | `forecast_lib` + `predictor_lib` |
| **AI Analysis** | **pick any company and an LLM (Ollama, OpenAI, Claude, Gemini, Groq or Kimi) and run the AI analysis** on the server: analysts, bull vs bear debate, trader, risk debate, portfolio manager, verifier; open earlier stored runs | `backend/app/ai_pipeline/` (the any_with_dse pipeline) + `backend/tradingagents/` |
| Return Calculator | what any amount could be worth in 7 days, per company | forecasts, AI-analysis record, backtest |
| Backtest, Dataset, News, Fundamentals, Memory | evidence and data behind the forecasts | engine data and trading logs |
| Watchlist, Settings, Help | pins, preferences, server token, glossary | your browser |

**Stored runs.** Every forecast and AI analysis run is saved in a database (SQLite, `backend/storage/raem.db`). Asking again on the same date for the same company and the same model(s) returns the stored result at once instead of running everything again. Tick "Run again" to force a new run. The notebooks also cache their own analysts per company, date and model.

**Every AI session is kept.** The trading logs keep every session; nothing is deleted after 10 days (`RAEM_LOG_MAX_DAYS=N` would bring a limit back). The server also copies every session into the database (table `sessions`) and keeps a compressed copy in `backend/data/session_archive.json.gz`, which is in git, so history survives new runs, restarts and fresh clones.

**Page links.** Every page has its own link, e.g. `/#ai`, `/#tk-AAPL`.

## Folder layout

```
web/
├── README.md, CLAUDE.md          this file; rules for working on the code
├── Dockerfile, docker-compose.yml, railway.json, vercel.json, DEPLOY.md, .dockerignore, .env.example, .gitignore
├── .github/workflows/tests.yml   runs the API tests and a page build on every push
├── backend/
│   ├── app/                      FastAPI, laid out like the DSE app (backend/app/)
│   │   ├── main.py               creates the app and includes the routers
│   │   ├── core/config.py        settings from the environment, model lists, paths
│   │   ├── db/runs.py            SQLite run store
│   │   ├── models/schemas.py     request bodies
│   │   ├── api/                  deps.py (tokens, known tickers, queueing) and routes/: health, stocks, forecast, analysis, refresh, runs
│   │   ├── services/             tickers.py (saved company data), live.py (progress of a running analysis)
│   │   ├── workers/jobs.py       the run queue and its runners (forecast, analysis, refresh)
│   │   ├── pipeline/             export_ticker.py (forecast one company -> JSON), add_company.py (any ticker: prices, forecast, page data), export_extra.py, patch_daily.py, sessions.py (trading logs -> JSON)
│   │   ├── ai_engine/            run_pipeline.py (the AI analysis of any ticker with a chosen LLM, Ollama or cloud)
│   │   └── ai_pipeline/          the any_with_dse pipeline: runner.py (16 steps), agents.py, llm.py (providers + saved answers), memory.py (RAEM memory), embeddings.py, market_data.py, logger.py
│   ├── tradingagents/            vendored TradingAgents package (data tools incl. the Dhaka Stock Exchange vendor)
│   ├── engine/                   forecasting code and its data (CSV prices, trading logs, memory)
│   │   └── notebooks/            trading_log_<TICKER>.json/.csv written by the AI analysis, and raem_chroma_db (episodic memory, news)
│   ├── data/                     saved results the page is built from (tickers/*.json, extra.json, session_archive.json.gz)
│   ├── storage/                  run database and saved LLM answers (created at runtime, not in git)
│   ├── tests/                    API tests (fake runners, no models needed)
│   ├── tools/sync_engine.py      copy fresh code and data from the research project
│   ├── refresh.py                re-run the default forecasts for all companies and rebuild the page
│   └── requirements.txt
└── frontend/
    ├── src/                      template2.html, extra.css, extra.js, live.js, handlers.js, merge.py, build.py
    └── dist/index.html           the built page (works on its own; talks to the backend when served by it)
```

## Run it on your computer

```bash
cd web
pip install -r backend/requirements.txt
python backend/refresh.py --build-only                       # build the page from the saved results
python -m uvicorn app.main:app --app-dir backend --port 8000
```

Open http://localhost:8000. API docs: http://localhost:8000/docs. Use `python -m uvicorn`, not plain `uvicorn`: on Windows the `uvicorn` command is often not on PATH.

For **AI analysis** you need an LLM: [Ollama](https://ollama.com) running a chat model, or a cloud API key (next section). No data-provider account is needed: prices and news come from Yahoo Finance (and bdshare / dsebd.org for Dhaka stocks).

```bash
ollama pull qwen2.5:7b
ollama pull nomic-embed-text      # RAEM's memory uses it (optional: a built-in model is used without it)
```

Any other chat model you pull (e.g. `llama3.2:3b`) appears in the model list. The AI analysis makes many long LLM calls (analysts, two debates, trader, verifier), so one run with `qwen2.5:7b` on a CPU-only machine takes about 1 to 3 hours. A GPU or a smaller model (`qwen2.5:1.5b`, `llama3.2:3b`) is much faster. The server allows 6 hours per run (`RAEM_ANALYSIS_TIMEOUT`). Keep the computer awake and plugged in during a run: on Windows the server keeps the PC and the display on while a run is going, but closing a laptop lid still sleeps. Its LLM answers are saved, so running it again continues from there. Forecast runs need no Ollama and take 1 to 3 minutes.

### Cloud LLMs (Claude, Kimi, OpenAI, Gemini, Groq)

A host without Ollama can still run AI analysis with a cloud model. Put a key in `.env` (see `.env.example`) and restart the server; that provider's models appear in the AI Analysis model list, grouped by provider:

| Provider | Key in `.env` | Default models (change with `RAEM_<PROVIDER>_MODELS`) |
|---|---|---|
| OpenAI | `OPENAI_API_KEY` | gpt-4.1-mini, gpt-4o-mini, gpt-4.1 |
| Anthropic Claude | `ANTHROPIC_API_KEY` | claude-haiku-5-5, claude-sonnet-5-5 |
| Google Gemini | `GOOGLE_API_KEY` | gemini-2.5-flash, gemini-2.5-flash-lite |
| Groq (free tier) | `GROQ_API_KEY` | llama-3.3-70b-versatile, llama-3.1-8b-instant |
| Kimi (Moonshot AI) | `MOONSHOT_API_KEY` (or `KIMI_API_KEY`) | kimi-k3.1-pro, kimi-k3, kimi-k2.6 (the older kimi-k2-*-preview models are retired); `RAEM_KIMI_BASE_URL` is `https://api.moonshot.ai/v1` by default, `https://api.moonshot.cn/v1` for a China account |

`RAEM_OPENAI_BASE_URL` points the OpenAI entry at any OpenAI-compatible server (OpenRouter, DeepSeek, LM Studio). Keys stay on the server and are never sent to the page. A cloud run takes minutes instead of hours, and the provider bills each run (about 40 LLM calls), so set `RAEM_RUN_TOKEN` on a public site.

How it works: `backend/app/ai_engine/run_pipeline.py` runs the pipeline in one Python process (no notebook, no per-company file). `app/ai_pipeline/llm.py` builds the chat model of the chosen provider and saves every answer for Hold / Continue. A billing error ("credit balance is too low") is reported at once with the provider's own message instead of being retried. Optional `FMP_API_KEY` + `FINNHUB_API_KEY` switch US stocks to those providers; without both, Yahoo Finance is used.

Memory: the episodic memory and news search use Ollama's `nomic-embed-text`. When the server can reach it, every run (cloud or Ollama) uses the original memory store (`raem_chroma_db`). Without it, the run uses chromadb's built-in embedding model in a separate store, `raem_chroma_db_local`, because vectors from two embedding models cannot be mixed. That model (about 80 MB) is downloaded into the Docker image at build time, or on the first run otherwise (up to `RAEM_EMBED_DOWNLOAD_TIMEOUT`, 15 minutes). If it cannot be downloaded, the run still finishes: memory then matches by shared words (`raem_chroma_db_hash`). At the start of each such run the original store's past episodes and news are copied into that store, embedded again with its model (only what it lacks), so past memory still counts; the original store keeps its contents. The daily prediction memory (`chroma_memory/`) uses number vectors, not an LLM, and works everywhere.

## Running models from the page

- **Search any company.** Type a ticker or name in the box at the top. A company already on the site opens; for any other the box lists matches from Yahoo Finance and an "Add" button (or "Add <text>" with a market choice: Yahoo Finance, Dhaka Stock Exchange, or automatic). The server then downloads two years of prices, registers the company (`backend/data/companies.json`), runs the 7-day forecast and the page data, and opens the company when done (several minutes; the progress card bottom right stays while you browse). It then works like the first eight: Forecast, Calculator, Backtest, Dataset, Memory and **AI Analysis** (run the notebook with the LLM you choose). A refresh keeps its prices current too. Needs internet for Yahoo Finance or dsebd.org; it does nothing on a static copy of the page.
- **Analyse any company.** The AI Analysis page has an "Analyse any company" box: type any ticker. The analysis needs no notebook and no preparation: `POST /api/analysis` takes any valid ticker, and the pipeline fetches the prices, fundamentals and news itself. A company that is not yet on the site can also be added (search box) to get its 7-day forecast. When a run fails, the log ends with `!! Cause: <the error>  (in step: <name>)`, and a refused model name, an API key or an empty credit balance gets a plain hint.
- **Forecast → Run.** On the hosted site press Run (there is no model choice). The server tops up the company's prices, runs the five models with `export_ticker.py`, the chart and tables switch to the new run, and the run is stored and also updates the saved page.
- **If you invest 100 tk.** Opening a company on the Forecast page shows what 100 tk could become after 7 trading days from the Ensemble forecast (profit or loss in tk and %, a likely range, day by day, and every model's own result). It follows the page's forecast, so it changes after a Run.
- **Forecast → Run.** On the hosted site Run fits the best 5 models now (N-BEATS, LASSO, Ridge, RandomForest, Theta), shows the progress, then the All models tab: every model's day-1 and day-7 prediction, its error vs "no change", and ★ on the two models the Ensemble averages.
- **AI Analysis.** Choose a company and an LLM (an Ollama model or a cloud model), press **Run AI analysis for <today>**: the pipeline runs for today's date (in the server's time zone, `TZ=Asia/Dhaka` by default in Docker). The page shows each step as it runs (the log is saved after every agent and the page reads it while the run goes), then the **Decision**: BUY, HOLD or SELL in large type with the portfolio manager's rating, the verifier and why; then the **AI debate** (bull vs bear, round by round, with the facilitator's verdict) and the **trader and risk debate**, and every analyst (fundamentals, market, news, sentiment, macro regime), post-mortem, trader, risk facilitator, portfolio manager and verifier. The model list shows only what the server has connected: the Ollama models it can reach and the cloud providers whose key is in `.env`. Runs go one company at a time.
- **Hold.** While an AI analysis runs, **Hold** stops it and keeps what it has done: every LLM answer is saved the moment it arrives (`backend/storage/llm_cache/`). **Resume** (or Run again later the same day, same company and model) runs the pipeline again from the top, but every LLM call with exactly the same prompt as before (same prices, news and earlier answers) gets the saved answer at once, so it continues where it stopped; a step whose market data changed asks the model again. If the server restarts during a run, that run is held too, and a run that stopped with an error offers **Continue** with its saved answers. When a run finishes its saved answers are deleted, so "Run again" then gives a fresh analysis.
- **The AI analysis moves the prediction a little.** The latest AI rating (Buy +1, Overweight +½, Hold 0, Underweight −½, Sell −1) times the stock's typical 7-day move is the AI view; the AI-adjusted forecast is 80% the models' Ensemble and 20% that view (10–35% once the AI has five graded calls, by how often it was right). A session more than 10 days older than the prices does not count. It shows on Home (each card and the "With AI" column), the Forecast summary, the Return Calculator ("AI-adjusted") and the AI Analysis page.
- Runs wait in one queue, so a second request waits for the first. An identical request joins the run already going.
- Closing or reloading the page does not stop a run. Open the page again and it shows the progress of runs still going, then their results.

A copy of the page without the server (a static host, the claude.ai artifact, the file opened from disk) shows the saved results and the latest saved AI analysis of each company; models run only on the hosted site.

## Refresh everything

```bash
python backend/refresh.py                  # top up prices, re-run all 8 stocks, extra pages, rebuild
python backend/refresh.py AAPL IBM         # only some stocks
python backend/refresh.py --no-top-up      # skip downloading new prices
```

When the research project changes (new AI runs, code, cache), bring the engine up to date first:

```bash
python backend/tools/sync_engine.py /path/to/research/project
python backend/refresh.py
```

## API

| Method and path | Returns |
|---|---|
| `GET /` | the website |
| `GET /api/health` | status, date, Ollama reachable, queue length, whether a token is needed |
| `GET /api/models` | forecast models (the five it runs), LLMs per provider (`llm_providers`), companies on the site |
| `GET /api/tickers`, `GET /api/tickers/{t}` | saved results per company |
| `GET /api/extra` | data for the extra pages |
| `GET /api/search?q=` | matches for a ticker or name: companies on the site, then Yahoo Finance suggestions |
| `GET /api/companies`, `GET /api/companies/{t}` | companies added from the search box; one company's saved forecast with its price table, backtest and FX rate |
| `POST /api/companies` `{"ticker", "market": "auto\|international\|dse", "name", "sector"}` | adds a company (queued run of kind `company`): prices, forecast, page data; `reused: "exists"` when it is already there |
| `POST /api/forecast` `{"ticker", "models": [...], "force"}` | stored run, or a new queued run |
| `POST /api/analysis` `{"ticker", "llm_provider", "llm_model", "force"}` | stored run, a resumed held run, or a new queued AI-analysis run (`llm_provider`: ollama, anthropic, kimi, openai, google, groq) |
| `POST /api/runs/{id}/hold` | hold a queued or running AI analysis; the next request for it resumes it |
| `GET /api/jobs/{id}` | progress: status, current step, log tail, place in queue |
| `GET /api/runs?kind=&ticker=&status=` | run history (`status=queued,running` lists runs in progress) |
| `GET /api/runs/latest?date=` | newest finished run per company for a date (`date=all`: any date), with results |
| `GET /api/runs/{id}` | one run with its full result |
| `POST /api/refresh` | full refresh (needs `RAEM_REFRESH_TOKEN`) |

POST responses look like `{"run": {...}, "reused": "stored" | "active" | "resumed" | null}`.

## Settings (`.env`)

| Name | Meaning |
|---|---|
| `RAEM_RUN_TOKEN` | when set, running a forecast or an AI analysis needs this token (Settings page → Access token) |
| `OLLAMA_BASE_URL` | Ollama server for AI analysis (default `http://localhost:11434`) |
| `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `GOOGLE_API_KEY`, `GROQ_API_KEY` | turn on that cloud provider for AI analysis; `RAEM_<PROVIDER>_MODELS` sets its model list, `RAEM_OPENAI_BASE_URL` an OpenAI-compatible server |
| `RAEM_LLM_DEFAULT` | model selected first (default `qwen2.5:7b`) |
| `RAEM_REFRESH_TOKEN` | turns `POST /api/refresh` on |
| `RAEM_CORS_ORIGINS` | other sites allowed to call the API from a browser, e.g. the GitHub Pages copy of the page (comma-separated) |
| `PORT` | port to listen on |
| `TZ` | time zone that decides "today" for runs and the AI analysis' trade date (default `Asia/Dhaka` in Docker; hosts usually run on UTC, a day behind Bangladesh after 18:00 UTC) |
| `RAEM_DB`, `RAEM_ANALYSIS_TIMEOUT`, `RAEM_FORECAST_TIMEOUT`, `RAEM_COMPANY_TIMEOUT` | optional: database file and time limits |

## Put it on GitHub and host it

**Railway + Vercel, or Hugging Face Spaces (free):** step by step in [DEPLOY.md](DEPLOY.md). Railway runs the app with one volume and Vercel serves the page and forwards `/api`; a Space runs the whole app on a free 16 GB CPU (`python backend/tools/deploy_hf.py <hf-username>/raem`) and keeps ChromaDB memory, runs and logs in a private Hugging Face dataset, so they survive restarts.

1. Create an empty repository and push this folder as its root (`git init`, `git add .`, `git commit`, `git push`). `.gitignore` keeps out `.env`, the run database and caches. The engine data (about 30 MB) is included so a fresh clone works.
2. Pick a host:

**Static page only (free).** Host `frontend/dist/index.html` on GitHub Pages, Netlify, Vercel or Cloudflare Pages. Everything works with the saved results, and runs are replays; refresh on your computer and push again for new data.

**Full app with Docker (live runs).** Any host that runs a Docker container with a persistent disk: a VPS (DigitalOcean, Hetzner), Render, Railway or Fly.io.

```bash
docker build -t raem-web .
docker run -p 8000:8000 -e RAEM_RUN_TOKEN=<long random string> -e OLLAMA_BASE_URL=http://<ollama-host>:11434 raem-web
# or: cp .env.example .env (fill it in), then: docker compose up --build -d
```

Know before you host:

- **AI analysis needs an LLM the server can reach.** Easiest online: a cloud API key in `.env` (Groq has a free tier). For local models, free web hosts cannot run a 7B model; use a VPS with 8 GB RAM or more and install Ollama on it (same machine: `docker compose` already points to `host.docker.internal`), or point `OLLAMA_BASE_URL` at another machine running Ollama. Even with a cloud LLM, Ollama with just `nomic-embed-text` (small, runs on any CPU) keeps the original memory store. Without any LLM, forecasts still run and stored AI runs still open.
- **Set `RAEM_RUN_TOKEN` on a public site.** Each run uses a CPU core for minutes; without a token anyone can queue runs.
- **Use a persistent disk** for `backend/storage` (run database), `backend/engine/*` and `backend/data`, or a redeploy forgets runs and refreshed prices. The compose file mounts them.
- The image includes PyTorch (CPU) and is a few GB; give the server at least 2 GB of RAM for forecasts alone.
- Serve over HTTPS (most hosts do it for you).

## The AI pipeline

The AI analysis is the pipeline of the any_with_dse app, ported without changes to its agents: `backend/app/ai_pipeline/runner.py` runs 16 steps in order: outcome backfill, fundamentals, market, news (with RAG), sentiment, macro regime, bull vs bear debate, facilitator, memory (regime reflection and retrieval of similar past episodes), post-mortem, trader, risk debate, risk facilitator, portfolio manager, decision verifier, save episode. One code path serves every ticker, international or Dhaka Stock Exchange (`backend/tradingagents/dataflows/dse_*.py`). There are no per-company notebooks. A fix in `agents.py` reaches every company.

The four analysts (fundamentals, market, news, sentiment) work as in the research notebooks: Fundamentals passes only code-extracted numbers to the other agents (its own verdict stays in the log), Market avoids bull/bear wording, News adds dated press coverage and stores every item once by type (company, web, sector, exchange-wide) without ever using news from after the trade date, and Sentiment is a score computed in code from the stored news with a keyword list, which the LLM only explains (`RAEM_RULE_SENTIMENT=0` turns that off). Dhaka companies read the notebook's DSE data layer (`backend/app/ai_pipeline/dse_layer.py`).

The debate agents (Bull, Bear, facilitators, trader, risk debaters, portfolio manager) also follow the notebook: each prompt carries the raw indicator numbers (never cut), a numbered fact sheet computed in code (price against the 50-day average, RSI zone, MACD sign, dividend trend, valuation, dated news items) and the news, macro and memory context, with rules to cite the fact id and not to invent events (`backend/app/ai_pipeline/debate_data.py`).

| Pipeline part | On the site |
|---|---|
| Fundamentals, Market, Macro regime, News (with RAG), Sentiment analysts | AI Analysis pipeline tabs, News and Fundamentals pages |
| Bull vs bear debate and facilitator, Post-mortem, Trader, Risk debate and facilitator, Portfolio manager | AI Analysis pipeline tabs and final decision |
| Decision Verifier | AI Analysis decision card |
| Episodic memory, regime reflection, outcome backfill | Memory page; used inside each run |
| Trading log, 7-day hit rate, forecast agent and reviewer | Track record, Reviewer tab, Backtest, Return Calculator |

The 7-day forecast (5 models, Ensemble, Fusion, reviewer) is a separate run (**Forecast → Run**). Older saved sessions, made by the former notebooks, also carry Graph Analyst, Forecasting Agent, Prediction Engine and 10-day scenario steps; they stay readable, new runs do not make them.

## Limits

- Cloud models are verified through the OpenAI-compatible path; each provider's own quirks (rate limits, model names) can differ. Reasoning models (o-series, gpt-5) are called without a temperature but are slower and dearer; prefer the listed models.
- `backend/engine/` is a copy of the research project; changes there reach the site after `tools/sync_engine.py`.
- The four DSE stocks' daily signals use the saved price snapshot plus the closes in the trading logs.
- The daily BUY/HOLD/SELL comes from `predictor_lib`'s 5-model pipeline, separate from the 7-day forecast.
