"""Deterministic validation of Gemini output.

Structured output guarantees shape, not truth. This module is the code half of
"LLM proposes, code validates": every decision and action must quote evidence
that actually occurs in the meeting notes, owners and deadlines must be
supported, and the client-facing email must not introduce facts of its own.

Ungrounded content is *removed* from the package rather than silently trusted,
and the removal is recorded as a critical issue so a human sees what the model
tried to assert.
"""

import re
from datetime import date

from pydantic import BaseModel, Field

from app.schemas.followup import ActionItem, EvidenceItem, FollowupExtraction

CRITICAL = "critical"
WARNING = "warning"

# Cues that legitimately justify a resolved relative deadline.
_RELATIVE_DATE_CUES = (
    "today",
    "tomorrow",
    "tonight",
    "next week",
    "this week",
    "next month",
    "end of week",
    "end of the week",
    "end of month",
    "eow",
    "eom",
    "monday",
    "tuesday",
    "wednesday",
    "thursday",
    "friday",
    "saturday",
    "sunday",
    "in a week",
    "in two weeks",
    "within a week",
    "next sprint",
)

_MONTHS = (
    "january|february|march|april|may|june|july|august|september|october"
    "|november|december|jan|feb|mar|apr|jun|jul|aug|sept|sep|oct|nov|dec"
)

_DATE_PATTERNS = (
    re.compile(r"\b\d{4}-\d{2}-\d{2}\b"),
    re.compile(rf"\b(?:{_MONTHS})\s+\d{{1,2}}(?:st|nd|rd|th)?\b", re.IGNORECASE),
    re.compile(rf"\b\d{{1,2}}(?:st|nd|rd|th)?\s+(?:{_MONTHS})\b", re.IGNORECASE),
    re.compile(r"\b\d{1,2}/\d{1,2}(?:/\d{2,4})?\b"),
)

# Meeting notes are untrusted. These markers do not prove an attack, but the
# operator should be told the input tried to issue instructions.
_INJECTION_MARKERS = (
    "ignore previous instructions",
    "ignore the previous instructions",
    "ignore all previous",
    "disregard previous instructions",
    "disregard the above",
    "system prompt",
    "you are now",
    "new instructions:",
    "send this email immediately",
    "send the email now",
    "do not require approval",
    "skip approval",
    "bypass approval",
    "act as",
    "override your",
)

_STOPWORDS = frozenset(
    """a an and are as at be by for from has have in is it its of on or that the
    to was were will with we you they this their our""".split()
)


def _normalize(text: str) -> str:
    """Lowercase, strip punctuation noise, collapse whitespace.

    Evidence is compared as a substring of the notes, so quoting differences
    (smart quotes, trailing periods, line wrapping) must not count as
    fabrication.
    """
    lowered = text.lower().replace("’", "'").replace("‘", "'")
    lowered = lowered.replace("“", '"').replace("”", '"')
    lowered = re.sub(r"[^a-z0-9'\"@./:-]+", " ", lowered)
    return " ".join(lowered.split())


def _tokens(text: str) -> set[str]:
    return {t for t in _normalize(text).split() if len(t) > 2 and t not in _STOPWORDS}


class ValidationIssue(BaseModel):
    severity: str
    code: str
    message: str
    item: str | None = None


class ValidationReport(BaseModel):
    issues: list[ValidationIssue] = Field(default_factory=list)

    @property
    def critical(self) -> list[ValidationIssue]:
        return [i for i in self.issues if i.severity == CRITICAL]

    @property
    def ok(self) -> bool:
        return not self.critical

    def add(
        self, severity: str, code: str, message: str, item: str | None = None
    ) -> None:
        self.issues.append(
            ValidationIssue(severity=severity, code=code, message=message, item=item)
        )


