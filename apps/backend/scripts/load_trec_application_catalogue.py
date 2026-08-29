"""Load frozen public TREC trials and retained vectors into a dedicated database.

This is benchmark infrastructure only.  It accepts the historical public TREC
trial files, never FHIR bundles or patient facts, and refuses a non-empty target
database so it cannot contaminate the application catalogue.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import xml.etree.ElementTree as element_tree
from collections.abc import Iterator, Sequence
from datetime import UTC, datetime
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5
from zipfile import ZipFile

import numpy
import psycopg
from pgvector.psycopg import register_vector
from psycopg.types.json import Jsonb

from src.retrieval.semantic_config import SEMANTIC_EMBEDDING_MODEL

_RETRIEVED_AT = datetime(2021, 4, 27, tzinfo=UTC)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Load the public TREC corpus into an isolated benchmark database."
    )
    parser.add_argument("--database-url", required=True)
    parser.add_argument("--semantic-dir", default="datasets/evaluation/trec/semantic")
    parser.add_argument("--batch-size", type=int, default=250)
    args = parser.parse_args()
    if args.batch_size < 1:
        parser.error("batch-size must be positive")
    _load(
        database_url=_psycopg_url(args.database_url),
        semantic_dir=Path(args.semantic_dir),
        batch_size=args.batch_size,
    )
    return 0


def _load(*, database_url: str, semantic_dir: Path, batch_size: int) -> None:
    ids, vectors = _retained_full_text_index(semantic_dir)
    archives = _archives()
    total = len(ids)
    if sum(_study_count(archive) for archive in archives) != total:
        raise SystemExit(
            "The public trial archives do not match the retained vector index."
        )
    with psycopg.connect(database_url) as connection:
        register_vector(connection)
        _require_empty_catalogue(connection)
        with connection.cursor() as cursor:
            for start, batch in _batches(_trials(archives), batch_size=batch_size):
                expected = ids[start : start + len(batch)]
                actual = [trial[0] for trial in batch]
                if actual != expected:
                    raise SystemExit(
                        "The public trial order does not match the retained vectors."
                    )
                _copy_batch(
                    cursor, batch=batch, vectors=vectors[start : start + len(batch)]
                )
                connection.commit()
                completed = start + len(batch)
                if completed % 10_000 == 0 or completed == total:
                    print(
                        json.dumps(
                            {"status": "loading", "trials": completed, "total": total}
                        )
                    )
            cursor.execute("ANALYZE trials")
            cursor.execute("ANALYZE trial_versions")
            cursor.execute("ANALYZE trial_embeddings")
        connection.commit()
    print(json.dumps({"status": "completed", "trials": total}))


def _retained_full_text_index(semantic_dir: Path) -> tuple[list[str], numpy.memmap]:
    manifest = json.loads((semantic_dir / "manifest.json").read_text())
    if not isinstance(manifest, dict) or manifest.get("status") != "completed":
        raise SystemExit("The retained semantic index is not complete.")
    trial_count = manifest.get("trial_count")
    file_name = manifest.get("embedding_file")
    if type(trial_count) is not int or not isinstance(file_name, str):
        raise SystemExit(
            "The benchmark requires the retained combined full-text index."
        )
    ids = (semantic_dir / "nct-ids.txt").read_text().splitlines()
    if len(ids) != trial_count:
        raise SystemExit("The retained vector identifiers do not match its manifest.")
    return ids, numpy.memmap(
        semantic_dir / file_name,
        dtype=numpy.float32,
        mode="r",
        shape=(trial_count, SEMANTIC_EMBEDDING_MODEL.dimensions),
    )


def _archives() -> list[Path]:
    base = Path("datasets/evaluation/trec/raw")
    return [base / f"ClinicalTrials.2021-04-27.part{part}.zip" for part in range(1, 6)]


def _study_count(archive_path: Path) -> int:
    with ZipFile(archive_path) as archive:
        return sum(member.filename.endswith(".xml") for member in archive.infolist())


def _trials(
    archives: Sequence[Path],
) -> Iterator[tuple[str, dict[str, object], str, list[str], list[dict[str, str]], str]]:
    for archive_path in archives:
        with ZipFile(archive_path) as archive:
            for member in archive.infolist():
                if not member.filename.endswith(".xml"):
                    continue
                yield _trial(archive.read(member))


def _batches(
    trials: Iterator[
        tuple[str, dict[str, object], str, list[str], list[dict[str, str]], str]
    ],
    *,
    batch_size: int,
) -> Iterator[
    tuple[
        int,
        list[tuple[str, dict[str, object], str, list[str], list[dict[str, str]], str]],
    ]
]:
    """Stream archive records once; rescanning would make a full load quadratic."""
    batch: list[
        tuple[str, dict[str, object], str, list[str], list[dict[str, str]], str]
    ] = []
    start = 0
    for trial in trials:
        batch.append(trial)
        if len(batch) == batch_size:
            yield start, batch
            start += len(batch)
            batch = []
    if batch:
        yield start, batch


def _trial(
    source: bytes,
) -> tuple[str, dict[str, object], str, list[str], list[dict[str, str]], str]:
    root = element_tree.fromstring(source)
    nct_id = root.findtext("id_info/nct_id")
    if not nct_id:
        raise ValueError("A public TREC trial has no NCT identifier.")
    title = root.findtext("brief_title") or ""
    conditions = [item.text for item in root.findall("condition") if item.text]
    interventions = [
        {
            key: value
            for key, value in {
                "name": intervention.findtext("intervention_name"),
                "description": intervention.findtext("description"),
            }.items()
            if value
        }
        for intervention in root.findall("intervention")
    ]
    eligibility = root.findtext("eligibility/criteria/textblock") or ""
    raw_study: dict[str, object] = {
        "protocolSection": {
            "identificationModule": {"nctId": nct_id, "briefTitle": title},
            "statusModule": {"overallStatus": "UNKNOWN"},
            "conditionsModule": {"conditions": conditions},
            "armsInterventionsModule": {"interventions": interventions},
            "eligibilityModule": {"eligibilityCriteria": eligibility},
        }
    }
    canonical = json.dumps(raw_study, sort_keys=True, separators=(",", ":"))
    return (
        nct_id,
        raw_study,
        title,
        conditions,
        interventions,
        hashlib.sha256(canonical.encode()).hexdigest(),
    )


def _copy_batch(
    cursor: psycopg.Cursor[object],
    *,
    batch: Sequence[
        tuple[str, dict[str, object], str, list[str], list[dict[str, str]], str]
    ],
    vectors: numpy.ndarray,
) -> None:
    trials: list[tuple[object, ...]] = []
    versions: list[tuple[object, ...]] = []
    embeddings: list[tuple[object, ...]] = []
    for (
        nct_id,
        raw_study,
        title,
        conditions,
        interventions,
        source_hash,
    ), vector in zip(batch, vectors, strict=True):
        version_id = uuid5(NAMESPACE_URL, f"trec-public-version:{nct_id}")
        trials.append(
            (
                nct_id,
                Jsonb(raw_study),
                title,
                Jsonb(conditions),
                Jsonb(interventions),
                "UNKNOWN",
                Jsonb([]),
                None,
                None,
                None,
                None,
                Jsonb([]),
                source_hash,
                None,
                _RETRIEVED_AT,
                _RETRIEVED_AT,
            )
        )
        versions.append(
            (
                version_id,
                nct_id,
                source_hash,
                source_hash,
                None,
                True,
                None,
                None,
                Jsonb(raw_study),
                None,
                _RETRIEVED_AT,
                _RETRIEVED_AT,
            )
        )
        embeddings.append(
            (
                uuid5(NAMESPACE_URL, f"trec-public-embedding:{nct_id}"),
                version_id,
                SEMANTIC_EMBEDDING_MODEL.configuration_version,
                source_hash,
                numpy.asarray(vector, dtype=numpy.float32),
                _RETRIEVED_AT,
            )
        )
    _copy(cursor, "trials", _TRIAL_COLUMNS, trials)
    _copy(cursor, "trial_versions", _VERSION_COLUMNS, versions)
    _copy(cursor, "trial_embeddings", _EMBEDDING_COLUMNS, embeddings)


def _copy(
    cursor: psycopg.Cursor[object],
    table: str,
    columns: str,
    rows: Sequence[tuple[object, ...]],
) -> None:
    with cursor.copy(f"COPY {table} ({columns}) FROM STDIN") as copy:
        for row in rows:
            copy.write_row(row)


def _require_empty_catalogue(connection: psycopg.Connection[object]) -> None:
    with connection.cursor() as cursor:
        cursor.execute("SELECT count(*) FROM trial_versions")
        count = cursor.fetchone()
    if not count or int(count[0]) != 0:
        raise SystemExit("The target benchmark database is not empty.")


def _psycopg_url(database_url: str) -> str:
    return database_url.replace("postgresql+asyncpg://", "postgresql://", 1)


_TRIAL_COLUMNS = (
    "nct_id,current_data,title,conditions,interventions,status,phases,eligibility_text,"
    "minimum_age,maximum_age,sex,locations,matching_source_hash,source_updated_at,"
    "retrieved_at,ingested_at"
)
_VERSION_COLUMNS = (
    "id,nct_id,source_hash,matching_source_hash,matching_reused_from_version_id,"
    "requires_reparse,superseded_by_version_id,superseded_at,raw_study,source_updated_at,"
    "retrieved_at,ingested_at"
)
_EMBEDDING_COLUMNS = (
    "id,trial_version_id,model_configuration_version,content_hash,embedding,created_at"
)


if __name__ == "__main__":
    raise SystemExit(main())
