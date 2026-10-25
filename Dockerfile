# Build from THIS folder (web/ is self-contained):
#   docker build -t raem-web .
#   docker run -p 8000:8000 -e RAEM_REFRESH_TOKEN=change-me raem-web
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONIOENCODING=utf-8
# "Today" for runs and the notebooks' TRADE_DATE: Bangladesh time, as on the research PC (override with TZ in .env)
ENV TZ=Asia/Dhaka
# One home for caches (chromadb's model, matplotlib, Jupyter) whichever user runs the container: root on Railway and
# docker compose, user 1000 on Hugging Face Spaces
ENV HOME=/home/user
RUN apt-get update && apt-get install -y --no-install-recommends tzdata && rm -rf /var/lib/apt/lists/* \
    && mkdir -p /home/user
WORKDIR /app

COPY backend/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir --extra-index-url https://download.pytorch.org/whl/cpu -r /tmp/requirements.txt
# chromadb's embedding model (about 80 MB): the notebooks' memory uses it when the server has no Ollama nomic-embed-text
RUN python -c "from chromadb.utils.embedding_functions.onnx_mini_lm_l6_v2 import ONNXMiniLM_L6_V2 as M; M()._download_model_if_not_exists()" \
    || echo "chromadb embedding model not downloaded; a run fetches it on first use"

# backend/ (API, engine, exporter, data) and frontend/ (page sources and built page)
COPY . .
RUN python backend/refresh.py --build-only \
    && chmod -R a+rwX /app /home/user
# the app writes its database, prices, logs and rebuilt page under /app, so any user may write there (Spaces run as 1000)

EXPOSE 8000
# hf_sync.py restores the saved data from a Hugging Face dataset when RAEM_HF_DATASET and HF_TOKEN are set (a Space);
# persist.py moves the changing folders onto the volume when RAEM_DATA_DIR is set (Railway); the page is then rebuilt
# from that data. Both do nothing when unset. One worker only: the run queue and SQLite live in this one process.
CMD ["sh", "-c", "python backend/tools/hf_sync.py restore && python backend/tools/persist.py && python backend/refresh.py --build-only && uvicorn app.main:app --app-dir backend --host 0.0.0.0 --port ${PORT:-8000} --workers 1"]
