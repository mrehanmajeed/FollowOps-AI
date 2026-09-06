"""Reply-detection decision table (evaluation cases TC11 and TC12)."""

from app.services.correlation import (
    CONFIRMED_REPLY,
    POSSIBLE_REPLY,
    UNRELATED,
    InboundMessage,
    OutboundRef,
    correlate,
    normalize_subject,
    parse_references,
)

OUTBOUND = [
    OutboundRef(
        message_id="out-1@followops.test",
        workflow_run_id="11111111-1111-1111-1111-111111111111",
        account_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        to_address="sarah@acme.test",
        subject="Follow-up: proposal and next steps",
    )
]
CONTACTS = {"sarah@acme.test": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"}


def inbound(**kwargs) -> InboundMessage:
    return InboundMessage(
        **{
            "message_id": "in-1@acme.test",
            "from_address": "sarah@acme.test",
            "subject": "Re: Follow-up: proposal and next steps",
            **kwargs,
        }
    )


# -- subject / header normalization ---------------------------------------


def test_subject_normalization_strips_stacked_prefixes():
    assert normalize_subject("RE: Fwd: Re[2]: Proposal") == "proposal"
    assert normalize_subject("AW: Proposal") == "proposal"
    assert normalize_subject(None) == ""


def test_reference_parsing_handles_multiple_ids():
    parsed = parse_references("<a@x.test> <b@y.test>")
    assert parsed == ("a@x.test", "b@y.test")


# -- TC11: confirmed reply --------------------------------------------------


def test_in_reply_to_is_a_confirmed_reply():
    result = correlate(
        inbound(in_reply_to="out-1@followops.test"), OUTBOUND, CONTACTS
    )
    assert result.correlation == CONFIRMED_REPLY
    assert result.workflow_run_id == OUTBOUND[0].workflow_run_id
    assert "in_reply_to" in result.evidence


def test_references_chain_is_a_confirmed_reply():
    result = correlate(
        inbound(references=("other@x.test", "out-1@followops.test")),
        OUTBOUND,
        CONTACTS,
    )
    assert result.correlation == CONFIRMED_REPLY
    assert result.matched_message_id == "out-1@followops.test"


def test_contact_plus_subject_match_is_confirmed_without_thread_headers():
    result = correlate(inbound(), OUTBOUND, CONTACTS)
    assert result.correlation == CONFIRMED_REPLY
    assert set(result.evidence) == {"sender_is_contact", "subject_match"}


# -- weaker evidence --------------------------------------------------------


def test_known_contact_on_an_unrelated_subject_is_only_possible():
    result = correlate(
        inbound(subject="Invoice for August"), OUTBOUND, CONTACTS
    )
    assert result.correlation == POSSIBLE_REPLY
    assert result.account_id == CONTACTS["sarah@acme.test"]


def test_matching_subject_from_an_unknown_sender_is_only_possible():
    result = correlate(
        inbound(from_address="colleague@acme.test"), OUTBOUND, CONTACTS
    )
    assert result.correlation == POSSIBLE_REPLY


def test_auto_responder_threading_to_our_email_is_not_a_confirmed_reply():
    result = correlate(
        inbound(in_reply_to="out-1@followops.test", auto_submitted=True),
        OUTBOUND,
        CONTACTS,
    )
    assert result.correlation == POSSIBLE_REPLY
    assert "automated_sender" in result.evidence


def test_bounce_message_is_not_a_confirmed_reply():
    result = correlate(
        inbound(
            from_address="mailer-daemon@acme.test",
            in_reply_to="out-1@followops.test",
        ),
        OUTBOUND,
        CONTACTS,
    )
    assert result.correlation == POSSIBLE_REPLY


def test_our_own_outbound_message_echoed_back_is_not_a_reply():
    """Found in the live end-to-end run: a self-addressed send lands in INBOX.

    It is from the contact and its subject matches, so without an explicit
    guard it scores as a confirmed reply and pauses the sequence.
    """
    echo = inbound(
        message_id="out-1@followops.test",
        subject="Follow-up: proposal and next steps",
        in_reply_to="",
    )
    result = correlate(echo, OUTBOUND, CONTACTS)
    assert result.correlation == UNRELATED
    assert "own_outbound_message" in result.evidence


def test_a_genuine_reply_to_our_message_is_still_confirmed():
    """The guard keys on the inbound id, not on the referenced id."""
    result = correlate(
        inbound(message_id="in-9@acme.test", in_reply_to="out-1@followops.test"),
        OUTBOUND,
        CONTACTS,
    )
    assert result.correlation == CONFIRMED_REPLY


# -- TC12: unrelated --------------------------------------------------------


def test_unrelated_message_is_not_correlated():
    result = correlate(
        inbound(from_address="newsletter@vendor.test", subject="Weekly digest"),
        OUTBOUND,
        CONTACTS,
    )
    assert result.correlation == UNRELATED
    assert result.workflow_run_id is None
    assert result.account_id is None


def test_no_outbound_history_means_nothing_correlates():
    result = correlate(inbound(), [], {})
    assert result.correlation == UNRELATED


def test_every_result_explains_itself():
    for message in (inbound(), inbound(from_address="x@y.test")):
        assert correlate(message, OUTBOUND, CONTACTS).reason
