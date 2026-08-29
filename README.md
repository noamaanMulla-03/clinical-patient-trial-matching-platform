# Trial Match Review

Trial Match Review is a local, research-only demonstration of a clinical-trial
review workflow. It accepts a **synthetic FHIR R4 patient Bundle**, loads public
ClinicalTrials.gov studies into a local catalogue, retrieves a bounded set of
possibly relevant trials, and shows the evidence a reviewer needs to inspect
them.

It is designed to make the question “why is this trial on this list?”
answerable without reading worker logs. It is **not** an eligibility engine,
medical device, treatment recommender, enrolment system, or outreach tool.

## Important boundary

This repository is for engineering research and portfolio demonstration only.

- Only synthetically marked FHIR data is accepted.
- Public trial records come from ClinicalTrials.gov.
- A result is a review candidate, never a decision that someone is eligible,
  should receive treatment, should enrol, or should be contacted.
- A clinician, study investigator, or qualified coordinator must make any
  real-world decision.
- The project makes no claim of clinical validity, regulatory approval, HIPAA,
  GDPR, FDA, or medical-device compliance.
- Production-like startup is blocked by default. Deliberately enabling it still
  does not permit real patient data or clinical use.

## What is implemented

The working local demonstration includes:

- A trial catalogue screen for loading a small fixed demo collection or a
  deliberately bounded public ClinicalTrials.gov selection.
- Synthetic FHIR R4 Bundle import and normalisation for patient demographics,
  conditions, observations, medications, procedures, and allergies.
- A patient timeline that shows source-linked facts, dates, data-quality flags,
  and a link to the exact FHIR resource snapshot that supplied each fact.
- Versioned public trial ingestion, including title, status, source update time,
  eligibility text, conditions, and interventions.
- Durable background jobs for trial updates, trial embeddings, and match runs.
- Hybrid trial retrieval: full-text lexical retrieval and local semantic
  retrieval are fused, then a conservative structured-evidence ordering is
  applied to already-retrieved candidates.
- Source-linked deterministic criterion evaluation for the currently supported
  narrow parser scope: explicit age clauses in clearly labelled inclusion or
  exclusion sections.
- Results tabs for potential matches, needs review, likely exclusions, and
  other non-relevant candidates, plus search and filters.
- Criterion detail with original trial text, parsed rule, patient evidence,
  dates, evaluation path, and an append-only reviewer-correction history.
- Local, reproducible regression checks using frozen synthetic fixtures,
  synthetic FHIR cases, and optional public TREC research evaluation tooling.

## How the reviewer workflow works

The interface is intentionally organised as a short sequence.

### 0. Load a public trial catalogue

Start on **Trial catalogue**. This does not use patient information.

**Load demo trials** queues a fixed three-study public collection used for the
local demo. It is source-controlled, so its membership is predictable. Pressing
it again fetches the same NCT records; trials are identified by NCT ID and their
source version, so the operation does not create duplicate current trials.

The **Advanced bounded update** is for loading public studies from
ClinicalTrials.gov by exactly one selection: an NCT ID, condition, or search
phrase. The page-size and page-range fields limit how much public catalogue data
can be collected in one job. They do not perform patient matching.

A trial update runs in the worker, not in the browser request. The catalogue
shows its latest update separately from the full list of trials currently
available for matching. A newer version of an existing NCT record supersedes its
current searchable version while the older source snapshot remains available for
historical traceability.

### 1. Import a synthetic patient Bundle

On **Patient import**, paste one complete FHIR R4 Bundle and select **Import
synthetic Bundle**. The Bundle must include this exact top-level marker:

```json
{
  "system": "urn:clinical-trial-matcher:data-classification",
  "code": "synthetic-data"
}
```

The API rejects unmarked Bundles before normalising or storing them. An accepted
import creates two deliberately different identifiers:

- The **synthetic patient ID** is the FHIR `Patient.id`. Use it to load the
  patient’s latest completed timeline.
- The **completed patient import ID** identifies one immutable import snapshot.
  Use it for a match run so a result can always be traced to the exact Bundle
  that was used, even if that same synthetic patient is imported again later.

Importing the same Bundle again creates another immutable import snapshot. It
does not silently merge facts across imports.

### 2. Inspect the patient timeline

The **Patient timeline** displays facts only from the latest completed import for
the patient ID you enter. Each fact shows:

- Its type and value, such as a condition, recorded sex, or birth date.
- The original coding system and code when available.
- Its effective or recorded date.
- Its source FHIR resource link.
- Any data-quality condition, including missing dates, conflicts, invalid values,
  or stale information.

“Stale” means the source date is more than 365 days before the import
evaluation date. A stale active chronic condition may still help find broad trial
candidates, but it is not treated as fresh evidence for a time-sensitive
criterion. Missing, conflicting, invalid, and unsupported facts are not guessed
at.

Use **Use for match run** to carry that exact completed import ID to the next
screen.

### 3. Queue and monitor a match run

On **Match-run status**, select **Queue run**. This creates a durable work item;
it does not make the browser wait for retrieval. The local worker claims the job,
marks it running, and then marks it completed, failed, or cancelled.

