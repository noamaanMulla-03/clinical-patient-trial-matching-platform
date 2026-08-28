"""TREC acquisition must retain its fixed official source boundaries."""

from __future__ import annotations

import hashlib
from pathlib import Path
from zipfile import ZipFile

import pytest

from scripts.download_trec_clinical_trials import (
    _CORPUS_SOURCES,
    _OFFICIAL_SHA256,
    _download_or_preserve,
    _verify_zip,
)


def test_trec_sources_pin_all_official_topic_and_qrels_checksums() -> None:
    assert set(_OFFICIAL_SHA256) == {
        "topics-2021.xml",
        "qrels-2021.txt",
        "topics-2022.xml",
        "qrels-2022.txt",
    }
    assert len(_CORPUS_SOURCES) == 5
    assert all(
        url.startswith("https://www.trec-cds.org/2021_data/")
        for _, url in _CORPUS_SOURCES
    )


def test_preserved_official_file_with_an_unexpected_checksum_is_rejected(
    tmp_path: Path,
) -> None:
    destination = tmp_path / "topics-2021.xml"
    destination.write_text("unexpected", encoding="utf-8")

    with pytest.raises(SystemExit, match="Checksum mismatch"):
        _download_or_preserve(
            destination,
            url="https://trec.nist.gov/data/trials/topics2021.xml",
            replace=False,
            expected_sha256=hashlib.sha256(b"expected").hexdigest(),
            label="test source",
        )


def test_corpus_zip_crc_verification_accepts_a_valid_archive(tmp_path: Path) -> None:
    archive_path = tmp_path / "corpus.zip"
    with ZipFile(archive_path, "w") as archive:
        archive.writestr("trial.xml", "<clinical_study />")

    _verify_zip(archive_path)


def test_corpus_zip_crc_verification_rejects_non_zip_input(tmp_path: Path) -> None:
    archive_path = tmp_path / "corpus.zip"
    archive_path.write_text("not a zip", encoding="utf-8")

    with pytest.raises(SystemExit, match="not a valid ZIP"):
        _verify_zip(archive_path)
