# MediShield Intake: Workflow

Upload a claim document (image). The system reads it, checks blur and identity,
extracts the claim, checks coverage against the policy, scores fraud, and returns
**APPROVE**, **REJECT** or **ESCALATE**. Uncertain cases go to a human
(human-in-the-loop, HIL) who can correct the document type or set the decision.

Layout:

```
backend/   FastAPI + LangGraph + MySQL + Qdrant   (uvicorn app.main:app --port 8000)
frontend/  Next.js UI                              (npm run dev, port 3000)
agents/notebook/   original research notebooks (not edited)
dataset/   synthetic demo documents and policy PDFs
```

---

## 0. Claim pipeline (one claim = all its documents, one decision)

This is the main flow. Sections 1–8 below describe the older single-document pipeline
(`POST /api/cases`), which is kept for one-off pages.

### 0.1 Graph ([backend/app/claim/graph.py](backend/app/claim/graph.py))

```
 one upload button: every document of the claim, no types chosen
      │
      ▼
 ┌──────────────┐  per page, one page at a time, in upload order (the queue):
 │ preprocess   │◄─┐   vision model: image -> text (shown in the UI) + image quality; blur score
 │ classify     │  │   vision model: document type, or UNKNOWN (blurry / unclassifiable -> ESCALATE)
 │ extract      │──┘   text model: that type's fields from the transcription
 └──────┬───────┘
        ▼
 ┌──────────────┐  orchestrator, first half: which agents apply, what is missing
 │ plan         │
 └──┬────────┬──┘
    ▼        ▼                         (parallel super-step)
 ┌──────┐ ┌────────┐   ┌────────┐
 │ kyc  │ │ claims │──►│ policy │  RAG: Qdrant clauses -> LLM decides from them only
 └──┬───┘ └───┬────┘   └───┬────┘
    └─────────┴────────────┘
              ▼
 ┌──────────────┐  deferred: waits for both branches (state aggregation)
 │ fraud        │  six tags of dataset_summary.md §3, across pages + DB history
 └──────┬───────┘
        ▼
 ┌──────────────┐  deferred: the weighted decision rule (0.2)
 │ decide       │  -> {decision, confidence, justification, agent_summaries}
 └──────┬───────┘
        ▼ ESCALATE only
 ┌──────────────┐  interrupt(): the run pauses at a LangGraph checkpoint (SQLite, CHECKPOINT_DB)
 │ human_review │  and waits in the review queue, across restarts; the reviewer's decision
 └──────────────┘  resumes it (Command(resume=...)). APPROVE / REJECT end without pausing.
```

Every node writes a timestamped row to `audit_event` as it finishes; human actions
(submit, override, reclassify, re-run) are audited too.

### 0.1a Configuration: nothing about the forms is coded

- **Model**: `.env` — `LLM_PROVIDER` (`ollama` local / `groq`), `OLLAMA_VISION_MODEL_NAME`, `OLLAMA_MODEL_NAME`.
- **Rules**: [backend/pipeline_rules.json](backend/pipeline_rules.json) (override path with `PIPELINE_RULES`):
  document types with their descriptions and field schemas (the vision prompt is built from
  these), required fields per type, required documents, treatment-date sources, thresholds,
  confidence weights, fraud severities and windows, policy retrieval settings, plan per policy prefix.
- Vision reads are cached by (file hash, model, prompt): changing the model or the rules re-reads pages.

### 0.2 Decision rule ([backend/app/claim/decide.py](backend/app/claim/decide.py))

```
ESCALATE  fraud score >= 0.3 OR any agent confidence < 0.6
          OR an agent errored, a page is UNKNOWN/blurry, or the claim form / ID is missing
REJECT    KYC failed OR a procedure is not covered OR schema invalid (claim form or prescription)
APPROVE   KYC passed + claim valid + covered + fraud score < 0.3
```

Precedence ESCALATE > REJECT > APPROVE. Confidence = weighted mean of the agents that ran
(`confidence_weights`). All numbers are in the rules file.

### 0.3 Agents

