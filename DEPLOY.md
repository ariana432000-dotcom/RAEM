# Deploy: Railway (app) + Vercel (page), or Hugging Face Spaces (free)

```
browser ──> Vercel  (frontend/dist/index.html)
              │  /api/*  is forwarded (vercel.json rewrite)
              ▼
            Railway (Dockerfile: FastAPI + run queue + notebooks + models)
              └─ Volume /data  (run database, prices, trading logs, memory, saved results)
```

Railway runs everything: the API, forecasts, the notebooks with Claude or Kimi, and the database. Vercel serves the page and forwards `/api/...` to Railway, so for the browser it is one site: no CORS settings, no server address to type. You can also use the Railway domain alone; Vercel is optional.

## Free option: Hugging Face Spaces

A Space runs the same Docker image on Hugging Face's free CPU (2 vCPU, 16 GB of memory). No Vercel needed.

1. huggingface.co → sign up. **Settings → Access Tokens → Create new token**, type **Write**, copy it (`hf_...`).
2. In PowerShell, from this folder:
   ```powershell
   $env:HF_TOKEN = "hf_..."
   python backend/tools/deploy_hf.py <hf-username>/ream
   ```
   The script creates the Space (Docker) and a private dataset `<hf-username>/ream-data` for its data, sets `PORT=8000`,
   `TZ` and `RAEM_HF_DATASET`, and uploads the files git would push.
3. The Space page → **Settings → Variables and secrets → New secret**: `RAEM_RUN_TOKEN`, `HF_TOKEN` (the same write token:
   it lets the Space save its data), and `ANTHROPIC_API_KEY` and/or `MOONSHOT_API_KEY`. (Or set them in the same PowerShell
   window first and add `--secrets` to the command.)
4. Wait for the build (the **Logs** tab, 10 to 20 minutes), then open `https://<hf-username>-ream.hf.space`.
5. Later changes: run step 2 again; the Space rebuilds.

**Data stays, for free.** A Space's own disk starts over on every restart, so the app keeps its changing data in the
private dataset: after every finished run it uploads ChromaDB memory, the run database, held runs, prices, trading logs
and saved results (only the changed parts travel), and on start it downloads them again (`backend/tools/hf_sync.py`).
A file you change in git and deploy wins over the saved copy. Free Spaces sleep after 48 hours without visitors; the next
visit wakes them in a minute or two.

## 0. Before you start

- Put this `web` folder on GitHub. Either push it as the repository root, or push the whole research project and set **Root Directory** to `web` in Railway and in Vercel.
- Have an API key: `ANTHROPIC_API_KEY` (Claude) and/or `MOONSHOT_API_KEY` (Kimi).
- Make a long random string for `RAEM_RUN_TOKEN` (e.g. `python -c "import secrets; print(secrets.token_urlsafe(32))"`).

## 1. Railway (the app)

1. railway.com → **New Project** → **Deploy from GitHub repo** → pick the repository.
   If the repository is the whole project: service **Settings → Source → Root Directory** = `web`.
   Railway finds the `Dockerfile` (and `railway.json`: health check `/api/health`, one replica).
2. **Settings → Volumes → New Volume**, mount path **`/data`**.
3. **Variables** (Raw Editor):
   ```
   RAEM_DATA_DIR=/data
   RAEM_RUN_TOKEN=<your long random string>
   ANTHROPIC_API_KEY=<key>          # Claude, and/or:
   MOONSHOT_API_KEY=<key>           # Kimi (China account: RAEM_KIMI_BASE_URL=https://api.moonshot.cn/v1)
   TZ=Asia/Dhaka
   ```
   Optional: `RAEM_ANTHROPIC_MODELS`, `RAEM_KIMI_MODELS`, `RAEM_REFRESH_TOKEN`.
4. **Deploy.** The first build installs PyTorch and takes about 10 to 15 minutes. Then **Settings → Networking → Generate Domain**,
   e.g. `https://raem-production.up.railway.app`.
5. Check it: open `https://<domain>/api/health` (should show `"status":"ok"`), then `https://<domain>/` (the site).
   On the AI Analysis page the model list shows only the providers whose key you set.

Railway notes:
- Give the service at least **4 GB of memory** (Settings → Resources): the forecast models and a notebook run use 2 to 3 GB.
- Keep **one replica** and leave **App Sleeping off**; a sleeping or second container would stop or split the run queue.
- The volume keeps the run database, held runs, AI sessions, prices, trading logs and memory across redeploys
  (`backend/tools/persist.py`). A file you change in git and push replaces the volume copy on the next deploy.
- No Ollama on Railway: the AI runs with Claude or Kimi. The notebooks' memory then uses chromadb's built-in embeddings
  (downloaded into the image at build time) in its own store, and each run first copies the notebooks' past episodes and news
  from `raem_chroma_db` into it (embedded again), so past memory still counts. Both ChromaDB stores ship in the repository
  (`backend/engine/chroma_memory`, `backend/engine/notebooks/raem_chroma_db`) and live on the volume.

## 2. Vercel (the page)

1. In `vercel.json` replace `https://YOUR-APP.up.railway.app` with your Railway domain, commit and push.
2. vercel.com → **Add New → Project** → import the repository.
   If the repository is the whole project: **Root Directory** = `web`. Framework preset: **Other** (vercel.json sets the rest:
   no build, output `frontend/dist`).
3. **Deploy.** Open `https://<project>.vercel.app/#ai`: the footer says "Live server" and Run works through Railway.

When the rewrite is wrong or Railway is down, the Vercel page still shows the saved results (no runs).

## 3. After deploy

- Open **Settings** on the site, paste `RAEM_RUN_TOKEN` into **Access token** (saved in that browser). Without it the server refuses runs.
- First test: Forecast → a company → Run (about 2 to 3 minutes for all 14 models); then AI Analysis → a company → a Claude or Kimi model → Run.
- New code or data: push to GitHub; Railway and Vercel redeploy by themselves. Rebuild the page first if you changed
  `frontend/src` or `backend/data` (`python backend/refresh.py --build-only`) and commit `frontend/dist/index.html`.

## Troubleshooting

| What you see | Why, and what to do |
|---|---|
| Railway health check fails | Look at the deploy logs. `persist:` lines show the volume setup; check `RAEM_DATA_DIR=/data` matches the volume mount path. |
| "No LLM is connected on this server" | No API key variable set (or a typo). Add `ANTHROPIC_API_KEY` / `MOONSHOT_API_KEY` and redeploy. |
| Run says 401 | Paste `RAEM_RUN_TOKEN` in Settings → Access token. |
| Vercel page shows no Run buttons | The `vercel.json` destination is not your Railway domain, or it was not pushed. Open `https://<project>.vercel.app/api/health`: it must answer like the Railway one. |
| A run fails with an API error | The key, the model name (`RAEM_*_MODELS`) or the provider's rate limit; the run log on the AI page shows the reason. |
