"""Workflow orchestration.

    CREATED -> PROCESSING -> AWAITING_APPROVAL -> APPROVED -> EXECUTING -> COMPLETED

The database enforces this transition table; this service must therefore move
through `approved` rather than jumping straight from `awaiting_approval` to
`executing`. There is no path that reaches an external side effect without
passing the approval gate.

Execution is not atomic and does not pretend to be. Each side effect is claimed
through `ExecutionService`, so a retry after a partial failure re-attempts only
what has not already succeeded.
"""

import logging
import time
from datetime import UTC, date, datetime
from typing import Any
from uuid import UUID, uuid4

from app.core.config import Settings
from app.core.exceptions import (
    NotFoundError,
    ValidationError,
    WorkflowExecutionError,
)
from app.repositories.supabase import SupabaseRepository
from app.schemas.followup import FollowupExtraction
from app.services.ai_service import GeminiAIService
from app.services.crm import CRMService
from app.services.execution import (
    CRM_UPDATE,
    EMAIL_SEND,
    TASK_CREATE,
    ExecutionService,
)
from app.services.reply_processor import record_outbound
from app.services.tasks import TaskService
from app.services.validator import FollowupValidator
from app.services.zoho import ZohoEmailService

logger = logging.getLogger(__name__)

EXECUTABLE_STATES = {"approved", "failed"}


class FollowupsPausedError(ValidationError):
    """The prospect already replied; sending again needs an explicit resume."""


