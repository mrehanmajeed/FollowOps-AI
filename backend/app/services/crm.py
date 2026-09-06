"""CRM integration boundary.

No real CRM is connected. Rather than fake a successful sync, the service runs
in `simulated` mode: the proposed update is recorded in the audit log and every
response says so, including the one the operator sees in the UI.

Adding a real provider means implementing `_push_real` and setting
`CRM_MODE=<provider>`; the workflow contract does not change.
"""

import logging
from typing import Any
from uuid import UUID

from app.core.config import Settings
from app.repositories.supabase import SupabaseRepository
from app.schemas.followup import CRMUpdate

logger = logging.getLogger(__name__)

SIMULATED = "simulated"


class CRMService:
    def __init__(
        self,
        repository: SupabaseRepository,
        settings: Settings | None = None,
    ):
        self.repository = repository
        self.mode = (settings.crm_mode if settings else SIMULATED) or SIMULATED

    async def update_account(
        self,
        account_id: UUID,
        workflow_run_id: UUID,
        crm_update: CRMUpdate,
    ) -> tuple[dict[str, Any], str | None]:
        if self.mode != SIMULATED:
            raise NotImplementedError(
                f"CRM mode '{self.mode}' has no adapter. Configure CRM_MODE="
                f"simulated or implement the provider."
            )

        payload = {
            "account_id": str(account_id),
            "summary": crm_update.summary,
            "next_step": crm_update.next_step,
            "next_followup_date": (
                crm_update.next_followup_date.isoformat()
                if crm_update.next_followup_date
                else None
            ),
            "mode": SIMULATED,
        }

        await self.repository.insert(
            "audit_events",
            {
                "workflow_run_id": str(workflow_run_id),
                "event_type": "crm_update",
                "message": (
                    "CRM update recorded (simulated — no external CRM "
                    "connected)"
                ),
                "metadata": payload,
            },
        )
        logger.info(
            "crm_update_simulated workflow=%s account=%s", workflow_run_id, account_id
        )
        return {**payload, "simulated": True}, None
