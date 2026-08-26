"""CLI entrypoint for reproducible research-only baseline evaluation."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from src.evaluation.runner import (
    EvaluationDatasetError,
    evaluate_criteria_dataset,
    evaluate_retrieval_dataset,
    verify_frozen_dataset,
)

_DEFAULT_DATASET_ROOT = Path("datasets/evaluation/frozen-demo")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run the research-only deterministic baseline evaluation."
    )
    subcommands = parser.add_subparsers(dest="command", required=True)
    _add_dataset_command(subcommands, "retrieval", "retrieval.json")
    _add_dataset_command(subcommands, "criteria", "criteria.json")
    _add_dataset_command(subcommands, "verify-frozen", "manifest.json")
    args = parser.parse_args()

    dataset = Path(args.dataset)
    try:
        result = {
            "retrieval": evaluate_retrieval_dataset,
            "criteria": evaluate_criteria_dataset,
            "verify-frozen": verify_frozen_dataset,
        }[args.command](dataset)
    except EvaluationDatasetError as error:
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
