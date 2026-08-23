# Clinical Trial Patient-Matching Platform

Decision support for finding clinical trials that may fit a patient's documented clinical profile.

> Status: technical design and implementation plan. This repository must not claim clinical validity until it has been evaluated on representative data and reviewed by qualified clinicians.

## Problem

Clinical-trial eligibility is usually published as long, semi-structured free text. A coordinator must search for trials, read every inclusion and exclusion criterion, compare each criterion with the patient's record, and identify missing information. This is slow and error-prone, especially when the patient record and trial criteria use different terminology.

This platform narrows the search space and explains its reasoning criterion by criterion. It is not an autonomous enrolment system.

## Product boundary

The system may return only these outcomes:

- `potential_match`: no documented exclusion was found, but a coordinator must verify the result.
- `likely_excluded`: at least one exclusion is supported by documented patient evidence.
- `needs_review`: required facts are missing, ambiguous, stale, or cannot be evaluated safely.
- `not_relevant`: the trial does not match the patient's condition or intent closely enough to evaluate.

It must never present `eligible` as a final clinical decision. It must never infer an absent patient fact as false, invent a laboratory value, recommend treatment, or contact a trial site automatically.

## Users

- Research coordinators reviewing candidate trials
- Clinicians assisting patients with trial discovery
- Researchers benchmarking patient-to-trial retrieval methods
- Developers testing FHIR-based decision-support workflows with synthetic data

## MVP scope

### Included

- Import synthetic FHIR R4 patient bundles
- Fetch and version trial records from ClinicalTrials.gov API v2
- Filter by condition, age, sex, study status, phase, country, and intervention type
- Hybrid retrieval using lexical and semantic search
- Parse inclusion and exclusion text into source-linked criteria
- Evaluate deterministic criteria such as age, sex, diagnosis, medication, and numeric laboratory thresholds
- Use an LLM only for criteria that cannot be resolved deterministically
- Display supporting patient facts and the original trial text for every result
- Allow a reviewer to correct criterion outcomes
- Evaluate retrieval using TREC Clinical Trials 2021/2022 topics and qrels

### Explicitly excluded from the MVP

- Production EHR integration
- Use of real protected health information
- Autonomous eligibility or enrolment decisions
- Treatment recommendations
- Trial-site outreach
- Claims of HIPAA, GDPR, FDA, or medical-device compliance
- A universal medical terminology service
- Continuous learning from reviewer corrections without a governed model-release process

## Architecture

```mermaid
flowchart TD
    UI[Reviewer web app] --> API[FastAPI API]
    API --> DB[(PostgreSQL)]
    API --> Q[Redis job queue]
    Q --> IW[Trial ingestion worker]
    Q --> MW[Matching worker]
    IW --> CTG[ClinicalTrials.gov API]
    IW --> IDX[(PostgreSQL search and pgvector)]
    MW --> IDX
    MW --> RULES[Criterion rule engine]
    MW --> LLM[LLM adjudicator]
    RULES --> DB
    LLM --> DB
```

### Recommended stack

| Layer | Technology | Reason |
|---|---|---|
| Frontend | React, TypeScript, TanStack Query, Tailwind CSS | Typed reviewer workflow and server-state management |
| API | FastAPI, Pydantic, SQLAlchemy 2 | Async API and strict schemas |
| Database | PostgreSQL | Transactional records, JSONB snapshots, audit history |
| Vector search | pgvector | Keeps the MVP operationally simple |
| Lexical search | PostgreSQL full-text search initially; OpenSearch later | BM25-style retrieval is useful but a separate cluster is unnecessary for the first version |
| Jobs | Celery or Dramatiq with Redis | Trial ingestion and matching are asynchronous |
| LLM gateway | Provider-neutral adapter | Prevents model-specific logic from leaking into the domain layer |
| Observability | OpenTelemetry, Prometheus, structured logs | Trace ingestion, retrieval, model calls, and rule outcomes |
| Local deployment | Docker Compose | Reproducible demo environment |

