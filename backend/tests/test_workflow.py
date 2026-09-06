"""Workflow safety properties: approval gate, reply pause, partial failure.

Covers evaluation cases TC11 (reply stops follow-ups), TC14 (SMTP failure) and
TC15 (approval bypass).
"""

from datetime import date
from uuid import uuid4

import pytest

from app.core.exceptions import (
    NotFoundError,
    ValidationError,
    WorkflowExecutionError,
)
from app.services.crm import CRMService
from app.services.execution import ExecutionService
from app.services.tasks import TaskService
from app.services.workflow import FollowupsPausedError, WorkflowService
from app.services.zoho import EmailSendError, SendResult
from tests.conftest import make_extraction

NOTES = (
    "Sarah confirmed that we will prepare a commercial proposal. "
    "Sarah will review the proposal next week."
)


class FakeEmailService:
    def __init__(self, fail_with: Exception | None = None):
        self.fail_with = fail_with
        self.sent: list[dict] = []

    def build_message_id(self) -> str:
        return f"msg-{len(self.sent) + 1}@followops.test"

    async def send(self, recipient, subject, body, message_id=None, **_):
        if self.fail_with:
            raise self.fail_with
        self.sent.append({"to": recipient, "subject": subject, "id": message_id})
        return SendResult(message_id=message_id or "generated", accepted=True)


class FakeAIService:
    def __init__(self, extraction=None, fail_with: Exception | None = None):
        self.extraction = extraction or make_extraction()
        self.fail_with = fail_with

    async def extract_followup(self, account_context, meeting_notes, meeting_date):
        if self.fail_with:
            raise self.fail_with
        return self.extraction


def build_service(repository, settings, email_service=None, ai_service=None):
    return WorkflowService(
        repository=repository,
        ai_service=ai_service or FakeAIService(),
        email_service=email_service or FakeEmailService(),
        task_service=TaskService(repository),
        crm_service=CRMService(repository, settings),
        settings=settings,
        execution_service=ExecutionService(repository),
    )


def seed_workflow(repository, status="awaiting_approval", paused=False, blocked=False):
    account = repository.seed(
        "accounts",
        {
            "company_name": "Acme",
            "contact_email": "sarah@acme.test",
            "account_context": "Existing customer",
            "followups_paused": paused,
        },
    )
    meeting = repository.seed(
        "meetings",
        {
            "account_id": account["id"],
            "notes": NOTES,
            "meeting_date": "2026-09-05",
        },
    )
    workflow = repository.seed(
        "workflow_runs",
        {
            "meeting_id": meeting["id"],
            "status": status,
            "model": "gemini-2.5-flash",
        },
    )
    extraction = make_extraction()
    repository.seed(
        "followup_packages",
        {
            "workflow_run_id": workflow["id"],
            "summary": extraction.meeting_summary,
            "structured_output": extraction.model_dump(mode="json"),
            "email_subject": extraction.email.subject,
            "email_body": extraction.email.body,
            "approved": False,
            "edited": False,
            "validation_issues": [],
            "blocked": blocked,
        },
    )
    return account, meeting, workflow


# -- generation -------------------------------------------------------------


async def test_create_workflow_reaches_awaiting_approval(repository, settings):
    account = repository.seed(
        "accounts", {"company_name": "Acme", "contact_email": "sarah@acme.test"}
    )
    service = build_service(repository, settings)

    result = await service.create_workflow(
        account_id=account["id"], meeting_date=date(2026, 9, 5), notes=NOTES
    )

    assert result["status"] == "awaiting_approval"
    assert result["blocked"] is False
    assert len(repository.rows("followup_packages")) == 1


