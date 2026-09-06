"""Poll the mailbox, correlate inbound mail, and pause follow-ups on a reply.

    IMAP poll -> parse -> correlate -> persist -> pause -> audit

Idempotency comes from the database, not from mailbox flags: `email_messages`
has a unique index on `(direction, message_id)`, so re-reading the same message
inserts nothing and changes nothing. This keeps the mailbox read-only and makes
the poller safe to restart at any point.

Only `confirmed_reply` pauses an account. A `possible_reply` is persisted and
surfaced for review, because pausing on weak evidence would silently strand a
sequence with no one being told.
"""

import asyncio
import logging
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from app.repositories.supabase import SupabaseRepository
from app.services.correlation import (
    CONFIRMED_REPLY,
    POSSIBLE_REPLY,
    InboundMessage,
    OutboundRef,
    correlate,
    normalize_subject,
)
from app.services.imap_client import ImapError, ZohoImapClient

logger = logging.getLogger(__name__)


@dataclass
class PollSummary:
    fetched: int = 0
    processed: int = 0
    duplicates: int = 0
    confirmed_replies: int = 0
    possible_replies: int = 0
    unrelated: int = 0
    paused_accounts: int = 0
    errors: list[str] = field(default_factory=list)
    ran_at: str = ""

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


class ReplyProcessor:
    def __init__(
        self,
        repository: SupabaseRepository,
        imap_client: ZohoImapClient,
    ):
        self.repository = repository
        self.imap_client = imap_client

    async def poll_once(self) -> PollSummary:
        summary = PollSummary(ran_at=datetime.now(UTC).isoformat())
        try:
            messages = await asyncio.to_thread(self.imap_client.fetch_recent)
        except ImapError as exc:
            # A Zoho outage must not crash the scheduler; the next tick retries.
            summary.errors.append(str(exc))
            logger.warning("imap_poll_failed error=%s", exc)
            return summary

        summary.fetched = len(messages)
        if not messages:
            return summary

        outbound = await self._outbound_refs()
        contacts = await self._contact_index()

        for message in messages:
            try:
                await self._process_message(message, outbound, contacts, summary)
            except Exception as exc:
                summary.errors.append(f"{message.message_id}: {type(exc).__name__}")
                logger.exception(
                    "reply_processing_failed message_id=%s", message.message_id
                )

        logger.info(
            "imap_poll_done fetched=%d processed=%d duplicates=%d confirmed=%d "
            "possible=%d unrelated=%d paused=%d",
            summary.fetched,
            summary.processed,
            summary.duplicates,
            summary.confirmed_replies,
            summary.possible_replies,
            summary.unrelated,
            summary.paused_accounts,
        )
        return summary

    # -- per message -------------------------------------------------------

    async def _process_message(
        self,
        message: InboundMessage,
        outbound: list[OutboundRef],
        contacts: dict[str, str],
        summary: PollSummary,
    ) -> None:
        if await self.repository.exists(
            "email_messages",
            {"direction": "inbound", "message_id": message.message_id},
        ):
            summary.duplicates += 1
            return

        result = correlate(message, outbound, contacts)

        try:
            await self.repository.insert(
                "email_messages",
                {
                    "direction": "inbound",
                    "message_id": message.message_id,
                    "in_reply_to": message.in_reply_to or None,
                    "references_header": " ".join(message.references) or None,
                    "account_id": result.account_id,
                    "workflow_run_id": result.workflow_run_id,
                    "from_address": message.from_address,
                    "to_address": ", ".join(message.to_addresses)[:500] or None,
                    "subject": message.subject[:2000] or None,
                    "normalized_subject": message.normalized_subject or None,
                    "mailbox": message.mailbox,
                    "imap_uid": message.imap_uid or None,
                    "correlation": result.correlation,
                    "correlation_reason": result.reason[:1000],
                    "received_at": (
                        message.received_at.isoformat() if message.received_at else None
                    ),
                },
            )
        except Exception:
            # The unique index rejected a concurrent insert of the same message.
            if await self.repository.exists(
                "email_messages",
                {"direction": "inbound", "message_id": message.message_id},
            ):
                summary.duplicates += 1
                return
            raise

        summary.processed += 1
        logger.info(
            "inbound_correlated message_id=%s correlation=%s workflow=%s account=%s",
            message.message_id,
            result.correlation,
            result.workflow_run_id,
            result.account_id,
        )

        if result.correlation == CONFIRMED_REPLY:
            summary.confirmed_replies += 1
            paused = await self._pause_followups(result, message)
            summary.paused_accounts += int(paused)
        elif result.correlation == POSSIBLE_REPLY:
            summary.possible_replies += 1
        else:
            summary.unrelated += 1

    async def _pause_followups(self, result, message: InboundMessage) -> bool:
        if not result.account_id:
            return False

        received = (message.received_at or datetime.now(UTC)).isoformat()
        await self.repository.update(
            "accounts",
            UUID(str(result.account_id)),
            {
                "followups_paused": True,
                "followups_paused_reason": (
                    f"Confirmed reply from {message.from_address} "
                    f"({', '.join(result.evidence) or 'thread match'})."
                )[:1000],
                "last_reply_at": received,
            },
        )

        if result.workflow_run_id:
            await self.repository.insert(
                "audit_events",
                {
                    "workflow_run_id": result.workflow_run_id,
                    "event_type": "reply_detected",
                    "message": "Confirmed prospect reply; automated follow-ups paused",
                    "metadata": {
                        "inbound_message_id": message.message_id,
                        "matched_outbound_message_id": result.matched_message_id,
                        "from_domain": message.from_address.split("@")[-1],
                        "evidence": result.evidence,
                        "reason": result.reason,
                    },
                },
            )
        return True

    # -- correlation inputs ------------------------------------------------

    async def _outbound_refs(self) -> list[OutboundRef]:
        rows = await self.repository.find(
            "email_messages",
            {"direction": "outbound"},
            order_by="created_at",
            columns="message_id,workflow_run_id,account_id,to_address,subject",
        )
        return [
            OutboundRef(
                message_id=row["message_id"],
                workflow_run_id=str(row["workflow_run_id"]),
                account_id=str(row["account_id"]) if row.get("account_id") else "",
                to_address=(row.get("to_address") or "").lower(),
                subject=row.get("subject") or "",
            )
            for row in rows
            if row.get("message_id")
        ]

    async def _contact_index(self) -> dict[str, str]:
        rows = await self.repository.find(
            "accounts", columns="id,contact_email"
        )
        return {
            row["contact_email"].lower(): str(row["id"])
            for row in rows
            if row.get("contact_email")
        }


async def record_outbound(
    repository: SupabaseRepository,
    *,
    message_id: str,
    workflow_run_id: UUID,
    account_id: UUID,
    to_address: str,
    subject: str,
    in_reply_to: str | None = None,
) -> None:
    """Persist an outbound email so a later reply can be correlated to it.

    Written *before* the SMTP call: if the send is interrupted, we still know a
    message with this id may exist in the world.
    """
    await repository.insert(
        "email_messages",
        {
            "direction": "outbound",
            "message_id": message_id,
            "in_reply_to": in_reply_to,
            "account_id": str(account_id),
            "workflow_run_id": str(workflow_run_id),
            "to_address": to_address,
            "subject": subject[:2000],
            "normalized_subject": normalize_subject(subject) or None,
            "correlation": "outbound",
            "sent_at": datetime.now(UTC).isoformat(),
        },
    )
