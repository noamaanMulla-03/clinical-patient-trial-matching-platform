# Frozen demo baseline results

Run date: 2026-08-24
Configuration: `lexical-v1`, `manual-v1`, `deterministic-v1`,
`source-coded-v1`; no prompt, model, semantic retrieval, or reranker is used.

Run from `apps/backend`:

```bash
uv run python -m app.evaluation verify-frozen
uv run python -m app.evaluation retrieval
uv run python -m app.evaluation criteria
```

The frozen data contains nine synthetic trial summaries, two synthetic
retrieval topics, and eight carefully annotated synthetic atomic-criterion
cases. It is a deterministic regression baseline, not a TREC result, clinical
validation, or generalization claim.

| Retrieval metric | Measured baseline |
| --- | ---: |
| nDCG@5 | 0.836647 |
| nDCG@10 | 0.836647 |
| Precision@5 | 0.300000 |
| Precision@10 | 0.150000 |
| Recall@50 | 0.750000 |
| MRR | 1.000000 |
| Excluded-trial rate, top 10 | 0.291667 |

The command also emits per-topic scorer latency in milliseconds. It is measured
at run time rather than frozen here because it depends on the computer and
load. Its scope is only in-process lexical candidate scoring and ranking.

| Criterion metric | Measured baseline |
| --- | ---: |
| Macro F1 | 1.000000 |
| Exclusion recall | 1.000000 |
| False-clearance rate | 0.000000 |
| Evidence precision | 1.000000 |
| Abstention rate | 0.500000 |

The perfect criterion score means only that the versioned deterministic engine
still produces the outcomes carefully annotated for these eight narrow cases.
It must not be presented as clinician-validated accuracy.
