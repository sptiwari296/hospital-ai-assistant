# Deployment and operations

## Architecture

```
                 Internet / browser
                        │  HTTPS :443  (HTTP :80 → redirect)
               ┌────────▼─────────┐
               │  proxy (Caddy)   │  TLS, HSTS, blocks /api/metrics & /docs from outside
               └───┬──────────┬───┘
         /api/*    │          │   everything else
        ┌──────────▼───┐  ┌───▼───────────┐
        │ api (FastAPI)│  │ web (Next.js) │      network: frontend (has internet egress)
        │ + models     │  └───────────────┘
        └──────┬───────┘
               │ least-privilege role hospital_api, Row-Level Security
        ┌──────▼───────┐   ┌──────────────┐
        │ db (Postgres │   │ prometheus   │      network: backend (internal: NO internet)
        │  + pgvector) │   │ (optional)   │
        └──────────────┘   └──────────────┘
   init (one-shot): role password → encrypt IDs → demo passwords → ingest knowledge base
```

- **One origin.** The browser only talks to `https://localhost`: `/api/*` goes to the API and everything else to the UI. No CORS is needed, and the session cookie is `Secure` + `HttpOnly` + `SameSite=Strict`.
- **The database is unreachable from outside:** no published port, and its network has no internet.
- **Images:** the API runs as uid 10001 with the embedding and reranker models baked in at build time (no runtime downloads). The web image is a Next.js *standalone* build running as uid 10001.
- **Secrets** come only from `.env` via `env_file` and are never copied into images (`.dockerignore`).

## Deploy from scratch
```powershell
copy .env.example .env      # fill in every value (see below)
docker compose -f docker-compose.prod.yml up -d --build
# optional monitoring
docker compose -f docker-compose.prod.yml --profile monitoring up -d
```
Start-up order is enforced by health checks: `db` healthy → `init` completes successfully → `api` ready (DB + models + indexed documents) → `proxy`.

Open **https://localhost**. Caddy's local CA isn't trusted by browsers until you trust it. To do that, export the root certificate from the `caddy_data` volume (`/data/caddy/pki/authorities/local/root.crt`) into your OS trust store, or accept the warning for a demo. For a real domain, replace `localhost` in `deploy/Caddyfile` with the domain and remove `tls internal`, and Caddy obtains a Let's Encrypt certificate automatically.

| `.env` key | Purpose |
|---|---|
| `POSTGRES_DB/USER/PASSWORD` | Database owner (migrations / setup only) |
| `API_DB_USER`, `API_DB_PASSWORD` | Least-privilege role the API uses |
| `JWT_SECRET` | ≥ 32 random characters |
| `FIELD_ENCRYPTION_KEY` | Fernet key for identity numbers (`Fernet.generate_key()`) |
| `DEMO_PASSWORD` | Password for the synthetic demo users |
| `GROQ_API_KEY` (or `LLM_PROVIDER=claude` + `ANTHROPIC_API_KEY`) | LLM |
| `HOSPITAL_TIMEZONE` | e.g. `Asia/Kolkata`: "effective today" uses the hospital's date |

## Monitoring
| Signal | Where |
|---|---|
| Liveness | `GET /api/health` |
| Readiness | `GET /api/ready`: database, searchable chunks > 0, models loaded, LLM key configured (503 when not ready; used by the container health check) |
| Metrics | `GET /api/metrics` (internal only), Prometheus at http://127.0.0.1:9090: `hospital_http_requests_total{route,status}`, `hospital_http_request_seconds` (latency histogram → p50/p95), `hospital_chat_answers_total{answer_type,intent}`, `hospital_llm_unavailable_total`, `hospital_logins_total{outcome}`, `hospital_ingestion_jobs_total{status}` |
| Logs | `docker compose -f docker-compose.prod.yml logs api`: one JSON line per request with `trace_id`, method, **route template**, status, latency and role. No query strings and no patient data |
| Audit | `audit_logs` table (append-only for the API), `GET /api/audit/{trace_id}` |

Useful alerts: readiness failing; 5xx rate; p95 latency above 8 s; `hospital_llm_unavailable_total` rising (LLM quota or outage); `hospital_logins_total{outcome="locked"}` spikes (password guessing); any `refused` spike for `other_patient` (probing).

## Runbook
- **Live document updates (R11):** sign in as a document admin → *Documents* → upload. Ingestion runs in the background and the document is searchable seconds later. Retiring or approving takes effect on the next question. Uploaded files persist in the `kbfiles` volume. No restart is needed.
- **Backups:** `docker compose -f docker-compose.prod.yml exec db pg_dump -U $POSTGRES_USER -Fc hospital > backup.dump`; restore with `pg_restore`. Encrypted ID numbers stay encrypted in the dump, so keep `FIELD_ENCRYPTION_KEY` backed up **separately**.
- **Key rotation:** JWT: change `JWT_SECRET` and restart `api` (all sessions end). Field key: decrypt with the old key and re-encrypt with the new one (Fernet `MultiFernet`), then restart.
- **Upgrade:** `git pull` → `docker compose -f docker-compose.prod.yml up -d --build`. New SQL migrations go into `db/init/` and must also be applied to existing databases (each file is idempotent).
- **Release gate:** CI (`.github/workflows/ci.yml`) runs the full test suite including the 160-case evaluation. A privacy or safety violation fails the build.

## Scaling notes
- The API runs one Uvicorn worker because the rate limiter and the loaded models live in process memory. To scale out, move rate limits to Redis, run several API containers behind the proxy, and keep Postgres as the single source of truth (sessions and pending actions are already stored there).
- Postgres: use a managed instance with encryption at rest, backups and `sslmode=require`.
- LLM: a paid tier removes the free-tier rate-limit spikes seen during evaluation.
