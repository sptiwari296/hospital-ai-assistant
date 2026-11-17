# Phase 0 — Requirements & System Design
**Project:** AI-Powered Hospital Patient Services Assistant
**Source brief:** `Hospital_AI_Assistant_Use_Case.pdf` (HCLTech Future Ready AI Engineer Hackathon, v1.0)
**Target maturity:** Level 4–5 (tool-based assistant with multi-step workflows). Per the brief's design principle we use *the simplest architecture* that satisfies grounding, privacy, deterministic decisioning, action control and auditability. Specialised agents (Level 6) are optional.

---

## 1. Goal (one paragraph)
A patient- and staff-facing chat assistant that answers hospital questions **only** from approved documents (with citations) and reads/changes patient data **only** through authorised, deterministic tools. It never invents medical facts, never decides eligibility itself, and every request is traceable.

## 2. Two information pathways (core design)
```
                        ┌──────────────────────────────┐
 Patient / Staff ──────►│ Web chat (Next.js)           │
                        └──────────────┬───────────────┘
                                       ▼
                        ┌──────────────────────────────┐
                        │ FastAPI backend               │  ← identity from login token,
                        │ auth · session · validation   │    NOT from chat text
                        └──────────────┬───────────────┘
                                       ▼
                        ┌──────────────────────────────┐
                        │ AI Orchestrator               │
                        │ classify → route → plan       │
                        └───────┬──────────────┬───────┘
              KNOWLEDGE pathway │              │ OPERATIONAL pathway
                                ▼              ▼
             ┌─────────────────────────┐  ┌─────────────────────────┐
             │ RAG over approved docs  │  │ Deterministic tools      │
             │ pgvector + metadata     │  │ (authz checked BEFORE    │
             │ version resolution      │  │  any patient data read)  │
             └───────────┬─────────────┘  └───────────┬─────────────┘
                         └──────────────┬─────────────┘
                                        ▼
                        ┌──────────────────────────────┐
                        │ Safety & response validation  │
                        │ evidence · citations · class  │
                        └──────────────┬───────────────┘
                                       ▼
                        Final JSON response + audit log (trace_id)
```
**Why separate?** An LLM must not invent insurance eligibility, compute clinical readiness, or claim an appointment changed. Facts about *policy* come from documents; facts about *a patient* come from systems via tools.

## 3. Query categories → which pathway handles them
| # | Category | Example | Responsibility | Typical answer_type |
|---|---|---|---|---|
| 1 | Policy fact | What are ICU visiting hours? | RAG | `retrieved_fact` |
| 2 | Procedure | How do I schedule an MRI? | RAG | `retrieved_fact` |
| 3 | Personal data | When is my appointment? | `get_appointments` tool | `calculated` |
| 4 | Eligibility | Is my knee replacement covered? | `check_insurance_eligibility` tool | `calculated` |
| 5 | Lab result | What is my latest HbA1c? | `get_lab_results` tool | `calculated` |
| 6 | Multi-step | What pre-op tests are still pending? | several tools + RAG | `calculated` |
| 7 | Action | Reschedule my cardiology appointment | authorised workflow (confirm first) | `action_completed` / `clarification_needed` |
| 8 | Not found | Treatment for a condition we don't handle | grounded fallback | `not_found` |
| – | Unauthorised / unsafe | Show me P1002's labs (as P1001) | backend authz | `refused` |
| – | Conflicting policies | Two equal-rank docs disagree | version resolver | `conflict_flagged` |

## 4. User roles & permission matrix
Identity always comes from the **trusted request context** (login token → `users` table). The LLM never decides access.

| Capability | patient | staff | doc_admin | sys_admin |
|---|---|---|---|---|
| Ask policy / procedure questions | ✅ | ✅ | ✅ | ✅ |
| Read **own** appointments, labs, insurance, bills, prescriptions | ✅ | – | – | – |
| Read patient data of **assigned** patients (`care_assignments`) | – | ✅ | ❌ | ❌ |
| Reschedule / cancel appointment | own only | assigned patients | ❌ | ❌ |
| Upload / approve / retire documents (R11) | ❌ | ❌ | ✅ | ❌ |
| Read audit logs, manage config/integrations | ❌ | ❌ | ❌ | ✅ |

**Rules**
1. Authorisation runs in the backend *before* any patient data is fetched. Tools receive `patient_id` from the session, never from the model's free text.
2. A patient asking about another patient → `refused` (no hint whether that patient exists).
3. Sensitive fields (e.g. `national_id`, phone, policy number) are masked in responses: `XXXX-XXXX-1234`.
4. Write actions (reschedule/cancel) require an explicit user confirmation step.

