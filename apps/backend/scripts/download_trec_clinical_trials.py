"""Download the public TREC Clinical Trials topics and relevance judgments.

This script intentionally does not download or import patient data into the
application. TREC topics are synthetic case descriptions used only outside the
product boundary for research evaluation.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from zipfile import BadZipFile, ZipFile, is_zipfile

_SOURCES = {
    "2021": {
        "topics-2021.xml": "https://trec.nist.gov/data/trials/topics2021.xml",
        "qrels-2021.txt": "https://trec.nist.gov/data/trials/qrels2021.txt",
    },
    "2022": {
        "topics-2022.xml": "https://trec.nist.gov/data/trials/topics2022.xml",
        "qrels-2022.txt": "https://trec.nist.gov/data/trials/qrels2022.txt",
    },
}

_OFFICIAL_SHA256 = {
    "topics-2021.xml": (
        "94bda921ce7c40a0353f251abb2ea938c77331759a9f83a36abd145ab5840aca"
    ),
    "qrels-2021.txt": (
        "ba7a2cddc90285e75cd76adcd483394a6c9bacf7017113222058ba6537e6d8ac"
    ),
    "topics-2022.xml": (
        "c5d37709ba14f6cb341b0bea35a7f43bd1cf93647f939659667975229a7abe91"
    ),
    "qrels-2022.txt": (
        "e569a531489e03f7b1fab03fe169c8ea66f4a59e8180fa9858b1a6e4bdcb0c5c"
    ),
}

_CORPUS_SOURCE_ROOT = "https://www.trec-cds.org/2021_data"
_CORPUS_SOURCES = tuple(
    (
        f"ClinicalTrials.2021-04-27.part{part}.zip",
        f"{_CORPUS_SOURCE_ROOT}/ClinicalTrials.2021-04-27.part{part}.zip",
    )
    for part in range(1, 6)
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Download official public TREC Clinical Trials topics and qrels."
    )
    parser.add_argument(
        "--year",
        choices=("2021", "2022", "all"),
        default="all",
        help="TREC collection year to download.",
    )
    parser.add_argument(
        "--include-corpus",
        action="store_true",
        help=(
            "Also download the five large historical ClinicalTrials.gov XML archives "
            "shared by the 2021 and 2022 tracks."
        ),
    )
    parser.add_argument(
        "--verify-corpus",
        action="store_true",
        help=(
            "Read each corpus archive after download and verify its ZIP CRCs. "
            "Requires --include-corpus."
        ),
    )
    parser.add_argument(
        "--output-dir",
        default="datasets/evaluation/trec/raw",
        help="Local, gitignored directory for the downloaded source files.",
    )
    parser.add_argument(
        "--replace",
        action="store_true",
        help="Replace an existing file rather than preserving its bytes.",
    )
    args = parser.parse_args()
    if args.verify_corpus and not args.include_corpus:
        parser.error("--verify-corpus requires --include-corpus.")
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    selected_years = tuple(_SOURCES) if args.year == "all" else (args.year,)
    manifest: dict[str, object] = {
        "downloaded_at": datetime.now(UTC).isoformat(),
        "sources": [],
    }
    for year in selected_years:
        for filename, url in _SOURCES[year].items():
            destination = output_dir / filename
            action, byte_count, sha256 = _download_or_preserve(
                destination,
                url=url,
                replace=args.replace,
                expected_sha256=_OFFICIAL_SHA256[filename],
                label=f"TREC {year} {filename}",
            )
            sources = manifest["sources"]
            assert isinstance(sources, list)
            sources.append(
                {
                    "year": year,
                    "filename": filename,
                    "url": url,
                    "sha256": sha256,
                    "bytes": byte_count,
                    "action": action,
                }
            )
    if args.include_corpus:
        for filename, url in _CORPUS_SOURCES:
            destination = output_dir / filename
            action, byte_count, sha256 = _download_or_preserve(
                destination,
                url=url,
                replace=args.replace,
                expected_sha256=None,
                label=f"TREC historical corpus {filename}",
            )
            if args.verify_corpus:
                _verify_zip(destination)
            sources = manifest["sources"]
            assert isinstance(sources, list)
            sources.append(
                {
                    "year": "2021-2022-shared",
                    "filename": filename,
                    "url": url,
                    "sha256": sha256,
                    "bytes": byte_count,
                    "action": action,
                    "zip_crc_verified": args.verify_corpus,
                }
            )
    (output_dir / "download-manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(f"Wrote {output_dir / 'download-manifest.json'}")
    return 0


def _download_or_preserve(
    destination: Path,
    *,
    url: str,
    replace: bool,
    expected_sha256: str | None,
    label: str,
) -> tuple[str, int, str]:
    if destination.exists() and not replace:
        action = "preserved"
        byte_count, sha256 = _file_digest(destination)
    else:
        print(f"Downloading {label}...")
        partial_destination = destination.with_suffix(destination.suffix + ".partial")
        partial_destination.unlink(missing_ok=True)
        request = Request(
            url,
            headers={"User-Agent": "clinical-trial-matcher-research-evaluation/0.1"},
        )
        try:
            digest = hashlib.sha256()
            byte_count = 0
            with (
                urlopen(request, timeout=60) as response,  # noqa: S310 - fixed public URLs
                partial_destination.open("wb") as destination_file,
            ):
                while chunk := response.read(1024 * 1024):
                    destination_file.write(chunk)
                    digest.update(chunk)
                    byte_count += len(chunk)
        except (HTTPError, URLError, OSError) as error:
            partial_destination.unlink(missing_ok=True)
            raise SystemExit(
                f"Could not download {destination.name}: {error}. "
                "No application data was changed."
            ) from error
        sha256 = digest.hexdigest()
        os.replace(partial_destination, destination)
        action = "downloaded"
    if expected_sha256 is not None and sha256 != expected_sha256:
        raise SystemExit(
            f"Checksum mismatch for {destination.name}; expected {expected_sha256}, "
            f"received {sha256}. The file was not accepted as official TREC input."
        )
    return action, byte_count, sha256


def _file_digest(path: Path) -> tuple[int, str]:
    digest = hashlib.sha256()
    byte_count = 0
    with path.open("rb") as source_file:
        while chunk := source_file.read(1024 * 1024):
            digest.update(chunk)
            byte_count += len(chunk)
    return byte_count, digest.hexdigest()


def _verify_zip(path: Path) -> None:
    if not is_zipfile(path):
        raise SystemExit(
            f"Downloaded corpus archive {path.name} is not a valid ZIP file."
        )
    try:
        with ZipFile(path) as archive:
            failed_member = archive.testzip()
    except BadZipFile as error:
        raise SystemExit(
            f"Downloaded corpus archive {path.name} is invalid: {error}."
        ) from error
    if failed_member is not None:
        raise SystemExit(
            f"Downloaded corpus archive {path.name} failed CRC verification at "
            f"{failed_member}."
        )


if __name__ == "__main__":
    raise SystemExit(main())
