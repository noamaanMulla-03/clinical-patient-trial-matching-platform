---
name: clinical-platform-safety-review
description: Review planned or implemented changes to the clinical trial patient-matching platform for patient-data safety, FHIR provenance, deterministic criterion behavior, LLM constraints, logging, auditing, privacy, and research-only decision-support boundaries. Use for code review, release review, or before enabling a new clinical workflow.
---

# Clinical Platform Safety Review

1. Inspect the actual changed code and its callers. Trace patient data from ingress through normalization, persistence, matching, model calls, logs, audit records, and reviewer output.
2. Confirm imports require the canonical synthetic-data marker and that no unmarked FHIR content is persisted, logged, normalized, or queued.
3. Confirm every criterion result is source-grounded: trial source span, valid patient fact IDs, timestamps, units, evaluator version, and explicit outcome.
4. Confirm supported exclusions dominate aggregation. Confirm missing, ambiguous, stale, conflicting, or unsupported evidence results in `unknown` or `needs_review`.
5. Confirm LLM use is bounded to unresolved criteria, validates its schema and evidence IDs, and cannot introduce patient facts or act autonomously.
6. Confirm audit events describe writes without raw clinical values or raw exception messages. Confirm logs and telemetry do not receive raw FHIR, patient facts, prompts, or model output.
7. Review test coverage for the safety property changed. Prioritize false clearance, privacy leakage, provenance loss, stale data, and unsafe default behavior.

## Output

Return only concrete findings ordered by severity, each with affected file/symbol, evidence, impact, and a focused remediation. State clearly when no critical finding is supported. Do not create Markdown files unless directly requested.