class FollowupValidator:
    """Validates and *sanitizes* an extraction against its source notes."""

    def validate(
        self,
        extraction: FollowupExtraction,
        source_notes: str,
        meeting_date: date | None = None,
    ) -> tuple[FollowupExtraction, ValidationReport]:
        report = ValidationReport()
        source = _normalize(source_notes)
        clean = extraction.model_copy(deep=True)

        self._flag_injection(source_notes, report)

        clean.decisions = self._filter_items(
            clean.decisions, source, "decision", report
        )
        clean.client_actions = self._filter_items(
            clean.client_actions, source, "client action", report
        )
        clean.internal_actions = self._filter_items(
            clean.internal_actions, source, "internal action", report
        )

        for item in (*clean.client_actions, *clean.internal_actions):
            self._check_owner(item, source, report)
            self._check_due_date(item, source, meeting_date, report)

        self._check_crm(clean, source, report)
        self._check_email(clean, source, meeting_date, report)

        return clean, report

    # -- grounding ---------------------------------------------------------

    def _filter_items[T: EvidenceItem](
        self,
        items: list[T],
        source: str,
        label: str,
        report: ValidationReport,
    ) -> list[T]:
        kept: list[T] = []
        for item in items:
            evidence = _normalize(item.evidence)
            if evidence and evidence in source:
                kept.append(item)
                continue
            report.add(
                CRITICAL,
                "ungrounded_evidence",
                f"Removed {label} because its quoted evidence does not appear "
                f"in the meeting notes.",
                item.text,
            )
        return kept

    @staticmethod
    def _check_owner(
        item: ActionItem, source: str, report: ValidationReport
    ) -> None:
        if not item.owner:
            return
        owner = _normalize(item.owner)
        # An owner is supported if the whole string, or any name part of it,
        # appears in the notes. "the client"/"we" style owners are generic.
        parts = [p for p in owner.split() if len(p) > 2]
        if owner in source or any(p in source for p in parts):
            return
        report.add(
            WARNING,
            "unsupported_owner",
            f"Owner '{item.owner}' is not named in the meeting notes; cleared "
            f"for human review.",
            item.text,
        )
        item.owner = None

    @staticmethod
    def _check_due_date(
        item: ActionItem,
        source: str,
        meeting_date: date | None,
        report: ValidationReport,
    ) -> None:
        if not item.due_date:
            return
        iso = item.due_date.isoformat()
        evidence = _normalize(item.evidence)
        if iso in source or str(item.due_date.day) in evidence:
            return
        if any(cue in evidence for cue in _RELATIVE_DATE_CUES):
            return
        if meeting_date and item.due_date < meeting_date:
            report.add(
                CRITICAL,
                "deadline_before_meeting",
                f"Deadline {iso} precedes the meeting date; cleared.",
                item.text,
            )
            item.due_date = None
            return
        report.add(
            WARNING,
            "unverifiable_deadline",
            f"Deadline {iso} is not traceable to the notes; cleared for human "
            f"review.",
            item.text,
        )
        item.due_date = None

    # -- downstream content ------------------------------------------------

    @staticmethod
    def _check_crm(
        extraction: FollowupExtraction, source: str, report: ValidationReport
    ) -> None:
        next_step = extraction.crm_update.next_step
        if not next_step:
            return
        grounded = _tokens(source)
        overlap = _tokens(next_step) & grounded
        if not overlap:
            report.add(
                WARNING,
                "unsupported_crm_next_step",
                "Proposed CRM next step shares no vocabulary with the meeting "
                "notes.",
                next_step,
            )

    @staticmethod
    def _check_email(
        extraction: FollowupExtraction,
        source: str,
        meeting_date: date | None,
        report: ValidationReport,
    ) -> None:
        body = extraction.email.body
        normalized_body = _normalize(body)

        # Any date the email states to the customer is a commitment. It must be
        # traceable to the notes or to a validated action deadline. Comparison
        # is by calendar date, not by string: an email may legitimately write
        # "September 6th" for a due date stored as 2026-09-06.
        allowed = set(_due_dates(extraction))
        year = meeting_date.year if meeting_date else None
        allowed |= _dates_in(source, year)
        # The meeting date is trusted workflow input, not something the model
        # invented, so an email may legitimately say "further to our call on
        # <date>". Found by evaluation case TC09, where a correct email was
        # blocked for restating the date of the meeting it summarises.
        if meeting_date:
            allowed.add(meeting_date)

        for pattern in _DATE_PATTERNS:
            for match in pattern.findall(body):
                token = _normalize(match)
                if token in source:
                    continue
                parsed = _parse_date_token(token, year)
                if parsed and parsed in allowed:
                    continue
                if parsed is None and any(
                    token in _normalize(d.isoformat()) for d in allowed
                ):
                    continue
                report.add(
                    CRITICAL,
                    "email_unsupported_date",
                    f"Email states date '{match}' which is not supported by the "
                    f"meeting notes.",
                    match,
                )

        grounded_items = [
            *extraction.decisions,
            *extraction.client_actions,
        ]
        if not grounded_items:
            return
        # Token overlap, not exact substring: a well-written email paraphrases.
        if not any(
            len(_tokens(item.text) & set(normalized_body.split())) >= 2
            for item in grounded_items
        ):
            report.add(
                WARNING,
                "email_not_traceable",
                "Email body does not visibly reference any validated decision "
                "or action.",
            )

    @staticmethod
    def _flag_injection(source_notes: str, report: ValidationReport) -> None:
        normalized = _normalize(source_notes)
        hits = [m for m in _INJECTION_MARKERS if m in normalized]
        if hits:
            report.add(
                WARNING,
                "prompt_injection_detected",
                "Meeting notes contain instruction-like text. It was treated as "
                "data; review the generated package before approving.",
                hits[0],
            )


