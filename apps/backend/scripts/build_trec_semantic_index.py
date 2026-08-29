"""Build a resumable, local-only semantic index for the TREC trial corpus."""

from __future__ import annotations

import argparse
import hashlib
import json
import xml.etree.ElementTree as element_tree
from collections.abc import Iterator, Mapping, Sequence
from pathlib import Path
from zipfile import ZipFile

import numpy

from src.retrieval.embedding_encoder import configured_embedding_encoder
from src.retrieval.semantic_config import SEMANTIC_EMBEDDING_MODEL

_FIELD_NAMES = ("title", "conditions", "interventions", "eligibility")
_DOCUMENT_PROFILE = "fielded-trial-retrieval-v1"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build a local, resumable TREC public-trial semantic index."
    )
    parser.add_argument(
        "--archive",
        action="append",
        default=[
            f"datasets/evaluation/trec/raw/ClinicalTrials.2021-04-27.part{part}.zip"
            for part in range(1, 6)
        ],
    )
    parser.add_argument(
        "--output-dir", default="datasets/evaluation/trec/semantic-fielded"
    )
    parser.add_argument("--batch-size", type=int, default=32)
    args = parser.parse_args()
    if args.batch_size < 1:
        parser.error("--batch-size must be positive.")

    archives = [Path(value) for value in args.archive]
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    index_paths = {
        field_name: output_dir / f"pubmedbert-{field_name}-embeddings.f32"
        for field_name in _FIELD_NAMES
    }
    identifiers_path = output_dir / "nct-ids.txt"
    state_path = output_dir / "build-state.json"
    total_trials = sum(_xml_member_count(archive) for archive in archives)
    state = _load_state(state_path, total_trials=total_trials)
    completed = int(state["completed_trials"])
    embeddings = {
        field_name: numpy.memmap(
            index_path,
            mode="r+" if index_path.exists() else "w+",
            dtype=numpy.float32,
            shape=(total_trials, SEMANTIC_EMBEDDING_MODEL.dimensions),
        )
        for field_name, index_path in index_paths.items()
    }
    encoder = configured_embedding_encoder()
    batch: list[tuple[str, dict[str, str]]] = []
    with identifiers_path.open("a" if completed else "w", encoding="utf-8") as ids_file:
        for trial_index, trial in enumerate(_iter_documents(archives)):
            if trial_index < completed:
                continue
            batch.append(trial)
            if len(batch) == args.batch_size:
                completed = _write_batch(
                    batch,
                    encoder=encoder,
                    embeddings=embeddings,
                    start=completed,
                    ids_file=ids_file,
                )
                _write_state(state_path, completed=completed, total_trials=total_trials)
                batch.clear()
        if batch:
            completed = _write_batch(
                batch,
                encoder=encoder,
                embeddings=embeddings,
                start=completed,
                ids_file=ids_file,
            )
            _write_state(state_path, completed=completed, total_trials=total_trials)
    for embedding in embeddings.values():
        embedding.flush()
    if completed != total_trials:
        raise SystemExit(
            "Semantic index stopped before all public trial records were encoded."
        )
    manifest = {
        "status": "completed",
        "trial_count": total_trials,
        "embedding_files": {
            field_name: {
                "file": index_path.name,
                "sha256": _sha256(index_path),
            }
            for field_name, index_path in index_paths.items()
        },
        "identifiers_file": identifiers_path.name,
        "identifiers_sha256": _sha256(identifiers_path),
        "document_profile": _DOCUMENT_PROFILE,
        "model": SEMANTIC_EMBEDDING_MODEL.snapshot(),
    }
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps({"status": "completed", "trial_count": total_trials}))
    return 0


def _xml_member_count(archive_path: Path) -> int:
    with ZipFile(archive_path) as archive:
        return sum(member.filename.endswith(".xml") for member in archive.infolist())


def _iter_documents(archives: Sequence[Path]) -> Iterator[tuple[str, dict[str, str]]]:
    for archive_path in archives:
        with ZipFile(archive_path) as archive:
            for member in archive.infolist():
                if member.filename.endswith(".xml"):
                    yield _field_documents_from_xml(archive.read(member))


def _field_documents_from_xml(source: bytes) -> tuple[str, dict[str, str]]:
    root = element_tree.fromstring(source)
    nct_id = root.findtext("id_info/nct_id")
    if not nct_id:
        raise ValueError("A TREC public-trial record is missing its NCT ID.")
    fields = {
        "title": root.findtext("brief_title") or "",
        "conditions": " ".join(value.text or "" for value in root.findall("condition")),
        "interventions": " ".join(
            value
            for intervention in root.findall("intervention")
            for value in (
                intervention.findtext("intervention_name") or "",
                intervention.findtext("description") or "",
            )
        ),
        "eligibility": root.findtext("eligibility/criteria/textblock") or "",
    }
    fields = {name: value.strip() for name, value in fields.items()}
    if not any(fields.values()):
        raise ValueError("A TREC public-trial record has no searchable text.")
    return nct_id, fields


def _write_batch(
    batch: Sequence[tuple[str, Mapping[str, str]]],
    *,
    encoder: object,
    embeddings: Mapping[str, numpy.memmap],
    start: int,
    ids_file: object,
) -> int:
    for field_name, embedding in embeddings.items():
        documents = [fields[field_name] for _, fields in batch]
        populated = [
            (index, document) for index, document in enumerate(documents) if document
        ]
        vectors = numpy.zeros(
            (len(batch), SEMANTIC_EMBEDDING_MODEL.dimensions), dtype=numpy.float32
        )
        if populated:
            encoded = encoder.encode_many(
                [document for _, document in populated], batch_size=len(populated)
            )  # type: ignore[attr-defined]
            vectors[[index for index, _ in populated]] = numpy.asarray(
                encoded, dtype=numpy.float32
            )
        embedding[start : start + len(batch)] = vectors
    ids_file.write("".join(f"{identifier}\n" for identifier, _ in batch))  # type: ignore[attr-defined]
    ids_file.flush()  # type: ignore[attr-defined]
    return start + len(batch)


def _load_state(path: Path, *, total_trials: int) -> dict[str, int]:
    if not path.exists():
        return {"completed_trials": 0, "total_trials": total_trials}
    state = json.loads(path.read_text(encoding="utf-8"))
    if state.get("total_trials") != total_trials:
        raise ValueError("Existing TREC semantic index has a different corpus size.")
    return {
        "completed_trials": int(state["completed_trials"]),
        "total_trials": total_trials,
    }


def _write_state(path: Path, *, completed: int, total_trials: int) -> None:
    path.write_text(
        json.dumps({"completed_trials": completed, "total_trials": total_trials})
        + "\n",
        encoding="utf-8",
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


if __name__ == "__main__":
    raise SystemExit(main())
