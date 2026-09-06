from datetime import date

import pytest
from pydantic import ValidationError

from app.schemas.followup import ActionItem, WorkflowCreate


def test_action_item_accepts_optional_owner_and_due_date():
    item = ActionItem(
        text="Prepare proposal",
        evidence="We will prepare the proposal",
        confidence=0.95,
    )
    assert item.owner is None
    assert item.due_date is None


def test_workflow_create_rejects_short_notes():
    with pytest.raises(ValidationError):
        WorkflowCreate(
            account_id="00000000-0000-0000-0000-000000000001",
            meeting_date=date(2026, 9, 5),
            notes="short",
        )
