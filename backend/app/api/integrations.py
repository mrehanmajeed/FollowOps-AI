"""Integration status and manual controls.

Every response reports what is actually connected. Nothing here reports success
it did not observe, and no response contains a credential — only hosts, user
names, booleans and provider error text.
"""

from fastapi import APIRouter, Depends

from app.api.dependencies import (
    get_email_service,
    get_imap_client,
    get_reply_processor,
    get_repository,
    get_scheduler,
)
from app.core.config import Settings, get_settings
from app.core.security import require_operator
from app.repositories.supabase import SupabaseRepository
from app.services.imap_client import ZohoImapClient
from app.services.reply_processor import ReplyProcessor
from app.services.scheduler import ReplyPollingScheduler
from app.services.zoho import ZohoEmailService

router = APIRouter(
    prefix="/integrations",
    tags=["integrations"],
    dependencies=[Depends(require_operator)],
)


@router.get("")
async def integration_status(
    settings: Settings = Depends(get_settings),
    scheduler: ReplyPollingScheduler = Depends(get_scheduler),
) -> dict:
    """Configuration as loaded. Does not contact any provider — see /verify."""
    return {
        "gemini": {"model": settings.gemini_model, "configured": True},
        "supabase": {"url": settings.supabase_url, "configured": True},
        "smtp": {
            "host": settings.zoho_smtp_host,
            "port": settings.zoho_smtp_port,
            "user": settings.zoho_smtp_user,
            "tls": settings.zoho_use_tls,
        },
        "imap": {
            "enabled": settings.zoho_imap_enabled,
            "host": settings.zoho_imap_host,
            "port": settings.zoho_imap_port,
            "ssl": settings.zoho_imap_use_ssl,
            "user": settings.zoho_imap_user,
            "mailbox": settings.zoho_imap_mailbox,
            "poll_interval_seconds": settings.zoho_imap_poll_interval_seconds,
            "polling": scheduler.running,
            "last_poll": scheduler.last_summary,
        },
        "crm": {
            "mode": settings.crm_mode,
            "real_integration": settings.crm_mode != "simulated",
        },
    }


@router.post("/smtp/verify")
async def verify_smtp(
    email_service: ZohoEmailService = Depends(get_email_service),
) -> dict:
    """Authenticate against SMTP without sending mail."""
    return await email_service.verify_connection()


@router.post("/imap/verify")
async def verify_imap(
    imap_client: ZohoImapClient = Depends(get_imap_client),
) -> dict:
    """Authenticate and list folders. Opens the mailbox read-only."""
    import asyncio

    return await asyncio.to_thread(imap_client.verify_connection)


@router.post("/imap/poll")
async def poll_now(
    processor: ReplyProcessor = Depends(get_reply_processor),
) -> dict:
    """Run one reply-detection pass immediately, outside the schedule."""
    summary = await processor.poll_once()
    return summary.as_dict()


@router.get("/replies")
async def list_replies(
    limit: int = 50,
    repository: SupabaseRepository = Depends(get_repository),
) -> list[dict]:
    """Inbound mail with its correlation verdict and the reason for it."""
    return await repository.find(
        "email_messages",
        {"direction": "inbound"},
        order_by="created_at",
        descending=True,
        limit=limit,
    )
