# Hospital Patient Services Assistant

A **grounded, secure and auditable** AI assistant for hospital patient services: hospital policies with citations, a patient's own appointments, labs, insurance, medicines and bills, appointment changes with confirmation, and the multi-step *"Can I proceed with surgery next week?"* workflow.

A personal project by **Sp Tiwari**, designed and built end to end: requirements, data, RAG, tools, orchestration, security, evaluation and deployment. **All data is synthetic.**

## What makes it different from a chatbot
- **Two pathways.** Policy questions are answered by **RAG over approved, currently effective documents** with citations (document, section, version, effective date). Questions about the patient are answered by **deterministic tools** over the hospital database. The LLM never invents eligibility, lab values or action results.
- **The LLM proposes, the backend decides.** The LLM classifies intent into a strict JSON schema; a routing policy validates tools, permissions and arguments. Changes are only *proposed*, and executed after the **user's own confirmation**, then re-read from the database to verify them.
- **Patient isolation in four layers:** a pre-check before any LLM call, tools that accept no patient id, identity taken from the login session, and **Postgres Row-Level Security** under a least-privilege database role.
- **Policy versioning (R4):** superseded and future versions are never used; conflicts are resolved by authority → version → date, otherwise `conflict_flagged`.
- **Every answer is one of 7 types:** `retrieved_fact`, `calculated`, `action_completed`, `clarification_needed`, `not_found`, `refused`, `conflict_flagged`. Each has a trace id and a redacted, append-only audit record.

## Results
| | |
|---|---|
| Evaluation (160 labelled cases, oracle routing) | 100% retrieval hit rate, version correctness, answer, citation, tool and action accuracy; **0 privacy / 0 safety violations** |
| Live red-team (10 attacks with the real LLM) | 10/10 contained, no data leak, no unauthorised action |
| Tests | 140 (unit, RAG, integration, security, release gate) |
| Deployment smoke test over HTTPS | 11/11 |

Details: [`docs/BUILD_LOG.md`](docs/BUILD_LOG.md) (how each phase was built and the bugs found), [`docs/SECURITY.md`](docs/SECURITY.md) (requirement → implementation → test), [`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md), [`data/evaluation_dataset/reports/`](data/evaluation_dataset/reports/).

## Tech stack
FastAPI · Next.js 15 (React 19, Tailwind) · PostgreSQL 17 + pgvector · local embeddings (bge-small) and cross-encoder reranker via fastembed/ONNX · LLM via LangChain + Groq (`gpt-oss-120b`) or Claude (swappable provider) · Pydantic · pytest · Docker + Caddy (HTTPS) · Prometheus

## Repository layout
```
frontend/                  Next.js app
  src/app/                 screens: login, chat, dashboard (records), admin (documents)
  src/components/          NavBar, AnswerCard (answer type, citations, confirm buttons)
  src/services/api.ts      API client (httpOnly cookie session + CSRF header)
  src/hooks/useMe.ts       current user
backend/
  app/api/                 FastAPI: auth, chat, patient data, documents, audit, monitoring
  app/orchestrator/        intent classifier, routing policy, workflows, response composer
  app/rag/                 query → hybrid retrieval → reranking → evidence gate → LLM → citation validator
  app/ingestion/           validation → extraction → cleaning → section-aware chunking → embeddings
  app/tools/               appointments, insurance, labs, medications, billing, care coordination
  app/evaluation/          evaluation harness (oracle + live)
  app/{audit,privacy,injection,crypto}.py   security building blocks
  database/init/           schema, synthetic seed, pgvector, Row-Level Security (01–06)
  requirements.txt
data/
  hospital_documents/      13 synthetic policies (Markdown sources, PDF/DOCX files, metadata.json)
  synthetic_patients/      where the synthetic patient data lives
  evaluation_dataset/      160-case dataset, its builder, and the latest reports
tests/                     unit/ rag/ integration/ security/ evaluation/ (release gate)
scripts/                   setup, ingestion, demos, evaluation, red-team, smoke test
docker/                    api & frontend Dockerfiles, Caddyfile, prometheus.yml
docker-compose.yml         full stack (HTTPS)        docker-compose.dev.yml  database only
```

## Run it
**Full stack in containers (HTTPS):**
```powershell
copy .env.example .env          # fill in the values (see docs/DEPLOYMENT.md)
docker compose up -d --build    # → https://localhost
```

**Local development:**
```powershell
docker compose -f docker-compose.dev.yml up -d               # database
py -3.12 -m venv .venv; .venv\Scripts\pip install -r backend\requirements.txt
.venv\Scripts\python scripts\setup_security.py               # API role password + encrypt ID numbers
.venv\Scripts\python scripts\set_demo_passwords.py
.venv\Scripts\python scripts\ingest_kb.py                    # extract → chunk → embed → index
.venv\Scripts\uvicorn app.api.main:app --app-dir backend --port 8000
cd frontend; npm install; npm run dev                        # → http://localhost:3000
```
Demo users: `rahul.sharma`, `priya.nair`, `arjun.mehta` (patients), `nurse.anita` (staff), `admin_01` (documents). The password is `DEMO_PASSWORD` in `.env`.

**Useful commands**
```powershell
.venv\Scripts\python -m pytest                                # all tests incl. the release gate
.venv\Scripts\python scripts\run_eval.py                      # 160-case evaluation (no LLM cost)
.venv\Scripts\python scripts\chat.py rahul.sharma --demo      # scripted conversation with the real LLM
.venv\Scripts\python scripts\redteam.py                       # live attack suite
.venv\Scripts\python scripts\smoke_test.py                    # checks a deployed stack over HTTPS
```

## Build phases
| Phase | Deliverable |
|---|---|
| 0 | Requirements & design: [`docs/01_requirements_and_design.md`](docs/01_requirements_and_design.md) |
| 1 | Database and synthetic data (6 hospital systems, 4 patient personas, 13 policy documents) |
| 2 | Document ingestion pipeline with version control |
| 3 | RAG: hybrid retrieval, reranking, not-found gate, validated citations, conflict rules |
| 4 | Deterministic tools with two-step confirmed writes |
| 5 | AI orchestrator: intent classification, validated routing, multi-step plans |
| 6 | FastAPI backend + Next.js frontend |
| 7 | Security, privacy and auditability (RLS, encryption, redacted audit, cookie auth, injection defences) |
| 8 | Evaluation: 160 cases, 8 metrics, privacy/safety release gate |
| 9 | Containers, HTTPS deployment, monitoring, CI |