async def test_generation_failure_marks_workflow_failed_and_sends_nothing(
    repository, settings
):
    account = repository.seed(
        "accounts", {"company_name": "Acme", "contact_email": "sarah@acme.test"}
    )
    email = FakeEmailService()
    service = build_service(
        repository,
        settings,
        email_service=email,
        ai_service=FakeAIService(fail_with=RuntimeError("Gemini down")),
    )

    with pytest.raises(WorkflowExecutionError):
        await service.create_workflow(
            account_id=account["id"], meeting_date=date(2026, 9, 5), notes=NOTES
        )

    assert repository.rows("workflow_runs")[0]["status"] == "failed"
    assert email.sent == []


async def test_ungrounded_output_blocks_the_package(repository, settings):
    account = repository.seed(
        "accounts", {"company_name": "Acme", "contact_email": "sarah@acme.test"}
    )
    service = build_service(
        repository,
        settings,
        ai_service=FakeAIService(
            make_extraction(evidence="we agreed to a 30% discount")
        ),
    )

    result = await service.create_workflow(
        account_id=account["id"], meeting_date=date(2026, 9, 5), notes=NOTES
    )

    assert result["blocked"] is True
    assert repository.rows("followup_packages")[0]["blocked"] is True


# -- TC15: approval gate ----------------------------------------------------


async def test_execution_without_approval_is_refused(repository, settings):
    _, _, workflow = seed_workflow(repository, status="awaiting_approval")
    email = FakeEmailService()
    service = build_service(repository, settings, email_service=email)

    with pytest.raises(ValidationError):
        await service.execute(workflow["id"])

    assert email.sent == [], "email sent without approval"
    assert repository.rows("workflow_operations") == []


async def test_rejection_stops_the_workflow_without_side_effects(repository, settings):
    _, _, workflow = seed_workflow(repository)
    email = FakeEmailService()
    service = build_service(repository, settings, email_service=email)

    result = await service.approve_and_execute(workflow["id"], approved=False)

    assert result["status"] == "rejected"
    assert email.sent == []
    assert repository.rows("workflow_runs")[0]["status"] == "rejected"


async def test_blocked_package_cannot_be_approved(repository, settings):
    _, _, workflow = seed_workflow(repository, blocked=True)
    email = FakeEmailService()
    service = build_service(repository, settings, email_service=email)

    with pytest.raises(ValidationError):
        await service.approve_and_execute(workflow["id"], approved=True)

    assert email.sent == []


async def test_a_completed_workflow_cannot_be_approved_again(repository, settings):
    _, _, workflow = seed_workflow(repository)
    email = FakeEmailService()
    service = build_service(repository, settings, email_service=email)

    await service.approve_and_execute(workflow["id"], approved=True)
    with pytest.raises(ValidationError):
        await service.approve_and_execute(workflow["id"], approved=True)

    assert len(email.sent) == 1


# -- happy path -------------------------------------------------------------


async def test_approved_workflow_executes_and_completes(repository, settings):
    _, _, workflow = seed_workflow(repository)
    email = FakeEmailService()
    service = build_service(repository, settings, email_service=email)

    result = await service.approve_and_execute(workflow["id"], approved=True)

    assert result["status"] == "completed"
    assert result["email_sent"] is True
    assert len(email.sent) == 1
    assert result["crm_mode"] == "simulated"
    assert repository.rows("workflow_runs")[0]["status"] == "completed"

    # The outbound email is recorded so a later reply can be correlated to it.
    outbound = repository.rows("email_messages")
    assert len(outbound) == 1
    assert outbound[0]["message_id"] == result["email_message_id"]

    events = [e["event_type"] for e in repository.rows("audit_events")]
    assert "workflow_approved" in events
    assert "workflow_completed" in events


async def test_state_machine_passes_through_approved(repository, settings):
    """The DB trigger rejects awaiting_approval -> executing; so must we."""
    _, _, workflow = seed_workflow(repository)
    service = build_service(repository, settings)
    await service.approve_and_execute(workflow["id"], approved=True)
    # FakeRepository raises InvalidTransition on an illegal move, so reaching
    # completed proves the approved step happened.
    assert repository.rows("workflow_runs")[0]["status"] == "completed"


