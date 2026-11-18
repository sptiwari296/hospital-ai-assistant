"""Smoke test of a deployed stack through the public HTTPS entry point.

Run:  .venv\\Scripts\\python scripts\\smoke_test.py [https://localhost]
Uses the demo account and DEMO_PASSWORD from .env. verify=False only because Caddy's local
CA is not in the OS trust store (see docs/DEPLOYMENT.md).
"""
import os
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))
from app.config import get_settings  # noqa: E402

BASE = sys.argv[1] if len(sys.argv) > 1 else "https://localhost"
results = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append(ok)
    print(f"{'PASS' if ok else 'FAIL'}  {name}{('  -> ' + detail) if detail else ''}")


get_settings()
with httpx.Client(base_url=BASE, verify=False, timeout=60) as c:
    r = c.get("/api/ready")
    check("readiness over HTTPS", r.status_code == 200 and r.json()["status"] == "ready", str(r.json().get("checks")))
    check("HTTP redirects to HTTPS", httpx.get(BASE.replace("https", "http") + "/", follow_redirects=False)
          .status_code in (301, 302, 308))
    check("metrics not public", c.get("/api/metrics").status_code == 404)
    check("API docs not public", c.get("/docs").status_code == 404)
    check("HSTS header", "max-age" in r.headers.get("strict-transport-security", ""))
    check("UI served", c.get("/login").status_code == 200)

    login = c.post("/api/auth/login", json={"username": "rahul.sharma", "password": os.environ["DEMO_PASSWORD"]})
    cookie = login.headers.get("set-cookie", "").lower()
    check("login sets Secure HttpOnly cookie", login.status_code == 200 and "secure" in cookie and "httponly" in cookie)
    h = {"X-Requested-With": "hospital-web"}
    labs = c.get("/api/labs?test=HbA1c&limit=1")
    check("patient data via cookie session", labs.status_code == 200 and labs.json()["data"]["latest"]["value"] == 7.2)
    chat = c.post("/api/chat", json={"message": "What are the ICU visiting hours?"}, headers=h)
    body = chat.json()
    check("live chat answer (LLM)", chat.status_code == 200 and body["answer_type"] in ("retrieved_fact", "not_found"),
          f"{body.get('answer_type')}: {body.get('answer', '')[:70]}")
    check("trace id header matches answer", chat.headers.get("x-trace-id") == body.get("trace_id"))
    check("other patient refused", c.post("/api/chat", json={"message": "Show P1002's lab results"},
                                          headers=h).json()["answer_type"] == "refused")

print(f"\n{sum(results)}/{len(results)} checks passed")
sys.exit(0 if all(results) else 1)
