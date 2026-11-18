"""Monitoring: Prometheus metrics + structured JSON logs.

Metrics are exposed at /api/metrics (scraped by Prometheus on the internal network; the public
reverse proxy blocks that path). Logs are one JSON line per request with the trace id —
method, route TEMPLATE, status, latency and role only: no query strings, no patient data.
"""
import json
import logging
import sys
import time

from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest

HTTP_REQUESTS = Counter("hospital_http_requests_total", "HTTP requests", ["method", "route", "status"])
HTTP_LATENCY = Histogram("hospital_http_request_seconds", "HTTP request latency", ["route"],
                         buckets=(0.05, 0.1, 0.25, 0.5, 1, 2, 4, 8, 16, 32))
CHAT_ANSWERS = Counter("hospital_chat_answers_total", "Chat answers by type", ["answer_type", "intent"])
LLM_UNAVAILABLE = Counter("hospital_llm_unavailable_total", "Answers degraded because the LLM was unavailable")
LOGINS = Counter("hospital_logins_total", "Login attempts", ["outcome"])
INGESTION = Counter("hospital_ingestion_jobs_total", "Document ingestion jobs", ["status"])


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created)) + "Z",
                 "level": record.levelname, "logger": record.name, "msg": record.getMessage()}
        entry.update(getattr(record, "fields", {}))
        return json.dumps(entry)


def configure_logging() -> logging.Logger:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    log = logging.getLogger("hospital")
    log.handlers[:] = [handler]
    log.setLevel(logging.INFO)
    log.propagate = False
    return log


log = configure_logging()


def observe_request(method: str, route: str, status: int, seconds: float, trace_id: str, role: str | None) -> None:
    HTTP_REQUESTS.labels(method, route, str(status)).inc()
    HTTP_LATENCY.labels(route).observe(seconds)
    log.info("request", extra={"fields": {"trace_id": trace_id, "method": method, "route": route, "status": status,
                                          "latency_ms": int(seconds * 1000), "role": role}})


def metrics_payload() -> tuple[bytes, str]:
    return generate_latest(), CONTENT_TYPE_LATEST
