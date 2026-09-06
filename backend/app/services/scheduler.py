"""Background IMAP polling.

One AsyncIOScheduler job, owned by the FastAPI lifespan. `max_instances=1` plus
`coalesce=True` means a slow poll cannot overlap itself and a backlog of missed
ticks collapses into one run rather than a stampede after a restart.

Polling never blocks a request: the blocking imaplib work runs in a thread (see
`ReplyProcessor.poll_once`), and failures are swallowed into the summary so a
Zoho outage degrades reply detection without taking down the API.
"""

import logging
from typing import Any

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from app.core.config import Settings
from app.repositories.supabase import SupabaseRepository, create_supabase_client
from app.services.imap_client import ZohoImapClient
from app.services.reply_processor import PollSummary, ReplyProcessor

logger = logging.getLogger(__name__)

JOB_ID = "imap_reply_poll"


class ReplyPollingScheduler:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._scheduler: AsyncIOScheduler | None = None
        self._processor: ReplyProcessor | None = None
        self.last_summary: dict[str, Any] | None = None

    async def build_processor(self) -> ReplyProcessor:
        """One client for the life of the process; a new one per tick leaks."""
        if self._processor is None:
            client = await create_supabase_client()
            self._processor = ReplyProcessor(
                SupabaseRepository(client), ZohoImapClient(self.settings)
            )
        return self._processor

    async def run_once(self) -> PollSummary:
        processor = await self.build_processor()
        summary = await processor.poll_once()
        self.last_summary = summary.as_dict()
        return summary

    def start(self) -> None:
        if not self.settings.zoho_imap_enabled:
            logger.info("imap_polling_disabled")
            return
        if self._scheduler:
            return

        self._scheduler = AsyncIOScheduler(timezone="UTC")
        self._scheduler.add_job(
            self._tick,
            IntervalTrigger(seconds=self.settings.zoho_imap_poll_interval_seconds),
            id=JOB_ID,
            max_instances=1,
            coalesce=True,
            misfire_grace_time=self.settings.zoho_imap_poll_interval_seconds,
        )
        self._scheduler.start()
        logger.info(
            "imap_polling_started interval_seconds=%d mailbox=%s",
            self.settings.zoho_imap_poll_interval_seconds,
            self.settings.zoho_imap_mailbox,
        )

    def shutdown(self) -> None:
        if self._scheduler:
            self._scheduler.shutdown(wait=False)
            self._scheduler = None

    @property
    def running(self) -> bool:
        return bool(self._scheduler and self._scheduler.running)

    async def _tick(self) -> None:
        try:
            await self.run_once()
        except Exception:
            # Never let a scheduled job raise into APScheduler's loop.
            logger.exception("imap_poll_tick_failed")
