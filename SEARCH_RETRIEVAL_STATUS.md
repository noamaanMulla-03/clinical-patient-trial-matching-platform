# Trial Search: Current Problem, Work Completed, Results, and Next Steps

## Purpose of this document

This document explains the current trial-search work in the Clinical Patient Trial Matching Platform for a reader who has no prior project context. It describes what the system does, why the search quality is not yet acceptable, every retrieval-related solution implemented so far, the measured results, and what is still required before making any performance claim.

This is a research-only system. It accepts synthetic FHIR R4 patient bundles and retrieves public ClinicalTrials.gov records as **review candidates**. A result is never an eligibility, treatment, enrollment, outreach, or clinical decision. The later criterion-review stage remains responsible for checking source-linked evidence and preserving uncertainty.

## Executive summary

The platform can now perform three useful jobs:

1. Load and version public trial records.
2. Find broadly relevant trial candidates with both keyword and semantic search.
3. Explain later criterion outcomes using the individual, source-linked patient facts rather than a vector score.

Semantic retrieval is clearly better than keyword-only retrieval at finding broadly relevant public trials in the TREC Clinical Trials benchmark. For example, on one full 375,580-trial run, semantic search achieved `nDCG@10 = 0.287` versus lexical search at `0.067`, and retrieved substantially more benchmark-relevant trials in its first 50 results.

However, it also ranked too many benchmark-judged unsuitable trials in the first ten results: `13.6%` for semantic search versus `2.6%` for lexical search. Hybrid search improved the chance of finding an early relevant result, but did not meet the agreed quality-and-safety gate. The best relevance-tuned hybrid configuration reached `nDCG@10 = 0.308` and `Precision@10 = 0.252` on held-out topics, but its unsuitable-result rate was `14.4%`, far above the maximum accepted `5%`.

The correct conclusion is not that semantic search is useless. It is that semantic similarity is a candidate-retrieval tool, not a reliable eligibility engine. It must remain bounded, review-only, and followed by source-linked deterministic criterion evaluation. The project deliberately does **not** enable an automated clinical decision from these search scores.

## What the product is searching

### Patient side

1. A synthetic FHIR R4 Bundle is imported.
2. The application stores an immutable import snapshot and normalizes its patient facts with provenance, dates, units, and data-quality flags.
3. A match run reads only facts from that one import snapshot. It does not silently merge facts across imports.
4. The retrieval query includes only documented, usable facts:
   - active conditions;
   - active medications;
   - completed procedures; and
   - documented age and recorded sex as metadata filters when unambiguous.
5. Facts that are stale, conflicting, incomplete, or otherwise flagged are not silently treated as reliable positive search evidence.

At present, these usable facts are combined into a temporary patient retrieval query when a match run is queued. The semantic vector for that query is generated at run time and is not persisted. The original fact identifiers remain attached to the query terms.

### Trial side

1. Public ClinicalTrials.gov records are loaded and versioned.
2. Each current trial version has structured fields, including title, conditions, interventions, status, source-update time, and eligibility text.
3. Loading or updating a trial queues a durable embedding job.
4. The embedding worker creates one stored, versioned 768-dimensional trial vector from the trial title, conditions, interventions, and full eligibility text. It records the embedding-model configuration and trial-content hash with that vector.

This means trial vectors are durable and versioned; the patient query vector is transient. That is acceptable for a small local demo, but persisting a versioned patient retrieval vector at import time would avoid repeat encoding for repeated runs against the same immutable patient import. It is a sensible next engineering improvement, not a change to the clinical decision boundary.

## Current production retrieval flow

```text
Synthetic FHIR import
        |
        v
Immutable normalized patient facts
        |
        v
Match run: build usable retrieval query from one import snapshot
        |
        +--> lexical retrieval (direct words/codes and filters)
        |
        +--> semantic retrieval (temporary patient vector against stored trial vectors)
                        |
                        v
              equal reciprocal-rank fusion
                        |
                        v
      deterministic structured-evidence re-ranking
                        |
                        v
  candidate list for review, then source-linked criterion evaluation
```

