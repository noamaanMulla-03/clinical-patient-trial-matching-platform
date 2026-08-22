---
name: clinical-platform-implementation
description: Implement or modify features in the clinical trial patient-matching platform, including FastAPI, React, workers, database, FHIR, retrieval, criterion evaluation, or LLM integrations. Use for any non-trivial feature or refactor that must preserve an end-to-end data flow and the project's research-only clinical-safety boundaries.
---

# Clinical Platform Implementation

1. Inspect the current codebase before changing it. Reuse its naming, schemas, error handling, and test conventions.
2. Map the complete affected cycle before implementation: UI input and state, API contract, validation, asynchronous work, persistence, retrieval or evaluation, response, and UI rendering. For backend-only work, trace every caller and downstream consumer.
3. State the relevant invariants before coding. For clinical data, preserve provenance, timestamps, units, source versions, evidence IDs, and explicit uncertainty.
4. Make the smallest complete change that satisfies the request. Avoid speculative abstractions, duplicate paths, compatibility shims, and unrelated refactors.
5. Keep data formatting identical at every boundary. Validate external payloads at the boundary and use shared schemas where available.
6. Prefer deterministic rules whenever a criterion can be resolved without model reasoning. Treat absent, ambiguous, stale, or conflicting evidence as unresolved; never convert it into a reassuring outcome.
7. Add comments for safety invariants, non-obvious choices, and cross-boundary behavior. Do not add comments that merely restate code.
8. Add focused tests for changed behavior and run the smallest relevant quality suite. Report verified behavior and any unverified external dependency.

## Non-negotiable boundaries

- Keep the application research-only and synthetic-data-only unless the user explicitly changes the product boundary.
- Do not create Markdown files unless the user directly asks for one. This SKILL.md is a required skill artifact and the only exception.
- Do not retain backward-compatible paths after an intentional implementation replacement unless the user explicitly asks for migration support.
- Do not log raw patient facts, FHIR content, prompts, or model output.
- Do not expose a final `eligible` result or make autonomous enrollment or treatment decisions.
