"""Behavioural verification of the hosted database, through PostgREST.

`supabase/tests/enforcement.sql` proves the same properties with direct SQL, but
that needs a psql connection. This script asserts the identical rules using the
same client the application uses, so it works against a hosted project with only
the server-side secret key.

It creates a small fixture, exercises the rules, and removes the fixture. It
deliberately never writes an audit event: `audit_events` is append-only, and a
workflow run that has one can never be deleted, so writing one here would leave
permanent residue. Audit immutability is checked separately against a real event
produced by the end-to-end run.

    cd backend && python ../evaluation/hosted_enforcement.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from uuid import UUID, uuid4

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from app.core.config import get_settings  # noqa: E402
from app.repositories.supabase import (  # noqa: E402
    SupabaseRepository,
    create_supabase_client,
)

failures: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}" + (f" — {detail}" if detail else ""))
    if not ok:
        failures.append(label)


async def must_fail(label: str, coro) -> None:
    try:
        await coro
        check(label, False, "the database allowed it")
    except Exception as exc:
        check(label, True, type(exc).__name__)


async def must_work(label: str, coro) -> None:
    try:
        await coro
        check(label, True)
    except Exception as exc:
        check(label, False, str(exc)[:90])


async def main() -> int:
    settings = get_settings()
    print(f"target: {settings.supabase_url}\n")
    repo = SupabaseRepository(await create_supabase_client())

    account = await repo.insert(
        "accounts",
        {"company_name": "enforcement-check", "contact_email": "enforce@example.com"},
    )
    meeting = await repo.insert(
        "meetings",
        {
            "account_id": account["id"],
            "meeting_date": "2026-09-05",
            "notes": "Sarah confirmed that we will prepare a commercial proposal.",
        },
    )
    wf = await repo.insert(
        "workflow_runs",
        {"meeting_id": meeting["id"], "status": "awaiting_approval", "model": "test"},
    )
    wf_id = UUID(str(wf["id"]))

    try:
        print("=== workflow transition trigger ===")
        await must_fail(
            "awaiting_approval -> completed rejected",
            repo.update("workflow_runs", wf_id, {"status": "completed"}),
        )
        await must_fail(
            "awaiting_approval -> executing rejected (approval gate)",
            repo.update("workflow_runs", wf_id, {"status": "executing"}),
        )
        await must_work(
            "awaiting_approval -> approved allowed",
            repo.update("workflow_runs", wf_id, {"status": "approved"}),
        )
        await must_work(
            "approved -> executing allowed",
            repo.update("workflow_runs", wf_id, {"status": "executing"}),
        )
        await must_work(
            "executing -> failed allowed",
            repo.update("workflow_runs", wf_id, {"status": "failed"}),
        )
        await must_work(
            "failed -> executing allowed (idempotent retry)",
            repo.update("workflow_runs", wf_id, {"status": "executing"}),
        )
        await must_work(
            "executing -> completed allowed",
            repo.update("workflow_runs", wf_id, {"status": "completed"}),
        )
        await must_fail(
            "completed is terminal",
            repo.update("workflow_runs", wf_id, {"status": "executing"}),
        )

        row = await repo.get_by_id("workflow_runs", wf_id)
        check("started_at set by trigger", bool(row.get("started_at")))
        check("completed_at set by trigger", bool(row.get("completed_at")))

        print("=== execution idempotency ===")
        key = f"{wf_id}:email_send"
        await must_work(
            "first operation claim accepted",
            repo.insert(
                "workflow_operations",
                {
                    "workflow_run_id": str(wf_id),
                    "operation_type": "email_send",
                    "idempotency_key": key,
                    "status": "succeeded",
                },
            ),
        )
        await must_fail(
            "duplicate idempotency key rejected",
            repo.insert(
                "workflow_operations",
                {
                    "workflow_run_id": str(wf_id),
                    "operation_type": "email_send",
                    "idempotency_key": key,
                    "status": "pending",
                },
            ),
        )
        await must_fail(
            "second operation of the same type rejected",
            repo.insert(
                "workflow_operations",
                {
                    "workflow_run_id": str(wf_id),
                    "operation_type": "email_send",
                    "idempotency_key": f"other-{uuid4()}",
                    "status": "pending",
                },
            ),
        )
        await must_fail(
            "unknown operation type rejected",
            repo.insert(
                "workflow_operations",
                {
                    "workflow_run_id": str(wf_id),
                    "operation_type": "delete_everything",
                    "idempotency_key": f"bad-{uuid4()}",
                    "status": "pending",
                },
            ),
        )

        print("=== inbound email deduplication ===")
        mid = f"enforce-{uuid4()}@example.com"
        await must_work(
            "first inbound message accepted",
            repo.insert(
                "email_messages",
                {"direction": "inbound", "message_id": mid, "from_address": "a@b.com"},
            ),
        )
        await must_fail(
            "duplicate inbound message_id rejected",
            repo.insert(
                "email_messages",
                {"direction": "inbound", "message_id": mid, "from_address": "a@b.com"},
            ),
        )
        await must_work(
            "same id in the other direction allowed",
            repo.insert(
                "email_messages",
                {
                    "direction": "outbound",
                    "message_id": mid,
                    "workflow_run_id": str(wf_id),
                    "to_address": "a@b.com",
                },
            ),
        )
        await must_fail(
            "outbound without a workflow rejected",
            repo.insert(
                "email_messages",
                {
                    "direction": "outbound",
                    "message_id": f"orphan-{uuid4()}@example.com",
                    "to_address": "a@b.com",
                },
            ),
        )

        print("=== task / account consistency ===")
        other = await repo.insert(
            "accounts", {"company_name": "other-account-enforcement"}
        )
        await must_fail(
            "task on the wrong account rejected",
            repo.insert(
                "tasks",
                {
                    "account_id": other["id"],
                    "workflow_run_id": str(wf_id),
                    "title": "wrong account",
                },
            ),
        )
        await must_work(
            "task on the correct account accepted",
            repo.insert(
                "tasks",
                {
                    "account_id": account["id"],
                    "workflow_run_id": str(wf_id),
                    "title": "right account",
                },
            ),
        )

        print("=== data validation constraints ===")
        await must_fail(
            "malformed contact email rejected",
            repo.insert(
                "accounts", {"company_name": "bad", "contact_email": "not-an-email"}
            ),
        )
        await must_fail(
            "notes below the minimum length rejected",
            repo.insert(
                "meetings",
                {
                    "account_id": account["id"],
                    "meeting_date": "2026-09-05",
                    "notes": "short",
                },
            ),
        )
        await must_fail(
            "confidence outside 0..1 rejected",
            repo.insert(
                "workflow_runs",
                {
                    "meeting_id": meeting["id"],
                    "status": "created",
                    "model": "m",
                    "overall_confidence": 1.5,
                },
            ),
        )
    finally:
        print("=== cleanup ===")
        for table, rid in (
            ("accounts", locals().get("other", {}).get("id")),
            ("meetings", meeting["id"]),
            ("accounts", account["id"]),
        ):
            if not rid:
                continue
            try:
                await repo.client.table(table).delete().eq("id", str(rid)).execute()
            except Exception as exc:
                print(f"  could not remove {table} {rid}: {type(exc).__name__}")
        leftover = await repo.find("workflow_runs", {"id": str(wf_id)})
        print(f"  fixture removed: {not leftover}")

    print()
    if failures:
        print(f"{len(failures)} FAILED: {failures}")
        return 1
    print("hosted enforcement: all checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