_MONTH_NUMBERS = {
    "jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3,
    "apr": 4, "april": 4, "may": 5, "jun": 6, "june": 6, "jul": 7, "july": 7,
    "aug": 8, "august": 8, "sep": 9, "sept": 9, "september": 9, "oct": 10,
    "october": 10, "nov": 11, "november": 11, "dec": 12, "december": 12,
}


def _parse_date_token(token: str, default_year: int | None) -> date | None:
    """Parse a date as written in prose. Returns None when it is not a date."""
    iso = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", token)
    if iso:
        return _safe_date(*(int(g) for g in iso.groups()))

    if default_year is None:
        return None

    month_first = re.fullmatch(r"([a-z]+)\s+(\d{1,2})(?:st|nd|rd|th)?", token)
    if month_first and month_first.group(1) in _MONTH_NUMBERS:
        return _safe_date(
            default_year,
            _MONTH_NUMBERS[month_first.group(1)],
            int(month_first.group(2)),
        )

    day_first = re.fullmatch(r"(\d{1,2})(?:st|nd|rd|th)?\s+([a-z]+)", token)
    if day_first and day_first.group(2) in _MONTH_NUMBERS:
        return _safe_date(
            default_year,
            _MONTH_NUMBERS[day_first.group(2)],
            int(day_first.group(1)),
        )
    return None


def _safe_date(year: int, month: int, day: int) -> date | None:
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _dates_in(text: str, default_year: int | None) -> set[date]:
    """Every calendar date the source text actually states."""
    found: set[date] = set()
    for pattern in _DATE_PATTERNS:
        for match in pattern.findall(text):
            parsed = _parse_date_token(_normalize(match), default_year)
            if parsed:
                found.add(parsed)
    return found


def _due_dates(extraction: FollowupExtraction) -> list[date]:
    dates = [
        item.due_date
        for item in (*extraction.client_actions, *extraction.internal_actions)
        if item.due_date
    ]
    if extraction.crm_update.next_followup_date:
        dates.append(extraction.crm_update.next_followup_date)
    return dates
