"""FollowOps AI evaluation runner.

Two kinds of case, deliberately separated because they are evidence of
different things:

* Extraction cases (TC01-TC10) sample a probabilistic model. They are scored
  against expected actions, decisions, owners, deadlines and forbidden claims.
  They need a real GEMINI_API_KEY.
* System cases (TC11-TC15) assert deterministic behaviour — reply detection,
  idempotency, the approval gate. They are executed as backend tests, so their
  result is pass/fail rather than a percentage.

Usage:

    python evaluation/run_eval.py                # everything
    python evaluation/run_eval.py --offline      # system cases only, no API key
    python evaluation/run_eval.py --cases TC01,TC08

Nothing here fabricates a number. A metric that could not be measured is
reported as null and labelled unmeasured.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import statistics
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
BACKEND = ROOT / "backend"
CASES_DIR = Path(__file__).parent / "cases"
RESULTS_DIR = Path(__file__).parent / "results"

sys.path.insert(0, str(BACKEND))

_STOPWORDS = frozenset(
    """a an and are as at be by for from has have in is it its of on or that the to
    was were will with we you they this their our them us""".split()
)

# An expected item counts as extracted when this share of its meaningful words
# appears in the extracted text. Chosen so paraphrase passes and a different
# commitment does not.
MATCH_THRESHOLD = 0.6


def tokens(text: str) -> set[str]:
    words = re.findall(r"[a-z0-9]+", (text or "").lower())
    return {w for w in words if len(w) > 2 and w not in _STOPWORDS}


def matches(expected: str, candidate: str) -> bool:
    expected_tokens = tokens(expected)
    if not expected_tokens:
        return False
    overlap = expected_tokens & tokens(candidate)
    return len(overlap) / len(expected_tokens) >= MATCH_THRESHOLD


def find_match(expected: str, items: list) -> Any | None:
    for item in items:
        if matches(expected, getattr(item, "text", str(item))):
            return item
    return None


@dataclass
class CaseResult:
    case_id: str
    description: str
    passed: bool = False
    skipped: bool = False
    error: str | None = None
    latency_ms: int | None = None
    action_recall: float | None = None
    decision_precision: float | None = None
    owner_score: float | None = None
    deadline_score: float | None = None
    forbidden_claims_found: list[str] = field(default_factory=list)
    unsupported_after_validation: int = 0
    validation_issues: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return self.__dict__


# ---------------------------------------------------------------------------
# Extraction cases
# ---------------------------------------------------------------------------


def load_extraction_cases(only: set[str] | None) -> list[dict[str, Any]]:
    cases = []
    for path in sorted(CASES_DIR.glob("TC*.json")):
        case = json.loads(path.read_text(encoding="utf-8"))
        if only and case["case_id"] not in only:
            continue
        cases.append(case)
    return cases


async def run_extraction_case(case: dict[str, Any], service, validator) -> CaseResult:
    result = CaseResult(case["case_id"], case["description"])
    meeting_date = date.fromisoformat(case["meeting_date"])

    started = time.perf_counter()
    try:
        raw = await service.extract_followup(
            account_context=case.get("account_context", ""),
            meeting_notes=case["notes"],
            meeting_date=meeting_date,
        )
    except Exception as exc:
        result.error = f"{type(exc).__name__}: {exc}"
        # Never report an unreachable provider as a failed safety case.
        result.skipped = _is_unmeasurable(result.error)
        return result
    result.latency_ms = int((time.perf_counter() - started) * 1000)

    # Score the package the operator would actually see: post-validation.
    extraction, report = validator.validate(raw, case["notes"], meeting_date)
    result.validation_issues = [f"{i.severity}:{i.code}" for i in report.issues]
    result.unsupported_after_validation = len(report.critical)

    actions = [*extraction.client_actions, *extraction.internal_actions]

    expected_actions = case.get("expected_actions", [])
    if expected_actions:
        hits = sum(1 for e in expected_actions if find_match(e, actions))
        result.action_recall = hits / len(expected_actions)
    elif not actions:
        result.action_recall = 1.0
    else:
        # Nothing was expected but something was produced: recall is undefined,
        # precision is what matters. Recorded as a note, not a fake 1.0.
        result.action_recall = None
        result.notes.append(f"no expected actions, model produced {len(actions)}")

    expected_decisions = case.get("expected_decisions", [])
    if extraction.decisions:
        correct = sum(
            1
            for d in extraction.decisions
            if any(matches(e, d.text) for e in expected_decisions)
        )
        result.decision_precision = correct / len(extraction.decisions)
    else:
        result.decision_precision = 1.0 if not expected_decisions else 0.0

    result.owner_score = score_owners(case, actions, result)
    result.deadline_score = score_deadlines(case, actions, result)

    # Two scopes, because "must not appear anywhere" and "must not be said to
    # the customer" are different assertions. A case that requires a conflict to
    # be *surfaced* must be able to name the thing it is flagging internally,
    # while still being forbidden from asserting it in the client-facing email.
    haystack = build_haystack(extraction)
    result.forbidden_claims_found = [
        claim
        for claim in case.get("forbidden_claims", [])
        if claim.lower() in haystack
    ]
    email_text = f"{extraction.email.subject} {extraction.email.body}".lower()
    result.forbidden_claims_found += [
        claim
        for claim in case.get("forbidden_in_email", [])
        if claim.lower() in email_text
    ]

    if case.get("expect_open_questions") and not (
        extraction.open_questions or extraction.risks
    ):
        result.notes.append("expected an open question or risk, got none")

    result.passed = (
        not result.forbidden_claims_found
        and result.unsupported_after_validation == 0
        and (result.action_recall is None or result.action_recall >= 0.9)
        and (result.decision_precision is None or result.decision_precision >= 0.9)
        and (result.owner_score is None or result.owner_score == 1.0)
        and (result.deadline_score is None or result.deadline_score == 1.0)
        and not result.notes
    )
    return result


# A case is *unmeasured*, not failed, when something prevented the model from
# being sampled at all. A 429 measures the billing plan and a DNS failure
# measures the network; neither says anything about extraction quality, and
# recording either as a failure would misreport the model. Deliberately narrow:
# only provider-availability and transport problems qualify, so a genuine bad
# response still counts as a failure.
_UNMEASURABLE_MARKERS = (
    # provider refused to serve the request
    "resource_exhausted",
    "429",
    "quota",
    "rate limit",
    "503",
    "unavailable",
    # never reached the provider
    "connecterror",
    "getaddrinfo",
    "name resolution",
    "connection refused",
    "connection reset",
    "connection aborted",
    "ssl",
    "timeout",
)


def _is_unmeasurable(message: str) -> bool:
    """Did the run fail to obtain a sample, rather than obtain a bad one?"""
    lowered = message.lower()
    return any(marker in lowered for marker in _UNMEASURABLE_MARKERS)


def score_owners(case, actions, result) -> float | None:
    checks: list[bool] = []
    for fragment, expected_owner in case.get("expected_owners", {}).items():
        item = find_match(fragment, actions)
        ok = bool(item and item.owner and expected_owner.lower() in item.owner.lower())
        checks.append(ok)
        if not ok:
            found = getattr(item, "owner", "<action not extracted>")
            result.notes.append(
                f"owner for '{fragment}': expected {expected_owner}, got {found}"
            )
    for fragment in case.get("must_be_null_owner", []):
        item = find_match(fragment, actions)
        ok = item is None or item.owner is None
        checks.append(ok)
        if not ok:
            result.notes.append(
                f"owner for '{fragment}' should be null, got {item.owner}"
            )
    return sum(checks) / len(checks) if checks else None


def score_deadlines(case, actions, result) -> float | None:
    checks: list[bool] = []
    for fragment, expected in case.get("expected_due_dates", {}).items():
        item = find_match(fragment, actions)
        ok = bool(item and item.due_date and item.due_date.isoformat() == expected)
        checks.append(ok)
        if not ok:
            found = getattr(item, "due_date", "<action not extracted>")
            result.notes.append(
                f"due date for '{fragment}': expected {expected}, got {found}"
            )
    for fragment in case.get("must_be_null_due_date", []):
        item = find_match(fragment, actions)
        ok = item is None or item.due_date is None
        checks.append(ok)
        if not ok:
            result.notes.append(
                f"due date for '{fragment}' should be null, got {item.due_date}"
            )
    return sum(checks) / len(checks) if checks else None


def build_haystack(extraction) -> str:
    parts = [
        extraction.meeting_summary,
        extraction.email.subject,
        extraction.email.body,
        extraction.crm_update.summary,
        extraction.crm_update.next_step or "",
        *[d.text for d in extraction.decisions],
        *[a.text for a in extraction.client_actions],
        *[a.text for a in extraction.internal_actions],
    ]
    return " ".join(parts).lower()


async def run_extraction_suite(
    only: set[str] | None, delay_seconds: float = 7.0
) -> list[CaseResult]:
    cases = load_extraction_cases(only)
    if not cases:
        return []

    from google import genai

    from app.core.config import get_settings
    from app.services.ai_service import GeminiAIService
    from app.services.validator import FollowupValidator

    settings = get_settings()
    client = genai.Client(api_key=settings.gemini_api_key.get_secret_value())
    service = GeminiAIService(client, settings)
    validator = FollowupValidator()

    results = []
    for index, case in enumerate(cases):
        # Pace the suite: free-tier Gemini quotas are per minute, and a burst of
        # ten calls trips them in a way that looks like a model failure.
        if index and delay_seconds:
            await asyncio.sleep(delay_seconds)
        print(f"  {case['case_id']} ... ", end="", flush=True)
        result = await run_extraction_case(case, service, validator)
        detail = result.error or "; ".join(result.notes) or "metric below target"
        if result.skipped:
            print(f"SKIPPED (could not sample the model: {detail[:70]})")
        else:
            print("PASS" if result.passed else f"FAIL ({detail})")
        results.append(result)
    return results


# ---------------------------------------------------------------------------
# System cases
# ---------------------------------------------------------------------------


def run_system_suite(only: set[str] | None) -> list[CaseResult]:
    path = CASES_DIR / "system_cases.json"
    if not path.exists():
        return []
    results = []
    for case in json.loads(path.read_text(encoding="utf-8")):
        if only and case["case_id"] not in only:
            continue
        print(f"  {case['case_id']} ... ", end="", flush=True)
        completed = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", *case["tests"]],
            cwd=BACKEND,
            capture_output=True,
            text=True,
        )
        result = CaseResult(case["case_id"], case["description"])
        result.passed = completed.returncode == 0
        if not result.passed:
            result.error = completed.stdout.strip().splitlines()[-1][:300]
        print("PASS" if result.passed else f"FAIL ({result.error})")
        results.append(result)
    return results


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


def aggregate(extraction: list[CaseResult], system: list[CaseResult]) -> dict[str, Any]:
    def mean(values):
        values = [v for v in values if v is not None]
        return round(sum(values) / len(values), 4) if values else None

    # Cases the provider refused to serve are unmeasured; averaging over them
    # would report a quota problem as a quality result.
    measured = [r for r in extraction if not r.skipped]
    skipped = [r for r in extraction if r.skipped]

    latencies = [r.latency_ms for r in measured if r.latency_ms is not None]
    forbidden = sum(len(r.forbidden_claims_found) for r in measured)
    unsupported = sum(r.unsupported_after_validation for r in measured)
    extraction = measured

    return {
        "extraction_cases_measured": len(measured),
        "extraction_cases_passed": sum(1 for r in measured if r.passed),
        "extraction_cases_unmeasured": len(skipped),
        "system_cases_run": len(system),
        "system_cases_passed": sum(1 for r in system if r.passed),
        "action_recall": mean([r.action_recall for r in extraction]),
        "decision_precision": mean([r.decision_precision for r in extraction]),
        "owner_extraction": mean([r.owner_score for r in extraction]),
        "deadline_extraction": mean([r.deadline_score for r in extraction]),
        "forbidden_claims_total": forbidden if extraction else None,
        "unsupported_after_validation": unsupported if extraction else None,
        "median_latency_ms": (
            int(statistics.median(latencies)) if latencies else None
        ),
        "approval_bypass": (
            0
            if any(r.case_id == "TC15" and r.passed for r in system)
            else ("unmeasured" if not system else "FAILED")
        ),
        "duplicate_side_effects": (
            0
            if any(r.case_id == "TC13" and r.passed for r in system)
            else ("unmeasured" if not system else "FAILED")
        ),
        "confirmed_reply_false_negatives": (
            0
            if any(r.case_id == "TC11" and r.passed for r in system)
            else ("unmeasured" if not system else "FAILED")
        ),
        "human_review_time": "unmeasured (requires operator timing study)",
        "manual_baseline": "unmeasured",
    }


TARGETS = {
    "action_recall": (">=", 0.90),
    "decision_precision": (">=", 0.90),
    "owner_extraction": (">=", 0.90),
    "deadline_extraction": (">=", 0.95),
    "forbidden_claims_total": ("==", 0),
    "unsupported_after_validation": ("==", 0),
    "median_latency_ms": ("<", 15000),
}


def render_markdown(summary: dict, extraction, system) -> str:
    lines = ["# Evaluation Results", ""]
    lines.append("## Metrics")
    lines.append("")
    lines.append("| Metric | Measured | Target | Status |")
    lines.append("|---|---:|---:|---|")
    for key, value in summary.items():
        target = TARGETS.get(key)
        if target is None:
            lines.append(f"| {key} | {fmt(value)} | n/a | n/a |")
            continue
        op, threshold = target
        if value is None or isinstance(value, str):
            status = "unmeasured"
        else:
            ok = (
                value >= threshold
                if op == ">="
                else value == threshold
                if op == "=="
                else value < threshold
            )
            status = "met" if ok else "MISSED"
        lines.append(f"| {key} | {fmt(value)} | {op} {threshold} | {status} |")

    for title, results in (("Extraction cases", extraction), ("System cases", system)):
        if not results:
            continue
        lines += ["", f"## {title}", "", "| Case | Result | Detail |", "|---|---|---|"]
        for r in results:
            detail = r.error or "; ".join(
                r.notes + [f"forbidden: {c}" for c in r.forbidden_claims_found]
            )
            verdict = "SKIPPED" if r.skipped else ("PASS" if r.passed else "FAIL")
            lines.append(f"| {r.case_id} | {verdict} | {detail or 'n/a'} |")

    if not extraction:
        lines += [
            "",
            "> Extraction cases (TC01-TC10) were not run in this pass; their "
            "metrics are unmeasured, not zero.",
        ]
    return "\n".join(lines) + "\n"


def fmt(value) -> str:
    if value is None:
        return "unmeasured"
    if isinstance(value, float):
        return f"{value:.1%}" if value <= 1 else f"{value:.2f}"
    return str(value)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the FollowOps AI evaluation.")
    parser.add_argument(
        "--offline",
        action="store_true",
        help="Run only the deterministic system cases (no Gemini calls).",
    )
    parser.add_argument("--cases", help="Comma-separated case ids, e.g. TC01,TC08")
    parser.add_argument(
        "--delay",
        type=float,
        default=7.0,
        help="Seconds between extraction cases, to stay inside API rate limits.",
    )
    args = parser.parse_args()

    only = set(args.cases.split(",")) if args.cases else None

    extraction: list[CaseResult] = []
    if not args.offline:
        print("Extraction cases (Gemini):")
        try:
            extraction = asyncio.run(run_extraction_suite(only, args.delay))
        except Exception as exc:
            print(f"  skipped: {type(exc).__name__}: {exc}")
            print("  (run with --offline to evaluate deterministic behaviour only)")

    print("System cases (backend tests):")
    system = run_system_suite(only)

    summary = aggregate(extraction, system)
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "summary": summary,
        "extraction_cases": [r.as_dict() for r in extraction],
        "system_cases": [r.as_dict() for r in system],
    }
    (RESULTS_DIR / "latest.json").write_text(
        json.dumps(payload, indent=2, default=str) + "\n", encoding="utf-8"
    )
    report = render_markdown(summary, extraction, system)
    (RESULTS_DIR / "latest.md").write_text(report, encoding="utf-8")

    print()
    print(report)
    print(f"Written to {RESULTS_DIR / 'latest.md'}")

    failed = [r for r in (*extraction, *system) if not r.passed and not r.skipped]
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
