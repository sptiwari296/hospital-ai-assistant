# Hospital Patient Services Assistant

A grounded, secure and auditable AI assistant for hospital patient services (HCLTech hackathon use case).
**All data is synthetic.**

## Status
| Phase | Deliverable | Status |
|---|---|---|
| 0 | Requirements & design: `docs/01_requirements_and_design.md` | ✅ |
| 1 | Database (Postgres + pgvector) and sample documents | ✅ |
| 2 | Document ingestion pipeline: `app/ingestion/` (26 tests passing) | ✅ |
| 3–9 | RAG, tools, orchestrator, app, security, evaluation, deployment | – |

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
