"""Unit checks for the public TREC adapter that invokes live retrieval code."""

from scripts.evaluate_trec_application_path import _query, _result


def test_trec_application_path_query_is_transient_and_token_based() -> None:
    query = _query("42", "Melanoma patient treatment")

    assert [term.text for term in query.terms] == ["melanoma", "treatment"]
    assert all(term.source_fact_id.startswith("trec-42-") for term in query.terms)


def test_trec_application_path_result_has_retrieval_metrics_only() -> None:
    result = _result("42", ["NCT00000001"], {"NCT00000001": 2})

    assert result["topic_id"] == "42"
    assert result["Precision@10"] == 0.1
    assert "outcome" not in result
