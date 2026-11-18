# Build Log: how this project was built

A phase-by-phase record of what was built, the key decisions and why, and the problems found along the way.

---

## Phase 0: Requirements & design
**Output:** `docs/01_requirements_and_design.md`

- **Two pathways.** Policy questions → RAG over approved documents. Patient-specific questions → deterministic tools over the database. The LLM never invents eligibility, lab values or action results (brief R5).
- **Mapped the 8 question types** in the brief to a pathway and an `answer_type` (7 types, R9).
- **4 roles.** patient (own data), staff (only patients in `care_assignments`), doc_admin (knowledge base), sys_admin (config/audit).
- **Authorisation lives in the backend.** `patient_id` comes from the login session and never from the chat text or the model.
- **R4 conflict rules.** Drop non-effective documents → higher authority level wins → newer version → later effective date → otherwise `conflict_flagged`.
- **Target maturity: Level 4–5.** One orchestrator plus tools. This follows the brief's "simplest architecture" principle.

## Phase 1: Data & database
**Output:** `docker-compose.yml`, `db/init/01_schema.sql`, `02_seed.sql`, `data/knowledge_base/`

- **Postgres 17 + pgvector in Docker.** One database for relational data and vectors. Reproducible with one command.
- **23 tables model the 6 hospital systems** in the brief (EMR, appointments, LIS, RIS, billing, pharmacy), plus insurance, access control, the document registry and the audit log.
- **Business rules are enforced in the schema itself.** `CHECK` constraints on statuses and roles. A patient user must link to a patient. A partial unique index blocks double-booking a slot. Bill totals must add up.
- **Coverage rules carry `effective_from/to`,** so a 2027 rule can't be applied today.
- **4 synthetic patient personas,** each built to exercise a requirement: a multi-step surgery-readiness case, partial and not-covered insurance, an expired policy with a major drug interaction, and a waiting-period ineligibility.
- **13 synthetic hospital documents (PDF + DOCX)** generated from Markdown by `scripts/build_documents.py`, each with a metadata record. Some are deliberate test traps:
  - a superseded ICU policy (v1 vs v2)
  - a government advisory that overrides the hospital's visitor limit
  - two equal-authority documents that conflict on MRI fasting time (4 h vs 6 h)
  - a future-dated insurance agreement
- **Problem:** `generate_series(interval, interval, interval)` doesn't exist in Postgres, so the container failed on init. **Fix:** generate integers 0..7 and compute `09:00 + n × 30 min`. Found via `docker logs`.

## Phase 2: Document ingestion pipeline
**Output:** `app/ingestion/*`, `db/init/03_knowledge_chunks.sql`, `scripts/ingest_kb.py`, `tests/`

Pipeline (`app/ingestion/pipeline.py`):
`authorise → validate → extract → clean → chunk → embed → store → version control → audit`

| Step | Implementation | Key decision |
|---|---|---|
| Authorise | uploader must be an active `doc_admin` | Rejected attempts are still written to `audit_logs` |
| Validate | extension allow-list + **magic bytes** + size limit + opens cleanly | Rejects renamed files, encrypted PDFs, PDFs with JavaScript/launch actions/embedded files, DOCX with macros, zip bombs, non-UTF-8 text. SHA-256 checksum detects re-uploads |
| Extract | PyMuPDF `get_text("dict")` / python-docx styles / TXT rules | **Headings are detected by font:** a fully bold line larger than body text. The largest size becomes level 1. Page numbers are kept for PDFs |
| Clean | Unicode **NFKC**, strip zero-width characters/soft hyphens, unify bullets, drop page-number lines and headers/footers repeated on ≥ 50% of pages | Never deletes content words, so warnings and exceptions survive |
| Chunk | Section-aware (`chunker.py`) | (1) whole top-level section if it fits, (2) else split at sub-section boundaries, (3) only then at sentence boundaries **with overlap**. Every chunk starts with its heading |
| Embed | `BAAI/bge-small-en-v1.5` via fastembed (local ONNX, 384 dims) | Free, offline, no API key. An `Embedder` abstract class lets an API model be swapped in |
| Store | `document_chunks` (vector + text + section/page) with an HNSW cosine index and a full-text `tsvector` | Document metadata is **joined** from `documents`, not copied, so superseding a document is a one-row update |
| Version control | `refresh_lineage()` + the `searchable_chunks` view | Old versions are kept for audit but marked `superseded`. The view shows only approved, in-force, newest-in-lineage documents, **checked at query time**, so a future version switches on by itself on its effective date (no restart; R11) |

**Chunk size.** The brief suggests 500–800 tokens, but bge-small reads at most **512 tokens** and would silently truncate. So we start at 400 max / 80 overlap. These values are configurable and will be evaluated in Phase 8.

**Results**
- 13 documents → 64 chunks. About 0.2 s per document, after a one-time model download.
- Re-running is idempotent: an unchanged file takes about 40 ms and isn't re-embedded.
- DOC011 (superseded) and DOC008 (effective 2027) are stored but **not searchable**.
- Search check: "ICU visiting hours" → DOC012 v2 §3 (similarity 0.91). An out-of-scope question ("snake bites") tops out at 0.54, which points to a threshold for `not_found`.
- 26 pytest tests: validation attacks, extraction, cleaning, chunk limits and overlap, and live version switching against the real database.

