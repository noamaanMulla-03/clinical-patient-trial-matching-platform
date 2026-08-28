"""CLI entrypoint for reproducible research-only baseline evaluation."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from src.evaluation.runner import (
    EvaluationDatasetError,
    compare_held_out_retrieval_dataset,
    evaluate_criteria_dataset,
    evaluate_parser_dataset,
    evaluate_retrieval_dataset,
    verify_frozen_dataset,
)
from src.evaluation.trec import TrecEvaluationError, evaluate_trec_lexical_baseline

_DEFAULT_DATASET_ROOT = Path("datasets/evaluation/frozen-demo")
_DEFAULT_TREC_RAW_ROOT = Path("datasets/evaluation/trec/raw")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run the research-only deterministic baseline evaluation."
    )
    subcommands = parser.add_subparsers(dest="command", required=True)
    _add_dataset_command(subcommands, "retrieval", "retrieval.json")
    _add_dataset_command(subcommands, "criteria", "criteria.json")
    _add_dataset_command(subcommands, "parser", "parser.json")
    _add_dataset_command(subcommands, "verify-frozen", "manifest.json")
    _add_dataset_command(
        subcommands,
        "compare-heldout-retrieval",
        "heldout-retrieval.json",
    )
    trec_lexical = subcommands.add_parser(
        "trec-lexical",
        help="Run the read-only TREC token-adapter lexical baseline.",
    )
    trec_lexical.add_argument(
        "--topics",
        default=str(_DEFAULT_TREC_RAW_ROOT / "topics-2022.xml"),
        help="Official TREC topics XML; it is never imported as a patient.",
    )
    trec_lexical.add_argument(
        "--qrels",
        default=str(_DEFAULT_TREC_RAW_ROOT / "qrels-2022.txt"),
        help="Official TREC relevance-judgment file.",
    )
    trec_lexical.add_argument(
        "--archive",
        action="append",
        default=[
            str(_DEFAULT_TREC_RAW_ROOT / f"ClinicalTrials.2021-04-27.part{part}.zip")
            for part in range(1, 6)
        ],
        help="Historical TREC trial archive; repeat to add another archive.",
    )
    trec_lexical.add_argument(
        "--candidate-limit",
        type=int,
        default=100,
        help="Maximum ranked public trials retained per synthetic TREC topic.",
    )
    trec_lexical.add_argument(
        "--topic-limit",
        type=int,
        help="Optional bounded smoke-test count; not suitable for benchmark claims.",
    )
    trec_lexical.add_argument(
        "--output", help="Optional JSON report path; stdout is always written."
    )
    args = parser.parse_args()

    try:
        if args.command == "trec-lexical":
            result = evaluate_trec_lexical_baseline(
                topics_path=Path(args.topics),
                qrels_path=Path(args.qrels),
                archives=[Path(archive) for archive in args.archive],
                candidate_limit=args.candidate_limit,
                topic_limit=args.topic_limit,
            )
        else:
            dataset = Path(args.dataset)
            result = {
                "retrieval": evaluate_retrieval_dataset,
                "criteria": evaluate_criteria_dataset,
                "parser": evaluate_parser_dataset,
                "verify-frozen": verify_frozen_dataset,
                "compare-heldout-retrieval": compare_held_out_retrieval_dataset,
            }[args.command](dataset)
    except (EvaluationDatasetError, TrecEvaluationError) as error:
        parser.error(str(error))
    _write_result(result, output=Path(args.output) if args.output else None)
    return 0


def _add_dataset_command(
    subcommands: argparse._SubParsersAction[argparse.ArgumentParser],
    name: str,
    default_filename: str,
) -> None:
    command = subcommands.add_parser(name)
    command.add_argument(
        "--dataset",
        default=str(_DEFAULT_DATASET_ROOT / default_filename),
        help="Path to the frozen evaluation JSON input.",
    )
    command.add_argument(
        "--output", help="Optional JSON report path; stdout is always written."
    )


def _write_result(result: dict[str, Any], *, output: Path | None) -> None:
    serialized = json.dumps(result, indent=2, sort_keys=True) + "\n"
    print(serialized, end="")
    if output is not None:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(serialized, encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
