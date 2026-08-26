"""Unit tests for clinical logging redaction."""

import logging

from src.observability.redaction import (
    REDACTED_FHIR_CONTENT,
    REDACTED_LOG_ARGUMENTS,
    REDACTED_VALUE,
    ClinicalContentRedactionFilter,
    redact_log_fields,
    redact_text,
)


def test_redacts_sensitive_structured_fields_recursively() -> None:
    fields = redact_log_fields(
        {
            "request_id": "request-123",
            "patient_bundle": {"resourceType": "Bundle", "entry": []},
            "metadata": {
                "nct_id": "NCT00000000",
                "lab_result": "11.2 g/dL",
            },
        }
    )

    assert fields["request_id"] == "request-123"
    assert fields["patient_bundle"] == REDACTED_VALUE
    assert fields["metadata"]["nct_id"] == "NCT00000000"
    assert fields["metadata"]["lab_result"] == REDACTED_VALUE


def test_redacts_identifiers_and_serialized_fhir_from_text() -> None:
    assert redact_text("Contact alice@example.com at 555-123-4567") == (
        "Contact [REDACTED] at [REDACTED]"
    )
    assert redact_text('{"resourceType": "Patient", "name": [{"text": "Alice"}]}') == (
        REDACTED_FHIR_CONTENT
    )


def test_filter_discards_dynamic_log_arguments() -> None:
    record = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="Imported patient %s",
        args=("Alice Example",),
        exc_info=None,
    )

    ClinicalContentRedactionFilter().filter(record)

    assert record.getMessage() == f"Imported patient %s {REDACTED_LOG_ARGUMENTS}"


def test_filter_preserves_uvicorn_access_log_argument_shape_without_content() -> None:
    record = logging.LogRecord(
        name="uvicorn.access",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg='%s - "%s %s HTTP/%s" %d',
        args=("127.0.0.1", "GET", "/patients/patient-1", "1.1", 200),
        exc_info=None,
    )

    ClinicalContentRedactionFilter().filter(record)

    assert record.args == (REDACTED_VALUE, "REQUEST", REDACTED_VALUE, "1.1", 0)
    assert "patient-1" not in record.getMessage()


def test_filter_redacts_sensitive_extra_attributes() -> None:
    record = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="FHIR import rejected",
        args=(),
        exc_info=None,
    )
    record.patient_facts = {"diagnosis": "example condition"}
    record.request_id = "request-123"

    ClinicalContentRedactionFilter().filter(record)

    assert record.patient_facts == REDACTED_VALUE
    assert record.request_id == "request-123"
