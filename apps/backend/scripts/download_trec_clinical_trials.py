"""Download the public TREC Clinical Trials topics and relevance judgments.

This script intentionally does not download or import patient data into the
application. TREC topics are synthetic case descriptions used only outside the
product boundary for research evaluation.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

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
            if destination.exists() and not args.replace:
                content = destination.read_bytes()
                action = "preserved"
            else:
                print(f"Downloading TREC {year} {filename}...")
                request = Request(
                    url,
                    headers={
                        "User-Agent": "clinical-trial-matcher-research-evaluation/0.1"
                    },
                )
                try:
                    with urlopen(request, timeout=60) as response:  # noqa: S310 - fixed NIST URLs
                        content = response.read()
                except (HTTPError, URLError) as error:
                    parser.error(
                        f"Could not download {filename} from NIST: {error}. "
                        "No application data was changed."
                    )
                destination.write_bytes(content)
                action = "downloaded"
            sources = manifest["sources"]
            assert isinstance(sources, list)
            sources.append(
                {
                    "year": year,
                    "filename": filename,
                    "url": url,
                    "sha256": hashlib.sha256(content).hexdigest(),
                    "bytes": len(content),
                    "action": action,
                }
            )
    (output_dir / "download-manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(f"Wrote {output_dir / 'download-manifest.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
