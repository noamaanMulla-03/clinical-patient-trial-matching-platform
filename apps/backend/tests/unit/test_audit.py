"""Unit tests for audit-event helpers."""

from datetime import UTC, datetime

import pytest

from app.audit import InMemoryAuditSink, audited_write, create_audit_event


def test_audited_write_records_success_after_the_write_completes() -> None:
    sink = InMemoryAuditSink()

    with audited_write(
        sink,
        request_id="request-123",
        actor_id="reviewer-123",
        action="patient.imported",
        target_type="patient_import",
        target_id="import-123",
        metadata={"source": "synthetic_fixture"},
    ):
        assert sink.events == []

    assert len(sink.events) == 1
    event = sink.events[0]
    assert event.outcome == "success"
    assert event.action == "patient.imported"
    assert event.metadata == {"source": "synthetic_fixture"}


def test_audited_write_records_failure_without_the_error_message() -> None:
    sink = InMemoryAuditSink()

    with (
        pytest.raises(RuntimeError, match="clinical details must not be audited"),
        audited_write(
            sink,
            request_id="request-123",
            actor_id="reviewer-123",
            action="patient.imported",
            target_type="patient_import",
            target_id="import-123",
        ),
    ):
        raise RuntimeError("clinical details must not be audited")

    assert len(sink.events) == 1
    event = sink.events[0]
    assert event.outcome == "failure"
    assert event.metadata == {"error_type": "RuntimeError"}
    assert "clinical details" not in str(event.metadata)


def test_audit_event_requires_operational_identifiers() -> None:
    with pytest.raises(ValueError, match="request_id must not be blank"):
        create_audit_event(
            request_id=" ",
            actor_id="system",
            action="trial.synced",
            target_type="trial_sync",
            target_id="sync-123",
            outcome="success",
        )


def test_audit_event_rejects_naive_timestamps() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        create_audit_event(
            request_id="request-123",
            actor_id="system",
            action="trial.synced",
            target_type="trial_sync",
            target_id="sync-123",
            outcome="success",
            occurred_at=datetime(2026, 8, 22),
        )


def test_audit_event_preserves_utc_timestamps() -> None:
    timestamp = datetime(2026, 8, 22, tzinfo=UTC)

    event = create_audit_event(
        request_id="request-123",
        actor_id="system",
        action="trial.synced",
        target_type="trial_sync",
        target_id="sync-123",
        outcome="success",
        occurred_at=timestamp,
    )

    assert event.occurred_at == timestamp
