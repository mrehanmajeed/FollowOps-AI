"""HTTP contract tests.

Run against the real FastAPI app with the Supabase repository and Gemini
swapped for test doubles, so routing, authentication and the approval gate are
verified end to end without touching a provider.
"""

import pytest
from fastapi.testclient import TestClient

from app.api import dependencies
from app.core.config import get_settings
from app.main import app
from tests.conftest import FakeRepository, make_extraction
from tests.test_workflow import (
    FakeAIService,
    FakeEmailService,
    build_service,
    seed_workflow,
)

TOKEN = "test-operator-key"


@pytest.fixture
def client(settings, repository):
    email_service = FakeEmailService()
    service = build_service(repository, settings, email_service=email_service)

    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[dependencies.get_repository] = lambda: repository
    app.dependency_overrides[dependencies.get_workflow_service] = lambda: service
    app.dependency_overrides[dependencies.get_email_service] = lambda: email_service

    with TestClient(app) as test_client:
        test_client.email_service = email_service
        test_client.repository = repository
        yield test_client

    app.dependency_overrides.clear()


def auth(client):
    return {"X-API-Key": TOKEN}


# -- authentication ---------------------------------------------------------


def test_health_needs_no_credentials(client):
    assert client.get("/health").json() == {"status": "ok"}


def test_protected_routes_reject_a_missing_token(client):
    for method, path in [
        ("get", "/api/v1/accounts"),
        ("get", "/api/v1/workflows"),
        ("get", "/api/v1/integrations"),
    ]:
        response = getattr(client, method)(path)
        assert response.status_code == 401, path


def test_protected_routes_reject_a_wrong_token(client):
    response = client.get("/api/v1/accounts", headers={"X-API-Key": "nope"})
    assert response.status_code == 401


def test_bearer_token_is_accepted(client):
    response = client.get(
        "/api/v1/accounts", headers={"Authorization": f"Bearer {TOKEN}"}
    )
    assert response.status_code == 200


# -- workflow contract ------------------------------------------------------


def test_create_and_approve_over_http(client):
    repository: FakeRepository = client.repository
    account = repository.seed(
        "accounts", {"company_name": "Acme", "contact_email": "sarah@acme.test"}
    )

    created = client.post(
        "/api/v1/workflows",
        headers=auth(client),
        json={
            "account_id": account["id"],
            "meeting_date": "2026-09-05",
            "notes": (
                "Sarah confirmed that we will prepare a commercial proposal. "
                "Sarah will review the proposal next week."
            ),
        },
    )
    assert created.status_code == 201, created.text
    workflow_id = created.json()["id"]
    assert created.json()["status"] == "awaiting_approval"

    detail = client.get(f"/api/v1/workflows/{workflow_id}", headers=auth(client))
    assert detail.status_code == 200
    assert detail.json()["package"]["blocked"] is False

    approved = client.post(
        f"/api/v1/workflows/{workflow_id}/approve",
        headers=auth(client),
        json={"approved": True},
    )
    assert approved.status_code == 200, approved.text
    body = approved.json()
    assert body["status"] == "completed"
    assert body["email_sent"] is True
    assert body["crm_mode"] == "simulated"
    assert len(client.email_service.sent) == 1


def test_retry_endpoint_rejects_an_unapproved_workflow(client):
    _, _, workflow = seed_workflow(client.repository, status="awaiting_approval")
    response = client.post(
        f"/api/v1/workflows/{workflow['id']}/retry", headers=auth(client)
    )
    assert response.status_code == 422
    assert client.email_service.sent == []


def test_unknown_workflow_returns_404(client):
    response = client.get(
        "/api/v1/workflows/00000000-0000-0000-0000-000000000009",
        headers=auth(client),
    )
    assert response.status_code == 404


def test_notes_shorter_than_the_minimum_are_rejected(client):
    account = client.repository.seed(
        "accounts", {"company_name": "Acme", "contact_email": "s@acme.test"}
    )
    response = client.post(
        "/api/v1/workflows",
        headers=auth(client),
        json={
            "account_id": account["id"],
            "meeting_date": "2026-09-05",
            "notes": "short",
        },
    )
    assert response.status_code == 422


def test_package_edit_revalidates_and_can_unblock(client, settings, repository):
    """A blocked package becomes approvable only after the content is fixed."""
    account = repository.seed(
        "accounts", {"company_name": "Acme", "contact_email": "sarah@acme.test"}
    )
    email_service = FakeEmailService()
    service = build_service(
        repository,
        settings,
        email_service=email_service,
        ai_service=FakeAIService(
            make_extraction(
                email_body="We confirm the agreed 25% discount on 2026-12-01."
            )
        ),
    )
    app.dependency_overrides[dependencies.get_workflow_service] = lambda: service

    created = client.post(
        "/api/v1/workflows",
        headers=auth(client),
        json={
            "account_id": account["id"],
            "meeting_date": "2026-09-05",
            "notes": (
                "Sarah confirmed that we will prepare a commercial proposal. "
                "Sarah will review the proposal next week."
            ),
        },
    )
    workflow_id = created.json()["id"]
    assert created.json()["blocked"] is True

    blocked = client.post(
        f"/api/v1/workflows/{workflow_id}/approve",
        headers=auth(client),
        json={"approved": True},
    )
    assert blocked.status_code == 422
    assert email_service.sent == []

    edited = client.patch(
        f"/api/v1/workflows/{workflow_id}/package",
        headers=auth(client),
        json={
            "email_body": "Thanks for the call. We will prepare a commercial "
            "proposal and Sarah will review it."
        },
    )
    assert edited.status_code == 200
    assert edited.json()["blocked"] is False

    approved = client.post(
        f"/api/v1/workflows/{workflow_id}/approve",
        headers=auth(client),
        json={"approved": True},
    )
    assert approved.status_code == 200
    assert len(email_service.sent) == 1


# -- accounts and integrations ----------------------------------------------


def test_account_pause_and_resume_over_http(client):
    account = client.repository.seed(
        "accounts",
        {
            "company_name": "Acme",
            # A routable-looking domain: email-validator rejects reserved TLDs
            # such as .test when the response model re-validates the address.
            "contact_email": "sarah@acme-demo.com",
            "followups_paused": False,
        },
    )
    paused = client.post(
        f"/api/v1/accounts/{account['id']}/followups",
        headers=auth(client),
        json={"paused": True, "reason": "manual hold"},
    )
    assert paused.status_code == 200
    assert paused.json()["followups_paused"] is True

    resumed = client.post(
        f"/api/v1/accounts/{account['id']}/followups",
        headers=auth(client),
        json={"paused": False},
    )
    assert resumed.json()["followups_paused"] is False


def test_integration_status_never_returns_a_secret(client):
    response = client.get("/api/v1/integrations", headers=auth(client))
    assert response.status_code == 200
    body = response.text.lower()
    for forbidden in ("password", "secret", "api_key", "service_role"):
        assert forbidden not in body, f"integration status leaked '{forbidden}'"
    assert response.json()["crm"]["real_integration"] is False
