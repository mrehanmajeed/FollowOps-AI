"""The evaluation runner must not report an unreachable provider as a failure.

A 429 measures the billing plan and a DNS failure measures the network. Either
one recorded as FAIL would misstate the model's quality, which is exactly the
kind of dishonesty the suite exists to prevent. This became a real problem when
a scheduled run fired before the laptop had network and marked all ten
extraction cases as failed.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "evaluation"))

from run_eval import _is_unmeasurable  # noqa: E402


def test_provider_refusals_are_unmeasured():
    for message in (
        "ClientError: 429 RESOURCE_EXHAUSTED. quota exceeded",
        "ServerError: 503 UNAVAILABLE",
        "rate limit exceeded",
    ):
        assert _is_unmeasurable(message), message


def test_transport_failures_are_unmeasured():
    for message in (
        "ConnectError: [Errno 11001] getaddrinfo failed",
        "ConnectError: connection refused",
        "httpx.ConnectError: [SSL: UNEXPECTED_EOF_WHILE_READING]",
        "TimeoutError",
    ):
        assert _is_unmeasurable(message), message


def test_a_genuine_bad_response_is_still_a_failure():
    for message in (
        "ValidationError: 3 validation errors for FollowupExtraction",
        "RuntimeError: Gemini returned an empty response",
        "KeyError: 'decisions'",
    ):
        assert not _is_unmeasurable(message), message
