"""Internal task creation.

Guarded against duplicates independently of the operation record: if tasks for
this workflow already exist, a retry adds nothing. Two layers is deliberate —
the operation claim can be lost to a crash, the row check cannot.
"""

import logging
from typing import Any
from uuid import UUID

from app.repositories.supabase import SupabaseRepository

logger = logging.getLogger(__name__)


class TaskService:
    def __init__(self, repository: SupabaseRepository):
        self.repository = repository

    async def create_internal_tasks(
        self,
        account_id: UUID,
        workflow_run_id: UUID,
        actions: list[Any],
    ) -> int:
        if not actions:
            return 0

        existing = await self.repository.find(
            "tasks", {"workflow_run_id": str(workflow_run_id)}, columns="title"
        )
        existing_titles = {row["title"] for row in existing}

        created = 0
        for action in actions:
            if action.text in existing_titles:
                continue
            await self.repository.insert(
                "tasks",
                {
                    "account_id": str(account_id),
                    "workflow_run_id": str(workflow_run_id),
                    "title": action.text[:1000],
                    "owner": action.owner,
                    "due_date": (
                        action.due_date.isoformat() if action.due_date else None
                    ),
                    "status": "open",
                },
            )
            created += 1

        if created != len(actions):
            logger.info(
                "tasks_deduplicated workflow=%s created=%d requested=%d",
                workflow_run_id,
                created,
                len(actions),
            )
        return created + len(existing_titles)
