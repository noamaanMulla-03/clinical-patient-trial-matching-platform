---
name: clinical-platform-debugging
description: Diagnose or fix a bug, failed test, UI regression, API error, worker problem, FHIR import issue, retrieval mismatch, or criterion-evaluation defect in the clinical trial patient-matching platform. Use when Codex must reproduce and trace a failure across frontend, backend, asynchronous jobs, storage, and safety rules before changing code.
---

# Clinical Platform Debugging

1. Reproduce or precisely characterize the failure before editing code. Capture the request, response, error, test assertion, or UI state that demonstrates it.
2. Trace the whole owning cycle: UI event and state, request payload, API validation, job queue/worker path, database read/write, retrieval or criterion engine, and returned result. Do not diagnose from one layer in isolation.
3. Compare actual data shape and formatting at every boundary. Check IDs, code systems, units, dates, normalized/pixel coordinates when relevant, and null or unknown propagation.
4. Identify the smallest root cause, not the closest symptom. Rule out stale processes, stale containers, cached data, old trial versions, and mismatched model/parser configuration.
5. Implement one targeted fix only after the failure mode is confirmed. Do not add fallback paths that preserve a replaced design.
6. Add a regression test at the lowest layer that can reliably prove the defect, then verify the end-to-end behavior where practical.
7. Keep clinical uncertainty explicit. A fix must not change missing, ambiguous, stale, or conflicting evidence into `met` or `potential_match`.

## Reporting

Report the reproduction, root cause, changed files, validation commands, and remaining limitations. Do not create Markdown files unless directly requested.