## 5. Functional requirements → design decision → phase
| Req | What the brief says | How we satisfy it | Phase |
|---|---|---|---|
| R1 Knowledge retrieval | Approved documents only | Index only `approval_status='approved'` docs | 2–3 |
| R2 Citations | doc, section, version, effective date | Every chunk stores these as metadata | 2–3 |
| R3 No hallucination | Return `not_found` | Relevance threshold + evidence check before answering | 3, 5 |
| R4 Version resolution | hierarchy, authority, version, date | Resolver (see §7) | 3 |
| R5 Deterministic tools | LLM never calculates eligibility | Python functions over Postgres | 4 |
| R6 Multi-step | Combine records + policies + tools | Orchestrator plans tool sequence | 5 |
| R7 Authz & privacy | Identity from context, refuse others, mask | Backend authz layer + masking | 6–7 |
| R8 Untrusted content | Documents are data only | Delimit retrieved text; system prompt rule; injection tests | 7 |
| R9 Answer classification | 7 answer types | Pydantic enum on every response | 5 |
| R10 Auditability | trace_id, sources, tools, privacy, latency, model | `audit_logs` row per request | 7 |
| R11 Live ingestion | Upload without restart | `POST /admin/documents` → ingest in background | 2, 6 |

## 6. Response contract (every answer looks like this)
```json
{
  "trace_id": "HSP-93214",
  "answer": "Your MRI appointment is scheduled for 24 Nov 2026 at 10:30 AM.",
  "answer_type": "calculated",
  "citations": [{"system": "Hospital Scheduling System"}],
  "tools_invoked": [{"tool": "get_appointments", "status": "success"}],
  "explanation": "Retrieved from the patient appointment management system."
}
```
Document citations look like:
`{"document_id": "DOC012", "document_name": "ICU_Visiting_Policy_v2.pdf", "section": "3.1", "version": "2.0", "effective_date": "2026-07-01"}`

`answer_type` ∈ `retrieved_fact | calculated | action_completed | clarification_needed | not_found | refused | conflict_flagged`

## 7. Policy conflict resolution rules (R4)
Applied in order when retrieved documents disagree:
1. Drop documents not currently effective (`effective_date > today`, `expiry_date < today`, or status ≠ `approved`).
2. Higher **authority level** wins: 1 = government/regulatory → 2 = clinical guideline board → 3 = hospital-wide policy → 4 = department SOP.
3. Same document lineage → higher **version** wins.
4. Otherwise → later **effective_date** wins.
5. Still tied (same authority, same date, different documents) → `conflict_flagged`; tell the user and recommend contacting the department.

## 8. Tools (operational pathway)
| Tool | System it represents | Read/Write |
|---|---|---|
| `get_appointments` | Appointment system | R |
| `find_available_slots` / `reschedule_appointment` | Appointment system | R / **W** |
| `check_insurance_eligibility` | Insurance + coverage rules | R (deterministic calc) |
| `get_lab_results` | LIS | R |
| `get_radiology_reports` | RIS | R |
| `get_bills` | Billing system | R |
| `get_prescriptions` / `drug_interaction_check` | Pharmacy | R |
| `check_preop_readiness` | EMR orders + protocol + checklist | R (deterministic calc) |

## 9. Example multi-step: "Can I proceed with surgery next week?"
1. Required pre-op tests for the procedure → `preop_requirements`
2. Completion status & validity → `medical_orders`, `lab_results`
3. Insurance authorisation → `prior_authorizations`, `coverage_rules`
4. Surgeon schedule → `appointments` / `appointment_slots`
5. Readiness checklist → `procedure_readiness`
→ Combine into one grounded answer that lists what is done, what is pending and the assumptions made.

## 10. Non-functional requirements
- **Latency:** report p50/p95; target p95 < 8 s for single-tool queries.
- **Privacy:** synthetic data only; no real patient data anywhere in this project.
- **Reproducibility:** everything runs via Docker.
- **Evaluation metrics (Phase 8):** answer accuracy, citation accuracy, privacy compliance, tool accuracy, retrieval hit rate, action success rate, latency, safety compliance.

## 11. Out of scope (prototype)
Real EMR/HL7/FHIR integration, real insurer APIs, diagnosis or treatment advice, payments, and multi-hospital tenancy.

## 12. Synthetic test personas (Phase 1 data)
| Patient | Story | Good for testing |
|---|---|---|
| **P1001** Rahul Sharma | Diabetic, knee replacement on 26 Nov 2026, 2 pre-op tests pending, pre-auth pending | multi-step, eligibility, labs, reschedule |
| **P1002** Priya Nair | MRI brain on 24 Nov, basic insurance plan | MRI procedure, partial coverage, not-covered |
| **P1003** Arjun Mehta | Cardiac patient on warfarin + aspirin, insurance **expired** | drug interactions, eligibility = not eligible, bills |
| **P1004** Sneha Kapoor | New policy (Aug 2026), still in the waiting period | eligibility edge case |
