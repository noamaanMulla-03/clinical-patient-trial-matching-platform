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

See [datasets/README.md](datasets/README.md) for a complete example and fixture rules.

## Clinical-content logging

Clinical content must never be placed in a log message. Use static event messages and pass only non-clinical operational fields such as a request ID, job ID, outcome, or NCT ID.

The API enables a defensive redaction filter at startup. It redacts structured fields with sensitive names, serialized FHIR content, common direct identifiers in free text, and all dynamic logging arguments. This is a safety net, not a reason to log clinical content; the caller remains responsible for keeping it out of messages and telemetry.

## Audit events

Every future state-changing operation must use the `audited_write()` helper. It writes one immutable success event after the operation completes, or a failure event with the exception type only. Audit metadata is limited to operational details; do not include FHIR resources, patient facts, prompts, or exception messages.

The current in-memory sink exists for local development and tests. A database-backed implementation will replace it when the `audit_events` table is added.

## Current status

The repository is in its initial scaffold stage. It includes the product design, delivery roadmap, local Docker infrastructure, and an Alembic migration foundation. Application features and domain tables have not yet been implemented.

## Repository layout

```text
apps/        # Web application and API application
services/    # FHIR normalization, ingestion, retrieval, and matching services
workers/     # Background jobs
packages/    # Shared schemas and observability code
tests/       # Unit, integration, contract, security, and evaluation tests
datasets/    # Synthetic fixtures and dataset download instructions only
migrations/  # Database migrations
```

## Local setup

### Prerequisites

The planned local environment requires:

- Docker and Docker Compose
- Node.js 22.12 or later
- Python 3.12 or later

### Current setup state

Docker Compose infrastructure and migration dependencies are included, but the API, worker, and web applications are not implemented yet. The next implementation steps are listed in [IMPLEMENTATION_ROADMAP.md](IMPLEMENTATION_ROADMAP.md).

Once the local stack exists, the intended startup command will be:

```bash
docker compose up --build
```

## Backend quality checks

Install backend development dependencies and run the checks from the repository root:

```bash
python -m venv .venv
.venv/bin/pip install -r apps/api/requirements-dev.txt
.venv/bin/ruff check .
.venv/bin/ruff format --check .
.venv/bin/mypy
.venv/bin/pytest
```

## Frontend quality checks

Install frontend dependencies and run the checks from the web app directory:

```bash
cd apps/web
npm install
npm run lint
npm run format
npm run typecheck
npm run test
```

## Project documents

- [Technical design](clinical-trial-patient-matching-README.md)
- [Implementation roadmap](IMPLEMENTATION_ROADMAP.md)

## Core safety rule

Missing, ambiguous, conflicting, stale, or unsupported evidence must result in `unknown` or `needs_review`—never a reassuring match outcome.