Do not deploy both OpenSearch and pgvector in the first milestone unless the PostgreSQL lexical baseline is measurably inadequate. Extra infrastructure without a benchmark devalues the project.

## Core workflow

### 1. Trial ingestion

1. Fetch records from ClinicalTrials.gov API v2.
2. Store the unmodified source response and retrieval timestamp.
3. Extract searchable fields, including NCT ID, title, conditions, interventions, phase, status, locations, minimum age, maximum age, sex, and eligibility text.
4. Hash the source fields used by the matcher.
5. Re-parse criteria only when that hash changes.
6. Generate embeddings for retrieval fields.
7. Mark superseded records without deleting their previous snapshots.

Every displayed match must show `source_updated_at` and `ingested_at`. Trial status and eligibility text can change after indexing.

### 2. Patient normalization

The MVP accepts a FHIR R4 `Bundle` containing a limited, documented subset:

- `Patient`
- `Condition`
- `Observation`
- `MedicationStatement` or `MedicationRequest`
- `Procedure`
- `AllergyIntolerance`

The normalizer converts these resources into a versioned set of patient facts. Each fact retains its FHIR resource ID, code system, effective time, value, unit, and provenance.

```json
{
  "fact_id": "fact_01J...",
  "patient_id": "synthetic-001",
  "kind": "observation",
  "code": {
    "system": "http://loinc.org",
    "value": "718-7",
    "display": "Hemoglobin"
  },
  "value": 11.2,
  "unit": "g/dL",
  "effective_at": "2026-07-02T00:00:00Z",
  "source": {
    "resource_type": "Observation",
    "resource_id": "obs-42"
  }
}
```

Missing data remains missing. The normalizer must not create negative facts such as “no liver disease” merely because no liver condition appears in the bundle.

### 3. Candidate retrieval

Retrieval happens before expensive criterion evaluation.

1. Build a query from active conditions, relevant procedures, medications, age, and sex.
2. Apply hard metadata filters only when the patient fact is reliable.
3. Retrieve lexical candidates.
4. Retrieve semantic candidates.
5. Fuse the rankings using reciprocal rank fusion.
6. Rerank the top candidates with a cross-encoder or a constrained LLM reranker.
7. Send only the top `K` trials to criterion-level evaluation.

Retrieve broadly enough to avoid losing viable trials, but count explicitly excluded trials as a serious ranking error.

### 4. Criterion parsing

The parser converts eligibility text into atomic clauses while retaining the exact source span.

```json
{
  "criterion_id": "NCT00000000-inc-07",
  "category": "inclusion",
  "source_text": "Hemoglobin must be at least 10 g/dL within 14 days before enrolment.",
  "field": "observation",
  "concept": "hemoglobin",
  "operator": ">=",
  "value": 10,
  "unit": "g/dL",
  "time_window_days": 14,
  "parser_confidence": 0.91,
  "requires_human_review": false
}
```

The structured representation is a parsing hypothesis, not ground truth. The UI always keeps the original text visible.

### 5. Criterion evaluation

Use a deterministic engine whenever possible:

- Age ranges
- Recorded sex requirements
- Exact coded diagnoses
- Numeric laboratory thresholds with compatible units
- Medication presence or absence when explicitly documented
- Date and recency windows
- Prior procedure presence

Use an LLM adjudicator only when:

- The criterion is qualitative
- Terminology differs substantially
- The relation requires limited textual reasoning
- A deterministic matcher returns `unknown`

The LLM receives only the criterion, relevant patient facts, their timestamps, and a strict output schema.

```json
{
  "outcome": "met | not_met | unknown | conflicting",
  "evidence_fact_ids": ["fact_01J..."],
  "reason": "Short, source-grounded explanation",
  "confidence": 0.0,
  "requires_review": true
}
```

The API rejects output containing unknown fact IDs. Confidence is model-reported metadata and must not be interpreted as a calibrated probability.

### 6. Trial-level aggregation