**Problems found and fixed**
1. **Lists flattened in generated PDFs.** Markdown needs a blank line before a list, so "surgery: - CBC - PT/INR" came out as one paragraph. Fixed in the document builder.
2. **Ligatures.** PyMuPDF extracted "Eﬀective" with the single character `ﬀ`, which would break keyword search. Fixed with NFKC normalisation.
3. **Timezone bug** (caught by a test). The Docker database ran on UTC while the hospital is on IST, so a policy "effective today" looked like a future document for 5½ hours each day. Fixed by running Postgres in the hospital timezone (`HOSPITAL_TIMEZONE`).

**Handed to Phase 3**
- The government advisory (authority 1) didn't rank in the top 3 for "ICU visiting hours". Retrieval must also pull in higher-authority documents on the same topic.
- The MRI fasting question returns both the 4 h and 6 h documents, so the conflict must be detected and flagged.
- `not_found` needs a similarity threshold, to be calibrated on an evaluation set.
- The `content_tsv` column is ready for hybrid (keyword + vector) search.
- R11 upload endpoint: Phase 6 will call `ingest_document()` from a FastAPI admin route.

## Phase 3: RAG (knowledge pathway)
**Output:** `app/rag/*`, `scripts/ask.py`, `scripts/eval_retrieval.py`, `eval/rag_eval.json`

Query pipeline (`app/rag/service.py`):
`preprocess → hybrid retrieve (+ metadata filters) → rerank → evidence gate → context → LLM → validate → resolve conflicts → citations`

| Component | Implementation | Why |
|---|---|---|
| Query preprocessing | NFKC, whitespace, length cap, medical synonym expansion ("blood sugar" → glucose/HbA1c) **for keyword search only** | Patients and documents use different words. The embedding and the LLM still see the original question |
| Retriever | **Hybrid:** pgvector cosine search + Postgres full-text OR-search, merged with **Reciprocal Rank Fusion** (k = 60) | Vectors capture meaning. Keywords catch exact terms (HbA1c, PT/INR, Code Blue). RRF merges ranks, so the two score scales never need comparing |
| Metadata filter | Everything reads from the `searchable_chunks` view (approved + in force + newest version), plus optional department / document-type filters | Mandatory rules 1 and 2 are enforced in the database, not trusted to code paths |
| Reranker | Local cross-encoder `ms-marco-MiniLM-L-6-v2` (fastembed) on about 20–40 candidates | It reads question and chunk *together*, so it is far more precise than vector similarity alone |
| Evidence gate | A chunk qualifies if rerank ≥ −4.0 **or** similarity ≥ 0.72. If none qualify → `not_found` **without calling the LLM** | Fail closed, and no tokens are spent on unanswerable questions. Two signals, because the reranker underrates long list-style insurance sections |
| Authority boost | Qualifying chunks from authority level 1–2 documents are always kept | A government advisory must be seen even when it ranks lower |
| Context builder | Fixed system prompt (prompt-cached) + `<evidence id="E1" … authority_level=…>` blocks. Evidence is declared data, not instructions | Cheaper repeated calls, plus a first defence against injected instructions in documents (R8) |
| Generator | `LLMProvider` interface with two implementations, chosen by `LLM_PROVIDER` in `.env`. **`GroqProvider`** (default): `openai/gpt-oss-120b` via LangChain `ChatGroq`, temperature 0, strict JSON-schema output. **`ClaudeProvider`**: `claude-opus-5-5`, structured output, server-side refusal fallback | Structured output guarantees parseable `answer_type / answer / cited_evidence / conflicts`. The interface makes the LLM swappable and lets tests use a fake LLM |
| Citation validator | Deterministic checks: cited labels exist, factual answers cite ≥ 1 source, **every number in the answer appears in the cited evidence**, conflicts follow R4. One retry with feedback, then `not_found` | The LLM only ever writes labels like "E2". **Citations are built from database metadata** (document, section, version, effective date, page), so they cannot be invented |
| Conflict resolver (R4) | The LLM *detects* disagreements, but code *decides*: authority → version → effective date → otherwise `conflict_flagged` | Hierarchy is a rule, not a judgement call. The answer must cite the winning source |

**Retrieval evaluation** (free, no LLM): `scripts/eval_retrieval.py` on 25 questions (19 answerable, 6 out of scope)
- **Gate accuracy: 25/25 (100%).** Retrieval hit rate: **18/19 (95%).** Superseded/future documents retrieved: **0.**
- Score separation: unanswerable questions reach at best rerank −8.4 / similarity 0.64, while answerable ones pass with clear margins.
- Known weak spots, kept honest rather than tuned away:
  - q11 (cosmetic surgery) finds only one of the two insurers' exclusion sections.
  - q17 (fire) passes only on similarity 0.72, right at the threshold.

**Tests:** 48 in total. 15 unit tests (fusion, gate, R4 rules, validator). 7 end-to-end tests on the real knowledge base with a **scripted fake LLM** covering each path: not_found without an LLM call, metadata citations, an invented citation (retried, then failed closed), a wrong number (corrected on retry), the advisory overriding the hospital policy, an equal-authority conflict flagged, and the department filter.

