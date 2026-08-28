"""Conservative structured-evidence ordering after candidate retrieval.

This module only changes the order of already retrieved, metadata-compatible
candidates.  It is deliberately not an eligibility evaluator: absent or
unmatched structured fields remain unknown and receive no penalty.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Any

from src.db.models import Trial
from src.retrieval.schemas import PatientDerivedRetrievalQuery

STRUCTURED_EVIDENCE_RERANKER_VERSION = "structured-evidence-reranker-v2"

_FIELDS_BY_TERM_KIND = {
    "condition": ("conditions", "title"),
    "medication": ("interventions", "title"),
    "procedure": ("interventions", "title"),
}


def rerank_fused_trial_candidates(
    ranked_trials: Sequence[tuple[Trial, dict[str, Any]]],
    query: PatientDerivedRetrievalQuery,
    *,
    candidate_limit: int,
) -> list[tuple[Trial, dict[str, Any]]]:
    """Prefer direct structured support while retaining unknown candidates.

    Full-text semantic retrieval supplies recall. This second stage gives a
    candidate a higher tier only when the corresponding kind of usable patient
    retrieval term appears directly in a structured trial field: conditions
    support condition facts; interventions support medication or procedure facts.
    A trial without that support is still retained and is ordered by its fused
    retrieval rank.  Documented conflicts must have been filtered before this
    function is called.
    """
    if candidate_limit < 1:
        raise ValueError("candidate_limit must be positive.")

    reranked: list[tuple[Trial, dict[str, Any]]] = []
    for original_rank, (trial, scores) in enumerate(ranked_trials, 1):
        rationale = _structured_support_rationale(trial, query)
        reranked.append(
            (
                trial,
                {
                    **scores,
                    "structured_evidence_reranker_version": (
                        STRUCTURED_EVIDENCE_RERANKER_VERSION
                    ),
                    "structured_evidence_reranker_input_rank": original_rank,
                    **rationale,
                },
            )
        )

    reranked.sort(
        key=lambda item: (
            -int(item[1]["structured_evidence_support_tier"]),
            int(item[1]["structured_evidence_reranker_input_rank"]),
            item[0].nct_id,
        )
    )
    return [
        (
            trial,
            {
                **scores,
                "structured_evidence_reranker_rank": rank,
            },
        )
        for rank, (trial, scores) in enumerate(reranked[:candidate_limit], 1)
    ]


def _structured_support_rationale(
    trial: Trial, query: PatientDerivedRetrievalQuery
) -> dict[str, Any]:
    fields = {
        "conditions": _normalized_values(trial.conditions),
        "title": _normalize(trial.title or ""),
        "interventions": _normalized_values(_intervention_texts(trial)),
    }
    support_by_field: dict[str, list[str]] = {field_name: [] for field_name in fields}
    supporting_fact_ids: list[str] = []
    for term in query.terms:
        normalized_term = _normalize(term.text)
        if not normalized_term:
            continue
        for field_name in _FIELDS_BY_TERM_KIND[term.kind]:
            field_text = fields[field_name]
            if _contains_complete_term(field_text, normalized_term):
                support_by_field[field_name].append(term.source_fact_id)
                supporting_fact_ids.append(term.source_fact_id)

    supported_fields = [
        field_name for field_name, fact_ids in support_by_field.items() if fact_ids
    ]
    # Conditions and interventions have explicit meaning in ClinicalTrials.gov.
    # A title is only weaker corroborating source text, never a clinical conclusion.
    tier = (
        3
        if "conditions" in supported_fields
        else 2
        if "interventions" in supported_fields
        else 1
        if "title" in supported_fields
        else 0
    )
    return {
        "structured_evidence_support_tier": tier,
        "structured_evidence_supported_fields": supported_fields,
        "structured_evidence_supporting_fact_ids": list(
            dict.fromkeys(supporting_fact_ids)
        ),
        "structured_evidence_status": "direct_support" if tier else "unknown",
        "structured_evidence_note": (
            "Direct patient-fact text appears in structured public trial fields."
            if tier
            else (
                "No direct structured support was found; retained for review "
                "without a penalty."
            )
        ),
    }


def _intervention_texts(trial: Trial) -> list[str]:
    values: list[str] = []
    for intervention in trial.interventions:
        if not isinstance(intervention, dict):
            continue
        for key in ("name", "description"):
            value = intervention.get(key)
            if isinstance(value, str):
                values.append(value)
        other_names = intervention.get("other_names")
        if isinstance(other_names, list):
            values.extend(value for value in other_names if isinstance(value, str))
    return values


def _normalized_values(values: Sequence[str]) -> str:
    return " ".join(_normalize(value) for value in values if isinstance(value, str))


def _normalize(value: str) -> str:
    return " ".join(value.casefold().split())


def _contains_complete_term(field_text: str, term: str) -> bool:
    """Require whole normalized words for a structured-support promotion."""
    field_words = tuple(re.findall(r"\w+", field_text))
    term_words = tuple(re.findall(r"\w+", term))
    if not term_words or len(term_words) > len(field_words):
        return False
    width = len(term_words)
    return any(
        field_words[index : index + width] == term_words
        for index in range(len(field_words) - width + 1)
    )
