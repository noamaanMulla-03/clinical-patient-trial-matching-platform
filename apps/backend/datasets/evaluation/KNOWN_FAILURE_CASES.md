# Known failure cases and non-claims

This project is research-only candidate retrieval and review support. These are
known limits, not exceptions that the interface should hide.

| Failure case | Current behavior | Safe handling |
| --- | --- | --- |
| Synonyms and coding differences | `lexical-v1` matches documented text only; it has no concept expansion. | It can miss a relevant trial. Show the measured lexical baseline and never imply completeness. |
| A stale, missing, invalid, or conflicting patient fact | The deterministic engine produces `unknown`, `conflicting`, or `needs_review`. | Do not turn uncertainty into a potential match. Review the linked source fact. |
| Missing condition in a partial record | Absence is not treated as evidence that the condition is absent. | The criterion remains `unknown` and needs review. |
| Free-text eligibility criteria outside the manual rule set | `manual-v1` does not infer a rule from arbitrary trial prose. | Leave the item for review; do not claim parser coverage. |
| Trial recruitment status, location, and current availability | Retrieval can return an indexed public trial irrespective of real-time site availability. | Display public source status and update time separately; reviewer verifies current availability. |
| TREC prose topics | The product accepts source-linked FHIR facts, not arbitrary clinical-note prose. | Keep TREC mappings outside the product, version them, and do not present a TREC score until the mapping is reviewed. |
| The frozen criterion suite | Eight annotated synthetic cases can detect regressions but cannot establish clinical validity. | Report it as a regression baseline only, not clinician-validated accuracy. |
| Latency | The baseline command measures only local in-process ranking. | Do not use it as an API, worker, database, or production-service latency claim. |

No result from this application is an eligibility determination, treatment
recommendation, enrolment decision, or authorization to contact a trial site.