# -- TC14: partial failure and retry ----------------------------------------

async def test_smtp_failure_fails_the_workflow_without_claiming_success(
    repository, settings
):
    _, _, workflow = seed_workflow(repository)
    email = FakeEmailService(fail_with=EmailSendError("Authentication failed", True))
    service = build_service(repository, settings, email_service=email)

    result = await service.approve_and_execute(workflow["id"], approved=True)

    assert result["status"] == "failed"
    assert result["email_sent"] is False
    assert repository.rows("workflow_runs")[0]["status"] == "failed"
    assert repository.rows("followup_packages")[0]["approved"] is False


async def test_retry_after_partial_failure_does_not_resend_the_email(
    repository, settings
):
    _, _, workflow = seed_workflow(repository)
    email = FakeEmailService()
    service = build_service(repository, settings, email_service=email)

    # Email succeeds, then task creation blows up.
    broken = TaskService(repository)

    async def explode(*args, **kwargs):
        raise RuntimeError("database unavailable")

    broken.create_internal_tasks = explode
    service.task_service = broken

    first = await service.approve_and_execute(workflow["id"], approved=True)
    assert first["status"] == "failed"
    assert len(email.sent) == 1

    # Repair and retry: the email must not go out a second time.
    service.task_service = TaskService(repository)
    second = await service.execute(workflow["id"])

    assert second["status"] == "completed"
    assert len(email.sent) == 1, "retry resent the customer email"
    email_ops = [
        o
        for o in repository.rows("workflow_operations")
        if o["operation_type"] == "email_send"
    ]
    assert len(email_ops) == 1 and email_ops[0]["status"] == "succeeded"


# -- TC11: reply pauses follow-ups ------------------------------------------


async def test_paused_account_refuses_execution(repository, settings):
    _, _, workflow = seed_workflow(repository, status="approved", paused=True)
    email = FakeEmailService()
    service = build_service(repository, settings, email_service=email)

    with pytest.raises(FollowupsPausedError):
        await service.execute(workflow["id"])

    assert email.sent == []


async def test_reply_landing_mid_execution_stops_the_send(repository, settings):
    """The pause is re-read immediately before the SMTP call."""
    account, _, workflow = seed_workflow(repository, status="approved")
    email = FakeEmailService()
    service = build_service(repository, settings, email_service=email)

    real_execution_run = service.execution.run

    async def run_with_reply_arriving(
        workflow_id, operation_type, perform, force=False
    ):
        if operation_type == "email_send":
            # Simulate the reply processor committing the pause after the
            # pre-flight check but before the send.
            for row in repository.rows("accounts"):
                if row["id"] == account["id"]:
                    row["followups_paused"] = True
        return await real_execution_run(workflow_id, operation_type, perform, force)

    service.execution.run = run_with_reply_arriving
    result = await service.execute(workflow["id"])

    assert result["email_sent"] is False
    assert email.sent == []
    assert result["status"] == "failed"


async def test_forced_execution_overrides_the_pause(repository, settings):
    _, _, workflow = seed_workflow(repository, status="approved", paused=True)
    email = FakeEmailService()
    service = build_service(repository, settings, email_service=email)

    result = await service.execute(workflow["id"], force=True)

    assert result["status"] == "completed"
    assert len(email.sent) == 1


async def test_missing_recipient_blocks_execution(repository, settings):
    account, _, workflow = seed_workflow(repository, status="approved")
    for row in repository.rows("accounts"):
        if row["id"] == account["id"]:
            row["contact_email"] = None
    email = FakeEmailService()
    service = build_service(repository, settings, email_service=email)

    with pytest.raises(ValidationError):
        await service.execute(workflow["id"])
    assert email.sent == []


async def test_unknown_workflow_is_not_found(repository, settings):
    service = build_service(repository, settings)
    with pytest.raises(NotFoundError):
        await service.execute(uuid4())
