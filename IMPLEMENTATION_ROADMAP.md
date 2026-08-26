# Clinical Trial Patient-Matching Platform — Implementation Roadmap

This roadmap translates the technical design into small, incremental delivery steps. Each phase ends with a usable, demonstrable system state.

> Scope: research and portfolio demonstration using synthetic data only. This application must not make final eligibility decisions, use real patient data in demos, or claim clinical validity without separate evaluation and review.

## Phase 0 — Foundation and Safety Boundaries

- [x] Create the repository structure with self-contained backend and web applications.
- [x] Add a root `README.md` with local setup and a clear research-only disclaimer.
- [x] Add `.env.example` with database, Redis, ClinicalTrials.gov, model, and synthetic-data settings.
- [x] Add Docker Compose services for PostgreSQL, Redis, API, worker, and web app.
- [x] Configure database migrations.
- [x] Configure backend linting, formatting, type checking, and tests.
- [x] Configure frontend linting, formatting, type checking, and tests.
- [x] Add CI that runs checks without requiring live external APIs or model keys.
- [x] Add a startup check that refuses production-like settings by default.
- [x] Define and enforce a synthetic-data marker for every imported FHIR bundle.
- [x] Add a log-redaction utility for clinical content.
- [x] Create a basic audit-event helper used by all write operations.

### Exit checks

- [x] `docker compose up --build` starts an empty but healthy local system.
- [x] A non-synthetic import is rejected when real patient data is disallowed.
- [x] Automated checks run successfully from a clean checkout.

## Phase 1 — Domain Model and API Contracts

- [x] Write Pydantic schemas for FHIR import requests and validation errors.
- [x] Define the normalized `PatientFact` schema, including value, unit, time, code, and FHIR provenance.
- [x] Define trial, trial-version, criterion, match-run, criterion-result, review-decision, and audit-event schemas.
- [x] Create database tables and migrations for patients, imports, patient facts, trials, and trial versions.
- [x] Create tables for criteria, match runs, trial matches, criterion results, and review decisions.
- [x] Add immutable version fields for parser, retrieval, rule engine, terminology mapping, prompt, and model configuration.
- [x] Decide which data is mutable current state versus immutable historical snapshot.
- [x] Add API error conventions and request IDs.
- [x] Publish an OpenAPI contract and add contract tests for core routes.

### Exit checks

- [x] A synthetic patient import can create a patient, import record, and facts.
- [x] A trial version can be stored without overwriting an earlier version.
- [x] Every persistent clinical fact has provenance and timestamps.

## Phase 2 — Synthetic FHIR Import and Normalization

- [x] Collect or generate small synthetic FHIR R4 bundles for testing.
- [x] Support `Patient` resource parsing for demographics and birth date.
- [x] Support `Condition` parsing with code system, code, display text, clinical status, and onset date.
- [x] Support `Observation` parsing with numeric value, unit, reference range, status, and effective date.
- [x] Support `MedicationStatement` and `MedicationRequest`.
- [x] Support `Procedure` and `AllergyIntolerance` as source-preserving facts.
- [x] Preserve resource IDs and a linkable copy of the source resource.
- [x] Normalize dates, quantities, and coding fields.
- [x] Preserve conflicting observations rather than selecting one silently.
- [x] Surface missing, stale, and invalid data explicitly.
- [x] Add patient retrieval endpoint: `GET /patients/{patient_id}`.
- [x] Add test fixtures for missing dates, multiple lab units, conflicting labs, and unknown codes.

### Exit checks

- [x] Importing a bundle produces a readable normalized patient timeline.
- [x] No absent FHIR resource becomes a negative clinical fact.
- [x] Every normalized fact can be traced to its source resource.

## Phase 3 — Trial Ingestion and Versioning

- [x] Implement a ClinicalTrials.gov API v2 client.
- [x] Add a worker job to ingest trials by query, condition, NCT ID, or a bounded page range.
- [x] Store the unmodified API response with retrieval time.
- [x] Extract NCT ID, title, conditions, interventions, status, phases, eligibility text, ages, sex, and locations.
- [x] Compute a source hash from fields relevant to matching.
- [x] Skip re-parsing when the relevant source hash has not changed.
- [x] Retain superseded trial snapshots.
- [x] Add ingestion status, failures, counts, and source-lag metrics.
- [x] Add `POST /trial-syncs` and `GET /trial-syncs/{job_id}`.
- [x] Add fixtures for ClinicalTrials.gov responses and client contract tests.
- [x] Start with a limited, reproducible trial collection for development.