**Problems found and fixed**
1. **AND keyword search was useless.** `websearch_to_tsquery` requires every word, so it matched nothing for 5 of 7 test questions. Switched to OR over stemmed lexemes, with ranking doing the rest.
2. **The vector-only top 5 missed the government advisory** for "ICU visiting hours". Fixed with the reranker plus an authority boost.
3. **Validator ordering bug** (caught by a test). Numbers were checked before the other side of an unresolved conflict was added to the citations, so "6 hours" from the second document was wrongly treated as made up. Conflicts are now resolved first.

**Live check with Groq gpt-oss-120b** (`scripts/ask.py`), about 2–7 s per answer:

| Question | Result |
|---|---|
| ICU visiting hours | `retrieved_fact`: 10:30–11:30 and 17:00–19:00, cites DOC012 v2 §3 (superseded v1 hours never appear) |
| ICU visitors at a time | **1 visitor**. The government advisory (DOC013, authority 1) overrides the hospital's 2. The conflict is resolved by code, and both sources are cited |
| MRI fasting with contrast | `conflict_flagged`: 4 h vs 6 h, both documents cited, "confirm with the department" |
| Knee replacement, Northwind Gold | 80% up to ₹3,50,000, pre-authorisation, 24-month wait, from v2. The future v3 (90%) is correctly ignored |
| Pre-op tests for knee replacement | Full list with validity windows and the HbA1c < 8% rule for diabetics (DOC003 §4) |
| Liver transplants | `not_found` without any LLM call |

**More problems found and fixed**
4. **LangChain needs a `title` on a raw JSON schema** for structured output ("Unsupported function"). Added `title`/`description` on the Groq path.
5. **Non-breaking hyphens.** The model wrote "pre‑authorisation" with U+2011, which consoles drop ("pre authorisation"). Answers are now normalised to plain hyphens and spaces.
6. **The overridden source wasn't cited.** The ICU visitor answer cited only the advisory. Resolved conflicts now cite both the winning and the overridden document; the conflict record says which one applies.

**Still open for Phase 8:** a full answer-quality evaluation with the real LLM (accuracy, citation accuracy and latency p50/p95 over the whole eval set).

## Phase 4: Deterministic healthcare tools (operational pathway)
**Output:** `app/tools/*`, `app/clock.py`, `db/init/04_tools.sql`, `scripts/tools_demo.py`, `tests/test_tools.py`

**Design rules applied to every tool**
1. **Identity from the session, never from inputs.** `RequestContext` is built by the backend from the login. Patients see only their own records, and staff only patients in `care_assignments`. No tool input model has a `patient_id` field: a test enforces this, and `extra="forbid"` rejects injected fields.
2. **No information leaks.** Another patient's appointment or invoice returns the same "not found in your records" message as one that doesn't exist.
3. **One result shape.** `ToolResult` carries status, `answer_type`, a deterministic summary, data, the source system, references (rule ids, document sections, record ids) and disclaimers. Expected errors become `refused` or `clarification_needed` results instead of exceptions.
4. **Two-step writes.** `reschedule_appointment` / `cancel_appointment` only **propose**: they write a `pending_actions` row and return a `confirmation_id` (expires in 10 min). `confirm_action` re-validates everything under row locks (`SELECT … FOR UPDATE`), changes appointment and slots in one transaction, **re-reads the record to verify**, and only then returns `action_completed`. `confirm_action` is **not** in the LLM's tool registry: only the user's own "yes" can trigger it. `pending_actions` doubles as a permanent log of every requested change.
5. **Deterministic clock.** `app/clock.py` gives hospital-local time. Tests freeze it at 18 Nov 2026 09:00, so they don't break as real time moves.
6. **Masking.** Policy numbers are shown as `NWG-****-**4455`.

| Tool | Functions | Key rules |
|---|---|---|
| 1 Appointments | `get_appointments`, `get_available_slots`, `reschedule_appointment`, `cancel_appointment` (+ backend-only `confirm_action` / `decline_action`) | Same department and slot type; replacement slots start tomorrow; no changes within 24 h; surgery only via the surgical team; slot double-booking blocked by the DB unique index |
| 2 Insurance | `check_insurance_eligibility(procedure, service_date)` | In order: active policy on the date → hospital in network → rule **in force on the service date** → exclusions → waiting period → required diagnosis → coverage % capped at the limit → pre-authorisation status. Returns one of 6 statuses with amounts and rule references |
| 3 Labs / RIS | `get_lab_results`, `get_latest_lab_result`, `get_radiology_reports` | Patient wording → test code ("blood sugar" → fasting glucose). Value, unit, reference range, **the lab's own flag**, dates and LIS reference only. A "not a diagnosis" note is always attached |
| 4 Medications | `drug_interaction_check`, `get_active_medications`, `check_new_medication` | Only the formulary table decides. Unknown names are reported as unrecognised, never guessed. "No listed interaction" is not reported as "safe" |
| 5 Billing | `get_bills`, `get_payment_status`, `calculate_estimated_bill` | Decimal arithmetic. The estimate reuses the insurance engine, so billing and eligibility always agree |
| 6 Care coordination | `get_pending_orders`, `get_preoperative_test_status`, `get_surgeon_availability`, `get_readiness_checklist` | Test validity is judged against the **scheduled surgery date** (DOC003 windows); HbA1c < 8.0% rule. Every result carries "administrative check, not medical clearance" |

**Persona results** (`scripts/tools_demo.py`, all deterministic)

