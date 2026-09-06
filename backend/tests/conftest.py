"""Test doubles.

`FakeRepository` mirrors the parts of the real schema that the safety
properties depend on: the unique indexes that make execution idempotent, and
the workflow transition trigger. Without those, a test could "pass" against a
fake that is more permissive than PostgreSQL.
"""

import os
from datetime import UTC, date, datetime
from typing import Any
from uuid import UUID, uuid4

import pytest

# `app.main` builds Settings at import time, so importing the application
# requires these to exist. Without them a fresh clone fails during collection —
# every test, not just the ones touching config. Placeholders keep the suite
# self-contained: no test contacts a provider, each either builds its own
# Settings or overrides the dependency. setdefault so a developer who has
# exported real values keeps them.
os.environ.setdefault("OPERATOR_API_KEY", "test-operator-key")
os.environ.setdefault("GEMINI_API_KEY", "test-gemini-key")
os.environ.setdefault("SUPABASE_URL", "https://example.supabase.co")
os.environ.setdefault("SUPABASE_SECRET_KEY", "test-secret")
os.environ.setdefault("ZOHO_SMTP_USER", "ops@followops.test")
os.environ.setdefault("ZOHO_SMTP_PASSWORD", "test-password")
os.environ.setdefault("ZOHO_IMAP_ENABLED", "false")

from app.core.config import Settings  # noqa: E402
from app.schemas.followup import (  # noqa: E402
    ActionItem,
    CRMUpdate,
    EmailDraft,
    EvidenceItem,
    FollowupExtraction,
)

# Mirrors public.enforce_workflow_transition in the migrations.
ALLOWED_TRANSITIONS = {
    ("created", "processing"),
    ("processing", "validating"),
    ("processing", "awaiting_approval"),
    ("processing", "failed"),
    ("validating", "awaiting_approval"),
    ("validating", "failed"),
    ("awaiting_approval", "approved"),
    ("awaiting_approval", "rejected"),
    ("awaiting_approval", "failed"),
    ("approved", "executing"),
    ("approved", "failed"),
    ("executing", "completed"),
    ("executing", "failed"),
    ("failed", "executing"),
}

# Mirrors the unique indexes added by the migrations.
UNIQUE_INDEXES = {
    "workflow_operations": [
        ("idempotency_key",),
        ("workflow_run_id", "operation_type"),
    ],
    "email_messages": [("direction", "message_id")],
    "followup_packages": [("workflow_run_id",)],
}


class UniqueViolation(RuntimeError):
    pass


class InvalidTransition(RuntimeError):
    pass


class FakeRepository:
    def __init__(self) -> None:
        self.tables: dict[str, list[dict[str, Any]]] = {}

    # -- helpers used by tests --------------------------------------------

    def rows(self, table: str) -> list[dict[str, Any]]:
        return self.tables.setdefault(table, [])

    def seed(self, table: str, record: dict[str, Any]) -> dict[str, Any]:
        record.setdefault("id", str(uuid4()))
        record.setdefault("created_at", datetime.now(UTC).isoformat())
        self.rows(table).append(record)
        return record

    # -- SupabaseRepository interface --------------------------------------

    async def insert(self, table: str, payload: dict[str, Any]) -> dict[str, Any]:
        record = dict(payload)
        record.setdefault("id", str(uuid4()))
        record.setdefault("created_at", datetime.now(UTC).isoformat())
        self._check_unique(table, record)
        self.rows(table).append(record)
        return dict(record)

    async def get_by_id(self, table: str, record_id: UUID) -> dict[str, Any] | None:
        for row in self.rows(table):
            if str(row.get("id")) == str(record_id):
                return dict(row)
        return None

    async def get_one_by(
        self, table: str, column: str, value: str
    ) -> dict[str, Any] | None:
        return await self.find_one(table, {column: value})

    async def update(
        self, table: str, record_id: UUID, payload: dict[str, Any]
    ) -> dict[str, Any]:
        for row in self.rows(table):
            if str(row.get("id")) != str(record_id):
                continue
            if table == "workflow_runs" and "status" in payload:
                self._check_transition(row.get("status"), payload["status"])
            row.update(payload)
            return dict(row)
        raise RuntimeError(f"Update failed for table '{table}'")

    async def list_all(self, table: str) -> list[dict[str, Any]]:
        return [dict(r) for r in self.rows(table)]

    async def find(
        self,
        table: str,
        filters: dict[str, Any] | None = None,
        order_by: str | None = None,
        descending: bool = False,
        limit: int | None = None,
        columns: str = "*",
    ) -> list[dict[str, Any]]:
        results = [
            dict(row)
            for row in self.rows(table)
            if all(str(row.get(k)) == str(v) for k, v in (filters or {}).items())
        ]
        if order_by:
            results.sort(key=lambda r: str(r.get(order_by) or ""), reverse=descending)
        return results[:limit] if limit else results

    async def find_one(
        self,
        table: str,
        filters: dict[str, Any],
        order_by: str | None = None,
        descending: bool = False,
    ) -> dict[str, Any] | None:
        found = await self.find(
            table, filters, order_by=order_by, descending=descending, limit=1
        )
        return found[0] if found else None

    async def exists(self, table: str, filters: dict[str, Any]) -> bool:
        return await self.find_one(table, filters) is not None

    # -- constraint emulation ---------------------------------------------

    def _check_unique(self, table: str, record: dict[str, Any]) -> None:
        for index in UNIQUE_INDEXES.get(table, []):
            if any(record.get(col) is None for col in index):
                continue
            for row in self.rows(table):
                if all(row.get(col) == record.get(col) for col in index):
                    raise UniqueViolation(f"{table}{index} already exists")

    @staticmethod
    def _check_transition(current: str | None, new: str) -> None:
        if current == new:
            return
        if (current, new) not in ALLOWED_TRANSITIONS:
            raise InvalidTransition(f"Invalid workflow transition: {current} -> {new}")


@pytest.fixture
def repository() -> FakeRepository:
    return FakeRepository()


@pytest.fixture
def settings() -> Settings:
    return Settings(
        operator_api_key="test-operator-key",
        gemini_api_key="test-gemini-key",
        supabase_url="https://example.supabase.co",
        supabase_secret_key="test-secret",
        zoho_smtp_user="ops@followops.test",
        zoho_smtp_password="test-password",
        zoho_imap_enabled=False,
    )


@pytest.fixture
def meeting_notes() -> str:
    return (
        "Sarah confirmed that we will prepare a commercial proposal. "
        "Sarah will review the proposal next week. "
        "We agreed to schedule a technical workshop after the review."
    )


def make_extraction(
    evidence: str = "we will prepare a commercial proposal",
    owner: str | None = None,
    due_date: date | None = None,
    email_body: str = "Thanks for the call. We will prepare a commercial proposal.",
) -> FollowupExtraction:
    return FollowupExtraction(
        meeting_summary="Customer requested a commercial proposal.",
        decisions=[
            EvidenceItem(
                text="Prepare a commercial proposal",
                evidence=evidence,
                confidence=0.9,
            )
        ],
        client_actions=[],
        internal_actions=[
            ActionItem(
                text="Prepare a commercial proposal",
                evidence=evidence,
                confidence=0.9,
                owner=owner,
                due_date=due_date,
            )
        ],
        open_questions=[],
        risks=[],
        crm_update=CRMUpdate(
            summary="Proposal requested", next_step="Prepare proposal"
        ),
        email=EmailDraft(subject="Follow-up", body=email_body),
        overall_confidence=0.9,
    )