### Exit checks

- [x] A trial sync stores searchable current records and immutable source snapshots.
- [x] Re-ingesting unchanged trials does not create unnecessary derived work.
- [x] Changed eligibility text creates a new version and invalidates affected derived records.

## Phase 4 — Deterministic Criterion Model and Rules

- [x] Define an atomic criterion representation with source text and exact source span.
- [x] Add manual or fixture-based criteria creation before attempting automated parsing.
- [x] Implement age-rule evaluation.
- [x] Implement recorded-sex evaluation.
- [x] Implement exact coded-condition evaluation.
- [x] Implement numeric lab-threshold evaluation.
- [x] Add validated unit compatibility and conversion for the first supported laboratory tests.
- [x] Implement date and recency-window evaluation.
- [x] Implement explicitly documented medication and procedure checks.
- [x] Return only `met`, `not_met`, `unknown`, or `conflicting` per criterion.
- [x] Record evidence fact IDs and an evaluator version for every non-unknown result.
- [x] Reject results that cite nonexistent evidence IDs.
- [x] Implement aggregation to `potential_match`, `likely_excluded`, `needs_review`, and `not_relevant`.
- [x] Add unit, property-based, and adversarial tests.

### Exit checks

- [x] Missing facts always produce `unknown`, never `met`.
- [x] A supported exclusion always yields `likely_excluded`.
- [x] Conflicting or stale evidence cannot produce `potential_match`.
- [x] Results contain criterion-level evidence and explanations.

## Phase 5 — Baseline Retrieval and Match Runs

- [x] Add PostgreSQL full-text indexes for titles, conditions, interventions, and eligibility text.
- [x] Build a patient-derived retrieval query from active conditions, medications, age, sex, and relevant procedures.
- [x] Add reliable metadata filters for condition, age, sex, study status, phase, country, and intervention type.
- [x] Keep filters conservative when patient information is uncertain.
- [x] Implement candidate scoring and persist retrieval ranks and scores.
- [x] Add a background match-run job.
- [x] Add `POST /match-runs`, `GET /match-runs/{run_id}`, and results endpoints.
- [x] Cap candidates evaluated per run and record the cap in the configuration snapshot.
- [x] Ensure a match run captures the exact patient import, trial versions, and rule-engine configuration used.
- [x] Add cancellation and failure handling for long-running work.

### Exit checks

- [x] A user can import a synthetic patient, start a run, and retrieve ranked results.
- [x] Each result identifies its matching trial version and evidence.
- [x] Re-running with the same inputs produces a separately traceable run.

## Phase 6 — Minimal Reviewer Interface

- [x] Create a patient import screen.
- [x] Create a patient timeline showing facts, source resource links, dates, and freshness.
- [x] Create a match-run status screen.
- [x] Create results tabs for potential matches, needs review, and likely exclusions.
- [x] Display trial title, NCT ID, study status, source update time, and retrieval relevance separately from outcome.
- [x] Add filtering and search across result lists.
- [x] Create a criterion-detail view.
- [x] Show original trial text, parsed criterion data, patient evidence, timestamps, and evaluation path.
- [x] Add clear visual treatment for unknown, conflicting, stale, and excluded outcomes.
- [x] Add a reviewer correction form.
- [x] Show audit history for criterion outcomes and corrections.

### Exit checks

- [x] A reviewer can understand why a trial is listed without reading internal logs.
- [x] A reviewer can navigate from a trial outcome to the exact source criterion and patient facts.
- [x] A correction creates an immutable audit record.

## Phase 7 — Baseline Evaluation and Acceptance Gates

