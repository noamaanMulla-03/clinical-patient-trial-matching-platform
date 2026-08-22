"""Audit-event primitives for state-changing application operations."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal, Protocol
from uuid import UUID, uuid4

AuditOutcome = Literal["success", "failure"]


class AuditSink(Protocol):
    """Port implemented by the eventual database-backed audit repository."""

    def append(self, event: AuditEvent) -> None:
        """Persist one immutable audit event."""


@dataclass(frozen=True, slots=True)
class AuditEvent:
    """A traceable record of one attempted state-changing operation."""

    id: UUID
    occurred_at: datetime
    request_id: str
    actor_id: str
    action: str
    target_type: str
    target_id: str
    outcome: AuditOutcome
    metadata: Mapping[str, Any] = field(default_factory=dict)


class InMemoryAuditSink:
    """Development and unit-test sink; replace with a database sink before use."""

    def __init__(self) -> None:
        self.events: list[AuditEvent] = []

    def append(self, event: AuditEvent) -> None:
        self.events.append(event)


def _require_nonblank(value: str, field_name: str) -> str:
    if not value.strip():
        raise ValueError(f"{field_name} must not be blank.")
    return value


def create_audit_event(
    *,
    request_id: str,
    actor_id: str,
    action: str,
    target_type: str,
    target_id: str,
    outcome: AuditOutcome,
    metadata: Mapping[str, Any] | None = None,
    occurred_at: datetime | None = None,
) -> AuditEvent:
    """Create an immutable audit event without clinical content or raw errors."""
    timestamp = occurred_at or datetime.now(UTC)
    if timestamp.tzinfo is None:
        raise ValueError("occurred_at must be timezone-aware.")

    return AuditEvent(
        id=uuid4(),
        occurred_at=timestamp,
        request_id=_require_nonblank(request_id, "request_id"),
        actor_id=_require_nonblank(actor_id, "actor_id"),
        action=_require_nonblank(action, "action"),
        target_type=_require_nonblank(target_type, "target_type"),
        target_id=_require_nonblank(target_id, "target_id"),
        outcome=outcome,
        metadata=dict(metadata or {}),
    )


@contextmanager
def audited_write(
    sink: AuditSink,
    *,
    request_id: str,
    actor_id: str,
    action: str,
    target_type: str,
    target_id: str,
    metadata: Mapping[str, Any] | None = None,
) -> Iterator[None]:
    """Wrap one write and persist a success or failure audit event.

    Callers must use a static action name (for example, ``patient.imported``)
    and operational metadata only. Clinical values and exception messages must
    never be included in ``metadata``.
    """
    try:
        yield
    except Exception as error:
        failure_metadata = dict(metadata or {})
        failure_metadata["error_type"] = type(error).__name__
        sink.append(
            create_audit_event(
                request_id=request_id,
                actor_id=actor_id,
                action=action,
                target_type=target_type,
                target_id=target_id,
                outcome="failure",
                metadata=failure_metadata,
            )
        )
        raise
    else:
        sink.append(
            create_audit_event(
                request_id=request_id,
                actor_id=actor_id,
                action=action,
                target_type=target_type,
                target_id=target_id,
                outcome="success",
                metadata=metadata,
            )
        )
