from datetime import date, datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class EvidenceItem(BaseModel):
    text: str = Field(min_length=1)
    evidence: str = Field(min_length=1)
    confidence: float = Field(ge=0, le=1)


class ActionItem(EvidenceItem):
    owner: str | None = None
    due_date: date | None = None


class CRMUpdate(BaseModel):
    summary: str
    next_step: str | None = None
    next_followup_date: date | None = None


class EmailDraft(BaseModel):
    subject: str = Field(min_length=1, max_length=200)
    body: str = Field(min_length=1)


class FollowupExtraction(BaseModel):
    meeting_summary: str
    decisions: list[EvidenceItem] = Field(default_factory=list)
    client_actions: list[ActionItem] = Field(default_factory=list)
    internal_actions: list[ActionItem] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    crm_update: CRMUpdate
    email: EmailDraft
    overall_confidence: float = Field(ge=0, le=1)


class WorkflowCreate(BaseModel):
    account_id: UUID
    meeting_date: date
    notes: str = Field(min_length=10, max_length=50000)


class WorkflowResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    meeting_id: UUID
    status: str
    model: str
    latency_ms: int | None = None
    estimated_cost: float | None = None
    overall_confidence: float | None = None
    created_at: datetime
    blocked: bool = False
    validation_issues: list[dict[str, Any]] = Field(default_factory=list)


class WorkflowSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    meeting_id: UUID
    status: str
    model: str
    latency_ms: int | None = None
    overall_confidence: float | None = None
    failure_message: str | None = None
    created_at: datetime
    completed_at: datetime | None = None


class WorkflowDetail(BaseModel):
    """Everything the operator console needs to review one workflow."""

    workflow: dict[str, Any]
    meeting: dict[str, Any] | None = None
    account: dict[str, Any] | None = None
    package: dict[str, Any] | None = None
    operations: list[dict[str, Any]] = Field(default_factory=list)
    audit_events: list[dict[str, Any]] = Field(default_factory=list)
    replies: list[dict[str, Any]] = Field(default_factory=list)


class PackageEdit(BaseModel):
    email_subject: str | None = Field(default=None, min_length=1, max_length=200)
    email_body: str | None = Field(default=None, min_length=1, max_length=50000)


class ApprovalRequest(BaseModel):
    approved: bool


class OperationResult(BaseModel):
    operation_type: str
    status: str
    skipped: bool = False
    provider_id: str | None = None
    error: str | None = None


class ExecutionResponse(BaseModel):
    workflow_run_id: UUID
    status: str
    email_sent: bool
    tasks_created: int
    crm_updated: bool
    email_message_id: str | None = None
    crm_mode: str = "simulated"
    operations: list[OperationResult] = Field(default_factory=list)


class EvaluationCase(BaseModel):
    case_id: str
    description: str
    meeting_date: date
    account_context: str = ""
    notes: str
    expected_actions: list[str] = Field(default_factory=list)
    expected_decisions: list[str] = Field(default_factory=list)
    expected_owners: dict[str, str] = Field(default_factory=dict)
    expected_due_dates: dict[str, str] = Field(default_factory=dict)
    forbidden_claims: list[str] = Field(default_factory=list)
    must_be_null_owner: list[str] = Field(default_factory=list)
    must_be_null_due_date: list[str] = Field(default_factory=list)


class EvaluationResult(BaseModel):
    case_id: str
    passed: bool
    action_recall: float
    decision_precision: float
    forbidden_claims_found: list[str]
    errors: list[str]
