# TREC Clinical Trials 2021 and 2022 data usage

## Purpose and boundary

TREC Clinical Trials is used only to assess trial retrieval in engineering
research. It is never uploaded through the product, stored in the application
database, displayed in the reviewer demo, or used to make an eligibility,
treatment, enrolment, or outreach decision.

The TREC topic descriptions are synthetic patient cases. They are still kept
outside the FHIR import workflow: the product accepts only explicitly marked
synthetic FHIR Bundles, while TREC topics are evaluation inputs and are not
FHIR Bundles. Do not combine them with any real patient data.

## Official sources

NIST publishes separate 2021 and 2022 topic and qrels files. A qrels judgment
of `0` is not relevant, `1` is excluded, and `2` is eligible. Both years use
the same historical ClinicalTrials.gov collection; the 2022 overview describes
it as the 27 April 2021 snapshot containing 375,581 trial descriptions.

- 2021 page: <https://trec.nist.gov/data/trials2021.html>
- 2022 page: <https://trec.nist.gov/data/trials2022.html>
- 2022 track overview: <https://trec.nist.gov/pubs/trec31/papers/Overview_trials.pdf>

The original trial-document collection is not mirrored in this repository. It
is large, historical source material and the NIST pages direct users to the
track source for collection access. Obtain it only under the source's terms,
keep it outside Git, and retain its supplied release information and checksum.

## Reproducible acquisition

From `apps/backend`, download the public topic and qrels files:

```bash
uv run python scripts/download_trec_clinical_trials.py --year all
```

This writes the four source files and `download-manifest.json` to
`datasets/evaluation/trec/raw/`. That directory is ignored by Git so a large
external benchmark is never accidentally committed. The manifest records the
source URL, timestamp, byte count, and SHA-256 of each downloaded file.

The official files downloaded for this baseline setup on 2026-08-24 had these
SHA-256 checksums. Re-downloads must be compared to these values and any
difference must be investigated rather than silently accepted.

| File | SHA-256 |
| --- | --- |
| `topics-2021.xml` | `94bda921ce7c40a0353f251abb2ea938c77331759a9f83a36abd145ab5840aca` |
| `qrels-2021.txt` | `ba7a2cddc90285e75cd76adcd483394a6c9bacf7017113222058ba6537e6d8ac` |
| `topics-2022.xml` | `c5d37709ba14f6cb341b0bea35a7f43bd1cf93647f939659667975229a7abe91` |
| `qrels-2022.txt` | `e569a531489e03f7b1fab03fe169c8ea66f4a59e8180fa9858b1a6e4bdcb0c5c` |

## How TREC is interpreted

The platform's lexical baseline is a patient-fact-to-trial retrieval system;
it does not parse arbitrary prose notes into clinical facts. Therefore TREC
topics must be deterministically mapped to documented retrieval terms before
they can be run through this exact product baseline. That mapping must be
versioned, reviewed, and kept separate from application data.

For a TREC run, create one `retrieval.json` compatible file containing:

- the historical trial fields extracted from the source collection;
- a fixed, auditable term map for each synthetic TREC topic; and
- that year's official qrels without changing its 0/1/2 judgments.

Then run:

```bash
uv run python -m src.evaluation retrieval --dataset /path/to/trec-2021-retrieval.json
```

The command reports nDCG at 5 and 10 with graded 0/1/2 judgments; Precision at
5 and 10, Recall at 50, and MRR treat `2` (eligible) as relevant; and the
excluded-trial rate is the proportion of judged `1` trials among results
actually returned in the first ten. Latency is in-process scorer latency only:
it does not include source download, database setup, network, or review time.

## Current committed baseline

The committed `frozen-demo` benchmark is intentionally synthetic and small. It
exercises exactly the current lexical scorer and does **not** claim to be a
TREC result or a clinical-performance result. It exists to detect deterministic
regressions while the reviewed TREC term map and historical corpus preparation
remain separate research work.
