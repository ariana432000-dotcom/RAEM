# RAEM Forecast Console

Seven-day stock forecasts and the notebooks' AI analysis for 8 stocks (AAPL, IBM, 2222.SR, 0300.HK and four Dhaka Stock Exchange stocks), as a website you can host. Research output, not investment advice.

This folder is self-contained: put it on GitHub, clone it anywhere and run it. It does not need the research project next to it.

## What it does

| Page | What you can do | Where it comes from |
|---|---|---|
| Home | featured chart, a card per stock, how it works | saved results |
| Forecast | 7-day path, daily BUY/HOLD/SELL, plain-language summary, every model's prediction, reviewer, track record; **Run** runs all 14 models (or the best 6) on the server, and the Ensemble averages the best two, as in the forecast notebook | `forecast_lib` + `predictor_lib` |
| **Portfolio** | virtual orders on any past date, P&L and projected curves, and **Run the forecast**: pick the models (6 best or all 14) and run them now on the CSV price files | live run on the server |
| **AI Analysis** | **pick a company and an LLM (Ollama, OpenAI, Claude, Gemini or Groq) and run that company's notebook** (Run All) on the server; open earlier stored runs | the per-company notebooks |
| Return Calculator | what any amount could be worth in 7 days, per company | forecasts, notebook record, backtest |
| Backtest, Dataset, News, Fundamentals, Memory, Papers | evidence and data behind the forecasts | engine data and notebook logs |
| Watchlist, Settings, Help | pins, preferences, server token, glossary | your browser |

**Stored runs.** Every forecast and AI analysis run is saved in a database (SQLite, `backend/storage/raem.db`). Asking again on the same date for the same company and the same model(s) returns the stored result at once instead of running everything again. Tick "Run again" to force a new run. The notebooks also cache their own analysts per company, date and model.

**Every notebook session is kept.** The notebooks keep only the last 10 days in their trading logs and drop older sessions on each run. The server copies every session into the database first (table `sessions`) and keeps a compressed copy in `backend/data/session_archive.json.gz`, which is in git, so history survives new runs, restarts and fresh clones.

**Page links.** Every page has its own link, e.g. `/#paper`, `/#ai`, `/#tk-AAPL`.

## Folder layout