| Agent | Decides | Key rules |
|---|---|---|
| ingest | page type, fields | vision model; type not in the rules or confidence < `classifier_confidence_min` -> UNKNOWN; model says blurry/unreadable or variance < `blur_variance_min` -> low confidence |
| kyc | `kyc_passed` | two checks only: the claimant's name is a member (DOB / policy pick one of several same-name members), and an insurance card's text does not say CARD EXPIRED / NOT VALID (`kyc` in the rules) |
| claims | `schema_valid` | each type's `required` fields, plus every field the page marks missing (`fields_marked_missing`) |
| policy | `covered` | clauses retrieved from Qdrant (member's plan only), LLM decides from them, validated as `PolicyDecision` |
| fraud | `fraud_score`, `tags` | investigates everything else: id_expired, dob_mismatch, policy_mismatch plus the six dataset tags; severities/windows from `fraud` in the rules; score = 1 − Π(1 − s) |

### 0.4 API

| Method | Path | Purpose |
|---|---|---|
| POST | `/api/claim-bundles` | files (+ optional title, patient); queues the claim |
| GET | `/api/claim-bundles/{id}` | status, decision, every agent output, pages |
| GET | `/api/claim-bundles/{id}/audit` | timestamped audit trail |
| GET | `/api/review-queue` | escalated claims, oldest first |
| POST | `/api/claim-bundles/{id}/review` | `{decision, note}` override (note required) or `{doc_types}` correct + re-run |
| POST | `/api/claim-bundles/{id}/rerun` | re-run as is |

UI: **Claims** (upload + history), claim page (decision, agent cards, review panel, audit log), **Review queue**.

### 0.5 Dataset run (2026-10-17, RapidOCR + rule extractors — superseded by the vision reader)

Vision on this machine (gemma3:4b, CPU only): about 150 s per page, so a full run is about
6.5 h; on a dense claim form it misread the DOB, claim number and service year and missed the
amount and codes. A stronger vision model (or a GPU) is set in `.env` without code changes.

```
cd backend
../.venv/Scripts/python.exe -m scripts.warm_vision     # read every page once with the vision model (resumable, cached)
../.venv/Scripts/python.exe -m scripts.seed_members    # member + kyc tables from the IDs (vision)
../.venv/Scripts/python.exe -m scripts.run_dataset     # 34 claims through the API -> storage/dataset_run/scorecard.md
```

| Measure | Result |
|---|---|
| Claim decision accuracy | 27/34 (79.4%); always-APPROVE baseline 5/34 |
| Recall APPROVE / REJECT / ESCALATE | 5/5 · 13/14 · 9/15 |
| Page classification | 155/155 |
| Fraud tag recall | 4/6 |

The 7 misses, each checked against the page images:

| Claim | Label | Why it is missed |
|---|---|---|
| C_004 | amount_under_10k ($9,875) | no page shows $9,875; the claim totals $23,290 |
| C_012 | uncovered only (REJECT) | claim total $9,950 is in the structuring band, so the fraud rule escalates |
| C_013, C_018, C_029 | tampered_id | no visible or pixel-level trace (text matches the other pages; noise and colour match clean IDs) |
| C_025, C_026 | blur_simulated Rx | edge sharpness equals sharp scans (126 vs 125–132); only rx_PT_39451 (47.8) is truly blurred |

C_006's name_mismatch label also has no evidence (the same name on all five pages); it is
escalated correctly for its blurry prescription. `dataset/claim_forms/pp.png` is a 0-byte file
not in metadata; it is skipped.

---

## 1. The picture

```
 upload image
      │
      ▼
 ┌──────────────────────────┐
 │ 1 preprocessor           │  Groq vision: text + fields
 │                          │  Laplacian blur score
 └────────────┬─────────────┘
              ▼
 ┌──────────────────────────┐
 │ 2 classifier             │  content markers -> doc_type
 └────────────┬─────────────┘
   ┌──────────┼───────────────────────────┬──────────────────────────┐
   │ CLAIM_FORM                           │ DISCHARGE / Rx           │ ID / AMENDMENT          │ UNKNOWN
   ▼                                      ▼                          ▼                         ▼
 ┌────────────┐ ┌────────────┐      ┌────────────┐                 ┌────────────┐          orchestrator
 │ 3a kyc     │ │ 3b claims  │      │ 3b claims  │                 │ 3a kyc     │          (ESCALATE)
 │ parallel   │ │ parallel   │      │ clinical   │                 └─────┬──────┘
 └─────┬──────┘ └─────┬──────┘      └─────┬──────┘                       │
       │              ▼                   │                              │
       │       ┌────────────┐             │                              │
       │       │ 4 policy   │  Qdrant RAG │                              │
       │       │ CPT covered│             │                              │
       │       └─────┬──────┘             │                              │
       │             ▼                    ▼                              │
       │       ┌──────────────────────────────┐                         │
       │       │ 5 fraud  (6 detectors, DB)   │                         │
       │       └──────────────┬───────────────┘                         │
       └──────────────┬───────┴─────────────────────────────────────────┘
                      ▼
            ┌──────────────────────────┐
            │ 6 orchestrator (LangGraph│  ESCALATE > REJECT > APPROVE
            │   deferred node)         │
            └────────────┬─────────────┘
                         ▼
          APPROVE / REJECT  ──► stored, done
          ESCALATE          ──► needs_review ──► HIL review (UI or API)
                                                  │ wrong type → re-run with override
                                                  └ decision   → recorded as reviewer decision
```

Code: [backend/app/graph.py](backend/app/graph.py). Wiring is in
`route_after_classifier`, `route_after_claims`, `route_after_policy`.

### 1.1 Routing

| doc_type | Agents that run | Why |
|---|---|---|
| `CLAIM_FORM` | kyc **∥** claims → policy → fraud → orchestrator | billing and identity fields both present; kyc and claims run in parallel |
| `DISCHARGE_SUMMARY`, `PRESCRIPTION` | claims (clinical) → fraud → orchestrator | clinical facts feed readmission and date checks |
| `ID_DOCUMENT`, `POLICY_AMENDMENT` | kyc → orchestrator | identity only |
| `UNKNOWN` or upstream error | orchestrator (ESCALATE) | cannot route safely |

Agents that did not run are *not applicable*, not failures.

### 1.2 Decision rule

```
ESCALATE  any agent errored
          OR doc_type == UNKNOWN
          OR confidence < 0.6 (preprocessor, classifier, kyc, policy)
          OR fraud_score >= 0.3
REJECT    kyc_passed == False
          OR claim schema_valid == False          (CLAIM only)
          OR covered == False
APPROVE   everything that ran passed
```

Precedence **ESCALATE > REJECT > APPROVE**. A human look is cheaper than
wrongly rejecting a policyholder.

---

## 2. Agents

| # | Agent | File | Reads | Writes | Gate |
|---|---|---|---|---|---|
| 1 | preprocessor | [agents/preprocessor.py](backend/app/agents/preprocessor.py) | image file | `raw_text`, `fields`, `blur_variance`, `is_blurry`, `confidence` | blurry → confidence 0.4 → ESCALATE |
| 2 | classifier | [agents/classifier.py](backend/app/agents/classifier.py) | `raw_text` (or override) | `doc_type`, `confidence` | UNKNOWN → ESCALATE |
| 3a | kyc | [agents/kyc.py](backend/app/agents/kyc.py) | `fields`, `raw_text`, DB | `kyc_passed`, `flags`, `confidence` | fail → REJECT |
| 3b | claims | [agents/claims.py](backend/app/agents/claims.py) | `raw_text` | `kind`, `extracted_fields`, `schema_valid`, `validation_errors` | schema invalid → REJECT (CLAIM only) |
| 4 | policy | [agents/policy.py](backend/app/agents/policy.py) | CPT codes, Qdrant | `PolicyDecision` (see 2.1) | not covered → REJECT |
| 5 | fraud | [agents/fraud.py](backend/app/agents/fraud.py) | claim/clinical facts, DB history | `fraud_score`, `anomalies`, `risk_level`, `member_id` | ≥ 0.3 → ESCALATE |
| 6 | orchestrator | [agents/orchestrator.py](backend/app/agents/orchestrator.py) | all of the above | `decision`, `confidence`, `justification`, `needs_review` | terminal |

### 2.1 Return types

Policy returns a validated Pydantic model:

```python
class PolicyDecision(BaseModel):
    covered: bool
    coverage_percentage: float      # 0..100
    policy_clause: str              # the clause that decided it
    exclusions: list[str]           # "CPT: reason" per excluded code
    cpt_codes_checked: list[str]
    confidence: float               # 0..1
```

Kyc returns `{kyc_passed, flags, confidence}`. Claims returns `{kind, extracted_fields, schema_valid, validation_errors}`.

### 2.2 KYC: the notebook's `insurance_validity`

Ported from `agents/notebook/app-v1.ipynb` as plain regex, with no LLM wrapper.
A document that is an insurance document and prints **NOT VALID**, **INVALID**,
**CARD EXPIRED** or **EXPIRED CARD** fails KYC. A non-insurance document (for
example a driver's licence) is not failed by this check. It falls through to the
identity checks.

Identity checks: member lookup (member id → policy number → name + DOB), DOB
and policy mismatch (only when both sides have a value), and ID expiry against the
treatment date on the same page (today if absent).

### 2.3 Fraud: `dataset_summary.md` section 3

| Detector | Rule | Severity |
|---|---|---|
| `duplicate_claim` | same member, service date and amount as a prior case | 0.9 |
| `amount_under_10k` | billed amount in [9000, 10000) | 0.6 |
| `proc_diag_mismatch` | CPT 59400 with no O-chapter ICD-10 | 0.5 |
| `name_mismatch` | document name differs from member record (initials ignored) | 0.7 |
| `readmission_30d` | prior stay (same document or DB) ended 1–30 days before admission | 0.7 |
| `date_conflict` | prescription date ≥ 45 days from service or discharge date | 0.6 |

`score = 1 − Π(1 − severity)`. A document with no identifiable member scores 0,
and KYC's rejection is the cause that gets reported.

---

## 3. Data

### 3.1 MySQL (`medishield`, root/root on localhost)

| Table | Purpose | Key columns |
|---|---|---|
| `member` | member master data, seeded from the ID documents | `member_id`, `full_name`, `date_of_birth`, `policy_number` |
| `kyc` | ID expiry per member | `member_id`, `id_expiry_date` |
| `claim` | every processed claim (duplicate and history source) | `case_id`, `member_id`, `service_date`, `claim_amount`, `cpt_codes` JSON, `icd10_codes` JSON |
| `clinical_event` | admission, discharge and Rx dates (readmission and date checks) | `case_id`, `member_id`, `admission_date`, `discharge_date`, `prescription_date` |
| `case_record` | every case: status, decision, full agent result JSON, reviewer override | `case_id`, `status`, `decision`, `result` |
| `policy_document` | ingested policy PDFs | `filename`, `chunks` |

Schema is created by `init_db()` in [backend/app/db.py](backend/app/db.py) on startup.

### 3.2 Vector store (Qdrant, embedded)

Policy PDFs are converted with Docling, split by section with `HybridChunker`
(256 tokens), embedded with `sentence-transformers/all-MiniLM-L6-v2` (fastembed),
and stored in `backend/storage/qdrant`. Dense search only, for now.

Currently ingested: `medishield_gold_plan.pdf` (59 chunks), `medishield_silver_plan.pdf` (43 chunks).

### 3.3 Seeding members

`cd backend && ../.venv/Scripts/python.exe -m scripts.seed_members`

Reads `dataset/metadata.json` for patient id and policy number, and reads name,
date of birth and ID expiry from each patient's ID document with the same vision
preprocessor. 30 members seeded. Some ID cards show no DOB, so those members have a
NULL DOB and resolve by policy number.

---

## 4. API

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/health` | service and policy chunk count |
| POST | `/api/cases` | upload an image (PNG, JPG, TIFF, BMP), run the pipeline, return the case |
| GET | `/api/cases` | list cases |
| GET | `/api/cases/{id}` | case with every agent output and the trace |
| GET | `/api/cases/{id}/document` | the uploaded image |
| POST | `/api/cases/{id}/review` | HIL: `{doc_type}` re-runs with that type; `{decision}` overrides the decision; `note` |
| POST | `/api/policies` | upload a policy PDF, ingest into Qdrant |
| GET | `/api/policies` | list ingested policies |

The UI in [frontend/](frontend/) has pages for the case list, upload, case detail
with the review form, and policy upload.

---

## 5. Plan and status

| # | Step | Acceptance | Status |
|---|---|---|---|
| 1 | Backend restructure, MySQL schema | tables created in `medishield` | ✅ |
| 2 | Vision preprocessor + blur | OCR and fields on real documents | ✅ live; blur threshold limited (§6.2) |
| 3 | Classifier | per-class recall on dataset | ✅ 98.0% on the tuning set, not held-out (§6.1) |
| 4 | KYC port of `insurance_validity` | NOT VALID card → REJECT | ✅ live on `id_PT_15075` (REJECT, NOT_VALID) |
| 5 | Claims extraction + schema | missing NPI/signature → REJECT | ✅ live on `claim_PT_82132` (REJECT) |
| 6 | Policy RAG (Docling + Qdrant) | CPT 17000 excluded, 20322 covered | ⚠ ingested (102 chunks). Two live results were wrong before fixing the prompt and clause cap (§6.6). Re-check pending quota. |
| 7 | Fraud, six detectors | duplicate, structuring, readmission, date conflict | ✅ duplicate live (ESCALATE); the other five by unit test |
| 8 | Parallel kyc ∥ claims | graph trace shows both | ✅ unit test `test_clean_claim_is_approved…` |
| 9 | HIL review | decision override recorded, `needs_review` cleared | ✅ live via API (no LLM). Doc-type re-run covered by unit test |
| 10 | FastAPI | endpoints in §4 | ✅ |
| 11 | Next.js UI | production build passes, five routes | ✅ `npm run build` |
| 12 | LangGraph orchestrator | orchestrator is the deferred final node | ✅ (single graph; no subgraph) |
| 13 | Full dataset run and scorecard | §6.4 | ☐ blocked by Groq daily quota (§6.3) |
| 14 | Held-out classifier set | ≥ 20 new docs | ☐ |

Test status: `cd backend && ../.venv/Scripts/python.exe -m unittest tests.test_pipeline` → **31 tests pass**.

### 5.1 Live results in this session

| Document | Result | Note |
|---|---|---|
| `claim_PT_82132` | REJECT: missing provider NPI and physician signature | matches dataset |
| `id_PT_15075` (insurance card, CARD EXPIRED / NOT VALID) | REJECT: NOT_VALID | matches dataset |
| `claim_PT_20322_dup` | ESCALATE: duplicate_claim (0.9) | matches dataset; the original was stored in the same run |
| `claim_PT_20322` | REJECT (before fix) → code now covers an unlisted CPT | re-check pending |
| `claim_PT_71993` (CPT 17000) | APPROVE (before fix) → clause truncation dropped the exclusion row | fixed; re-check pending |
| `claim_PT_69470` | REJECT: DOB mismatch (before fix) → missing DOB was counted as a mismatch | fixed; re-check pending |
| `discharge_PT_19116` | APPROVE (before fix) → prior stay not read from the document | prior-stay field added; re-check pending |
| `rx_PT_39451` | ESCALATE: Groq daily token quota | re-run pending |

---

## 6. Findings and limits

### 6.1 Classifier

Tuned on the same 155 dataset images, so optimistic: **98.0% on 151 labelled docs**
(discharge 28/30, ID 30/30, amendment 30/30, Rx 29/30, claim 31/31, unknown 4/4).
A held-out check is still needed.

### 6.2 Blur

Laplacian variance catches 2 of the 4 dataset docs labelled `blur_simulated`
(variances 17 and 25). The other two (about 2300–2500) look sharp by this metric.
One sharp doc is falsely flagged (variance 6.9, a blank page). Threshold 50–200
makes no difference. The OCR-confidence gate is the second line of defence.

### 6.3 Groq limits

The key is on the Groq on-demand tier: **200,000 tokens per day** for
`qwen/qwen3.8-27b`. A full-size page costs about 2000 tokens. Each claim uses
roughly 5–8k tokens across vision, extraction and policy calls. The daily quota
is exhausted after about 25–30 documents.

Mitigations in place: vision input downscaled to 1280 px JPEG (about 40× less base64),
rate-limit waits of up to 90 s, and no retry on a daily-quota error.

For the full dataset run either wait for the quota to refill, or upgrade the tier.

### 6.4 Scorecard (to fill after a full run)

| Criterion | Weight | Measured |
|---|---|---|
| Classification accuracy | 20% | 98.0% tuning set (held-out pending) |
| Extraction completeness | 20% | ☐ |
| Policy retrieval quality | 15% | ☐ spot-check |
| Decision correctness | 25% | ☐ |
| UI functionality | 10% | ☐ |
| Code quality | 10% | typed state and models, no hardcoded secrets |

### 6.5 Known limits

- The orchestrator is a LangGraph node, not a separate subgraph.
- Policy retrieval is dense only. The notebook's sparse BM25 hybrid is not ported.
- Discharge readmission works only when the prior stay is on the same page or already in the DB.
- Duplicate detection is stateful: re-running the same claim flags it against its own earlier run. The `case_id` exclusion stops a case matching itself, but a second upload of the same claim is still flagged. Clear `claim` for demo runs.
- The HIL re-run is synchronous. A large batch would need a queue.
- Vision output is not scored for accuracy beyond the spot checks.

---

### 6.6 What the live run taught us

1. **Policy default was wrong.** The first prompt treated a code missing from the exclusion tables as not covered. Fixed: covered unless an exclusion applies (D4).
2. **Clause truncation cut the exclusion table.** The CPT 17000–17999 row sat at the end of a chunk, and a 900-character cap removed it. Cap raised to 1800 characters, and the query now uses the table's own wording.
3. **Vision misfiled a policy number as a member id.** `resolve_member` now tries a policy-shaped member id as a policy number (D8).
4. **A blank member DOB looked like a mismatch.** Only a DOB present on both sides is compared.
5. **A full-size scan costs about 2000 tokens per call.** Images are now downscaled (D12).

## 7. Decisions log

| # | Decision | Why |
|---|---|---|
| D1 | OCR and field extraction use the Groq vision model (`VISION_MODEL_NAME`), not Tesseract | the vision model now works with this key. It reads fields, not only text. |
| D2 | kyc runs **in parallel** with claims for a claim form (user request) | expiry is checked against the service date read on the same page, so no dependency on claims |
| D3 | Policy is retrieved from Qdrant (RAG), not a rule table | user request; the policy PDFs are stored as vectors |
| D4 | Policy treats a code as covered unless an exclusion applies | the exclusion tables list specific codes; "not listed" is not excluded |
| D5 | KYC invalid-marking check is deterministic (port of the notebook function) | the notebook wrapped a regex in an LLM agent for no gain |
| D6 | Non-insurance documents are not failed by the marking check | a driver's licence would be rejected otherwise |
| D7 | ESCALATE > REJECT > APPROVE | a human look beats wrongly rejecting a policyholder |
| D8 | Member lookup order: member id → policy number → name + DOB; a policy-shaped member id is tried as a policy | vision reads sometimes put a policy number in the member id field |
| D9 | Blurry scan lowers preprocessor confidence to 0.4, so the orchestrator escalates | blur is a quality problem for a human, not a rejection |
| D10 | HIL: wrong type → re-run with `doc_type_override`; decision only → recorded override | keeps one decision path; the re-run gives every agent the corrected type |
| D11 | Fraud for a document with no identifiable member scores 0 and does not error | KYC already rejects an unknown member; an error would turn that into ESCALATE |
| D12 | Vision input downscaled to 1280 px JPEG | token budget; about 40× less base64 |
| D13 | Retry: no retry on daily-quota errors; wait up to 90 s on rate limits | a daily quota does not refill in seconds |
| D14 | MySQL, root/root on localhost | user request |

---

## 8. How to run

```
# MySQL must be running on localhost:3306 (root/root)

cd backend
../.venv/Scripts/python.exe -m unittest tests.test_pipeline -v     # offline, no keys
../.venv/Scripts/python.exe -m uvicorn app.main:app --port 8000    # API

cd ../frontend
npm install
npm run dev                                                        # UI on :3000
```

Requires: `GROQ_API_KEY`, `VISION_MODEL_NAME`, `MODEL_NAME` in the project-root `.env`.
Optional: `MYSQL_HOST`, `MYSQL_PORT`, `MYSQL_USER`, `MYSQL_PASSWORD`, `MYSQL_DB`.
