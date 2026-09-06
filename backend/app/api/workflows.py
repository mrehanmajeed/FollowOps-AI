from uuid import UUID

from fastapi import APIRouter, Depends

from app.api.dependencies import get_workflow_service
from app.core.security import require_operator
from app.schemas.followup import (
    ApprovalRequest,
    ExecutionResponse,
    PackageEdit,
    WorkflowCreate,
    WorkflowDetail,
    WorkflowResponse,
    WorkflowSummary,
)
from app.services.workflow import WorkflowService

router = APIRouter(
    prefix="/workflows",
    tags=["workflows"],
    dependencies=[Depends(require_operator)],
)


@router.post("", response_model=WorkflowResponse, status_code=201)
async def create_workflow(
    payload: WorkflowCreate,
    service: WorkflowService = Depends(get_workflow_service),
) -> WorkflowResponse:
    result = await service.create_workflow(
        account_id=payload.account_id,
        meeting_date=payload.meeting_date,
        notes=payload.notes,
    )
    return WorkflowResponse.model_validate(result)


@router.get("", response_model=list[WorkflowSummary])
async def list_workflows(
    limit: int = 50,
    service: WorkflowService = Depends(get_workflow_service),
) -> list[WorkflowSummary]:
    records = await service.list_workflows(limit=limit)
    return [WorkflowSummary.model_validate(r) for r in records]


@router.get("/{workflow_id}", response_model=WorkflowDetail)
async def get_workflow(
    workflow_id: UUID,
    service: WorkflowService = Depends(get_workflow_service),
) -> WorkflowDetail:
    return WorkflowDetail.model_validate(await service.get_detail(workflow_id))


@router.patch("/{workflow_id}/package")
async def edit_package(
    workflow_id: UUID,
    payload: PackageEdit,
    service: WorkflowService = Depends(get_workflow_service),
) -> dict:
    return await service.edit_package(
        workflow_id=workflow_id,
        email_subject=payload.email_subject,
        email_body=payload.email_body,
    )


@router.post("/{workflow_id}/approve", response_model=ExecutionResponse)
async def approve_workflow(
    workflow_id: UUID,
    payload: ApprovalRequest,
    service: WorkflowService = Depends(get_workflow_service),
    operator: str = Depends(require_operator),
) -> ExecutionResponse:
    result = await service.approve_and_execute(
        workflow_id=workflow_id,
        approved=payload.approved,
        approver=operator,
    )
    return ExecutionResponse.model_validate(result)


@router.post("/{workflow_id}/retry", response_model=ExecutionResponse)
async def retry_execution(
    workflow_id: UUID,
    force: bool = False,
    service: WorkflowService = Depends(get_workflow_service),
) -> ExecutionResponse:
    """Re-run the side effects of an already approved workflow.

    Operations that already succeeded are skipped. `force=true` also overrides
    an in-doubt email operation and must only be used after checking the
    mailbox — it can send a second copy.
    """
    result = await service.execute(workflow_id, force=force)
    return ExecutionResponse.model_validate(result)
