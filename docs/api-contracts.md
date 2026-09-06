# API Contracts

Base prefix:

```text
/api/v1
```

## Authentication

Every endpoint under `/api/v1` requires the operator token. `/health` does not.

```http
X-API-Key: <OPERATOR_API_KEY>
```

or

```http
Authorization: Bearer <OPERATOR_API_KEY>
```

A missing or wrong token returns `401`. The token authenticates the operator
console; it is not a provider credential and grants no direct access to Gemini,
Supabase or Zoho.

## Errors

| Status | Meaning |
|---|---|
| 401 | Missing or invalid operator credentials |
| 404 | Resource not found |
| 422 | Validation failed, or the workflow is in a state that forbids the action |
| 502 | Workflow execution failed |
| 500 | Unhandled error — the body is always `{"detail": "Internal server error"}` |

Provider exception text is never returned raw.

## Health

```http
GET /health
```

```json
{ "status": "ok" }
```

## Accounts

```http
POST /api/v1/accounts
```

```json
{
  "company_name": "Acme Logistics",
  "contact_name": "Sarah Khan",
  "contact_email": "sarah@acme.example",
  "account_context": "Existing customer evaluating a warehouse rollout."
}
```

```http
GET /api/v1/accounts
```

Returns accounts including `followups_paused`, `followups_paused_reason` and
`last_reply_at`.

```http
POST /api/v1/accounts/{account_id}/followups
```

```json
{ "paused": false, "reason": "Reviewed the reply; sequence still relevant" }
```

Pauses or resumes automated follow-ups. Resuming after a confirmed reply is a
deliberate human act, which is why it is a separate call rather than a flag on
approval.

## Workflows

```http
POST /api/v1/workflows
```

```json
{
  "account_id": "uuid",
  "meeting_date": "2026-09-05",
  "notes": "Sarah confirmed that we will prepare a commercial proposal."
}
```

Response:

```json
{
  "id": "uuid",
  "meeting_id": "uuid",
  "status": "awaiting_approval",
  "model": "gemini-2.5-flash",
  "latency_ms": 6408,
  "overall_confidence": 0.9,
  "blocked": false,
  "validation_issues": [],
  "created_at": "2026-09-05T10:00:00Z"
}
```

Generating never sends anything.

```http
GET /api/v1/workflows
GET /api/v1/workflows/{workflow_id}
```

The detail response carries everything the review screen needs:

```json
{
  "workflow": { "...": "..." },
  "meeting": { "...": "..." },
  "account": { "...": "..." },
  "package": {
    "structured_output": { "...": "..." },
    "email_subject": "...",
    "email_body": "...",
    "blocked": false,
    "validation_issues": [
      {
        "severity": "warning",
        "code": "unsupported_owner",
        "message": "Owner 'Michael' is not named in the meeting notes; cleared for human review.",
        "item": "Prepare the proposal"
      }
    ]
  },
  "operations": [],
  "audit_events": [],
  "replies": []
}
```

```http
PATCH /api/v1/workflows/{workflow_id}/package
```

```json
{ "email_subject": "...", "email_body": "..." }
```

Applies operator edits and **re-runs validation**. This is how a blocked
package becomes approvable. Allowed only in `awaiting_approval`.

```http
POST /api/v1/workflows/{workflow_id}/approve
```

```json
{ "approved": true }
```

Response:

```json
{
  "workflow_run_id": "uuid",
  "status": "completed",
  "email_sent": true,
  "email_message_id": "1757148000.123@followops.example",
  "tasks_created": 2,
  "crm_updated": true,
  "crm_mode": "simulated",
  "operations": [
    {
      "operation_type": "email_send",
      "status": "succeeded",
      "skipped": false,
      "provider_id": "1757148000.123@followops.example",
      "error": null
    }
  ]
}
```

Refused with `422` when the workflow is not `awaiting_approval`, when the
package is `blocked`, or when the account has a confirmed reply pause.

```http
POST /api/v1/workflows/{workflow_id}/retry?force=false
```

Re-runs the side effects of an already approved workflow. Operations that
already succeeded are skipped, not repeated. `force=true` additionally
overrides an `IN_DOUBT` email operation and a reply pause — it can send a
second copy, so it is for use only after checking the mailbox.

## Integrations

```http
GET /api/v1/integrations
```

Reports configuration and last poll state. Contains hosts, ports, user names
and booleans — never a credential.

```json
{
  "gemini": { "model": "gemini-2.5-flash", "configured": true },
  "supabase": { "url": "https://<ref>.supabase.co", "configured": true },
  "smtp": { "host": "smtp.zoho.com", "port": 587, "user": "...", "tls": true },
  "imap": {
    "enabled": true,
    "host": "imap.zoho.com",
    "port": 993,
    "mailbox": "INBOX",
    "poll_interval_seconds": 120,
    "polling": true,
    "last_poll": { "fetched": 12, "processed": 1, "confirmed_replies": 1 }
  },
  "crm": { "mode": "simulated", "real_integration": false }
}
```

```http
POST /api/v1/integrations/smtp/verify   # authenticates, sends no mail
POST /api/v1/integrations/imap/verify   # authenticates, lists folders, read-only
POST /api/v1/integrations/imap/poll     # one reply-detection pass now
GET  /api/v1/integrations/replies       # inbound mail with correlation verdicts
```

## Design Principles

- Validate every payload with Pydantic.
- Keep provider credentials server-side.
- Never expose a raw provider error.
- Never let an approval endpoint execute a workflow in an invalid state.
- Report what actually happened, including partial success, and label a
  simulated integration as simulated.