| Condition | Result |
|---|---|
| At least one supported exclusion criterion | `likely_excluded` |
| No supported exclusion and all mandatory inclusion criteria met | `potential_match` |
| Any mandatory criterion unknown, conflicting, or stale | `needs_review` |
| Trial fails the relevance threshold | `not_relevant` |

Ranking within `potential_match` and `needs_review` may consider relevance, number of unresolved criteria, trial status freshness, and geographic suitability. It must not combine these into a fake “eligibility probability.”

## Data model

| Table | Important fields |
|---|---|
| `organizations` | `id`, `name`, `created_at` |
| `users` | `id`, `organization_id`, `role`, `email`, `password_hash` |
| `patients` | `id`, `organization_id`, `external_ref`, `synthetic`, `created_at` |
| `patient_imports` | `id`, `patient_id`, `fhir_version`, `source_hash`, `status` |
| `patient_facts` | `id`, `patient_id`, `kind`, `code`, `value`, `unit`, `effective_at`, `provenance` |
| `trials` | `nct_id`, current searchable fields, `source_updated_at`, `ingested_at` |
| `trial_versions` | `id`, `nct_id`, `source_hash`, raw JSON snapshot |
| `criteria` | `id`, `trial_version_id`, `category`, source text, parsed JSON, parser version |
| `match_runs` | `id`, `patient_import_id`, configuration snapshot, status, timestamps |
| `trial_matches` | `id`, `match_run_id`, `nct_id`, retrieval scores, final outcome |
| `criterion_results` | criterion, outcome, evidence IDs, evaluator version, explanation |
| `review_decisions` | reviewer, previous value, corrected value, reason, timestamp |
| `audit_events` | actor, action, target, request ID, timestamp, metadata |

All model, prompt, parser, terminology, and retrieval versions used in a match run must be immutable and queryable.

## API design

| Method | Endpoint | Purpose |
|---|---|---|
| `POST` | `/auth/login` | Create an authenticated session |
| `POST` | `/patients/import/fhir` | Validate and enqueue a synthetic FHIR bundle import |
| `GET` | `/patients/{patient_id}` | Return normalized patient data |
| `POST` | `/trial-syncs` | Start an authorized trial ingestion job |
| `GET` | `/trial-syncs/{job_id}` | Read ingestion status and counts |
| `POST` | `/match-runs` | Start a versioned matching run |
| `GET` | `/match-runs/{run_id}` | Read run status and configuration |
| `GET` | `/match-runs/{run_id}/results` | List ranked trial matches |
| `GET` | `/matches/{match_id}` | Return criterion-level evidence |
| `POST` | `/criterion-results/{id}/review` | Record a reviewer correction |
| `GET` | `/trials/{nct_id}/versions` | Display source changes over time |

Long-running endpoints return `202 Accepted` and a job ID. Do not hold HTTP requests open while parsing hundreds of trials.

## Reviewer interface

### Patient page

- Timeline of conditions, medications, procedures, and observations
- Data freshness indicators
- Missing-data warnings
- Direct link from each normalized fact to its source FHIR resource

### Match-results page

- Separate tabs for potential matches, review required, and exclusions
- Trial status and source-update timestamp
- Search and filter controls
- Clear distinction between retrieval relevance and criterion outcome

### Criterion review page

- Original criterion text
- Parsed representation
- Patient evidence with timestamps
- Deterministic or LLM evaluation path
- Reviewer correction form
- Full audit history

## Repository layout

```text
clinical-trial-matcher/
├── apps/
│   ├── backend/                 # FastAPI, matching, workers, migrations, tests, and fixtures
│   │   ├── app/
│   │   ├── migrations/
│   │   ├── tests/
│   │   └── datasets/
│   └── web/                     # React application
├── docker-compose.yml
└── README.md
```

## Local development

### Prerequisites

- Docker and Docker Compose
- Node.js 22+
- Python 3.12+
- An LLM provider key or a configured local model

### Environment

```bash
cp .env.example .env
```

