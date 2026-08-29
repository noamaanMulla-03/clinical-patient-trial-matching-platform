"""Compare local semantic and reciprocal-rank-fused TREC retrieval results."""

from __future__ import annotations

import argparse
import json
import time
import xml.etree.ElementTree as element_tree
from pathlib import Path
from zipfile import ZipFile

import numpy

from src.db.models import Trial
from src.evaluation.metrics import (
    mean,
    ndcg_at_k,
    precision_at_k,
    recall_at_k,
    reciprocal_rank,
    trec_grade_1_rate_at_k,
)
from src.evaluation.trec import evaluate_trec_lexical_baseline, tokenize_trec_text
from src.retrieval.embedding_encoder import configured_embedding_encoder
from src.retrieval.fusion import RECIPROCAL_RANK_FUSION_RANK_CONSTANT
from src.retrieval.reranking import rerank_fused_trial_candidates
from src.retrieval.schemas import PatientDerivedRetrievalQuery, RetrievalTerm
from src.retrieval.semantic_config import SEMANTIC_EMBEDDING_MODEL

# Eligibility prose is useful for recall but is the noisiest field. It receives
# less retrieval influence than directly structured public trial fields. This is
# a retrieval-only weighting; it does not interpret eligibility or patient facts.
_FIELD_WEIGHTS = {
    "title": 1.0,
    "conditions": 1.25,
    "interventions": 1.0,
    "eligibility": 0.5,
}
_CANDIDATE_LIMIT = 100


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Evaluate local TREC semantic and hybrid retrieval."
    )
    parser.add_argument(
        "--partial",
        action="store_true",
        help=(
            "Evaluate the completed portion of a resumable index. This produces "
            "a non-comparable preview, not a full-corpus result."
        ),
    )
    parser.add_argument(
        "--semantic-dir",
        default="datasets/evaluation/trec/semantic",
        help="Completed local semantic index directory to evaluate.",
    )
    parser.add_argument(
        "--trial-limit",
        type=int,
        help="Bound evaluation to the first indexed trials for a fair profile preview.",
    )
    parser.add_argument(
        "--document-profile",
        help="Required label for an unfinished index whose manifest is not available.",
    )
    parser.add_argument("--output", help="Optional JSON report path.")
    parser.add_argument(
        "--structured-reranker",
        action="store_true",
        help=(
            "Apply the production structured-support ordering to hybrid ranks. "
            "This remains a read-only public TREC token adapter, not a patient "
            "or eligibility evaluation."
        ),
    )
    parser.add_argument(
        "--semantic-field-fusion",
        choices=("weighted-score", "weighted-rrf"),
        default="weighted-score",
        help="Combine field vectors by direct score or rank-only weighted RRF.",
    )
    parser.add_argument(
        "--field-weight",
        action="append",
        default=[],
        metavar="FIELD=WEIGHT",
        help="Override one public-trial field weight; repeat as needed.",
    )
    parser.add_argument("--lexical-weight", type=float, default=1.0)
    parser.add_argument("--semantic-weight", type=float, default=1.0)
    args = parser.parse_args()
    field_weights = _field_weights(args.field_weight)
    if args.lexical_weight <= 0 or args.semantic_weight <= 0:
        parser.error("lexical and semantic weights must be positive.")
    base = Path("datasets/evaluation/trec")
    semantic_dir = Path(args.semantic_dir)
    manifest_path = semantic_dir / "manifest.json"
    manifest: dict[str, object] = {}
    if manifest_path.exists():
        loaded_manifest = json.loads(manifest_path.read_text())
        if not isinstance(loaded_manifest, dict):
            raise SystemExit("The semantic index manifest must be a JSON object.")
        manifest = loaded_manifest
        completed_trials = _required_int(manifest, "trial_count")
        trial_count = _trial_count(args.trial_limit, completed_trials=completed_trials)
        identifiers_file = _required_text(manifest, "identifiers_file")
        evaluation_scope = (
            "full_corpus"
            if trial_count == completed_trials
            else "partial_index_preview"
        )
    elif args.partial:
        state = json.loads((semantic_dir / "build-state.json").read_text())
        if not isinstance(state, dict):
            raise SystemExit("The semantic index build state must be a JSON object.")
        trial_count = _trial_count(
            args.trial_limit,
            completed_trials=_required_int(state, "completed_trials"),
        )
        identifiers_file = "nct-ids.txt"
        evaluation_scope = "partial_index_preview"
    else:
        raise SystemExit(
            "The semantic index is incomplete. Finish it or pass --partial for a "
            "clearly-labelled preview."
        )
    ids = (semantic_dir / identifiers_file).read_text().splitlines()[:trial_count]
    if len(ids) != trial_count:
        raise SystemExit(
            "The index identifiers do not match the completed vector count."
        )
    available_ids = set(ids)
    vectors, semantic_representation = _semantic_vectors(
        semantic_dir, manifest=manifest, trial_count=trial_count
    )
    topics = _topics(base / "raw/topics-2022.xml")
    qrels = _qrels(base / "raw/qrels-2022.txt")
    if evaluation_scope == "partial_index_preview":
        lexical = evaluate_trec_lexical_baseline(
            topics_path=base / "raw/topics-2022.xml",
            qrels_path=base / "raw/qrels-2022.txt",
            archives=[
                base / "raw" / f"ClinicalTrials.2021-04-27.part{part}.zip"
                for part in range(1, 6)
            ],
            trial_limit=trial_count,
        )
    else:
        lexical = json.loads((base / "results/2022-token-lexical.json").read_text())
    lexical_by_topic = _ranks_by_topic(lexical.get("topics"))
    encoder = configured_embedding_encoder()
    lexical_results = []
    semantic_results = []
    hybrid_results = []
    hybrid_ids_by_topic: dict[str, list[str]] = {}
    for topic_id, text in topics:
        query = numpy.asarray(encoder.encode(text), dtype=numpy.float32)
        started = time.perf_counter()
        semantic_ids = _semantic_ids(
            vectors,
            query=query,
            ids=ids,
            field_weights=field_weights,
            fusion=args.semantic_field_fusion,
        )
        elapsed = (time.perf_counter() - started) * 1000
        semantic_results.append(
            _result(topic_id, semantic_ids, qrels[topic_id], elapsed)
        )
        lexical_ids = lexical_by_topic[topic_id]
        if evaluation_scope != "partial_index_preview":
            lexical_ids = [nct_id for nct_id in lexical_ids if nct_id in available_ids]
        lexical_results.append(_result(topic_id, lexical_ids, qrels[topic_id], 0.0))
        hybrid_ids = _fuse(
            lexical_ids,
            semantic_ids,
            lexical_weight=args.lexical_weight,
            semantic_weight=args.semantic_weight,
        )
        hybrid_ids_by_topic[topic_id] = hybrid_ids
        hybrid_results.append(_result(topic_id, hybrid_ids, qrels[topic_id], elapsed))
    structured_reranker: dict[str, object] | None = None
    if args.structured_reranker:
        trials_by_nct = _read_structured_trials(
            archives=_archives(base),
            nct_ids={
                nct_id
                for ranked_ids in hybrid_ids_by_topic.values()
                for nct_id in ranked_ids
            },
        )
        reranked_results = []
        for topic_id, text in topics:
            started = time.perf_counter()
            reranked_ids = _rerank_with_structured_support(
                hybrid_ids_by_topic[topic_id],
                topic_id=topic_id,
                topic_text=text,
                trials_by_nct=trials_by_nct,
            )
            elapsed = (time.perf_counter() - started) * 1000
            reranked_results.append(
                _result(topic_id, reranked_ids, qrels[topic_id], elapsed)
            )
        structured_reranker = {
            "metrics": _metrics(reranked_results),
            "topics": reranked_results,
            "scope": (
                "public TREC token adapter; structured trial support is used only "
                "to order already retrieved candidates"
            ),
        }
    output = {
        "evaluation": "trec-token-adapter-semantic-hybrid-comparison",
        "claimable": False,
        "scope": evaluation_scope,
        "trial_count": trial_count,
        "document_profile": args.document_profile
        or manifest.get("document_profile", "legacy-unknown"),
        "semantic_representation": semantic_representation,
        "field_weights": field_weights,
        "fusion_configuration": {
            "semantic_field_fusion": args.semantic_field_fusion,
            "lexical_weight": args.lexical_weight,
            "semantic_weight": args.semantic_weight,
        },
        "topic_count": len(topics),
        "semantic": {"metrics": _metrics(semantic_results), "topics": semantic_results},
        "hybrid": {"metrics": _metrics(hybrid_results), "topics": hybrid_results},
        "lexical": {"metrics": _metrics(lexical_results)},
    }
    if structured_reranker is not None:
        output["hybrid_structured_reranker"] = structured_reranker
    suffix = "partial" if args.partial and evaluation_scope != "full_corpus" else "full"
    if args.output:
        path = Path(args.output)
    elif semantic_dir == base / "semantic":
        path = base / f"results/2022-semantic-hybrid-{suffix}.json"
    else:
        profile = str(manifest.get("document_profile", "legacy-unknown")).replace(
            "-v1", ""
        )
        path = base / f"results/2022-semantic-hybrid-{profile}-{suffix}.json"
    path.write_text(json.dumps(output, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"status": "completed", "output": str(path)}))
    return 0