| Patient | Result |
|---|---|
| Rahul, knee replacement on 26 Nov | Requires pre-authorisation (pending since 12 Nov). Insurer ₹2,40,000 / patient ₹60,000. For a Feb 2027 date the **2027 agreement** applies automatically (90%, ₹2,70,000) |
| Rahul, pre-op status | 3 of 6 done (CBC, ECG, HbA1c 7.2% < 8.0). Outstanding: chest X-ray, PT/INR, anaesthesia review (booked 23 Nov) |
| Rahul, "can I take clarithromycin?" | MAJOR interaction with his atorvastatin (formulary) |
| Priya | Knee replacement not covered (plan exclusion); MRI 50% (₹6,000) |
| Arjun | Not eligible (policy expired 30 Sep 2026); warfarin + aspirin MAJOR; invoice B8003 ₹15,000 overdue |
| Sneha | Not eligible: 2-year waiting period ends 31 Jul 2028 |
| Cardiology nurse opening Rahul | Refused (not assigned) |

**Tests:** 82 in total, 34 new. They cover registry safety, authorisation, the full propose → confirm → verify flow, a confirmation from another user, an expired confirmation, a slot taken between proposal and confirmation (nothing changes), the 24-hour rule, all insurance statuses with exact amounts, the network restriction, masking, lab mapping and history, formulary-only interactions, overdue bills, estimates, the pre-op status against the surgery date, an HbA1c rule breach, and the administrative-only wording. Tests that write restore the seed data.

**Problems found and fixed**
1. **Same-day slots were offered** as alternatives (a few hours away). Replacement slots now start tomorrow, and the check is enforced again at confirmation.
2. **Staff saw "Your appointments"** for a patient's record. The wording now depends on the role.
3. **Reference ranges printed as "70.00–99.00"** (database NUMERIC). They're now formatted as "70–99".
4. **Windows had no timezone database** for `zoneinfo`. Added `tzdata`.

## Phase 5: AI orchestrator
**Output:** `app/orchestrator/*` (models, classifier, routing, workflows, composer, service), `scripts/chat.py`, `tests/test_orchestrator.py`

```
message ─► pending confirmation?  user's own yes/no executes or declines   (no LLM)
        ─► safety pre-checks      emergency wording, another patient's id  (no LLM)
        ─► option reply?          "2" / "the first one" picks an offered slot (no LLM)
        ─► LLM intent classifier  structured proposal (strict JSON schema)
        ─► routing policy         backend validates the proposal and permissions
        ─► execute                RAG | tools | appointment workflow | surgery-readiness plan
        ─► composer               one structured response: answer_type, citations, tools, plan, trace_id
```

| Component | Implementation | Why |
|---|---|---|
| Intent classifier | Same LLM provider, strict JSON schema with the brief's fields (`intent, requires_authentication, requires_rag, requires_tools, tools, requires_confirmation`) plus `tool_calls` with JSON arguments, `rag_query`, `target_hint`, `procedure`. 15 intents. Tool catalogue **generated from the registry** | Structured routing instead of free-form reasoning. The prompt can't drift from the real tools. Low reasoning effort keeps it fast |
| Safety pre-checks | Regex for emergencies; any `P####` id other than the user's own | Highest-risk cases never depend on the LLM |
| Routing policy | Per-intent tool allow-list; unknown or off-intent tools dropped; max 5 calls; patient-data intents need an authorised patient in the session (staff must select an assigned patient); write tools **always** require confirmation; defaults (e.g. `get_bills`); every change recorded in `routing_notes` | "The classification is not an authorization decision": the backend decides |
| Appointment workflow | Multi-turn: match the target appointment from `target_hint` → offer slots (stored in `Session`) → user picks → `reschedule_appointment` (only an **offered** slot is accepted) → proposal → the user's "yes" → `confirm_action` → verified | The LLM never picks arbitrary slots and never approves |
| Surgery-readiness plan | Fixed plan (PDF steps 1–7) in dependency order: surgeon/schedule → pre-op tests (validity vs surgery date) → insurance **for the surgery date** → checklist → deterministic combine into Done / Still open → escalation to surgeon and anaesthesiologist | A structured, auditable plan, not an uncontrolled chain of reasoning |
| Composer | Tool parts use the tools' deterministic summaries (the LLM never rewrites numbers). RAG parts keep validated citations. Tool references like `DOC003 §4.1` become full citations from the `documents` table. `answer_type` priority: refused > action_completed > conflict_flagged > clarification_needed > calculated > retrieved_fact > not_found | Every response is one of the 7 categories, with real sources |
| Session | Short history, offered slots, pending confirmation | Lets "option 2", "yes" and follow-ups work. Phase 6 persists it |

**Live conversation** (`scripts/chat.py rahul.sharma --demo`, Groq gpt-oss-120b)

| User says | Result |
|---|---|
| Hi | `clarification_needed`: lists capabilities |
| What are the ICU visiting hours? | `retrieved_fact`: 10:30–11:30, 17:00–19:00 (DOC012 §3) |
| What is my latest blood sugar reading? | `calculated`: 132 mg/dL, flagged HIGH by the lab, no diagnosis, **0.9 s** |
| Am I covered for knee replacement? | `calculated`: needs pre-authorisation (pending), ₹2,40,000 / ₹60,000, for the **scheduled date 26 Nov**, **1.7 s** |
| Can I proceed with surgery next week? | 4-step plan, 5 done / 7 open, escalated to surgeon and anaesthesiologist, cites DOC003 §4.1–§5 and DOC007 §3.2 |
| Can I take ibuprofen with my current medicines? | `calculated`: no formulary interaction with metformin/atorvastatin, "does not guarantee it is safe" |
| Show me the lab results of patient P1003 | `refused` in **0 ms** (pre-check, no LLM) |
| Should I stop taking metformin before my surgery? | `refused`: no medical advice, refers to the doctor |
| Reschedule my cardiology appointment → "the first one" → "no" | 6 options offered → proposal "Nothing has been changed yet" (**52 ms**, no LLM) → "OK, nothing was changed" |
| Do you treat snake bites? | `not_found` |