```dotenv
DATABASE_URL=postgresql+asyncpg://app:app@postgres:5432/trial_matcher
REDIS_URL=redis://redis:6379/0
CLINICAL_TRIALS_API_BASE_URL=https://clinicaltrials.gov/api/v2
LLM_PROVIDER=local
LLM_MODEL=replace-with-an-explicit-version
EMBEDDING_MODEL=replace-with-an-explicit-version
ALLOW_REAL_PATIENT_DATA=false
```

The application must refuse imports when `ALLOW_REAL_PATIENT_DATA=false` and the payload lacks the project's synthetic-data marker.

### Start

```bash
docker compose up --build
```

### Test

```bash
docker compose exec api pytest
npm --prefix apps/web test
```

## Evaluation plan

### Retrieval benchmark

Use the TREC Clinical Trials 2021 and 2022 topics and qrels. Their judgements distinguish non-relevant, excluded, and eligible trial documents.

Report:

- nDCG@5 and nDCG@10
- Precision@5 and Precision@10 using eligible trials as relevant
- Recall@50
- Mean reciprocal rank
- Excluded-trial rate in the top 10
- Retrieval latency

Never train on a benchmark year and report that same year's test performance without a separate untouched split.

### Criterion benchmark

Create a clinician-reviewed evaluation set containing atomic criteria, patient facts, expected outcomes, and evidence spans.

Report:

- Macro F1 across `met`, `not_met`, `unknown`, and `conflicting`
- Exclusion recall
- False-clearance rate: exclusion criteria incorrectly marked as met or unknown
- Evidence precision
- Abstention rate
- Calibration curves only if confidence has been calibrated on held-out data

### End-to-end acceptance criteria

These are engineering gates, not clinical-validity claims:

- Every output cites at least one trial source span
- Every non-unknown criterion result cites valid patient fact IDs
- Missing patient facts produce `unknown`, never `met`
- A supported exclusion always prevents `potential_match`
- Re-running an immutable configuration produces a traceable new run
- Trial-record changes invalidate affected cached criterion results
- No real patient data is used in public demos or committed fixtures

Do not invent target accuracy percentages before a baseline exists.

## Testing strategy

### Unit tests

- Unit and date normalization
- Age calculations at trial cutoff dates
- Inclusion/exclusion aggregation
- Missing-value propagation
- Trial-source hashing
- FHIR resource mapping

### Property-based tests

- Unit conversions preserve equivalence
- Adding an unresolved fact cannot turn `needs_review` into `potential_match`
- Adding a supported exclusion cannot improve a match outcome
- Reordering criteria does not change aggregation

### Contract tests

- ClinicalTrials.gov response fixtures against the API client
- FHIR R4 bundle validation
- LLM output schema and evidence-ID validation

### Adversarial tests

- Prompt injection embedded in trial text
- Negated diagnoses
- Conflicting laboratory results
- Stale observations
- Multiple units for the same measurement
- Ambiguous temporal language
- Criteria containing nested AND/OR logic

## Security and privacy

- Use synthetic data in development and public demos
- Encrypt stored sensitive data and use TLS in transit
- Apply organization-level row isolation
- Keep model-provider data retention disabled where supported
- Redact clinical content from logs and traces
- Scan uploaded files and enforce size/type limits
- Use least-privilege service credentials
- Record all patient reads and reviewer changes in an audit log
- Set retention and deletion policies explicitly

These controls do not make the system HIPAA compliant. Compliance requires organizational policies, contracts, infrastructure controls, risk assessment, and legal review beyond this codebase.

## Observability

Track:

- Trial ingestion counts, failures, and source lag
- Retrieval latency and candidate counts
- Rule-engine versus LLM resolution rate
- Unknown and conflicting criterion rates
- Model tokens, latency, errors, and cost
- Reviewer override rate by criterion type
- Outcome changes between evaluator versions

Do not log raw patient facts or unrestricted prompts in production telemetry.

## Logical roadblocks and limitations