def _trial_count(trial_limit: int | None, *, completed_trials: int) -> int:
    trial_count = completed_trials if trial_limit is None else trial_limit
    if trial_count < 100:
        raise SystemExit("At least 100 indexed trials are needed for a preview.")
    if trial_count > completed_trials:
        raise SystemExit("The requested trial limit exceeds the completed index.")
    return trial_count


def _archives(base: Path) -> list[Path]:
    return [
        base / "raw" / f"ClinicalTrials.2021-04-27.part{part}.zip"
        for part in range(1, 6)
    ]


def _required_int(payload: dict[str, object], field: str) -> int:
    value = payload.get(field)
    if type(value) is not int or value < 1:
        raise SystemExit(f"The semantic index {field} must be a positive integer.")
    return value


def _required_text(payload: dict[str, object], field: str) -> str:
    value = payload.get(field)
    if not isinstance(value, str) or not value.strip():
        raise SystemExit(f"The semantic index {field} must be non-empty text.")
    return value


def _semantic_vectors(
    semantic_dir: Path, *, manifest: dict[str, object], trial_count: int
) -> tuple[dict[str, numpy.memmap], str]:
    """Open a completed public-trial index without silently changing its format.

    The retained full-corpus index predates fielded retrieval and contains one
    combined public-trial representation.  It is still useful for a clearly
    labelled full-text semantic comparison because the model and corpus match.
    Field weights and field-level fusion apply only when the manifest declares
    separately embedded fields.
    """
    files = manifest.get("embedding_files")
    if isinstance(files, dict):
        vectors: dict[str, numpy.memmap] = {}
        for field_name in _FIELD_WEIGHTS:
            entry = files.get(field_name)
            if not isinstance(entry, dict):
                raise SystemExit(f"The fielded semantic index is missing {field_name}.")
            file_name = entry.get("file")
            if not isinstance(file_name, str) or not file_name:
                raise SystemExit(
                    f"The fielded semantic index has no {field_name} file."
                )
            vectors[field_name] = numpy.memmap(
                semantic_dir / file_name,
                dtype=numpy.float32,
                mode="r",
                shape=(trial_count, SEMANTIC_EMBEDDING_MODEL.dimensions),
            )
        return vectors, "fielded-public-trial-v1"

    file_name = manifest.get("embedding_file")
    if not isinstance(file_name, str) or not file_name:
        raise SystemExit(
            "The semantic index must declare either fielded or full-text embeddings."
        )
    return {
        "full_text": numpy.memmap(
            semantic_dir / file_name,
            dtype=numpy.float32,
            mode="r",
            shape=(trial_count, SEMANTIC_EMBEDDING_MODEL.dimensions),
        )
    }, "legacy-combined-public-trial-text"