The status card records when the run was queued, started, and completed, its
candidate count, its frozen candidate limit, and versions of the parser,
retrieval, terminology mapping, rule engine, prompt contract, and model
configuration. These are run provenance, not medical conclusions.

Each run uses the patient-import snapshot selected above and a catalogue
snapshot as it existed when the run was created. It can retain at most 100
review candidates. Cancelling a queued or running job stops future work without
deleting its immutable input or prior history.

### 4. Review candidates and criteria

The results list separates retrieval relevance from the criterion outcome:

- **Retrieval relevance** explains why the search surfaced the study: lexical
  matches, semantic similarity, fused rank, and any direct support from public
  trial conditions, interventions, or title.
- **Outcome** is a bounded review label based on the available deterministic
  criterion evidence. It is not an eligibility decision.

Selecting a criterion opens **Criterion detail**. It presents the original
trial text and exact character span, parsed rule, source-linked patient facts,
timestamps, evaluation path, and historical corrections. A reviewer correction
is appended as a new immutable record; it does not overwrite the original
deterministic result.

## How trial retrieval works

Retrieval is a way to find a manageable list for human review. It does not
interpret a trial’s full eligibility requirements.

1. The system builds a retrieval query from usable documented facts in the chosen
   patient import. Active conditions, active medications, and completed
   procedures can contribute terms. A clearly documented age or recorded sex may
   be used as conservative metadata filtering. Quality issues can cause a fact
   to be omitted rather than inferred.
2. The worker searches the immutable public trial catalogue in two ways:
   **lexical search** finds direct text matches, while **semantic search** finds
   related language using vectors.
3. Semantic search uses the pinned local model
   `NeuML/pubmedbert-base-embeddings` at a fixed revision. A trial vector is
   created after that public trial version is ingested from its title, conditions,
   intervention names/descriptions, and eligibility text. The patient-derived
   query vector is created only in memory for the run and is never stored.
4. Lexical and semantic ranks are combined with reciprocal-rank fusion. The
   resulting candidates are then ordered conservatively: direct matching text in
   structured public trial fields may promote a candidate, but lack of direct
   support does not become a negative medical conclusion.
5. The system stores the component scores, ranks, candidate sources, catalogue
   version policy, and safe aggregate execution metadata with the match run.

Semantic retrieval requires complete vector coverage for the current catalogue
and an available pinned local model. If either requirement is not met, the run
continues in explicit lexical-only mode and records why; it does not quietly
pretend semantic retrieval occurred.

## How criterion outcomes work

Eligibility prose is difficult and often ambiguous. This build intentionally
parses only explicit age bounds and ranges that occur inside clearly labelled
**Inclusion criteria** or **Exclusion criteria** sections. Every parsed criterion
retains its exact original text and source positions.

The parser is deterministic, versioned, and has no generative prompt or model.
Ambiguous wording, nested clauses, symbolic wording, unsupported requirements,
and absent sections are kept review-only. Automated use of parsed criteria is
currently disabled by configuration, so parsed criteria are conservatively
treated as requiring review in the final aggregation.

The possible review labels are:

| Label | Meaning |
| --- | --- |
| `potential_match` | Retrieved candidate with complete supported deterministic evidence. It is still not an eligibility or enrolment conclusion. |
| `likely_excluded` | Available supported evidence does not meet a parsed exclusion criterion. A reviewer must still confirm it. |
| `needs_review` | Evidence is missing, stale where freshness matters, conflicting, unknown, incomplete, or the criterion needs human review. |
| `not_relevant` | Available supported evidence does not meet a parsed inclusion criterion. It is not a clinical exclusion decision. |

The safety rule is simple: uncertainty must stay visible. It becomes `unknown`
or `needs_review`, never a reassuring match label.

## Architecture and stored records

Docker Compose runs six local services:

- **PostgreSQL with pgvector** stores patient-import snapshots, normalised facts,
  public trial versions, embeddings, jobs, match runs, evidence, and correction
  history.
- **Redis** is available to the local stack for background-work infrastructure.
- **Migration job** applies the database schema before the API starts.
- **API** is a FastAPI application exposing the documented HTTP interface.
- **Worker** polls durable work records and processes trial syncs, embeddings,
  and match runs one job at a time in the supplied local configuration.
- **Web** is the React reviewer interface.

The database separates current searchable trial projections from immutable
source versions. Patient facts retain their source resource, date, precision,
unit, and data-quality signals. Match candidates retain the exact patient-import
and trial-version identifiers used by that run. Operational logging and status
records intentionally avoid raw FHIR, patient facts, prompts, vectors, model
output, and source exception details.

## Run locally

### Requirements

- Docker Desktop with Docker Compose
- For direct development: Python 3.12+, Node.js 22.12+, and npm 10+

### Start the complete local stack

```bash
cp apps/backend/.env.example apps/backend/.env
docker compose --env-file apps/backend/.env up --build
```

Then open:

