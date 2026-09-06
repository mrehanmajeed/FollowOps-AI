"""IMAP parsing and end-to-end reply processing (TC11, TC12, TC13)."""

import email as email_module

from app.services.imap_client import parse_message
from app.services.reply_processor import ReplyProcessor, record_outbound

ACCOUNT_ID = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
WORKFLOW_ID = "11111111-1111-1111-1111-111111111111"


def raw(
    message_id="in-1@acme.test",
    sender="Sarah <sarah@acme.test>",
    subject="Re: Follow-up: proposal",
    in_reply_to="<out-1@followops.test>",
    extra="",
):
    headers = [
        f"Message-ID: <{message_id}>",
        f"From: {sender}",
        "To: ops@followops.test",
        f"Subject: {subject}",
        "Date: Sat, 06 Sep 2026 10:00:00 +0000",
    ]
    if in_reply_to:
        headers.append(f"In-Reply-To: {in_reply_to}")
    if extra:
        headers.append(extra)
    return email_module.message_from_string("\r\n".join(headers) + "\r\n\r\nbody")


class FakeImapClient:
    def __init__(self, messages):
        self.messages = messages
        self.calls = 0

    def fetch_recent(self, since=None):
        self.calls += 1
        return list(self.messages)


# -- header parsing ---------------------------------------------------------


def test_parse_extracts_threading_headers():
    parsed = parse_message(raw())
    assert parsed.message_id == "in-1@acme.test"
    assert parsed.from_address == "sarah@acme.test"
    assert parsed.in_reply_to == "out-1@followops.test"
    assert parsed.normalized_subject == "follow-up: proposal"


def test_parse_decodes_encoded_subjects():
    parsed = parse_message(raw(subject="=?utf-8?B?UmU6IFByb3Bvc2Fs?="))
    assert parsed.subject == "Re: Proposal"


def test_parse_detects_auto_submitted():
    parsed = parse_message(raw(extra="Auto-Submitted: auto-replied"))
    assert parsed.auto_submitted is True


def test_parse_rejects_a_message_without_a_message_id():
    message = email_module.message_from_string("From: x@y.test\r\n\r\nbody")
    assert parse_message(message) is None


def test_parse_survives_a_malformed_from_header():
    parsed = parse_message(raw(sender="not-an-address"))
    assert parsed is not None
    # Kept verbatim as data; with no "@" it can never match a contact address.
    assert "@" not in parsed.from_address


# -- processing -------------------------------------------------------------


async def seed_outbound(repository):
    await record_outbound(
        repository,
        message_id="out-1@followops.test",
        workflow_run_id=WORKFLOW_ID,
        account_id=ACCOUNT_ID,
        to_address="sarah@acme.test",
        subject="Follow-up: proposal",
    )
    repository.seed(
        "accounts",
        {
            "id": ACCOUNT_ID,
            "company_name": "Acme",
            "contact_email": "sarah@acme.test",
            "followups_paused": False,
        },
    )
    repository.seed(
        "workflow_runs",
        {"id": WORKFLOW_ID, "meeting_id": "m1", "status": "completed", "model": "x"},
    )


async def test_confirmed_reply_pauses_followups_and_audits(repository):
    """TC11."""
    await seed_outbound(repository)
    processor = ReplyProcessor(repository, FakeImapClient([parse_message(raw())]))

    summary = await processor.poll_once()

    assert summary.confirmed_replies == 1
    assert summary.paused_accounts == 1
    account = await repository.get_by_id("accounts", ACCOUNT_ID)
    assert account["followups_paused"] is True
    assert account["last_reply_at"]
    events = [e["event_type"] for e in repository.rows("audit_events")]
    assert "reply_detected" in events


async def test_unrelated_message_does_not_pause_anything(repository):
    """TC12."""
    await seed_outbound(repository)
    unrelated = parse_message(
        raw(
            message_id="spam-1@vendor.test",
            sender="news@vendor.test",
            subject="Weekly digest",
            in_reply_to=None,
        )
    )
    processor = ReplyProcessor(repository, FakeImapClient([unrelated]))

    summary = await processor.poll_once()

    assert summary.unrelated == 1
    assert summary.paused_accounts == 0
    account = await repository.get_by_id("accounts", ACCOUNT_ID)
    assert account["followups_paused"] is False


async def test_processing_the_same_message_twice_creates_one_event(repository):
    """TC13: idempotent IMAP processing."""
    await seed_outbound(repository)
    client = FakeImapClient([parse_message(raw())])
    processor = ReplyProcessor(repository, client)

    first = await processor.poll_once()
    second = await processor.poll_once()

    assert first.processed == 1
    assert second.processed == 0
    assert second.duplicates == 1
    inbound = [
        m for m in repository.rows("email_messages") if m["direction"] == "inbound"
    ]
    assert len(inbound) == 1
    reply_events = [
        e
        for e in repository.rows("audit_events")
        if e["event_type"] == "reply_detected"
    ]
    assert len(reply_events) == 1


async def test_possible_reply_is_recorded_but_does_not_pause(repository):
    await seed_outbound(repository)
    weak = parse_message(
        raw(
            message_id="in-2@acme.test",
            subject="Unrelated question about invoicing",
            in_reply_to=None,
        )
    )
    processor = ReplyProcessor(repository, FakeImapClient([weak]))

    summary = await processor.poll_once()

    assert summary.possible_replies == 1
    assert summary.paused_accounts == 0
    account = await repository.get_by_id("accounts", ACCOUNT_ID)
    assert account["followups_paused"] is False


async def test_imap_outage_is_reported_without_raising(repository):
    from app.services.imap_client import ImapError

    class BrokenClient:
        def fetch_recent(self, since=None):
            raise ImapError("Zoho unavailable")

    summary = await ReplyProcessor(repository, BrokenClient()).poll_once()

    assert summary.fetched == 0
    assert summary.errors and "Zoho unavailable" in summary.errors[0]