def _topics(path: Path) -> list[tuple[str, str]]:
    root = element_tree.parse(path).getroot()
    return [
        (node.attrib["number"], " ".join(node.itertext()))
        for node in root.findall(".//topic")
    ]


def _qrels(path: Path) -> dict[str, dict[str, int]]:
    rows: dict[str, dict[str, int]] = {}
    for line in path.read_text().splitlines():
        topic, _, nct_id, grade = line.split()
        rows.setdefault(topic, {})[nct_id] = int(grade)
    return rows


def _ranks_by_topic(topics: object) -> dict[str, list[str]]:
    if not isinstance(topics, list):
        raise SystemExit("The lexical benchmark output must contain ranked topics.")
    ranks: dict[str, list[str]] = {}
    for topic in topics:
        if not isinstance(topic, dict):
            raise SystemExit("The lexical benchmark contains an invalid topic.")
        topic_id = topic.get("topic_id")
        nct_ids = topic.get("ranked_nct_ids")
        if not isinstance(topic_id, str) or not isinstance(nct_ids, list):
            raise SystemExit("The lexical benchmark topic is missing ranked IDs.")
        if not all(isinstance(nct_id, str) for nct_id in nct_ids):
            raise SystemExit("The lexical benchmark contains an invalid trial ID.")
        ranks[topic_id] = nct_ids
    return ranks