```
web/
├── README.md, CLAUDE.md          this file; rules for working on the code
├── Dockerfile, docker-compose.yml, railway.json, vercel.json, DEPLOY.md, .dockerignore, .env.example, .gitignore
├── .github/workflows/tests.yml   runs the API tests and a page build on every push
├── backend/
│   ├── app/                      FastAPI: main.py (routes), jobs.py (run queue), db.py (SQLite), config.py
│   ├── exporter/
│   │   ├── export_ticker.py      forecast one company with chosen models -> JSON
│   │   ├── run_notebook.py       run a company notebook headlessly with a chosen LLM (Ollama or cloud)
│   │   ├── sessions.py           notebook trading logs -> AI-analysis JSON (all logged steps)
│   │   ├── export_extra.py       data for the extra pages
│   │   └── patch_daily.py        refresh only the daily-signal block (optional)
│   ├── engine/                   forecasting code, notebooks, TradingAgents package and their data (CSV prices, logs, memory)
│   ├── data/                     saved results the page is built from (tickers/*.json, extra.json, session_archive.json.gz)
│   ├── storage/                  run database and executed notebook copies (created at runtime, not in git)
│   ├── tests/                    API tests (fake runners, no models needed)
│   ├── tools/sync_engine.py      copy fresh code, notebooks and data from the research project
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

For **AI analysis** you need an LLM: [Ollama](https://ollama.com) running with the notebooks' models, or a cloud API key (next section).

```bash
ollama pull qwen2.5:7b
ollama pull nomic-embed-text      # the notebooks' memory uses it
```

Any other chat model you pull (e.g. `llama3.2:3b`) appears in the model list. A notebook makes many long LLM calls (analysts, two debates, trader, verifier), so one run with `qwen2.5:7b` on a CPU-only machine takes about 2 to 3 hours; a single debate cell can take 30 to 60 minutes. A GPU or a smaller model (`qwen2.5:1.5b`, `llama3.2:3b`) is much faster. The server allows 2 hours per cell and 6 hours per run (`RAEM_CELL_TIMEOUT`, `RAEM_NOTEBOOK_TIMEOUT`). Keep the computer awake and plugged in during a run: on Windows the server keeps the PC and the display on while a run is going (a display that turns off puts Modern Standby laptops to sleep), but closing a laptop lid still sleeps, and the run then stops with a cell time-out when it wakes. Its LLM answers are saved, so running it again continues from there. Forecast runs need no Ollama and take 1 to 3 minutes.

### Cloud LLMs (Claude, Kimi, OpenAI, Gemini, Groq)

A host without Ollama can still run AI analysis with a cloud model. Put a key in `.env` (see `.env.example`) and restart the server; that provider's models appear in the AI Analysis model list, grouped by provider:

| Provider | Key in `.env` | Default models (change with `RAEM_<PROVIDER>_MODELS`) |
|---|---|---|
| OpenAI | `OPENAI_API_KEY` | gpt-4.1-mini, gpt-4o-mini, gpt-4.1 |
| Anthropic Claude | `ANTHROPIC_API_KEY` | claude-haiku-5-5, claude-sonnet-5-5 |
| Google Gemini | `GOOGLE_API_KEY` | gemini-2.5-flash, gemini-2.5-flash-lite |
| Groq (free tier) | `GROQ_API_KEY` | llama-3.3-70b-versatile, llama-3.1-8b-instant |
| Kimi (Moonshot AI) | `MOONSHOT_API_KEY` (or `KIMI_API_KEY`) | kimi-k2-turbo-preview, kimi-k2-0905-preview, moonshot-v1-32k; `RAEM_KIMI_BASE_URL` is `https://api.moonshot.ai/v1` by default, `https://api.moonshot.cn/v1` for a China account |

`RAEM_OPENAI_BASE_URL` points the OpenAI entry at any OpenAI-compatible server (OpenRouter, DeepSeek, LM Studio). Keys stay on the server and are never sent to the page. A cloud run takes minutes instead of hours, and the provider bills each run (about 40 LLM calls), so set `RAEM_RUN_TOKEN` on a public site.

How it works: the notebook still runs cell by cell exactly as written. In the server's in-memory copy each `ChatOllama(...)` becomes `RAEMCloudChat(...)`, a ChatOllama subclass whose text comes from the cloud model, so the notebook's Fact Guard, analyst cache, debates and all later steps work unchanged. The notebook file is never edited.

Memory: the notebooks' episodic memory and news search use Ollama's `nomic-embed-text`. When the server can reach it, every run (cloud or Ollama) uses the notebooks' own memory store. Without it, the run uses chromadb's built-in embedding model in a separate store, `raem_chroma_db_local`, because vectors from two embedding models cannot be mixed. That model (about 80 MB) is downloaded into the Docker image at build time, or on the first run otherwise (up to `RAEM_EMBED_DOWNLOAD_TIMEOUT`, 15 minutes). If it cannot be downloaded, the run still finishes: memory then matches by shared words (`raem_chroma_db_hash`). At the start of each such run the notebooks' past episodes and news (`raem_chroma_db`) are copied into that store, embedded again with its model (only what it lacks), so past memory still counts; the notebooks' own store keeps its contents. The daily prediction memory (`chroma_memory/`) uses number vectors, not an LLM, and works everywhere.

## Running models from the page