class WorkflowService:
    def __init__(
        self,
        repository: SupabaseRepository,
        ai_service: GeminiAIService,
        email_service: ZohoEmailService,
        task_service: TaskService,
        crm_service: CRMService,
        settings: Settings,
        execution_service: ExecutionService | None = None,
        validator: FollowupValidator | None = None,
    ):
        self.repository = repository
        self.ai_service = ai_service
        self.email_service = email_service
        self.task_service = task_service
        self.crm_service = crm_service
        self.settings = settings
        self.execution = execution_service or ExecutionService(repository)
        self.validator = validator or FollowupValidator()

    # -- generation --------------------------------------------------------

    async def create_workflow(
        self,
        account_id: UUID,
        meeting_date: date,
        notes: str,
    ) -> dict[str, Any]:
        account = await self.repository.get_by_id("accounts", account_id)
        if not account:
            raise NotFoundError("Account not found")

        meeting = await self.repository.insert(
            "meetings",
            {
                "account_id": str(account_id),
                "notes": notes,
                "meeting_date": meeting_date.isoformat(),
            },
        )

        workflow_id = uuid4()
        await self.repository.insert(
            "workflow_runs",
            {
                "id": str(workflow_id),
                "meeting_id": meeting["id"],
                "status": "processing",
                "model": self.settings.gemini_model,
                "started_at": datetime.now(UTC).isoformat(),
            },
        )

        started = time.perf_counter()
        try:
            extraction = await self.ai_service.extract_followup(
                account_context=account.get("account_context") or "",
                meeting_notes=notes,
                meeting_date=meeting_date,
            )
            clean, report = self.validator.validate(extraction, notes, meeting_date)
            latency_ms = int((time.perf_counter() - started) * 1000)

            package = await self.repository.insert(
                "followup_packages",
                {
                    "workflow_run_id": str(workflow_id),
                    "summary": clean.meeting_summary,
                    "structured_output": clean.model_dump(mode="json"),
                    "email_subject": clean.email.subject,
                    "email_body": clean.email.body,
                    "approved": False,
                    "edited": False,
                    "validation_issues": [i.model_dump() for i in report.issues],
                    # A critical issue means the model asserted something the
                    # notes do not support. The package is still shown to the
                    # operator, but it cannot be approved until it is edited.
                    "blocked": not report.ok,
                },
            )

            workflow = await self.repository.update(
                "workflow_runs",
                workflow_id,
                {
                    "status": "awaiting_approval",
                    "latency_ms": latency_ms,
                    "overall_confidence": clean.overall_confidence,
                },
            )

            await self._audit(
                workflow_id,
                "workflow_ready",
                "Follow-up package generated and validated",
                {
                    "latency_ms": latency_ms,
                    "issues": len(report.issues),
                    "critical_issues": len(report.critical),
                    "blocked": not report.ok,
                },
            )

            return {
                **workflow,
                "meeting_id": meeting["id"],
                "package_id": package["id"],
                "blocked": not report.ok,
                "validation_issues": [i.model_dump() for i in report.issues],
            }

        except Exception as exc:
            await self._mark_failed(workflow_id, _safe(exc))
            logger.exception("Workflow %s generation failed", workflow_id)
            raise WorkflowExecutionError(_safe(exc)) from exc

    # -- approval ----------------------------------------------------------

    async def approve_and_execute(
        self,
        workflow_id: UUID,
        approved: bool,
        approver: str = "operator",
    ) -> dict[str, Any]:
        workflow = await self._require_workflow(workflow_id)

        if workflow["status"] != "awaiting_approval":
            raise ValidationError(
                f"Workflow cannot be approved from status '{workflow['status']}'"
            )

        if not approved:
            await self.repository.update(
                "workflow_runs", workflow_id, {"status": "rejected"}
            )
            await self._audit(
                workflow_id,
                "workflow_rejected",
                "Human approval rejected the follow-up package",
                {"approver": approver},
            )
            return {
                "workflow_run_id": workflow_id,
                "status": "rejected",
                "email_sent": False,
                "tasks_created": 0,
                "crm_updated": False,
                "operations": [],
            }

        package = await self._require_package(workflow_id)
        if package.get("blocked"):
            raise ValidationError(
                "Package has unresolved critical validation issues and cannot be "
                "approved. Edit the package to remove unsupported content first."
            )

        await self.repository.update(
            "workflow_runs", workflow_id, {"status": "approved"}
        )
        await self._audit(
            workflow_id,
            "workflow_approved",
            "Human approved external execution",
            {"approver": approver},
        )
        return await self.execute(workflow_id)

    # -- execution ---------------------------------------------------------

    async def execute(
        self, workflow_id: UUID, force: bool = False
    ) -> dict[str, Any]:
        """Run the approved side effects. Safe to call again after a failure."""
        workflow = await self._require_workflow(workflow_id)

        if workflow["status"] not in EXECUTABLE_STATES:
            # This is the approval gate. Nothing below it can run otherwise.
            raise ValidationError(
                f"Workflow in status '{workflow['status']}' is not approved for "
                f"execution"
            )

        package = await self._require_package(workflow_id)
        meeting = await self.repository.get_by_id(
            "meetings", UUID(str(workflow["meeting_id"]))
        )
        if not meeting:
            raise NotFoundError("Meeting not found")

        account = await self.repository.get_by_id(
            "accounts", UUID(str(meeting["account_id"]))
        )
        if not account:
            raise NotFoundError("Account not found")

        recipient = account.get("contact_email")
        if not recipient:
            raise ValidationError("Account does not have a contact email")

        if account.get("followups_paused") and not force:
            raise FollowupsPausedError(
                "Follow-ups for this account are paused because a prospect reply "
                "was detected. Resume the account explicitly to send anyway."
            )

        account_id = UUID(str(account["id"]))
        extraction = FollowupExtraction.model_validate(package["structured_output"])

        await self.repository.update(
            "workflow_runs", workflow_id, {"status": "executing"}
        )

        outcomes = []

        # --- email ---------------------------------------------------------
        async def send_email() -> tuple[dict[str, Any], str | None]:
            # Re-read immediately before the send: a reply may have landed
            # while this execution was starting.
            fresh = await self.repository.get_by_id("accounts", account_id)
            if fresh and fresh.get("followups_paused") and not force:
                raise FollowupsPausedError(
                    "A prospect reply was detected for this account; the email "
                    "was not sent."
                )

            message_id = self.email_service.build_message_id()
            # Persist before sending: if we crash mid-send we still know a
            # message with this id may exist.
            await record_outbound(
                self.repository,
                message_id=message_id,
                workflow_run_id=workflow_id,
                account_id=account_id,
                to_address=recipient,
                subject=package["email_subject"],
            )
            result = await self.email_service.send(
                recipient=recipient,
                subject=package["email_subject"],
                body=package["email_body"],
                message_id=message_id,
            )
            return (
                {"recipient_domain": recipient.split("@")[-1], "accepted": True},
                result.message_id,
            )

        email_outcome = await self.execution.run(
            workflow_id, EMAIL_SEND, send_email, force=force
        )
        outcomes.append(email_outcome)

        # --- internal tasks --------------------------------------------------
        async def create_tasks() -> tuple[dict[str, Any], str | None]:
            created = await self.task_service.create_internal_tasks(
                account_id=account_id,
                workflow_run_id=workflow_id,
                actions=extraction.internal_actions,
            )
            return {"tasks_created": created}, None

        task_outcome = await self.execution.run(
            workflow_id, TASK_CREATE, create_tasks, force=force
        )
        outcomes.append(task_outcome)

        # --- CRM -------------------------------------------------------------
        async def update_crm() -> tuple[dict[str, Any], str | None]:
            return await self.crm_service.update_account(
                account_id=account_id,
                workflow_run_id=workflow_id,
                crm_update=extraction.crm_update,
            )

        crm_outcome = await self.execution.run(
            workflow_id, CRM_UPDATE, update_crm, force=force
        )
        outcomes.append(crm_outcome)

        all_ok = all(o.succeeded for o in outcomes)
        tasks_created = int(task_outcome.result.get("tasks_created", 0) or 0)

        if all_ok:
            await self.repository.update(
                "followup_packages",
                UUID(str(package["id"])),
                {"approved": True},
            )
            await self.repository.update(
                "workflow_runs",
                workflow_id,
                {
                    "status": "completed",
                    "completed_at": datetime.now(UTC).isoformat(),
                },
            )
            await self._audit(
                workflow_id,
                "workflow_completed",
                "External actions completed",
                {
                    "email_sent": email_outcome.succeeded,
                    "email_message_id": email_outcome.provider_id,
                    "tasks_created": tasks_created,
                    "crm_mode": self.settings.crm_mode,
                    "retried": any(o.skipped for o in outcomes),
                },
            )
            status = "completed"
        else:
            failures = {
                o.operation_type: o.error for o in outcomes if not o.succeeded
            }
            await self._mark_failed(
                workflow_id,
                "; ".join(f"{k}: {v}" for k, v in failures.items())[:2000],
            )
            await self._audit(
                workflow_id,
                "workflow_execution_failed",
                "One or more external actions did not succeed",
                {
                    "failures": failures,
                    # What DID happen still has to be visible, or a retry is
                    # guesswork.
                    "succeeded": [o.operation_type for o in outcomes if o.succeeded],
                    "email_message_id": email_outcome.provider_id,
                },
            )
            status = "failed"

        return {
            "workflow_run_id": workflow_id,
            "status": status,
            "email_sent": email_outcome.succeeded,
            "email_message_id": email_outcome.provider_id,
            "tasks_created": tasks_created,
            "crm_updated": crm_outcome.succeeded,
            "crm_mode": self.settings.crm_mode,
            "operations": [
                {
                    "operation_type": o.operation_type,
                    "status": o.status,
                    "skipped": o.skipped,
                    "provider_id": o.provider_id,
                    "error": o.error,
                }
                for o in outcomes
            ],
        }

    # -- package editing ---------------------------------------------------

    async def edit_package(
        self,
        workflow_id: UUID,
        email_subject: str | None,
        email_body: str | None,
    ) -> dict[str, Any]:
        """Apply operator edits and re-run validation against the source notes.

        This is how a blocked package becomes approvable: the human removes the
        unsupported content, and the validator confirms it.
        """
        workflow = await self._require_workflow(workflow_id)
        if workflow["status"] != "awaiting_approval":
            raise ValidationError(
                f"Package cannot be edited from status '{workflow['status']}'"
            )

        package = await self._require_package(workflow_id)
        meeting = await self.repository.get_by_id(
            "meetings", UUID(str(workflow["meeting_id"]))
        )
        if not meeting:
            raise NotFoundError("Meeting not found")

        extraction = FollowupExtraction.model_validate(package["structured_output"])
        if email_subject:
            extraction.email.subject = email_subject
        if email_body:
            extraction.email.body = email_body

        clean, report = self.validator.validate(
            extraction,
            meeting["notes"],
            date.fromisoformat(str(meeting["meeting_date"])),
        )

        updated = await self.repository.update(
            "followup_packages",
            UUID(str(package["id"])),
            {
                "summary": clean.meeting_summary,
                "structured_output": clean.model_dump(mode="json"),
                "email_subject": clean.email.subject,
                "email_body": clean.email.body,
                "edited": True,
                "validation_issues": [i.model_dump() for i in report.issues],
                "blocked": not report.ok,
            },
        )
        await self._audit(
            workflow_id,
            "package_edited",
            "Operator edited the follow-up package",
            {"blocked": not report.ok, "critical_issues": len(report.critical)},
        )
        return updated

    # -- reads -------------------------------------------------------------

    async def get_detail(self, workflow_id: UUID) -> dict[str, Any]:
        workflow = await self._require_workflow(workflow_id)
        meeting = await self.repository.get_by_id(
            "meetings", UUID(str(workflow["meeting_id"]))
        )
        account = (
            await self.repository.get_by_id(
                "accounts", UUID(str(meeting["account_id"]))
            )
            if meeting
            else None
        )
        package = await self.repository.find_one(
            "followup_packages", {"workflow_run_id": str(workflow_id)}
        )
        return {
            "workflow": workflow,
            "meeting": meeting,
            "account": account,
            "package": package,
            "operations": await self.execution.list_operations(workflow_id),
            "audit_events": await self.repository.find(
                "audit_events",
                {"workflow_run_id": str(workflow_id)},
                order_by="created_at",
            ),
            "replies": await self.repository.find(
                "email_messages",
                {"workflow_run_id": str(workflow_id), "direction": "inbound"},
                order_by="created_at",
            ),
        }

    async def list_workflows(self, limit: int = 50) -> list[dict[str, Any]]:
        return await self.repository.find(
            "workflow_runs", order_by="created_at", descending=True, limit=limit
        )

    # -- internals ---------------------------------------------------------

    async def _require_workflow(self, workflow_id: UUID) -> dict[str, Any]:
        workflow = await self.repository.get_by_id("workflow_runs", workflow_id)
        if not workflow:
            raise NotFoundError("Workflow not found")
        return workflow

    async def _require_package(self, workflow_id: UUID) -> dict[str, Any]:
        package = await self.repository.find_one(
            "followup_packages", {"workflow_run_id": str(workflow_id)}
        )
        if not package:
            raise NotFoundError("Follow-up package not found")
        return package

    async def _mark_failed(self, workflow_id: UUID, message: str) -> None:
        try:
            await self.repository.update(
                "workflow_runs",
                workflow_id,
                {"status": "failed", "failure_message": message[:5000]},
            )
            await self._audit(workflow_id, "workflow_failed", message[:5000], {})
        except Exception:
            logger.exception("Unable to persist failure state for %s", workflow_id)

    async def _audit(
        self,
        workflow_id: UUID,
        event_type: str,
        message: str,
        metadata: dict[str, Any],
    ) -> None:
        await self.repository.insert(
            "audit_events",
            {
                "workflow_run_id": str(workflow_id),
                "event_type": event_type,
                "message": message[:5000] or event_type,
                "metadata": metadata,
            },
        )


def _safe(exc: Exception) -> str:
    return f"{type(exc).__name__}: {exc}"[:2000]