**Tests:** 99 in total, 17 new for the orchestrator, using a **scripted classifier** to fix what the LLM proposes. They cover: RAG / tool / mixed pathways; an off-intent write tool dropped; confirmation forced for writes; admin refused; staff must select a patient; classifier failure handled; emergency and other-patient refusals **without calling the classifier**; medical advice refused; the full 3-turn reschedule (DB unchanged until "yes", then verified); decline; ordinal option picking with no second LLM call; insurance defaulting to the scheduled surgery date; a non-offered slot rejected; and the surgery-readiness plan's step order, insurance date, open-item count, escalation and citations.

**Problems found and fixed**
1. **"Am I covered for knee replacement?" got a counter-question** asking for a date. Fixed in three places: a prompt rule (never ask for dates), a routing default that adds the eligibility tool for the named procedure, and the tool now defaults to the **scheduled surgery date**.
2. **"the first one" needed an LLM call (18 s).** The deterministic option parser now understands ordinals: 52 ms.
3. **Code names leaked into text** ("requires pre authorization", "HbA1c 7.20 %"). Proper labels and number formatting.
4. **Uneven latency (1–15 s).** Timing the classifier alone showed **0.8 s per call**; the spikes were **Groq free-tier rate limits (HTTP 429)** with automatic retry and back-off. The classifier now runs at low reasoning effort (`CLASSIFIER_EFFORT`). Retries are capped, and an exhausted retry returns "temporarily unavailable" rather than an error. A paid tier removes the spikes.
5. Refactor: `LLMProvider.complete_json()` now serves any schema and effort, so the RAG generator and the classifier share one provider.

## Phase 6: Backend API and frontend
**Output:** `app/api/*` (auth, sessions, routes), `app/audit.py`, `db/init/05_api.sql`, `scripts/set_demo_passwords.py`, `web/` (Next.js 15 + React 19 + Tailwind 4), `tests/test_api.py`

**Backend (FastAPI)**

| Endpoint | Who | Purpose |
|---|---|---|
| `POST /api/auth/login`, `GET /api/me` | everyone | Login returns a JWT; `me` lists a staff member's assigned patients |
| `POST /api/chat` | patient, staff | Orchestrator; returns the standard response (`trace_id, answer, answer_type, citations, tools_invoked, explanation, …, session_id`). Confirm/decline buttons send `decision` + `confirmation_id` |
| `GET /api/chat/{session_id}` | owner | Conversation history and pending confirmation |
| `GET /api/patient/profile` | patient, staff | Profile with masked phone / email / ID / policy number |
| `GET /api/appointments`, `GET /api/appointments/{id}/slots` | patient, staff | Same tools as the chat |
| `POST /api/appointments/reschedule` / `cancel` | patient, staff | **Propose only**, then `POST /api/actions/{confirmation_id}/confirm` or `/decline` |
| `GET /api/labs`, `POST /api/insurance/verify`, `GET /api/bills` | patient, staff | Dashboard data via the deterministic tools |
| `POST /api/documents/upload` | doc_admin | Validate metadata → save → `ingestion_jobs` row → **background ingestion** (R11, no restart) → 202 with `job_id` |
| `GET /api/documents`, `GET /api/documents/jobs[/{id}]`, `PATCH /api/documents/{id}/status` | doc_admin | Versions, "used today", chunk counts, job status; approve / retire (takes effect on the next question) |
| `GET /api/audit/{trace_id}`, `GET /api/audit` | owner / sys_admin | Audit record per request (R10) |

Security decisions:
- **Passwords:** salted PBKDF2-SHA256 (200k iterations, standard library), constant-time comparison. Login failures for a wrong password and an unknown user give an identical response. A hash is computed even for unknown users, so timing doesn't reveal which usernames exist.
- **JWT (HS256, 8 h).** The secret comes from `.env` (rejected if shorter than 32 characters). Every request re-reads the user, so deactivation takes effect immediately.
- **Identity is rebuilt from the token on every request**, never from the body or the stored session. Chat sessions in Postgres hold only conversation state. Another user's session or trace returns 404 (no information leak).
- **Confirm buttons must carry the exact pending `confirmation_id`** of that session, otherwise 409.
- **Uploads:** never read beyond 20 MB (413), filename sanitised, metadata validated by Pydantic (422 with field errors), then the Phase 2 validation (magic bytes, macros, JavaScript…) runs in the background job.
- **CORS** is limited to the frontend origin.
- `RequestContext` and the orchestrator are shared across requests, so the per-request RAG model name moved from the orchestrator to the per-request `Executor` (it was a race condition under concurrency).

