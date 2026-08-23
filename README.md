# Clinical Trial Patient-Matching Platform

A research-oriented decision-support platform for finding clinical trials that may fit a patient's documented clinical profile. It is designed to help reviewers narrow a trial search and trace each result back to the source trial text and documented patient facts.

## Research-only disclaimer

This project is for engineering research and portfolio demonstration only.

- It does **not** provide medical advice or determine clinical-trial eligibility.
- It does **not** replace review by trial investigators, clinicians, or qualified research coordinators.
- It must not make autonomous enrolment decisions, recommend treatment, or contact trial sites.
- Development and public demonstrations use synthetic data only.
- The project makes no clinical-validity, regulatory, HIPAA, GDPR, FDA, or medical-device compliance claim.

The API starts in `development` mode by default. `production`, `prod`, `staging`, and `stage` are blocked unless `ALLOW_PRODUCTION_LIKE_ENVIRONMENT=true` is set deliberately; that override still does not allow real patient data. `ALLOW_REAL_PATIENT_DATA=true` is rejected by this research-only build.

## Synthetic FHIR import marker

Every imported FHIR R4 Bundle must carry the exact top-level `meta.tag` marker below. The import guard checks the `system` and `code` and rejects unmarked payloads before they can be processed:

```json
{
"system": "urn:clinical-trial-matcher:data-classification",
"code": "synthetic-data"
}
```

See [apps/backend/datasets/README.md](apps/backend/datasets/README.md) for a
complete example and fixture rules.

## Clinical-content logging

Clinical content must never be placed in a log message. Use static event messages and pass only non-clinical operational fields such as a request ID, job ID, outcome, or NCT ID.

The API enables a defensive redaction filter at startup. It redacts structured fields with sensitive names, serialized FHIR content, common direct identifiers in free text, and all dynamic logging arguments. This is a safety net, not a reason to log clinical content; the caller remains responsible for keeping it out of messages and telemetry.

## Audit events

Every future state-changing operation must use the `audited_write()` helper. It writes one immutable success event after the operation completes, or a failure event with the exception type only. Audit metadata is limited to operational details; do not include FHIR resources, patient facts, prompts, or exception messages.

The current in-memory sink exists for local development and tests. A database-backed implementation will replace it when the `audit_events` table is added.

## Current status

The backend implements Phases 0–5 of the roadmap: synthetic-data safety boundaries, FHIR normalization, trial versioning, deterministic criterion evaluation, and bounded lexical match runs.

It currently supports:

- Synthetic FHIR R4 imports for patients, conditions, observations, medications, procedures, and allergies.
- Immutable patient-import and trial-version snapshots with source provenance.
- Bounded ClinicalTrials.gov API v2 sync jobs and current searchable trial projections.
- Deterministic criterion outcomes with evidence fact IDs and safe `unknown` handling.
- PostgreSQL full-text trial retrieval, conservative metadata filtering, deterministic candidate scoring, and ranked match results.
- Durable match-run cancellation requests, safe failure status, and immutable run/version traceability.

The reviewer web interface and durable worker dispatcher are implemented. Semantic
retrieval and formal evaluation tooling remain roadmap items. Docker Compose starts
PostgreSQL, Redis, the migration job, API, worker, and web application.

## Repository layout

```text
apps/backend/  # Python backend, migrations, tests, and synthetic fixtures
apps/web/      # React reviewer interface
docker-compose.yml  # Local PostgreSQL, Redis, migration, API, worker, and web services
IMPLEMENTATION_ROADMAP.md  # Completed and remaining delivery phases
```

## Local setup

### Prerequisites

Requirements:

- Docker Desktop with Docker Compose
- Python 3.12 or later

### Run the backend locally

Start the complete local system:

```bash
cp apps/backend/.env.example apps/backend/.env
docker compose --env-file apps/backend/.env up --build
```

For direct backend development, work inside the backend directory. Create the
environment template first if needed:

```bash
cd apps/backend
cp .env.example .env
python -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
```

Set a host-reachable database URL and apply the schema migrations:

```bash
export DATABASE_URL=postgresql+asyncpg://app:app@localhost:5432/trial_matcher
.venv/bin/python -m alembic upgrade head
```

Start FastAPI with reload enabled:

```bash
DATABASE_URL=postgresql+asyncpg://app:app@localhost:5432/trial_matcher \
  .venv/bin/uvicorn app.main:app --reload
```
Open <http://127.0.0.1:8000/docs> for the generated OpenAPI interface. Run the
local dispatcher with `.venv/bin/python -m app.workers.dispatcher` when working
outside Docker Compose.

## Current API surface

- `GET /health`
- `POST /patients/import/fhir` and `GET /patients/{patient_id}`
- `POST /trial-syncs` and `GET /trial-syncs/{job_id}`
- `POST /match-runs`, `GET /match-runs/{run_id}`, and `GET /match-runs/{run_id}/results`
- `POST /match-runs/{run_id}/cancel`
- `GET /criterion-results/{criterion_result_id}` and `POST /criterion-results/{criterion_result_id}/corrections`

`POST /trial-syncs` and `POST /match-runs` deliberately queue durable work; they do not perform external ingestion or matching in the HTTP request. A match result is a ranked retrieval candidate, not an eligibility decision or enrollment recommendation.

## Backend validation

Run backend checks from `apps/backend`:

```bash
.venv/bin/python -m ruff format --check .
.venv/bin/python -m ruff check .
.venv/bin/python -m mypy
TEST_DATABASE_URL=postgresql+asyncpg://app:app@localhost:5432/trial_matcher .venv/bin/python -m pytest
```

## Frontend

The reviewer interface runs at <http://127.0.0.1:5173> in Docker Compose. For
frontend-only checks, run `npm --prefix apps/web test`, `npm --prefix apps/web run
lint`, and `npm --prefix apps/web run build` from the repository root.

## Core safety rule

Missing, ambiguous, conflicting, stale, or unsupported evidence must result in `unknown` or `needs_review`—never a reassuring match outcome.
