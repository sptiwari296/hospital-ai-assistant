"""FastAPI application.

Run:  .venv\\Scripts\\uvicorn app.api.main:app --port 8000
Docs: http://localhost:8000/docs
"""
import os
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from starlette.concurrency import run_in_threadpool

from app import audit
from app.api.monitoring import log, metrics_payload, observe_request
from app.api.routes import audit as audit_routes
from app.api.routes import chat, documents, patient
from app.config import get_settings
from app.db import connect
from app.ingestion.embeddings import get_embedder
from app.rag.reranker import get_reranker

get_settings()  # load .env before anything reads environment variables
_state = {"models_ready": False}


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Warm up the embedding and reranking models at startup, so the first user isn't slow."""
    def warm():
        get_embedder().embed_query("warm up")
        get_reranker().score("warm up", ["warm up"])
        _state["models_ready"] = True
    await run_in_threadpool(warm)
    log.info("startup", extra={"fields": {"models_ready": True, "llm_provider": get_settings().llm_provider}})
    yield


app = FastAPI(title="Hospital Patient Services Assistant", version="0.9.0", lifespan=lifespan,
              description="Grounded, secure and auditable assistant (synthetic data only).")

SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",                  # patient data must never sit in browser or proxy caches
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
}


@app.middleware("http")
async def trace_secure_and_audit(request: Request, call_next):
    """One trace id per request, security headers on every response, audit row per authenticated call."""
    started = time.perf_counter()
    request.state.trace_id = f"HSP-{uuid.uuid4().hex[:8].upper()}"
    response = await call_next(request)
    response.headers["X-Trace-Id"] = request.state.trace_id
    for name, value in SECURITY_HEADERS.items():
        response.headers.setdefault(name, value)
    if not request.url.path.startswith(("/docs", "/redoc", "/openapi")):
        response.headers.setdefault("Content-Security-Policy", "default-src 'none'; frame-ancestors 'none'")
    if os.getenv("COOKIE_SECURE", "false").lower() == "true":
        response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")

    user = getattr(request.state, "audit_user", None)
    route_path = getattr(request.scope.get("route"), "path", "unmatched")   # template: no ids, no query string
    observe_request(request.method, route_path, response.status_code, time.perf_counter() - started,
                    request.state.trace_id, user.role if user else None)
    if (user or getattr(request.state, "audit_requested_patient", None)) \
            and not getattr(request.state, "audited_by_route", False):
        route = request.scope.get("route")
        endpoint = f"{request.method} {getattr(route, 'path', request.url.path)}"
        await run_in_threadpool(
            audit.record_request, trace_id=request.state.trace_id, user_id=user.user_id if user else None,
            role=user.role if user else None, endpoint=endpoint, http_status=response.status_code,
            patient=getattr(request.state, "audit_patient", None),
            requested_patient=getattr(request.state, "audit_requested_patient", None),
            latency_ms=int((time.perf_counter() - started) * 1000))
    return response


# Added after the middleware above, so CORS wraps it (CORS headers also on error responses)
app.add_middleware(CORSMiddleware, allow_origins=[os.getenv("FRONTEND_ORIGIN", "http://localhost:3000")],
                   allow_credentials=True,  # the session cookie
                   allow_methods=["GET", "POST", "PATCH"],
                   allow_headers=["Authorization", "Content-Type", "X-Requested-With"],
                   expose_headers=["X-Trace-Id"])

for module in (chat, patient, documents, audit_routes):
    app.include_router(module.router)


@app.get("/api/health")
def health():
    """Liveness: the process is up."""
    return {"status": "ok"}


@app.get("/api/ready")
def ready(response: Response):
    """Readiness: database reachable, knowledge base indexed, models loaded, LLM key configured."""
    checks = {"models_loaded": _state["models_ready"]}
    try:
        with connect() as conn:
            checks["searchable_chunks"] = conn.execute("SELECT count(*) AS n FROM searchable_chunks").fetchone()["n"]
        checks["database"] = True
    except Exception:  # noqa: BLE001 — any DB failure means not ready
        checks["database"], checks["searchable_chunks"] = False, 0
    provider = get_settings().llm_provider
    checks["llm_key_configured"] = bool(os.getenv("GROQ_API_KEY") if provider == "groq" else os.getenv("ANTHROPIC_API_KEY"))
    ok = checks["database"] and checks["models_loaded"] and checks["searchable_chunks"] > 0
    response.status_code = 200 if ok else 503
    return {"status": "ready" if ok else "not_ready", "checks": checks, "llm_provider": provider}


@app.get("/api/metrics", include_in_schema=False)
def metrics():
    body, content_type = metrics_payload()
    return Response(body, media_type=content_type)
