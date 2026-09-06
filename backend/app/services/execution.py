"""Idempotent external side effects.

A workflow execution touches three systems that cannot share a transaction:
Zoho SMTP, the task store and the CRM. PostgreSQL cannot roll back an email
that Zoho already accepted, so instead of pretending execution is atomic, each
side effect gets its own durable record that is *claimed* before the effect is
attempted and settled afterwards.

    claim -> perform -> settle

The claim is a unique row in `workflow_operations`. A second executor (a retry,
a duplicate request, a second process) loses the insert and reads the existing
record instead, which yields one of three outcomes:

    succeeded  -> the effect already happened; skip it, report the stored result
    failed     -> a previous attempt finished and failed; safe to retry
    pending    -> a previous attempt was interrupted *while in flight*

`pending` is the genuinely hard case: the process died between handing the mail
to Zoho and recording the outcome, and SMTP offers no way to ask "did you
accept this message?". Guessing either way is wrong, so the operation is
reported as IN_DOUBT and requires an explicit operator decision rather than an
automatic resend.
"""

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from app.repositories.supabase import SupabaseRepository

logger = logging.getLogger(__name__)

CLAIMED = "claimed"
ALREADY_SUCCEEDED = "already_succeeded"
IN_DOUBT = "in_doubt"

EMAIL_SEND = "email_send"
TASK_CREATE = "task_create"
CRM_UPDATE = "crm_update"


class OperationInDoubtError(RuntimeError):
    """A previous attempt was interrupted in flight; a human must decide."""


@dataclass
class OperationOutcome:
    operation_type: str
    status: str
    result: dict[str, Any]
    provider_id: str | None = None
    error: str | None = None
    skipped: bool = False

    @property
    def succeeded(self) -> bool:
        return self.status == "succeeded"


def idempotency_key(workflow_run_id: UUID, operation_type: str) -> str:
    """One approved workflow yields exactly one key per side effect."""
    return f"{workflow_run_id}:{operation_type}"


class ExecutionService:
    def __init__(self, repository: SupabaseRepository):
        self.repository = repository

    async def run(
        self,
        workflow_run_id: UUID,
        operation_type: str,
        perform: Callable[[], Awaitable[tuple[dict[str, Any], str | None]]],
        force: bool = False,
    ) -> OperationOutcome:
        """Claim, perform and settle one side effect exactly once.

        `perform` returns `(result, provider_id)`. `force=True` overrides an
        IN_DOUBT record, which is how an operator says "I checked the mailbox,
        it was not sent, send it".
        """
        state, record = await self._claim(workflow_run_id, operation_type, force)

        if state == ALREADY_SUCCEEDED:
            logger.info(
                "operation_skipped workflow=%s type=%s reason=already_succeeded",
                workflow_run_id,
                operation_type,
            )
            return OperationOutcome(
                operation_type,
                "succeeded",
                record.get("result") or {},
                record.get("provider_id"),
                skipped=True,
            )

        if state == IN_DOUBT:
            message = (
                f"A previous {operation_type} attempt for this workflow was "
                f"interrupted while in flight and its outcome is unknown. "
                f"Verify the external system before retrying."
            )
            logger.warning(
                "operation_in_doubt workflow=%s type=%s",
                workflow_run_id,
                operation_type,
            )
            return OperationOutcome(
                operation_type, "in_doubt", record.get("result") or {}, error=message
            )

        try:
            result, provider_id = await perform()
        except Exception as exc:
            await self._settle(record, "failed", error=_safe_error(exc))
            logger.warning(
                "operation_failed workflow=%s type=%s error=%s",
                workflow_run_id,
                operation_type,
                type(exc).__name__,
            )
            return OperationOutcome(
                operation_type, "failed", {}, error=_safe_error(exc)
            )

        await self._settle(record, "succeeded", result=result, provider_id=provider_id)
        return OperationOutcome(operation_type, "succeeded", result, provider_id)

    async def list_operations(self, workflow_run_id: UUID) -> list[dict[str, Any]]:
        return await self.repository.find(
            "workflow_operations",
            {"workflow_run_id": str(workflow_run_id)},
            order_by="created_at",
        )

    # -- internals ---------------------------------------------------------

    async def _claim(
        self, workflow_run_id: UUID, operation_type: str, force: bool
    ) -> tuple[str, dict[str, Any]]:
        key = idempotency_key(workflow_run_id, operation_type)
        existing = await self.repository.find_one(
            "workflow_operations", {"idempotency_key": key}
        )

        if existing is None:
            try:
                record = await self.repository.insert(
                    "workflow_operations",
                    {
                        "workflow_run_id": str(workflow_run_id),
                        "operation_type": operation_type,
                        "idempotency_key": key,
                        "status": "pending",
                        "attempts": 1,
                    },
                )
                return CLAIMED, record
            except Exception:
                # Lost the race against a concurrent executor: fall through and
                # read whatever they wrote.
                existing = await self.repository.find_one(
                    "workflow_operations", {"idempotency_key": key}
                )
                if existing is None:
                    raise

        status = existing.get("status")
        if status == "succeeded":
            return ALREADY_SUCCEEDED, existing
        if status == "pending" and not force:
            return IN_DOUBT, existing

        record = await self.repository.update(
            "workflow_operations",
            UUID(str(existing["id"])),
            {
                "status": "pending",
                "attempts": int(existing.get("attempts") or 0) + 1,
                "error": None,
            },
        )
        return CLAIMED, record

    async def _settle(
        self,
        record: dict[str, Any],
        status: str,
        result: dict[str, Any] | None = None,
        provider_id: str | None = None,
        error: str | None = None,
    ) -> None:
        payload: dict[str, Any] = {
            "status": status,
            "error": error,
            "completed_at": datetime.now(UTC).isoformat(),
        }
        if result is not None:
            payload["result"] = result
        if provider_id:
            payload["provider_id"] = provider_id
        await self.repository.update(
            "workflow_operations", UUID(str(record["id"])), payload
        )


def _safe_error(exc: Exception) -> str:
    """Never let a provider exception carry a credential into the database."""
    return f"{type(exc).__name__}: {exc}"[:2000]
