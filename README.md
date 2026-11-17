# Hospital Patient Services Assistant

A grounded, secure and auditable AI assistant for hospital patient services (HCLTech hackathon use case).
**All data is synthetic.**

## Status
| Phase | Deliverable | Status |
|---|---|---|
| 0 | Requirements & design: `docs/01_requirements_and_design.md` | ✅ |
| 1 | Database (Postgres + pgvector) and sample documents | ✅ |
| 2 | Document ingestion pipeline: `app/ingestion/` (26 tests passing) | ✅ |
| 3 | RAG: hybrid retrieval, reranking, cited answers, conflict rules (`app/rag/`) | ✅ |
| 4 | Deterministic tools: appointments, insurance, labs, medications, billing, care coordination (`app/tools/`) | ✅ |
| 5 | AI orchestrator: intent classification, validated routing, multi-step plans, confirmations (`app/orchestrator/`) | ✅ |
| 6 | FastAPI backend with JWT login + Next.js frontend (chat, dashboard, admin uploads) | ✅ |
| 7 | Security: Row-Level Security, least-privilege DB role, encryption at rest, redacted audit, cookie auth, CSRF, lockout, rate limits, injection quarantine (`docs/SECURITY.md`) | ✅ |
| 8–9 | Evaluation, deployment | – |

## Run the full application
```powershell
docker compose up -d                                             # database
.venv\Scripts\python scripts\setup_security.py                   # once: API DB role password + encrypt ID numbers
.venv\Scripts\python scripts\set_demo_passwords.py               # once: hashes DEMO_PASSWORD from .env for all demo users
.venv\Scripts\uvicorn app.api.main:app --port 8000               # API  -> http://localhost:8000/docs
cd web; npm install; npm run dev                                 # UI   -> http://localhost:3000
```
Log in as `rahul.sharma` (patient), `nurse.anita` (staff), or `admin_01` (documents) with the `DEMO_PASSWORD` from `.env`.

## Quick start
```powershell
copy .env.example .env            # then set a password
docker compose up -d              # starts Postgres with schema + seed data
py -3.12 -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\python scripts\build_documents.py     # builds PDFs/DOCX
Get-Content db\verify.sql -Raw | docker exec -i hospital_db psql -U hospital_app -d hospital
.venv\Scripts\python scripts\ingest_kb.py           # extract → chunk → embed → index all documents
.venv\Scripts\python scripts\search_demo.py "What are ICU visiting hours?"
.venv\Scripts\python scripts\eval_retrieval.py      # retrieval + not_found gate evaluation (no LLM cost)
.venv\Scripts\python scripts\ask.py "What are the ICU visiting hours?"   # needs ANTHROPIC_API_KEY in .env
.venv\Scripts\python scripts\tools_demo.py          # every operational tool, per synthetic patient (no LLM)
.venv\Scripts\python scripts\chat.py rahul.sharma --demo   # full assistant conversation (needs GROQ_API_KEY)
.venv\Scripts\python scripts\chat.py rahul.sharma          # interactive chat
.venv\Scripts\python -m pytest                      # tests (DB must be running)
```
How it was built, with design decisions and the bugs we fixed: see `docs/BUILD_LOG.md`.

## Layout
```
docs/                     design documents
db/init/                  01_schema.sql, 02_seed.sql (run on first DB start)
db/verify.sql             sanity-check queries
data/knowledge_base/      source/ (Markdown), files/ (PDF, DOCX), metadata.json
scripts/                  helper scripts
```
