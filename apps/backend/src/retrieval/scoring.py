"""Deterministic lexical candidate scoring with field-level score components."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from src.retrieval.schemas import PatientDerivedRetrievalQuery
from src.retrieval.trial_documents import SearchableTrial

_FIELD_WEIGHTS = {
    "conditions": 4.0,
    "title": 3.0,
    "interventions": 2.0,
    "eligibility_text": 1.0,
}


def score_trial_candidate(
    trial: SearchableTrial, query: PatientDerivedRetrievalQuery
) -> dict[str, Any] | None:
    """Score a lexical candidate and retain only explainable, deterministic counts."""
    normalized_fields = _normalized_trial_fields(trial)
    matched_fact_ids: list[str] = []
    field_matches = {field_name: 0 for field_name in _FIELD_WEIGHTS}
    lexical_score = 0.0
    for term in query.terms:
        normalized_term = _normalize(term.text)
        if not normalized_term:
            continue
        matching_fields = [
            field_name
            for field_name, field_text in normalized_fields.items()
            if normalized_term in field_text
        ]
        if not matching_fields:
            continue
        # One term contributes its strongest documented field match, avoiding a
        # score increase merely because an identical phrase appears in several fields.
        best_field = max(matching_fields, key=_FIELD_WEIGHTS.__getitem__)
        lexical_score += _FIELD_WEIGHTS[best_field]
        field_matches[best_field] += 1
        matched_fact_ids.append(term.source_fact_id)
    if not matched_fact_ids:
        return None
    return {
        "lexical_score": lexical_score,
        "matched_term_count": len(matched_fact_ids),
        "query_term_count": len(query.terms),
        "field_matches": field_matches,
        "matched_fact_ids": list(dict.fromkeys(matched_fact_ids)),
    }


def rank_scored_trials[SearchableTrialType: SearchableTrial](
    scored_trials: Iterable[tuple[SearchableTrialType, dict[str, Any]]],
) -> list[tuple[SearchableTrialType, dict[str, Any]]]:
    """Rank deterministically so reruns are reproducible for the same snapshots."""
    return sorted(
        scored_trials,
        key=lambda item: (-float(item[1]["lexical_score"]), item[0].nct_id),
    )


def _normalized_trial_fields(trial: SearchableTrial) -> dict[str, str]:
    return {
        "title": _normalize(trial.title or ""),
        "conditions": _normalize_values(trial.conditions),
        "interventions": _normalize_values(_intervention_texts(trial.interventions)),
        "eligibility_text": _normalize(trial.eligibility_text or ""),
    }


def _intervention_texts(interventions: list[dict[str, Any]]) -> list[str]:
    texts: list[str] = []
    for intervention in interventions:
        for field_name in ("name", "description"):
            value = intervention.get(field_name)
            if isinstance(value, str):
                texts.append(value)
        other_names = intervention.get("other_names")
        if isinstance(other_names, list):
            texts.extend(value for value in other_names if isinstance(value, str))
    return texts


def _normalize_values(values: Iterable[str]) -> str:
    return " ".join(_normalize(value) for value in values)


def _normalize(value: str) -> str:
    return " ".join(value.casefold().split())
