"""Benchmark the live PostgreSQL retrieval path against public TREC topics.

The target database must be a dedicated, migrated benchmark catalogue containing
the frozen public TREC trials and their matching vectors.  This command refuses
to score a partial catalogue so a small development catalogue cannot be mistaken
for a full-corpus result.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import xml.etree.ElementTree as element_tree
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from src.evaluation.application_path import (
    current_catalogue_trial_count,
    retrieve_application_path_candidates,
)
from src.evaluation.metrics import (
    mean,
    ndcg_at_k,
    precision_at_k,
    recall_at_k,
    reciprocal_rank,
    trec_grade_1_rate_at_k,
)
from src.evaluation.trec import tokenize_trec_text
from src.retrieval.embedding_encoder import configured_embedding_encoder
from src.retrieval.schemas import PatientDerivedRetrievalQuery, RetrievalTerm

_EXPECTED_TREC_TRIAL_COUNT = 375_580


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Evaluate the live PostgreSQL retrieval path on public TREC topics."
        )
    )
    parser.add_argument("--database-url", required=True)
    parser.add_argument(
        "--expected-trial-count", type=int, default=_EXPECTED_TREC_TRIAL_COUNT
    )
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    if args.expected_trial_count < 1:
        parser.error("expected-trial-count must be positive")
    asyncio.run(_evaluate(args))
    return 0


async def _evaluate(args: argparse.Namespace) -> None:
    base = Path("datasets/evaluation/trec/raw")
    topics = _topics(base / "topics-2022.xml")
    qrels = _qrels(base / "qrels-2022.txt")
    catalogue_as_of = datetime.now(UTC)
    engine = create_async_engine(args.database_url, pool_pre_ping=True)
    try:
        async with (
            engine.connect() as connection,
            connection.begin(),
            AsyncSession(bind=connection) as session,
        ):
            count = await current_catalogue_trial_count(
                session, catalogue_as_of=catalogue_as_of
            )
            if count != args.expected_trial_count:
                raise SystemExit(
                    "The benchmark database must contain exactly "
                    f"{args.expected_trial_count} current frozen trials; "
                    f"found {count}."
                )
            encoder = configured_embedding_encoder()
            rows: dict[str, list[dict[str, object]]] = {
                "lexical": [],
                "semantic": [],
                "hybrid": [],
                "final_ordering": [],
            }
            for topic_id, text in topics:
                query = _query(topic_id, text)
                candidates = await retrieve_application_path_candidates(
                    session,
                    query,
                    catalogue_as_of=catalogue_as_of,
                    encoder=encoder,
                )
                if candidates.mode != "hybrid":
                    raise SystemExit(
                        "Semantic branch was unavailable for topic "
                        f"{topic_id}: {candidates.mode}"
                    )
                for name, ranked in (
                    ("lexical", candidates.lexical_nct_ids),
                    ("semantic", candidates.semantic_nct_ids),
                    ("hybrid", candidates.hybrid_nct_ids),
                    ("final_ordering", candidates.final_nct_ids),
                ):
                    rows[name].append(_result(topic_id, ranked, qrels[topic_id]))
    finally:
        await engine.dispose()
    report = {
        "evaluation": "trec-live-postgresql-retrieval-path",
        "claimable": False,
        "scope": (
            "public TREC token adapter; no FHIR patient data or eligibility decision"
        ),
        "catalogue_as_of": catalogue_as_of.isoformat(),
        "trial_count": args.expected_trial_count,
        "topic_count": len(topics),
        "methods": {name: _metrics(values) for name, values in rows.items()},
    }
    Path(args.output).write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"status": "completed", "output": args.output}))


def _topics(path: Path) -> list[tuple[str, str]]:
    root = element_tree.parse(path).getroot()
    return [
        (node.attrib["number"], " ".join(node.itertext()))
        for node in root.findall(".//topic")
    ]


def _qrels(path: Path) -> dict[str, dict[str, int]]:
    values: dict[str, dict[str, int]] = {}
    for line in path.read_text().splitlines():
        topic, _, nct_id, grade = line.split()
        values.setdefault(topic, {})[nct_id] = int(grade)
    return values


def _query(topic_id: str, text: str) -> PatientDerivedRetrievalQuery:
    return PatientDerivedRetrievalQuery(
        terms=[
            RetrievalTerm(
                text=token,
                source_fact_id=f"trec-{topic_id}-{index}",
                kind="condition",
            )
            for index, token in enumerate(sorted(tokenize_trec_text(text)), 1)
        ]
    )


def _result(
    topic_id: str, ranked: list[str], judgments: dict[str, int]
) -> dict[str, object]:
    grades = [judgments.get(nct_id, 0) for nct_id in ranked]
    ideal = list(judgments.values())
    eligible = sum(grade == 2 for grade in ideal)
    return {
        "topic_id": topic_id,
        "nDCG@10": ndcg_at_k(grades, ideal_relevances=ideal, k=10),
        "Precision@10": precision_at_k(grades, k=10),
        "Recall@50": recall_at_k(grades, total_relevant=eligible, k=50),
        "MRR": reciprocal_rank(grades),
        "trec_grade_1_rate_top_10": trec_grade_1_rate_at_k(grades, k=10),
    }


def _metrics(results: list[dict[str, object]]) -> dict[str, float]:
    names = (
        "nDCG@10",
        "Precision@10",
        "Recall@50",
        "MRR",
        "trec_grade_1_rate_top_10",
    )
    return {name: mean(float(result[name]) for result in results) for name in names}


if __name__ == "__main__":
    raise SystemExit(main())
