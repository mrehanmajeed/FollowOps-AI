"""Correlate an inbound mailbox message to a FollowOps outbound email.

Pure logic, no IMAP and no database, so the decision table is directly
testable. `sender == contact_email` alone is not sufficient evidence: a shared
mailbox receives newsletters, bounces and unrelated threads from the same
people we email.

Evidence is layered, strongest first:

    1. In-Reply-To matches one of our outbound Message-IDs   -> confirmed
    2. References contains one of our outbound Message-IDs   -> confirmed
    3. Sender is the account contact AND the normalized
       subject matches one of our outbound subjects          -> confirmed
    4. Sender is the account contact, no thread evidence     -> possible
    5. Subject matches an outbound thread, unknown sender    -> possible
    6. no evidence                                           -> unrelated

Automated messages (vacation responders, bounces, bulk mail) are never
confirmed: a mail server acknowledging delivery is not a prospect replying.
Only `confirmed_reply` pauses follow-ups; `possible_reply` is queued for human
review, because wrongly pausing is cheaper than wrongly emailing a prospect who
already answered, but silently pausing on weak evidence hides work.
"""

import re
from dataclasses import dataclass, field
from datetime import datetime

CONFIRMED_REPLY = "confirmed_reply"
POSSIBLE_REPLY = "possible_reply"
UNRELATED = "unrelated"

_SUBJECT_PREFIX = re.compile(
    r"^\s*(?:(?:re|aw|fwd?|sv|vs|antw|rif)\s*(?:\[\d+\])?\s*:\s*)+",
    re.IGNORECASE,
)

_AUTO_SENDER_MARKERS = (
    "mailer-daemon@",
    "postmaster@",
    "no-reply@",
    "noreply@",
    "donotreply@",
    "bounce",
)


def normalize_subject(subject: str | None) -> str:
    """Strip any depth of Re:/Fwd: prefixes and collapse whitespace."""
    if not subject:
        return ""
    stripped = _SUBJECT_PREFIX.sub("", subject)
    return " ".join(stripped.lower().split())


def normalize_message_id(value: str | None) -> str:
    """Message-IDs are compared bare, without angle brackets or whitespace."""
    if not value:
        return ""
    return value.strip().strip("<>").strip().lower()


def parse_references(value: str | None) -> tuple[str, ...]:
    """Split a References/In-Reply-To header into normalized Message-IDs."""
    if not value:
        return ()
    ids = re.findall(r"<[^<>]+>", value)
    if not ids:
        ids = value.split()
    return tuple(dict.fromkeys(normalize_message_id(i) for i in ids if i.strip()))


@dataclass(frozen=True)
class InboundMessage:
    message_id: str
    from_address: str
    subject: str = ""
    in_reply_to: str = ""
    references: tuple[str, ...] = ()
    to_addresses: tuple[str, ...] = ()
    received_at: datetime | None = None
    auto_submitted: bool = False
    mailbox: str = "INBOX"
    imap_uid: str = ""

    @property
    def normalized_subject(self) -> str:
        return normalize_subject(self.subject)

    @property
    def looks_automated(self) -> bool:
        sender = self.from_address.lower()
        return self.auto_submitted or any(
            marker in sender for marker in _AUTO_SENDER_MARKERS
        )


@dataclass(frozen=True)
class OutboundRef:
    """One outbound email we previously sent, as correlation evidence."""

    message_id: str
    workflow_run_id: str
    account_id: str
    to_address: str
    subject: str = ""

    @property
    def normalized_subject(self) -> str:
        return normalize_subject(self.subject)


@dataclass
class CorrelationResult:
    correlation: str
    reason: str
    workflow_run_id: str | None = None
    account_id: str | None = None
    matched_message_id: str | None = None
    evidence: list[str] = field(default_factory=list)

    @property
    def is_confirmed(self) -> bool:
        return self.correlation == CONFIRMED_REPLY


