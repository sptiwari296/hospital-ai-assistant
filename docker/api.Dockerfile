# API image: FastAPI + orchestrator + local embedding/reranking models.
# Build from the repository root:  docker build -f docker/api.Dockerfile -t hospital-api .
# The models are downloaded at BUILD time, so containers start without internet access to Hugging Face.
# Layout inside the image mirrors the repository (/srv = repo root): backend/app, scripts, data.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1 \
    HF_HUB_DISABLE_SYMLINKS_WARNING=1 PYTHONPATH=/srv/backend
WORKDIR /srv

COPY backend/requirements.txt backend/requirements.txt
RUN pip install -r backend/requirements.txt

# Bake the local models into the image
RUN python -c "from fastembed import TextEmbedding; from fastembed.rerank.cross_encoder import TextCrossEncoder; \
TextEmbedding('BAAI/bge-small-en-v1.5', cache_dir='/srv/.cache/fastembed'); \
TextCrossEncoder('Xenova/ms-marco-MiniLM-L-6-v2', cache_dir='/srv/.cache/fastembed')"

COPY backend/app backend/app
COPY scripts scripts
COPY data/hospital_documents data/hospital_documents

# Run as an unprivileged user
RUN useradd --uid 10001 --no-create-home appuser \
    && mkdir -p data/uploads && chown -R appuser /srv/data /srv/.cache
USER appuser

EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=5s --start-period=60s --retries=5 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8000/api/ready', timeout=4).status == 200 else 1)"

# One worker: the rate limiter and the loaded models live in process memory (see docs/DEPLOYMENT.md)
CMD ["uvicorn", "app.api.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]
