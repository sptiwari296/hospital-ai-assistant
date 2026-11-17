"""FastAPI application.

Run:  .venv\\Scripts\\uvicorn app.api.main:app --port 8000
Docs: http://localhost:8000/docs
"""
import os
import time
import uuid

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from starlette.concurrency import run_in_threadpool

from app import audit
from app.api.routes import audit as audit_routes
from app.api.routes import chat, documents, patient
from app.config import get_settings
from app.db import connect

get_settings()  # load .env before anything reads environment variables

app = FastAPI(title="Hospital Patient Services Assistant", version="0.7.0",
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
    with connect() as conn:
        chunks = conn.execute("SELECT count(*) AS n FROM searchable_chunks").fetchone()["n"]
    return {"status": "ok", "searchable_chunks": chunks, "llm_provider": get_settings().llm_provider}