- [x] Download and document TREC Clinical Trials 2021 and 2022 data usage.
- [x] Build a reproducible retrieval evaluation command.
- [x] Measure nDCG@5, nDCG@10, Precision@5, Precision@10, Recall@50, MRR, latency, and excluded-trial rate in top 10.
- [x] Establish a baseline with lexical retrieval only.
- [x] Build a small clinician-reviewed or carefully annotated deterministic-criteria test set.
- [x] Measure criterion macro F1, exclusion recall, false-clearance rate, evidence precision, and abstention rate.
- [x] Add end-to-end acceptance tests from the design.
- [x] Document known failure cases instead of masking them.
- [x] Freeze a demo dataset and deterministic configuration.

### Exit checks

- [x] The project has measured baseline behavior, not assumed accuracy.
- [x] Every demo claim is supported by a reproducible test or evaluation result.
- [x] The demo shows a potential match, likely exclusion, and needs-review result.

## Phase 8 — Semantic Retrieval and Criterion Parsing

- [x] Choose and version an embedding model.
- [x] Add pgvector storage and embedding-generation jobs.
- [x] Implement semantic retrieval alongside lexical retrieval.
- [ ] Fuse rankings using reciprocal rank fusion.
- [ ] Compare lexical-only versus hybrid retrieval on held-out TREC topics.
- [ ] Define criterion-parser input and output schemas.
- [ ] Parse eligibility text into atomic, source-linked criteria.
- [ ] Mark ambiguous, nested, or low-confidence criteria for review.
- [ ] Version parser prompts and models and preserve raw parser output.
- [ ] Evaluate parsing separately from retrieval and final matching.
- [ ] Do not enable automated use of parsed criteria until source-span and safety tests pass.

### Exit checks

- [ ] Hybrid retrieval measurably improves an agreed metric without increasing harmful excluded-trial ranking.
- [ ] Parsed criteria always retain original text and source spans.
- [ ] Ambiguous criteria safely abstain.

## Phase 9 — LLM Fallback and Governance

- [ ] Build a provider-neutral LLM adapter.
- [ ] Limit LLM input to one criterion and relevant patient facts only.
- [ ] Enforce strict structured output and evidence-ID validation.
- [ ] Add prompt-injection defenses for trial text and patient content.
- [ ] Log model version, token count, latency, error status, and cost without raw clinical content.
- [ ] Add fallback behavior when model calls fail or time out.
- [ ] Require `unknown` plus review when the output is unsupported or invalid.
- [ ] Evaluate LLM adjudication against the reviewed criterion set.
- [ ] Add outcome-diff reports between evaluator versions.
- [ ] Add a governed release process before using reviewer corrections to change models or prompts.

### Exit checks

- [ ] The system remains useful if the LLM is disabled.
- [ ] Unsupported LLM outputs cannot affect a result.
- [ ] Model changes can be compared against prior immutable match runs.

## Phase 10 — Hardening and Research Integrations

- [ ] Add organization-level data isolation.
- [ ] Add authentication and role-based authorization if the project moves beyond a local demo.
- [ ] Encrypt sensitive data and configure TLS for deployed environments.
- [ ] Add file scanning, payload limits, and rate limiting.
- [ ] Set explicit retention and deletion policies.
- [ ] Complete a security review and threat model.
- [ ] Integrate a SMART on FHIR sandbox only after the synthetic workflow is stable.
- [ ] Add a versioned terminology-service adapter only for concepts that demonstrably need it.
- [ ] Add site-level and distance information as advisory context, never automatic outreach.
- [ ] Obtain clinical, legal, privacy, and regulatory review before considering real-patient workflows.

### Exit checks

- [ ] The system remains explicitly research-only unless separate governance work is completed.
- [ ] Security, privacy, and operational claims are evidence-based and scoped accurately.

## Recommended Build Order

Complete these before starting semantic retrieval or LLM work:

- [x] Phase 0: Foundation and safety boundaries
- [x] Phase 1: Domain model and API contracts
- [x] Phase 2: Synthetic FHIR import and normalization
- [x] Phase 3: Trial ingestion and versioning
- [x] Phase 4: Deterministic criterion rules
- [x] Phase 5: Lexical retrieval and match runs
- [ ] Phase 6: Minimal reviewer interface
- [ ] Phase 7: Baseline evaluation

The central safety rule throughout implementation is: missing, ambiguous, conflicting, stale, or unsupported evidence must lead to `unknown` or `needs_review`, never to a reassuring match outcome.