def correlate(
    inbound: InboundMessage,
    outbound: list[OutboundRef],
    contacts: dict[str, str] | None = None,
) -> CorrelationResult:
    """Decide whether `inbound` replies to one of our `outbound` emails.

    `contacts` maps a lowercased contact email address to its account id, so a
    message from a known contact is recognized even with no thread headers.
    """
    contacts = {k.lower(): v for k, v in (contacts or {}).items()}
    sender = inbound.from_address.lower().strip()
    by_message_id = {o.message_id: o for o in outbound if o.message_id}

    # Our own outbound message can come back into the mailbox: a self-addressed
    # test, a Bcc to self, a mailing list reflecting it, or a provider that
    # files sent mail in the inbox. It carries a Message-ID we minted, so it is
    # never a reply — and without this guard it matches on "sender is the
    # contact" plus "subject matches" and would wrongly pause the sequence.
    if inbound.message_id in by_message_id:
        own = by_message_id[inbound.message_id]
        return CorrelationResult(
            UNRELATED,
            "This is our own outbound message returned to the mailbox, not a "
            "reply to it.",
            evidence=["own_outbound_message"],
            matched_message_id=own.message_id,
        )

    # 1 & 2 — RFC 5322 threading headers are the strongest available evidence.
    thread_ids = [
        normalize_message_id(inbound.in_reply_to),
        *inbound.references,
    ]
    for candidate in thread_ids:
        match = by_message_id.get(candidate)
        if not match:
            continue
        if inbound.looks_automated:
            return CorrelationResult(
                POSSIBLE_REPLY,
                "Threads to one of our emails but is an automated message "
                "(auto-responder or delivery notification), so it is not "
                "treated as a prospect reply.",
                match.workflow_run_id,
                match.account_id,
                match.message_id,
                ["thread_header", "automated_sender"],
            )
        header = "in_reply_to" if candidate == thread_ids[0] else "references"
        return CorrelationResult(
            CONFIRMED_REPLY,
            f"{header} header matches outbound Message-ID {match.message_id}.",
            match.workflow_run_id,
            match.account_id,
            match.message_id,
            [header],
        )

    sender_account = contacts.get(sender)
    subject_matches = [
        o
        for o in outbound
        if o.normalized_subject
        and o.normalized_subject == inbound.normalized_subject
    ]

    # 3 — no thread headers, but the right person answering the right subject.
    if sender_account:
        same_account = [o for o in subject_matches if o.account_id == sender_account]
        if same_account and not inbound.looks_automated:
            match = same_account[-1]
            return CorrelationResult(
                CONFIRMED_REPLY,
                "Sender is the account contact and the normalized subject "
                f"matches outbound email {match.message_id}.",
                match.workflow_run_id,
                match.account_id,
                match.message_id,
                ["sender_is_contact", "subject_match"],
            )

        # 4 — known contact, but nothing ties it to a specific outbound email.
        addressed = [o for o in outbound if o.account_id == sender_account]
        match = addressed[-1] if addressed else None
        return CorrelationResult(
            POSSIBLE_REPLY,
            "Sender is a known account contact but no thread or subject "
            "evidence links the message to a specific outbound email.",
            match.workflow_run_id if match else None,
            sender_account,
            match.message_id if match else None,
            ["sender_is_contact"],
        )

    # 5 — subject matches a thread we started, but from someone we do not know
    # (a forwarded thread, a colleague looped in).
    if subject_matches:
        match = subject_matches[-1]
        return CorrelationResult(
            POSSIBLE_REPLY,
            "Subject matches an outbound thread but the sender is not a known "
            "account contact.",
            match.workflow_run_id,
            match.account_id,
            match.message_id,
            ["subject_match"],
        )

    # 6 — nothing at all.
    return CorrelationResult(
        UNRELATED,
        "No thread header, contact or subject evidence links this message to a "
        "FollowOps workflow.",
        evidence=[],
    )