def _field_weights(overrides: list[str]) -> dict[str, float]:
    weights = dict(_FIELD_WEIGHTS)
    for override in overrides:
        field_name, separator, value = override.partition("=")
        if not separator or field_name not in weights:
            raise SystemExit(
                "field weights must use title, conditions, interventions, "
                "or eligibility"
            )
        try:
            weight = float(value)
        except ValueError as error:
            raise SystemExit("field weights must be numeric") from error
        if weight <= 0:
            raise SystemExit("field weights must be positive")
        weights[field_name] = weight
    return weights


def _semantic_ids(
    vectors: dict[str, numpy.memmap],
    *,
    query: numpy.ndarray,
    ids: list[str],
    field_weights: dict[str, float],
    fusion: str,
) -> list[str]:
    full_text = vectors.get("full_text")
    if full_text is not None:
        # This is one complete public-trial document per row, not a fielded index.
        # Do not invent field weighting for a representation that does not have it.
        return _rank_ids(full_text @ query, ids)
    ranked_by_field = {
        field_name: _rank_ids(vector @ query, ids)
        for field_name, vector in vectors.items()
    }
    if fusion == "weighted-rrf":
        return _fuse_ranked_lists(
            [
                (ranked_by_field[field_name], field_weights[field_name])
                for field_name in field_weights
            ]
        )
    scores = sum(
        field_weights[field_name] * (vector @ query)
        for field_name, vector in vectors.items()
    )
    return _rank_ids(scores, ids)


def _rank_ids(scores: numpy.ndarray, ids: list[str]) -> list[str]:
    indices = numpy.argpartition(scores, -_CANDIDATE_LIMIT)[-_CANDIDATE_LIMIT:]
    return [ids[index] for index in indices[numpy.argsort(scores[indices])[::-1]]]


def _fuse_ranked_lists(ranked_lists: list[tuple[list[str], float]]) -> list[str]:
    scores: dict[str, float] = {}
    for ranked, weight in ranked_lists:
        for rank, nct_id in enumerate(ranked, 1):
            scores[nct_id] = scores.get(nct_id, 0.0) + weight / (
                RECIPROCAL_RANK_FUSION_RANK_CONSTANT + rank
            )
    ordered = sorted(scores.items(), key=lambda item: (-item[1], item[0]))
    return [nct_id for nct_id, _ in ordered[:_CANDIDATE_LIMIT]]


def _fuse(
    lexical: list[str],
    semantic: list[str],
    *,
    lexical_weight: float = 1.0,
    semantic_weight: float = 1.0,
) -> list[str]:
    scores: dict[str, float] = {}
    for ranked, weight in ((lexical, lexical_weight), (semantic, semantic_weight)):
        for rank, nct_id in enumerate(ranked, 1):
            scores[nct_id] = scores.get(nct_id, 0) + weight / (
                RECIPROCAL_RANK_FUSION_RANK_CONSTANT + rank
            )
    return [
        nct_id
        for nct_id, _ in sorted(scores.items(), key=lambda item: (-item[1], item[0]))[
            :_CANDIDATE_LIMIT
        ]
    ]


