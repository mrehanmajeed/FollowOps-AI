"""Idempotency of external side effects (evaluation cases TC13 and TC14)."""

from uuid import uuid4

import pytest

from app.services.execution import EMAIL_SEND, ExecutionService, idempotency_key


@pytest.fixture
def workflow_id():
    return uuid4()


async def ok(value="sent"):
    return {"value": value}, "provider-1"


async def boom():
    raise RuntimeError("SMTP unavailable")


async def test_first_attempt_claims_and_performs(repository, workflow_id):
    service = ExecutionService(repository)
    calls = []

    async def perform():
        calls.append(1)
        return await ok()

    outcome = await service.run(workflow_id, EMAIL_SEND, perform)
    assert outcome.succeeded and not outcome.skipped
    assert len(calls) == 1
    assert repository.rows("workflow_operations")[0]["status"] == "succeeded"


async def test_second_attempt_does_not_repeat_a_succeeded_effect(
    repository, workflow_id
):
    """TC13: a duplicate execution must not send a second email."""
    service = ExecutionService(repository)
    calls = []

    async def perform():
        calls.append(1)
        return await ok()

    await service.run(workflow_id, EMAIL_SEND, perform)
    outcome = await service.run(workflow_id, EMAIL_SEND, perform)

    assert len(calls) == 1, "side effect performed twice"
    assert outcome.succeeded and outcome.skipped
    assert outcome.provider_id == "provider-1"
    assert len(repository.rows("workflow_operations")) == 1


async def test_failed_operation_is_retryable(repository, workflow_id):
    """TC14: a provider failure is recorded as failed, not silently completed."""
    service = ExecutionService(repository)

    failed = await service.run(workflow_id, EMAIL_SEND, boom)
    assert not failed.succeeded
    assert failed.status == "failed"
    assert "SMTP unavailable" in failed.error

    retried = await service.run(workflow_id, EMAIL_SEND, ok)
    assert retried.succeeded
    record = repository.rows("workflow_operations")[0]
    assert record["attempts"] == 2
    assert record["status"] == "succeeded"


async def test_interrupted_operation_is_reported_in_doubt_not_resent(
    repository, workflow_id
):
    """A crash between "handed to Zoho" and "recorded" must not auto-resend."""
    service = ExecutionService(repository)
    repository.seed(
        "workflow_operations",
        {
            "workflow_run_id": str(workflow_id),
            "operation_type": EMAIL_SEND,
            "idempotency_key": idempotency_key(workflow_id, EMAIL_SEND),
            "status": "pending",
            "attempts": 1,
        },
    )
    calls = []

    async def perform():
        calls.append(1)
        return await ok()

    outcome = await service.run(workflow_id, EMAIL_SEND, perform)
    assert outcome.status == "in_doubt"
    assert calls == [], "in-doubt operation must not be retried automatically"
    assert "interrupted" in outcome.error


async def test_force_overrides_an_in_doubt_operation(repository, workflow_id):
    service = ExecutionService(repository)
    repository.seed(
        "workflow_operations",
        {
            "workflow_run_id": str(workflow_id),
            "operation_type": EMAIL_SEND,
            "idempotency_key": idempotency_key(workflow_id, EMAIL_SEND),
            "status": "pending",
            "attempts": 1,
        },
    )
    outcome = await service.run(workflow_id, EMAIL_SEND, ok, force=True)
    assert outcome.succeeded


async def test_idempotency_key_is_stable_per_workflow_and_operation(workflow_id):
    assert idempotency_key(workflow_id, EMAIL_SEND) == idempotency_key(
        workflow_id, EMAIL_SEND
    )
    assert idempotency_key(workflow_id, EMAIL_SEND) != idempotency_key(
        uuid4(), EMAIL_SEND
    )
