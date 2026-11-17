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