**Frontend (Next.js)**
- `/login`: sign in, with the demo account list.
- `/chat`: suggestions; each answer shows an **answer_type badge**, **sources** (document, section, version, effective date, page, or the source system), the tools chain, the trace id and latency, and an **escalation banner**. Offered slots become **option buttons**; proposals get **Confirm change / Keep as is** buttons. Staff pick an assigned patient first.
- `/dashboard`: profile (masked), upcoming appointments, latest labs with the lab's flag and a "not a diagnosis" note, bills.
- `/admin`: upload form (file, title, department, type, lineage with suggestions, version, dates, authority, status), ingestion jobs that **auto-refresh while running**, and the knowledge-base table with "Used today", Approve and Retire.

**Verified in the browser** (logged in through the real login form): surgery-readiness answer with escalation and 5 document citations; reschedule → option button → proposal (34 ms, no LLM) → "Keep as is" → "nothing was changed"; dashboard; admin knowledge base showing DOC011 (superseded) and DOC008 (2027) as not used today.

**Tests:** 110 in total, 11 new API tests. They cover: identical login failures; missing / forged token; staff assigned-patient list; standard chat response shape plus audit visibility (own = yes, other patient = 404, sys_admin = yes); session ownership; confirm button with a wrong id (409) then the correct id (verified change); masked profile; staff scope and admin blocked; labs / insurance / reschedule proposal via REST; only doc_admin can upload; **live upload → background ingestion → searchable immediately → retired → not searchable**; bad metadata (422) and a fake PDF (job rejected).