The candidate list is capped at 100 trials. The application records lexical rank, semantic rank, fusion score, re-ranking rationale, and trial-version identity so the result can be reviewed later.

If the local embedding model or trial vectors are unavailable, the worker safely falls back to lexical retrieval rather than inventing a semantic result. A candidate being high in the list still does not mean the patient is eligible for the trial.

## Why semantic search is not good enough by itself

### 1. Similarity is not clinical logic

Semantic search can recognize that a trial and a patient are both about diabetes, melanoma, or a drug class. It does not reliably understand the full logical rule:

> “Patient has the disease, **but** is excluded because of a particular medication, lab value, prior therapy, time window, age limit, organ function, or combination of findings.”

Eligibility text contains inclusion requirements, exclusions, negation, numeric thresholds, timelines, and nested exceptions. A single similarity score cannot reliably enforce those conditions.

### 2. A short patient representation is compared with a long trial document

The patient query may contain only a few usable facts. A trial combines a title, conditions, interventions, and a long eligibility section. Compressing all of that trial content into one vector can blur the important part of the trial with less relevant text.

### 3. One vector loses field meaning

The title, the condition list, an intervention, and an exclusion criterion serve different purposes. A title match is weaker than a direct match between a patient condition and the trial's structured condition field. A single combined vector cannot make that distinction clearly enough.

### 4. Generic biomedical embeddings are not trained for this exact task

The selected model is `NeuML/pubmedbert-base-embeddings`, pinned as `pubmedbert-embeddings-v1` at a specific immutable revision. It has biomedical language coverage, but it was not trained specifically to rank FHIR-derived patient snapshots against ClinicalTrials.gov records while respecting inclusion and exclusion rules.

### 5. The benchmark is useful but not the product's exact input

The TREC Clinical Trials topics are public, synthetic free-text case descriptions with relevance judgments. They are not imported patient records and are never stored as such by this project. They are useful for comparing retrieval approaches at large scale, but they do not prove performance on this application's FHIR workflow or on clinical eligibility review.

### 6. The corpus is large and medically repetitive

The evaluation corpus contains 375,580 public trial records. Many trials share the same disease, intervention, or general vocabulary. A meaningful semantic relation can therefore still be a poor review candidate.

## How to read the metrics

All values below are averages across benchmark topics. Higher is better except for the unsuitable-result rate.

| Metric                       | Plain-English meaning                                                                                                                                               |
| ---------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `nDCG@10`                    | Whether the first ten results are useful and ordered well.                                                                                                          |
| `Precision@10`               | The share of the first ten results judged relevant. `0.25` means about 2.5 of ten on average.                                                                       |
| `Recall@50`                  | How much of the benchmark's known relevant set is found in the first 50 results.                                                                                    |
| `MRR`                        | How early the first relevant result appears.                                                                                                                        |
| `excluded_trial_rate_top_10` | The share of the first ten results that the benchmark judged grade `0` (not relevant). This is a ranking-safety proxy, not a real-world patient exclusion decision. |

The agreed Phase 8.1 acceptance gate is deliberately strict:

- `nDCG@10 >= 0.25`;
- `Precision@10 >= 0.25`;
- unsuitable-result rate in the first ten `<= 5%`; and
- no worsening compared with lexical baseline on that rate.

No configuration has passed all of these conditions.

## Solutions implemented and measured

### 1. Lexical baseline: direct word/token retrieval

**What was implemented.** A deterministic lexical benchmark indexes terms from title, conditions, interventions, and eligibility text with fixed field weights. It provides the conservative baseline that every later approach must beat.

**Why it helps.** Exact wording is reliable when the patient/query and trial use the same term. It also has the lowest unsuitable-result rate of the tested approaches.

**Why it is insufficient.** It misses synonyms, abbreviations, related concepts, and differently worded descriptions.

**Full-corpus result: 50 topics, 375,580 trials.**

| Method           | nDCG@10 | Precision@10 | Recall@50 |   MRR | Unsuitable in top 10 |
| ---------------- | ------: | -----------: | --------: | ----: | -------------------: |
| Lexical baseline |   0.067 |        0.042 |     0.015 | 0.185 |             **2.6%** |