- **Portfolio → Run the forecast.** Choose models, press Run. The server runs `export_ticker.py` on its CSV price files, the chart and tables switch to the new run, and the run is stored. A run with the six default models also updates the saved page.
- **Forecast → Run.** On the hosted site Run fits the models now (all 14 by default, as in the forecast notebook, or the best 6), shows the progress, then the All models tab: every model's day-1 and day-7 prediction, its error vs "no change", and ★ on the two models the Ensemble averages.
- **AI Analysis.** Choose a company and an LLM (an Ollama model or a cloud model), press **Run AI analysis for <today>**: the notebook runs for today's date (its `TRADE_DATE`, in the server's time zone, `TZ=Asia/Dhaka` by default in Docker). The server runs that company's notebook top to bottom with the model you chose, shows each cell as it runs, shows each agent as it finishes (the notebook saves its log after every agent, and the page reads it while the run goes), then the **Decision**: BUY, HOLD or SELL in large type with the portfolio manager's rating, the verifier, the entry price, the 7-day outlook and why; then the **AI debate** (bull vs bear, round by round, with the facilitator's verdict) and the **trader and risk debate**, and every analyst, Graph Analyst, Forecasting Agent, Prediction Engine, Verifier, Position Sizing and the 10-day scenario. The model list shows only what the server has connected: the Ollama models it can reach and the cloud providers whose key is in `.env`. Runs go one company at a time.
- **Hold.** While an AI analysis runs, **Hold** stops it and keeps what it has done: every LLM answer is saved the moment it arrives (`backend/storage/llm_cache/`). **Resume** (or Run again later the same day, same company and model) runs the notebook again from the top, but every LLM call with exactly the same prompt as before (same prices, news and earlier answers) gets the saved answer at once, so it continues where it stopped; a step whose market data changed asks the model again. If the server restarts during a run, that run is held too, and a run that stopped with an error offers **Continue** with its saved answers. When a run finishes its saved answers are deleted, so "Run again" then gives a fresh analysis.
- **The AI analysis moves the prediction a little.** The latest AI rating (Buy +1, Overweight +½, Hold 0, Underweight −½, Sell −1) times the stock's typical 7-day move is the AI view; the AI-adjusted forecast is 80% the models' Ensemble and 20% that view (10–35% once the AI has five graded calls, by how often it was right). A session more than 10 days older than the prices does not count. It shows on Home (each card and the "With AI" column), the Forecast summary, Portfolio (chart line, P&L and the model table), the Return Calculator ("AI-adjusted") and the AI Analysis page.
- **Portfolio** (was Paper Trading): virtual orders with equity, total P&L (realized and open), projected day 7, cash, return, max drawdown, win rate and exposure, an **Indicators** card for the selected stock (RSI, MACD, 20/50-day averages, Bollinger %B, volatility, 52-week range, the daily signal, the Ensemble and the AI decision), the portfolio curve and the model outputs.
- **Portfolio keeps its run.** The model table under the chart lists every model of the forecast shown, and the newest run of each company stays on the page after a reload or on another day, until newer prices or a newer run replace it.
- Runs wait in one queue, so a second request waits for the first. An identical request joins the run already going.
- Closing or reloading the page does not stop a run. Open the page again and it shows the progress of runs still going, then their results.

A copy of the page without the server (a static host, the claude.ai artifact, the file opened from disk) shows the saved results and the latest saved AI analysis of each company; models run only on the hosted site.

## Refresh everything

```bash
python backend/refresh.py                  # top up prices, re-run all 8 stocks, extra pages, rebuild
python backend/refresh.py AAPL IBM         # only some stocks
python backend/refresh.py --no-top-up      # skip downloading new prices
```

When the research project changes (new notebook runs, code, cache), bring the engine up to date first:

```bash
python backend/tools/sync_engine.py /path/to/research/project
python backend/refresh.py
```

## API

| Method and path | Returns |
|---|---|
| `GET /` | the website |
| `GET /api/health` | status, date, Ollama reachable, queue length, whether a token is needed |
| `GET /api/models` | forecast models (and the default six), LLMs per provider (`llm_providers`), companies with a notebook |
| `GET /api/tickers`, `GET /api/tickers/{t}` | saved results per company |
| `GET /api/extra` | data for the extra pages |
| `POST /api/forecast` `{"ticker", "models": [...], "force"}` | stored run, or a new queued run |
| `POST /api/analysis` `{"ticker", "llm_provider", "llm_model", "force"}` | stored run, a resumed held run, or a new queued notebook run (`llm_provider`: ollama, anthropic, kimi, openai, google, groq) |
| `POST /api/runs/{id}/hold` | hold a queued or running AI analysis; the next request for it resumes it |
| `GET /api/jobs/{id}` | progress: status, current step or notebook cell, log tail, place in queue |
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
| `TZ` | time zone that decides "today" for runs and the notebooks' `TRADE_DATE` (default `Asia/Dhaka` in Docker; hosts usually run on UTC, a day behind Bangladesh after 18:00 UTC) |
| `RAEM_DB`, `RAEM_NOTEBOOK_TIMEOUT`, `RAEM_FORECAST_TIMEOUT`, `RAEM_CELL_TIMEOUT` | optional: database file and time limits |

## Put it on GitHub and host it

**Railway + Vercel, or Hugging Face Spaces (free):** step by step in [DEPLOY.md](DEPLOY.md). Railway runs the app with one volume and Vercel serves the page and forwards `/api`; a Space runs the whole app on a free 16 GB CPU (`python backend/tools/deploy_hf.py <hf-username>/ream`) and keeps ChromaDB memory, runs and logs in a private Hugging Face dataset, so they survive restarts.

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

- **AI analysis needs an LLM the server can reach.** Easiest online: a cloud API key in `.env` (Groq has a free tier). For local models, free web hosts cannot run a 7B model; use a VPS with 8 GB RAM or more and install Ollama on it (same machine: `docker compose` already points to `host.docker.internal`), or point `OLLAMA_BASE_URL` at another machine running Ollama. Even with a cloud LLM, Ollama with just `nomic-embed-text` (small, runs on any CPU) keeps the notebooks' own memory store. Without any LLM, forecasts still run and stored AI runs still open.
- **Set `RAEM_RUN_TOKEN` on a public site.** Each run uses a CPU core for minutes; without a token anyone can queue runs.
- **Use a persistent disk** for `backend/storage` (run database), `backend/engine/*` and `backend/data`, or a redeploy forgets runs and refreshed prices. The compose file mounts them.
- The image includes PyTorch (CPU) and is a few GB; give the server at least 2 GB of RAM for forecasts alone.
- Serve over HTTPS (most hosts do it for you).

## Notebook features on the site

| Notebook part | On the site |
|---|---|
| Fundamentals, Market, Macro regime, News (with RAG), Sentiment analysts | AI Analysis pipeline tabs, News and Fundamentals pages |
| Graph Analyst, Forecasting Agent (10-day) | AI Analysis pipeline tabs (sessions from the current notebooks) |
| Bull vs bear debate and facilitator, Post-mortem, Trader, Risk debate and facilitator, Portfolio manager | AI Analysis pipeline tabs and final decision |
| Prediction Engine (5 models), 7-day forecast graph (14 models) | Forecast page, Portfolio run, AI Analysis tabs |
| Decision Verifier, Position Sizing (rule + RL overlay), 10-day scenario | AI Analysis pipeline tabs |
| Episodic memory, regime reflection, outcome backfill | Memory page; used inside each notebook run |
| Trading log, 7-day hit rate, forecast agent and reviewer | Track record, Reviewer tab, Backtest, Return Calculator |

Older saved sessions (before the notebooks gained the newer agents) show only the stages they logged; every new run shows all of them.

## Limits

- Cloud models are verified through the OpenAI-compatible path; each provider's own quirks (rate limits, model names) can differ. Reasoning models (o-series, gpt-5) do not take the notebooks' temperature and output cap; the runner retries without a temperature, but prefer the listed models.
- `backend/engine/` is a copy of the research project; changes there reach the site after `tools/sync_engine.py`.
- The four DSE stocks get new prices only from the notebooks' daily logs (no automatic download).
- The daily BUY/HOLD/SELL comes from `predictor_lib`'s 5-model pipeline, separate from the 7-day forecast.