def _read_structured_trials(
    *, archives: list[Path], nct_ids: set[str]
) -> dict[str, Trial]:
    """Read only public structured fields for already bounded candidate IDs."""
    trials: dict[str, Trial] = {}
    for archive_path in archives:
        with ZipFile(archive_path) as archive:
            for member in archive.infolist():
                if not member.filename.endswith(".xml"):
                    continue
                root = element_tree.fromstring(archive.read(member))
                nct_id = root.findtext("id_info/nct_id")
                if nct_id not in nct_ids:
                    continue
                interventions = []
                for intervention in root.findall("intervention"):
                    item = {
                        "name": intervention.findtext("intervention_name"),
                        "description": intervention.findtext("description"),
                    }
                    interventions.append(
                        {key: value for key, value in item.items() if value}
                    )
                trials[nct_id] = Trial(
                    nct_id=nct_id,
                    title=root.findtext("brief_title"),
                    conditions=[
                        value
                        for condition in root.findall("condition")
                        if (value := condition.text)
                    ],
                    interventions=interventions,
                )
    missing = nct_ids - trials.keys()
    if missing:
        raise SystemExit(
            "The historical TREC corpus did not contain every candidate required "
            "for structured re-ranking."
        )
    return trials


def _rerank_with_structured_support(
    ranked_nct_ids: list[str],
    *,
    topic_id: str,
    topic_text: str,
    trials_by_nct: dict[str, Trial],
) -> list[str]:
    """Apply the production re-ranker with public benchmark-token stand-ins.

    TREC topics are not FHIR records. Their tokens are used solely to check
    ordering against a fixed public benchmark; no input is stored or treated as
    patient evidence.
    """
    query = PatientDerivedRetrievalQuery(
        terms=[
            RetrievalTerm(
                text=token,
                source_fact_id=f"trec-{topic_id}-{index}",
                kind="condition",
            )
            for index, token in enumerate(sorted(tokenize_trec_text(topic_text)), 1)
        ]
    )
    candidates = [
        (trials_by_nct[nct_id], {"reciprocal_rank_fusion_rank": rank})
        for rank, nct_id in enumerate(ranked_nct_ids, 1)
    ]
    return [
        trial.nct_id
        for trial, _ in rerank_fused_trial_candidates(
            candidates,
            query,
            candidate_limit=len(candidates),
        )
    ]


def _result(
    topic_id: str, ranked: list[str], judgments: dict[str, int], latency: float
) -> dict[str, object]:
    grades = [judgments.get(nct_id, 0) for nct_id in ranked]
    ideal = list(judgments.values())
    eligible = sum(grade == 2 for grade in ideal)
    return {
        "topic_id": topic_id,
        # NCT identifiers are public source identifiers. Retaining only the bounded
        # rank list makes later held-out tuning reproducible without retaining trial
        # text or evaluation-topic content in the application database.
        "ranked_nct_ids": ranked,
        "nDCG@5": ndcg_at_k(grades, ideal_relevances=ideal, k=5),
        "nDCG@10": ndcg_at_k(grades, ideal_relevances=ideal, k=10),
        "Precision@5": precision_at_k(grades, k=5),
        "Precision@10": precision_at_k(grades, k=10),
        "Recall@50": recall_at_k(grades, total_relevant=eligible, k=50),
        "MRR": reciprocal_rank(grades),
        "trec_grade_1_rate_top_10": trec_grade_1_rate_at_k(grades, k=10),
        "latency_ms": latency,
    }


def _metrics(results: list[dict[str, object]]) -> dict[str, float]:
    names = (
        "nDCG@5",
        "nDCG@10",
        "Precision@5",
        "Precision@10",
        "Recall@50",
        "MRR",
        "trec_grade_1_rate_top_10",
        "latency_ms",
    )
    return {
        name: mean(_numeric_metric(item, name) for item in results) for name in names
    }


def _numeric_metric(item: dict[str, object], name: str) -> float:
    value = item.get(name)
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ValueError(f"TREC result has an invalid {name} metric.")
    return float(value)


if __name__ == "__main__":
    raise SystemExit(main())