The lexical result is weak for relevance and recall, but it establishes an important safety reference: low false-positive-style ranking noise.

### 2. Versioned semantic trial embeddings

**What was implemented.**

- The embedding model choice is versioned, pinned to an exact source revision, and has a fixed 768-vector contract.
- Trial load/update queues a durable embedding job.
- The worker generates and stores one vector per trial version from title, conditions, interventions, and eligibility text.
- Semantic retrieval makes a temporary vector from the usable patient retrieval query and uses cosine similarity against current trial vectors.

**Why it helps.** It can retrieve trials with related meaning even when exact terms differ.

**Full-corpus result.**

| Method | nDCG@10 | Precision@10 | Recall@50 | MRR | Unsuitable in top 10 | Retrieval latency |
| --- | ---: | ---: | ---: | ---: | ---: |
| Semantic only, combined trial vector | **0.287** | **0.234** | **0.146** | 0.390 | 13.6% | ~79 ms |

This is a large retrieval improvement over lexical search: it finds more relevant trials and orders them much better. It is not acceptable as a final ranking because the unsuitable-result rate increased from 2.6% to 13.6%.

### 3. Equal reciprocal-rank fusion (hybrid retrieval)

**What was implemented.** Lexical and semantic results are combined with reciprocal-rank fusion (RRF), using rank positions rather than incorrectly adding incomparable keyword scores and cosine-similarity scores. The fusion configuration and source ranks are persisted with each candidate.

**Why it helps.** A trial that ranks well in both systems should be promoted. It is intended to combine lexical precision with semantic recall.

**Full-corpus result.**

| Method                       | nDCG@10 | Precision@10 | Recall@50 |       MRR | Unsuitable in top 10 |
| ---------------------------- | ------: | -----------: | --------: | --------: | -------------------: |
| Equal lexical + semantic RRF |   0.223 |        0.170 |     0.110 | **0.393** |                 9.8% |

Hybrid RRF achieved the best early-hit score (`MRR`) in this run, but reduced the semantic-only relevance scores and still ranked too many unsuitable trials. It therefore remains review-only.

### 4. Deterministic structured-evidence re-ranker

**What was implemented.** After lexical/semantic fusion, the platform checks for direct, source-linked support in structured trial fields:

- a patient condition directly appearing in a trial's structured conditions receives the strongest promotion;
- a medication or procedure directly appearing in a structured intervention receives the next level;
- title-only support is weaker;
- no direct support remains `unknown` and is retained without a penalty.

The re-ranker records the supporting patient fact IDs and trial fields. It does not decide eligibility and it never treats a missing match as a negative.

**Why it helps.** It gives an explainable boost to a direct structured connection instead of trusting a dense vector alone.

**Measured result.** The existing TREC run with the re-ranker produced the same ranking-quality values as equal RRF (`nDCG@10 0.223`, `Precision@10 0.170`, unsuitable rate 9.8%). This is not evidence that the re-ranker is useless: TREC free-text topics do not provide the same source-linked FHIR facts that activate the product's structured support rules. It is implemented and unit-tested, but needs evaluation with reviewed FHIR-style scenarios before its benefit can be claimed.

### 5. Weighted lexical/semantic hybrid tuning on held-out topics

**What was implemented.** The 50 public benchmark topics were split into 25 tuning topics and 25 held-out topics. The project tested fixed lexical weight `1.0` with semantic weights from `0.1` to `8.0` using weighted RRF. This avoids choosing a production configuration by looking only at the same data used to tune it.

**Held-out results.**

| Method                                     |   nDCG@10 | Precision@10 | Recall@50 |       MRR | Unsuitable in top 10 |
| ------------------------------------------ | --------: | -----------: | --------: | --------: | -------------------: |
| Lexical only                               |     0.116 |        0.076 |     0.021 |     0.307 |             **3.2%** |
| Semantic only                              |     0.277 |        0.228 |     0.123 |     0.357 |                15.2% |
| Equal hybrid RRF                           |     0.258 |        0.200 |     0.096 |     0.433 |                10.8% |
| Equal hybrid + structured re-ranker        |     0.253 |        0.200 |     0.086 |     0.421 |                 9.6% |
| Weighted hybrid: lexical 1.0, semantic 1.5 | **0.308** |    **0.252** |     0.122 | **0.435** |                14.4% |