| Risk | Why it matters | Required mitigation |
|---|---|---|
| Eligibility criteria are free text | Nested logic and temporal requirements are difficult to parse reliably | Preserve source text, use atomic clauses, abstain on ambiguous logic, require review |
| Patient records are incomplete | Absence of evidence is not evidence of absence | Use three/four-valued outcomes and propagate `unknown` |
| TREC is primarily a retrieval benchmark | Good TREC scores do not prove criterion-level or clinical safety | Maintain a separate clinician-reviewed criterion set |
| Synthetic-to-real data gap | Synthea is useful for software testing but does not reproduce real EHR complexity | Make no real-world performance claim until representative data is evaluated |
| Terminology mismatch | Trial text, SNOMED CT, LOINC, and local codes may not align | Use a versioned terminology adapter and expose unmapped concepts |
| Unit and reference-range variation | A numeric match can be wrong even when values look comparable | Use a validated unit library and retain source units and ranges |
| Trial records change | Cached matches can become stale | Version source records and invalidate derived results |
| Site status differs from study status | A recruiting study may not recruit at every listed site | Display site-level uncertainty and verify current contact information manually |
| LLM explanations can sound authoritative | Fluent text may conceal an unsupported judgement | Require evidence IDs, schema validation, abstention, and reviewer confirmation |
| Demographic and access bias | Ranking by distance or record completeness can disadvantage patients | Audit outcome distributions and keep ranking factors visible |
| Regulatory ambiguity | Decision-support software can enter regulated territory | Keep the MVP research-only and obtain legal/regulatory review before clinical use |
| Cost and latency | Criterion-level LLM calls can grow with candidates × criteria | Retrieve first, batch safely, cache by immutable hashes, and cap work per run |

The project is valuable only if it demonstrates calibrated abstention and traceability. Hiding these limitations would make it look less credible, not more advanced.

## Delivery roadmap

### Phase 1: deterministic baseline

- Trial ingestion and versioning
- Synthetic FHIR import
- PostgreSQL lexical retrieval
- Age, sex, condition, date, and numeric rules
- Reviewer UI
- TREC retrieval baseline

### Phase 2: semantic matching

- Embedding retrieval and reciprocal rank fusion
- Criterion parser
- Source-linked LLM fallback
- Criterion evaluation set

### Phase 3: review and audit

- Reviewer corrections
- Immutable configuration snapshots
- Evaluation dashboard
- Outcome-diff reports between releases

### Phase 4: integration research

- SMART on FHIR sandbox integration
- Terminology-service adapter
- Site-distance filters
- Security review

Real EHR deployment is not a normal roadmap checkbox; it requires a separate governance and validation programme.

## Demo script

1. Import a synthetic FHIR R4 patient bundle.
2. Show the normalized clinical timeline and missing-data warnings.
3. Run the deterministic baseline.
4. Display a potential match, a likely exclusion, and a needs-review case.
5. Open one criterion and trace it to the original trial sentence and patient fact.
6. Correct an outcome and show the audit record.
7. Compare two evaluator versions on the same immutable patient/trial snapshot.

## References

- [ClinicalTrials.gov Data and API](https://clinicaltrials.gov/data-api)
- [ClinicalTrials.gov API v2](https://clinicaltrials.gov/data-api/api)
- [ClinicalTrials.gov study-data structure](https://clinicaltrials.gov/data-api/about-api/study-data-structure)
- [TREC 2022 Clinical Trials Track](https://trec.nist.gov/data/trials2022.html)
- [TREC 2021 Clinical Trials Track](https://trec.nist.gov/data/trials2021.html)
- [HL7 FHIR R4 Patient](https://hl7.org/fhir/R4/patient.html)
- [HL7 FHIR R4 Condition](https://hl7.org/fhir/R4/condition.html)
- [HL7 FHIR R4 Observation](https://hl7.org/fhir/R4/observation.html)
- [SMART App Authorization Guide](https://docs.smarthealthit.org/authorization/)
- [Synthea synthetic patient generator](https://github.com/synthetichealth/synthea)

## Disclaimer

This project is for engineering research and portfolio demonstration. It does not provide medical advice, determine clinical-trial eligibility, or replace review by trial investigators and qualified healthcare professionals.