- Reviewer interface: <http://127.0.0.1:5173>
- API health endpoint: <http://127.0.0.1:8000/health>
- Interactive API documentation: <http://127.0.0.1:8000/docs>

Stop the stack with:

```bash
docker compose --env-file apps/backend/.env down
```

The database volume is intentionally preserved. To remove local database and
Redis data as well, use `docker compose --env-file apps/backend/.env down -v`.
That command deletes local demo data and should only be used when that is the
intended outcome.

### Run the backend directly

Start PostgreSQL and Redis through Compose first, then in another terminal:

```bash
cd apps/backend
cp .env.example .env
python -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt

export DATABASE_URL=postgresql+asyncpg://app:app@localhost:5432/trial_matcher
.venv/bin/python -m alembic upgrade head
.venv/bin/uvicorn src.main:app --reload
```

In a second backend terminal, run the dispatcher:

```bash
cd apps/backend
DATABASE_URL=postgresql+asyncpg://app:app@localhost:5432/trial_matcher \
  .venv/bin/python -m src.workers.dispatcher
```

## API overview

| Purpose | Endpoint |
| --- | --- |
| System health | `GET /health` |
| Import synthetic FHIR | `POST /patients/import/fhir` |
| View latest patient timeline | `GET /patients/{patient_id}` |
| View exact fact source | `GET /patients/{patient_id}/facts/{fact_id}/source` |
| Queue a public trial update | `POST /trial-syncs` |
| Queue the fixed demo collection | `POST /trial-syncs/development-collection` |
| Check one trial update | `GET /trial-syncs/{job_id}` |
| Check/list catalogue | `GET /trial-catalogue`, `GET /trial-catalogue/trials` |
| Queue a match run | `POST /match-runs` |
| Check or cancel a match run | `GET /match-runs/{run_id}`, `POST /match-runs/{run_id}/cancel` |
| Read candidate results | `GET /match-runs/{run_id}/results` |
| Inspect a criterion | `GET /criterion-results/{criterion_result_id}` |
| Append reviewer correction | `POST /criterion-results/{criterion_result_id}/corrections` |

Creating a trial update or match run returns quickly with a queued job. Poll its
status until it reaches a terminal state; the worker does the actual ingestion,
embedding, retrieval, and criterion work.

## Development checks

Run backend checks from `apps/backend`:

```bash
.venv/bin/python -m ruff format --check .
.venv/bin/python -m ruff check .
.venv/bin/python -m mypy
.venv/bin/python -m pytest
```

Database-backed integration tests need a reachable local PostgreSQL instance:

```bash
TEST_DATABASE_URL=postgresql+asyncpg://app:app@localhost:5432/trial_matcher \
  .venv/bin/python -m pytest
```

Run web checks from the repository root:

```bash
npm --prefix apps/web test
npm --prefix apps/web run lint
npm --prefix apps/web run format
npm --prefix apps/web run build
```

## Evaluation and what it means

The project includes several reproducible engineering evaluations. They are
regression tools, not evidence that the system is clinically accurate.

From `apps/backend`, run the small committed checks:

```bash
PYTHONPATH=. .venv/bin/python -m src.evaluation verify-frozen
PYTHONPATH=. .venv/bin/python -m src.evaluation retrieval
PYTHONPATH=. .venv/bin/python -m src.evaluation criteria
PYTHONPATH=. .venv/bin/python -m src.evaluation parser
PYTHONPATH=. .venv/bin/python -m src.evaluation synthetic-fhir
```

The synthetic-FHIR benchmark exercises the full in-memory engineering path:
FHIR safety validation, normalisation, query construction, candidate ordering,
atomic criterion evaluation, and conservative aggregation. Its cases are
engineering-authored synthetic regression fixtures, not clinician-reviewed
validation.

Optional TREC tooling evaluates retrieval against a large historical public
ClinicalTrials.gov corpus and relevance judgements. It is isolated from normal
application data and uses no patient Bundles. TREC topics are a research adapter,
not a substitute for testing the real FHIR-to-trial workflow. Do not turn any
benchmark number into a clinical-performance claim.

## Current limitations

- The application is a local research demo, not a deployable clinical service.
- The supplied Compose worker processes one durable job at a time; larger use
  would need deliberate queueing, concurrency, monitoring, security, and
  operational design.
- The parser covers only a narrow age-criterion subset and deliberately sends
  ambiguous or unsupported eligibility logic to review.
- Retrieval can surface useful candidates but cannot establish full trial
  eligibility, site availability, patient consent, or suitability.
- Semantic search depends on complete current trial-vector coverage and the
  pinned model being available locally; otherwise the explicit lexical fallback
  is used.
- The included evaluation fixtures catch regressions but are not a substitute for
  a clinician-reviewed, representative, held-out validation set.

## Repository layout

```text
apps/backend/     FastAPI API, database models/migrations, workers, retrieval,
                  criteria, tests, evaluation code, and synthetic fixtures
apps/web/         React/Vite reviewer interface
docker-compose.yml Local PostgreSQL, Redis, migration, API, worker, and web stack
```