The semantic-weight-1.5 configuration passes the two relevance thresholds, but fails the safety threshold badly. It was intentionally **not selected** as the production configuration.

### 6. Separate trial-field vectors and weighted field search

**What was implemented.** The research evaluator can now create four separate vectors per public trial:

1. title;
2. conditions;
3. interventions; and
4. eligibility text.

The initial full-corpus evaluation used weights of `1.0` for title, `1.25` for conditions, `1.0` for interventions, and `0.5` for eligibility text. These weights intentionally reduce the influence of long eligibility prose, which can otherwise drown out the more specific structured fields.

**Why it helps.** A strong condition match should not be treated as equivalent to generic resemblance inside a very long eligibility section.

**Full-corpus result: 50 topics, 375,580 trials.**

| Method | nDCG@10 | Precision@10 | Recall@50 | MRR | Unsuitable in top 10 | Retrieval latency |
| --- | ---: | ---: | ---: | ---: | ---: |
| Fielded semantic search | 0.239 | 0.190 | 0.132 | 0.335 | 13.8% | ~136 ms |
| Fielded equal hybrid | 0.213 | 0.152 | 0.097 | 0.360 | 12.2% | ~136 ms |

This first fielded configuration was worse than the earlier combined-vector semantic configuration. It did not solve the unsuitable-result problem and is not connected to production storage or ranking.

After that run, the evaluator was improved to support **weighted RRF across individual fields**, which is more principled than adding raw field similarity scores. That code and its tests exist, but the full 375,580-trial weighted-field-RRF benchmark has **not** been run yet. Its results are therefore unknown and must not be represented as an improvement.

The temporary GPU machine used to build the first fielded vector corpus was terminated after the report was downloaded. The fielded vectors were not retained, so a future full evaluation must rebuild them or explicitly archive them first.

### 7. Atomic, source-linked eligibility parsing

**What was implemented.** Trial eligibility text can be represented as atomic parsed criteria with original source text/spans, parser configuration/version, raw parser output, confidence, and review flags. Ambiguous, nested, or low-confidence criteria are routed to review rather than silently treated as clear rules.

**Why it matters to search.** This does not make semantic retrieval better by itself. It is the necessary second stage that lets the platform move from “this trial may be related” to “here is the precise criterion text and the patient evidence that a reviewer should inspect.”

**Current result.** The parser's source-span preservation and abstention behavior are covered by tests. It is not used to make an automated eligibility decision. A separate reviewed criterion test set is still needed to quantify real parsing and criterion-matching quality.

## What is currently deployed in the code versus what is experimental

| Capability                                         | Status                                      | Important limitation                                                                         |
| -------------------------------------------------- | ------------------------------------------- | -------------------------------------------------------------------------------------------- |
| Lexical retrieval                                  | Implemented                                 | Low recall and relevance on the large benchmark.                                             |
| Combined semantic trial vectors                    | Implemented                                 | Vector similarity cannot apply trial logic.                                                  |
| Trial embedding jobs and pgvector storage          | Implemented                                 | Requires an available worker/model to generate vectors.                                      |
| Hybrid equal RRF                                   | Implemented                                 | Did not pass the top-ten unsuitable-result gate.                                             |
| Structured-evidence re-ranking                     | Implemented                                 | Needs reviewed FHIR-style evaluation to demonstrate impact.                                  |
| Criterion parsing with source spans and abstention | Implemented with safety gating              | Not an automated eligibility engine.                                                         |
| Persisted patient retrieval vectors                | Not implemented                             | Patient vector is recomputed at match-run time.                                              |
| Separate trial field vectors in production         | Not implemented                             | Current work is evaluator-only and did not improve results yet.                              |
| Weighted per-field RRF evaluation                  | Implemented but unbenchmarked at full scale | No performance claim is possible yet.                                                        |
| LLM fallback                                       | Not implemented                             | Planned only for carefully constrained ambiguous criterion review, not for candidate search. |

