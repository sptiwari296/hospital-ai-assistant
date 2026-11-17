"""FastAPI application.

Run:  .venv\\Scripts\\uvicorn app.api.main:app --reload --port 8000
Docs: http://localhost:8000/docs
"""
import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings
from app.db import connect
from app.api.routes import audit, chat, documents, patient

get_settings()  # load .env before anything reads environment variables

app = FastAPI(title="Hospital Patient Services Assistant", version="0.6.0",
              description="Grounded, secure and auditable assistant (synthetic data only).")
app.add_middleware(CORSMiddleware, allow_origins=[os.getenv("FRONTEND_ORIGIN", "http://localhost:3000")],
                   allow_methods=["GET", "POST", "PATCH"], allow_headers=["Authorization", "Content-Type"])

for module in (chat, patient, documents, audit):
    app.include_router(module.router)


@app.get("/api/health")
def health():
    with connect() as conn:
        chunks = conn.execute("SELECT count(*) AS n FROM searchable_chunks").fetchone()["n"]
    return {"status": "ok", "searchable_chunks": chunks, "llm_provider": get_settings().llm_provider}
