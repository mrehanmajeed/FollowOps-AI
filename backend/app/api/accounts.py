from uuid import UUID

from fastapi import APIRouter, Depends

from app.api.dependencies import get_repository
from app.core.exceptions import NotFoundError
from app.core.security import require_operator
from app.repositories.supabase import SupabaseRepository
from app.schemas.account import AccountCreate, AccountResponse, PauseRequest

router = APIRouter(
    prefix="/accounts",
    tags=["accounts"],
    dependencies=[Depends(require_operator)],
)


@router.post("", response_model=AccountResponse, status_code=201)
async def create_account(
    payload: AccountCreate,
    repository: SupabaseRepository = Depends(get_repository),
) -> AccountResponse:
    record = await repository.insert("accounts", payload.model_dump(mode="json"))
    return AccountResponse.model_validate(record)


@router.get("", response_model=list[AccountResponse])
async def list_accounts(
    repository: SupabaseRepository = Depends(get_repository),
) -> list[AccountResponse]:
    records = await repository.find("accounts", order_by="created_at", descending=True)
    return [AccountResponse.model_validate(record) for record in records]


@router.post("/{account_id}/followups", response_model=AccountResponse)
async def set_followup_pause(
    account_id: UUID,
    payload: PauseRequest,
    repository: SupabaseRepository = Depends(get_repository),
    operator: str = Depends(require_operator),
) -> AccountResponse:
    """Pause or resume automated follow-ups for an account.

    Resuming after a confirmed reply is an explicit human decision, which is
    why it is a separate call rather than a flag on the approve request.
    """
    if not await repository.get_by_id("accounts", account_id):
        raise NotFoundError("Account not found")

    reason = payload.reason or ("Paused by operator" if payload.paused else None)
    record = await repository.update(
        "accounts",
        account_id,
        {"followups_paused": payload.paused, "followups_paused_reason": reason},
    )

    # audit_events hangs off a workflow run, so attach this to the most recent
    # workflow that emailed the account. With none, the account state itself is
    # the only record there is to keep.
    latest = await repository.find_one(
        "email_messages",
        {"account_id": str(account_id), "direction": "outbound"},
        order_by="created_at",
        descending=True,
    )
    if latest and latest.get("workflow_run_id"):
        await repository.insert(
            "audit_events",
            {
                "workflow_run_id": str(latest["workflow_run_id"]),
                "event_type": (
                    "followups_paused" if payload.paused else "followups_resumed"
                ),
                "message": f"Operator set follow-ups paused={payload.paused}",
                "metadata": {
                    "account_id": str(account_id),
                    "operator": operator,
                    "reason": reason,
                },
            },
        )
    return AccountResponse.model_validate(record)