## Why the current results should not be called “good” yet

The results demonstrate a real retrieval gain, but they do not demonstrate safe high-quality matching:

- Semantic-only search improves relevance substantially but has too much top-ten noise.
- Equal hybrid search improves first-result discovery but does not preserve the lexical baseline's low unsuitable-result rate.
- Weighting semantic results harder improves relevance further, but makes the unsuitable-result rate worse.
- Separate-field vectors did not improve the first full-corpus run.
- None of these tests measure final eligibility, because they should not. Eligibility is a separate, source-evidence-driven review problem.

The honest current claim is:

> The system can retrieve and rank public trial candidates using lexical and semantic signals, with provenance and conservative follow-up checks. It has not yet demonstrated an acceptable safety-quality trade-off for autonomous or strongly prioritized trial matching.

## Recommended next steps

### Highest-value improvements

1. **Persist a patient retrieval vector per immutable patient import.** Generate it after import from only usable facts, tie it to the import/version/model, and regenerate it only for a new import snapshot. This reduces repeated work without changing what evidence is shown to reviewers.
2. **Create a small, clinician-reviewed FHIR-style retrieval set.** Each example should contain a synthetic FHIR import, a controlled trial set, known candidate judgments, and documented reasons. This measures the actual product task rather than only free-text TREC similarity.
3. **Evaluate the structured re-ranker on that reviewed set.** Its intended benefit—directly connecting source fact IDs to trial conditions/interventions—cannot be proven by text-only TREC topics.
4. **Run the unvalidated weighted-field-RRF benchmark before integrating it.** Archive the vector index or its necessary artifacts if future rebuild cost matters. Do not switch production behavior based on the unrun configuration.
5. **Use hard, source-supported filters where appropriate.** Examples include known recruitment status, documented age/sex constraints, and other explicit metadata. Missing or ambiguous facts should remain unknown rather than being converted into a negative.
6. **Consider a trained second-stage re-ranker only after a reviewed data set exists.** It should consume a bounded candidate list and produce traceable evidence, not make an eligibility decision.

### What not to do

- Do not replace criterion evaluation with a semantic score.
- Do not call a retrieved candidate “eligible.”
- Do not tune repeatedly against the final held-out topics and then claim the result is independent.
- Do not use an LLM as an unbounded search or eligibility decider.
- Do not treat missing, stale, conflicting, ambiguous, or unsupported patient facts as reassuring evidence.

## Reproducibility and evidence locations

The source-controlled implementation and recorded reports are located here:

- lexical evaluator: `apps/backend/src/evaluation/trec.py`;
- semantic/hybrid evaluator: `apps/backend/scripts/evaluate_trec_hybrid.py`;
- weighted tuning: `apps/backend/scripts/tune_trec_hybrid.py`;
- four-field vector builder: `apps/backend/scripts/build_trec_semantic_index.py`;
- AWS benchmark runner: `apps/backend/scripts/run_trec_fielded_benchmark_aws.sh`;
- lexical baseline report: `apps/backend/datasets/evaluation/trec/results/2022-token-lexical.json`;
- combined semantic/hybrid full-corpus report: `apps/backend/datasets/evaluation/trec/results/2022-semantic-hybrid-full.json`;
- structured re-ranker full-corpus report: `apps/backend/datasets/evaluation/trec/results/2022-semantic-hybrid-structured-reranker-full.json`;
- weighted held-out tuning report: `apps/backend/datasets/evaluation/trec/results/2022-weighted-hybrid-tuning.json`; and
- first fielded full-corpus report: `trec-fielded-benchmark.json` (downloaded locally after the temporary GPU run; the key numbers are reproduced in this document).

The benchmark data and reports are engineering evidence, not a clinical-performance claim. Any future release claim should require an independent, reviewed FHIR-style evaluation set, the acceptance gates above, and clinical/privacy/regulatory review appropriate to the intended use.
