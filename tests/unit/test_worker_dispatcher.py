"""Unit checks for the local durable-job dispatcher configuration."""

import pytest

from app.workers.dispatcher import DEFAULT_POLL_SECONDS, worker_poll_seconds


def test_worker_poll_seconds_uses_a_short_default() -> None:
    assert worker_poll_seconds({}) == DEFAULT_POLL_SECONDS


def test_worker_poll_seconds_accepts_a_bounded_override() -> None:
    assert worker_poll_seconds({"WORKER_POLL_SECONDS": "0.25"}) == 0.25


@pytest.mark.parametrize("value", ["", "not-a-number", "0", "-1", "61"])
def test_worker_poll_seconds_rejects_invalid_values(value: str) -> None:
    with pytest.raises(ValueError):
        worker_poll_seconds({"WORKER_POLL_SECONDS": value})