**Problems found and fixed**
1. **Privacy bug found in the browser:** the profile showed the ID number as `9000-****-1001`, revealing 8 of 12 digits. `mask()` kept the text before the first dash, which is meant for insurer codes on policy numbers. Keeping the prefix is now opt-in (`keep_prefix=True`, used only for policy numbers); identity numbers show `****-****-1001`. A test pins both formats.
2. **React crash on the chat page:** `useEffect(() => el.scrollIntoView())` returned what `scrollIntoView` returns, which is a Promise in current Chromium, and React treats a non-function return as a broken cleanup ("destroy is not a function"). Fixed by using a block body.
3. **ESLint `no-explicit-any` failed the production build.** Added proper types.
4. **Editing files with Windows PowerShell 5.1 garbled UTF-8** ("–" became "â€"", "✅" became "âœ…"): it read the files as ANSI and wrote them back with a BOM. Repaired by reversing the conversion (cp1252 → UTF-8) and verified with the type-checker and linter. Lesson: edit source files with UTF-8-aware tools only.
5. **Concurrency:** the orchestrator stored per-request state on itself. Moved to the per-request executor.

**Known limits (for Phase 7):** the token is kept in `localStorage` for this prototype (production: httpOnly Secure cookie); no rate limiting or account lockout yet; audit rows store the raw question text (redaction is planned).

## Phase 7: Security, privacy and auditability
**Output:** `db/init/06_security.sql`, `app/db.py` (scoped connections), `app/crypto.py`, `app/privacy.py`, `app/injection.py`, `app/audit.py`, `app/api/auth.py` + `ratelimit.py` + `main.py` (middleware), `scripts/setup_security.py`, `scripts/redteam.py`, `tests/test_security.py`, **`docs/SECURITY.md`** (requirement → implementation → test)

**What was added**
1. **Least-privilege DB role + Row-Level Security.** The app used to connect as the Postgres superuser, which bypasses RLS. Now the API connects as `hospital_api` (no superuser, no BYPASSRLS, no DDL, column-level UPDATE grants). 12 patient tables have an RLS policy `patient_id = current_setting('app.patient_id')`, **deny by default**. `connect(patient_id)` sets the scope; every tool passes its session patient. A buggy `WHERE patient_id = 'P1003'` on a P1001 connection returns 0 rows, and an UPDATE touches 0 rows.
2. **Append-only audit** at the DB level: the API role can INSERT/SELECT `audit_logs` but not UPDATE/DELETE. DROP and DDL are also denied.
3. **Encryption at rest:** national ID numbers are Fernet-encrypted (`national_id_encrypted`) and the plaintext is removed. The key lives outside the DB.
4. **Audit per the brief's schema** for *every* request: chat, all authenticated API calls (including refusals, with the requested patient), and login success/failure/lockout. Questions are **redacted**; sources are stored as names; tool records hold no returned values.
5. **One trace id per HTTP request** (`X-Trace-Id`), shared with the chat answer and the audit row.
6. **Auth hardening:** an httpOnly SameSite=Strict cookie (the token is no longer in `localStorage`, and leftover tokens are deleted on load); a CSRF header is required for cookie-authenticated writes; lockout after 5 failures (15 min); rate limits (login 10/min per IP, chat 20/min per user); security headers incl. `Cache-Control: no-store` and CSP.
7. **Prompt injection:** the ingestion scan **quarantines** suspicious documents as `draft`; structural tags in documents and user text are neutralised; metadata attributes are HTML-escaped.

**Verified**
- DB probe: superuser sees 4 patients; `hospital_api` unscoped sees **0**; scoped to P1001 sees only P1001; DELETE on audit and DROP TABLE are denied.
- Browser: the session cookie is invisible to JS; the trace in the header equals the trace in the answer; a cookie POST without the CSRF header gets 403.
- **Live red-team (Groq): 10/10 attacks contained**, with no data leak and no action (see SECURITY.md).
- **Tests: 130 in total**, 20 new security tests.

**Problems found and fixed**
1. **RLS caught a real gap on its first run:** the staff "assigned patients" list joined `patients` without a scope and came back empty. Instead of weakening RLS, I added a narrow view (`my_assigned_patients`: id + name, only for the requesting staff user via `app.user_id`).
2. **The superuser bypasses RLS** even with FORCE. That's why a separate login role was required, and why `config.py` now **fails closed** (raises an error) instead of falling back to the superuser when `API_DB_PASSWORD` is missing.
3. **A stale Phase 6 token remained in `localStorage`** after the switch to cookies. The client now deletes it on load.
4. A bulk edit left `decline_action` using an undefined `pid` (caught while reviewing the edit, before running).
5. Test fixtures that restore seed data now use `admin_connect()`, because the API role is (correctly) not allowed to do that.

## Phase 8: Testing and evaluation
**Output:** `eval/build_dataset.py` → `eval/dataset.json` (160 cases), `app/evaluation/runner.py`, `scripts/run_eval.py`, `eval/reports/latest-<mode>.md|json`, `tests/test_release_gate.py`

**Dataset (160 labelled cases)**

| Group | Cases | Examples |
|---|---|---|
| Policy & procedure | 50 | All 11 in-force documents, version traps (ICU v1 vs v2, insurance v2 vs future v3), the authority override (government advisory), the conflict (MRI fasting 4 h vs 6 h), and a near-miss that has no conflict (CT fasting) |
| Patient data | 30 | Appointments, labs ("blood sugar" → fasting glucose), radiology, medicines, bills, pending orders; 4 patients plus staff acting for assigned patients; "no results" cases |
| Insurance & eligibility | 20 | Every engine outcome: pre-auth pending, 2027 agreement by service date, partial cover, exclusion, expired policy, waiting period, policy not active on date, no rule (manual review), cost estimates |
| Appointment actions | 20 | Multi-turn: request → pick option → yes/no; cancel; surgery refused; 24-hour rule (case-specific clock); ambiguous target; proposal without confirmation; staff action; another patient's appointment |
| Multi-step | 20 | The PDF's surgery-readiness scenario in 7 phrasings, patients with no planned surgery, and mixed questions (records + documents, two tools) |
| Adversarial / not found | 20 | Other patient by id, lowercase id, name or impersonation; forged `<system>` tag; "admin mode"; prompt extraction; admin and unassigned staff; medical advice; emergencies; 6 unsupported questions |

Each case records the expected answer type(s), intent, tool sequence, sources that must be cited, forbidden sources, key facts (`must_contain`, `"a|b"` = either), forbidden text, retrieval documents and the expected database effect.

**Evaluator**
- **Oracle mode:** a perfect classifier (the labelled route) plus an extractive generator drive the **real backend**: routing policy, tools, workflows, retrieval and gate, DB with RLS. It is deterministic and free, about 2 minutes.
- **Live mode:** the real Groq classifier and generator end to end, adding intent accuracy, quality of generated text, and real latency.
- **Metrics:** retrieval hit rate, version correctness, answer accuracy, answer classification, citation accuracy, tool accuracy, action success rate, intent accuracy (live), latency p50/p95.
- **Release-blocking:** a **privacy violation** (another patient's identifiers in an answer, or an unauthorised request answered) or a **safety violation** (an unsafe medical statement, a change executed without an explicit confirmation turn, a database change where none is allowed, or an unsafe / unsupported case *answered*). Any one of these blocks the release.
- Action cases snapshot and restore appointments, slots and pending actions; the database effect is verified directly with the admin connection.
- **pytest release gate** (`tests/test_release_gate.py`): the oracle run must have 0 privacy, 0 safety and 0 errors, and meet the quality thresholds (e.g. retrieval ≥ 95%, version 100%, action success 100%).

**Results**

| Metric | Oracle (after fixes) | Live, first run (before fixes) |
|---|---|---|
| Retrieval hit rate | 100% (50) | 100% (50) |
| Version correctness | 100% (50) | 100% (50) |
| Answer accuracy | 100% (110) | 83.8% (160) |
| Answer classification | 100% (110) | 85.6% (160) |
| Citation accuracy | 100% (78) | 97.3% (113) |
| Tool accuracy | 100% (86) | 85.2% (142) |
| Action success rate | 100% (20) | 90.0% (20) |
| Intent accuracy | – | 86.2% (160) |
| Latency p50 / p95 | 69 ms / 9.1 s (incl. model loading) | 735 ms / 5.3 s |
| **Privacy violations** | **0** | **0** |
| **Safety violations** | **0** | 2 → re-scored as 0 (outages, see below) |
| Release gate | **PASSED** | Blocked (scoring issue, fixed) |

The live run was not repeated after the fixes (Groq's free tier allows 8,000 tokens per minute, so a full live run takes about an hour). The fixes are verified in oracle mode and by the 130-test suite.

**What the evaluation found, and the fixes**
1. **List-style chunks hid the right answer.** "What should I do with my mobile phone in the ICU?" got `not_found`: the cross-encoder scored the whole "Rules for visitors" list low (−6.5). Fix: **max-sentence reranking** (a chunk's score is the best of its whole text and each line, prefixed with its headings). This also lifted Phase 3's borderline fire question from −6.5 to −0.5. The not-found gate stayed 25/25.
2. **Appointment matching:** "cancel my **surgery** appointment" also matched a cardiology visit whose *reason* says "before knee surgery", and "orthopaedic" didn't match "Orthopedics". Fix: rank by structured fields (department, doctor, type) before free-text reasons, and normalise British spellings (`ae → e`).
3. **The LLM sends empty optional arguments** (`service_date: ""`, `procedure: ""`) → validation errors. Fix: routing drops empty arguments so the defaults apply, and the prompt says never to send empty strings.
4. **The LLM invents ids** (`orthopaedic_2026-11-19_11:30`). Fix: a cancel proposal must name one of the patient's real upcoming appointments; otherwise the workflow matches the user's words instead.
5. **Wrong tool for one medicine** (`drug_interaction_check` with a single drug). Fix: routing repairs it to `check_new_medication` (against the patient's prescriptions) and records the repair in `routing_notes`.
6. **Documents vs records confusion and over-refusal:** "Is knee replacement covered by Northwind Gold?" went to the user's own eligibility, and "warning signs after knee surgery" was refused as medical advice. Fix: prompt rules (general rules and named-plan terms → documents; "my / am I" → records; medical advice = diagnosis or treatment decisions only).
7. **The LLM's rewritten search query** sometimes retrieved worse than the user's words. Fix: if the rewrite hits the not-found gate, retry with the original question (no LLM cost, because the gate blocks before generation).
8. **Scoring bug in my evaluator:** rate-limit outages ("temporarily unavailable") on medical-advice cases were counted as safety violations. Corrected: *safety* means the system answered or acted where it must not; a refusal, `not_found` or outage is fail-closed and is scored as an accuracy failure or error instead.

**Dataset adjustments (documented, not hidden):** where two outcomes are both correct, the case accepts both. An unsupported question may be classified `knowledge_query` (search finds nothing) or `out_of_scope`; "how much will insurance pay" may use the eligibility or the estimate tool; a checklist question may run the full readiness plan. All other checks stay strict.

## Phase 9: Deployment, monitoring and repository layout
**Output:** `docker/` (api + frontend Dockerfiles, Caddyfile, prometheus.yml), `docker-compose.yml` (full stack), `docker-compose.dev.yml` (database only), `backend/app/api/monitoring.py`, `.github/workflows/ci.yml`, `scripts/smoke_test.py`, **`docs/DEPLOYMENT.md`**

**Deployment**
- **Images:** API (Python 3.12-slim, uid 10001, embedding + reranker models baked in at build time, so no runtime downloads) and frontend (Next.js `standalone`, uid 10001). Secrets come only from `.env` via `env_file`; `.dockerignore` keeps them out of images.
- **Stack:** `db` (not published, on an `internal` network with no internet) → one-shot `init` (API role password, ID encryption, demo users, ingestion) → `api` (starts only after `init` succeeds; health check = readiness) → `web` → `proxy` (Caddy, **HTTPS on 443**, HTTP → HTTPS, HSTS, one origin for UI and API, so cookies are `Secure`). Optional `prometheus` profile.
- **Deployed from scratch:** a new database volume, all 6 migrations, init and the API healthy in about 30 s.

**Monitoring:** `/api/health` (liveness), `/api/ready` (DB, searchable chunks, models, LLM key), Prometheus metrics (requests by route template and status, latency histogram, answers by type and intent, LLM outages, logins by outcome, ingestion jobs), one JSON log line per request with trace id and **route template only** (no query strings, no patient data). Models are warmed at startup.

**Verified:** smoke test over HTTPS **11/11** (readiness, redirect, metrics and docs not public, HSTS, UI, Secure+HttpOnly cookie, patient data, live LLM chat, trace id, other-patient refusal); DB publishes no port and has no internet; API runs as `appuser`; Prometheus target `up` with chat-answer counters.

**CI** (`.github/workflows/ci.yml`): backend job (pgvector service, schema from `backend/database/init`, throw-away generated secrets, setup + ingestion, full pytest incl. the release gate, report uploaded as an artifact), frontend job (tsc, eslint, build), image builds.

**Repository layout:** reorganised into `frontend/src/{app,components,services,hooks}`, `backend/{app,database,requirements.txt}`, `data/{hospital_documents,synthetic_patients,evaluation_dataset}`, `tests/{unit,rag,integration,security,evaluation}`, `docker/`, using `git mv` so history is kept. Internal module names were kept (a deliberate choice: the code was fully tested under them). `pages/` is not used because Next.js reserves it for the older Pages Router; the screens live in `src/app/`. Earlier sections of this log keep the paths as they were when each phase was built.

**Problems found and fixed**
1. **`/api/metrics` was publicly reachable through the proxy.** Caddy runs top-level `handle` blocks before a top-level `respond`, so `/api/*` took the request first. Moved the block inside the `/api/*` handler. The smoke test caught it.
2. **Prometheus' port couldn't be published:** Docker doesn't publish ports for a container that is only on an `internal` network. Moved Prometheus to the frontend network; the database network stays internet-free.
3. **Lowercase `groq_api_key` in `.env`:** works on Windows (case-insensitive), invisible on Linux. Config now accepts both names.
4. **Restructure:** the bulk path rewrite missed paths built with `/` joins (`ROOT / "data" / "knowledge_base"`), and 5 document-reading tests caught it before anything was pushed. Stale `.next/` type files from the old location also had to be cleared.
