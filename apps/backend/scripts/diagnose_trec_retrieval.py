"""Create a public-ID-only TREC retrieval failure report for Phase 8.1."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def main() -> int:
    base = Path("datasets/evaluation/trec")
    lexical = json.loads((base / "results/2022-token-lexical.json").read_text())
    comparison = json.loads(
        (base / "results/2022-semantic-hybrid-full.json").read_text()
    )
    report = diagnose_topics(
        lexical.get("topics"), comparison.get("semantic", {}).get("topics")
    )
    output = {
        "evaluation": "trec-2022-retrieval-diagnosis",
        "claimable": False,
        "scope": (
            "public topic identifiers, public NCT identifiers, and benchmark "
            "metrics only; no patient record or eligibility outcome is created"
        ),
        **report,
    }
    output_path = base / "results/2022-retrieval-diagnosis.json"
    output_path.write_text(json.dumps(output, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"status": "completed", "output": str(output_path)}))
    return 0


def diagnose_topics(
    lexical_topics: object, semantic_topics: object
) -> dict[str, object]:
    lexical_by_id = _topics_by_id(lexical_topics)
    semantic_by_id = _topics_by_id(semantic_topics)
    if set(lexical_by_id) != set(semantic_by_id):
        raise ValueError("Lexical and semantic reports must cover the same topics.")
    cases = []
    for topic_id in sorted(lexical_by_id, key=int):
        lexical = lexical_by_id[topic_id]
        semantic = semantic_by_id[topic_id]
        cases.append(
            {
                "topic_id": topic_id,
                "lexical_nDCG@10": _metric(lexical, "nDCG@10"),
                "semantic_nDCG@10": _metric(semantic, "nDCG@10"),
                "lexical_trec_grade_1_rate_top_10": _metric(
                    lexical, "trec_grade_1_rate_top_10"
                ),
                "semantic_trec_grade_1_rate_top_10": _metric(
                    semantic, "trec_grade_1_rate_top_10"
                ),
                "semantic_ranked_nct_ids": _identifiers(semantic),
            }
        )
    return {
        "topic_count": len(cases),
        "highest_semantic_trec_grade_1_rate": sorted(
            cases,
            key=lambda case: (
                -float(case["semantic_trec_grade_1_rate_top_10"]),
                int(str(case["topic_id"])),
            ),
        )[:10],
        "semantic_worse_than_lexical_nDCG@10": [
            case
            for case in cases
            if float(case["semantic_nDCG@10"]) < float(case["lexical_nDCG@10"])
        ],
    }


def _topics_by_id(topics: object) -> dict[str, dict[str, Any]]:
    if not isinstance(topics, list):
        raise ValueError("TREC report must contain a topics list.")
    result: dict[str, dict[str, Any]] = {}
    for topic in topics:
        if not isinstance(topic, dict) or not isinstance(topic.get("topic_id"), str):
            raise ValueError("TREC report contains an invalid topic.")
        result[topic["topic_id"]] = topic
    return result


def _metric(topic: dict[str, Any], name: str) -> float:
    value = topic.get(name)
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ValueError(f"TREC topic is missing numeric {name}.")
    return float(value)


def _identifiers(topic: dict[str, Any]) -> list[str]:
    identifiers = topic.get("ranked_nct_ids")
    if not isinstance(identifiers, list) or not all(
        isinstance(identifier, str) for identifier in identifiers
    ):
        raise ValueError("TREC topic is missing bounded public trial ranks.")
    return identifiers


if __name__ == "__main__":
    raise SystemExit(main())
