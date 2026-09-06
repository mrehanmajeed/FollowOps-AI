# FollowOps AI — Supabase

This directory contains the database contract for FollowOps AI.

## Responsibilities

The database provides:

- normalized relational storage for accounts, meetings, workflow runs, follow-up packages, tasks, and audit events
- foreign-key integrity and deletion behavior
- domain-level constraints for input bounds and confidence ranges
- workflow-state transition enforcement
- idempotency-key uniqueness
- append-only audit events
- timestamp maintenance
- indexes for workflow, task, meeting, and audit queries
- Row Level Security with a server-only service-role access model

Supabase RLS is enabled on every application table in the exposed `public` schema. Anonymous and authenticated Data API access is revoked for these tables. The FastAPI backend uses the Supabase service-role credential server-side.

## Directory

```text
supabase/
├── config.toml
├── migrations/
│   ├── 20260905000100_initial_schema.sql
│   └── 20260906000100_execution_and_email.sql
├── tests/
│   ├── invariants.sql
│   └── rls.sql
├── seed.sql
└── README.md
```

Apply migrations in filename order. The second migration adds execution
idempotency, inbound/outbound email, reply-driven pausing, and the package
validation columns.

## Local setup

Install the Supabase CLI, then from the repository root:

```bash
supabase start
supabase db reset
```

The reset applies migrations and then `seed.sql`.

## Production migration

Review the migration in CI before deployment:

```bash
supabase db lint
supabase db push
```

Use the production project only after the migration has passed review and staging validation.

## Data ownership

The current MVP intentionally keeps database access behind FastAPI. The browser does not receive a database credential.

The backend currently uses `SUPABASE_SERVICE_ROLE_KEY`, so it is responsible for enforcing application authorization before database operations. This is a deliberate MVP boundary and must not be changed into a browser-exposed service-role client.

## Workflow integrity

The database enforces these transitions:

```text
created
  -> processing
processing
  -> validating
  -> awaiting_approval
  -> failed
validating
  -> awaiting_approval
  -> failed
awaiting_approval
  -> approved
  -> rejected
  -> failed
approved
  -> executing
  -> failed
executing
  -> completed
  -> failed
failed
  -> executing        (retry only; operations are idempotent)
```

Terminal states are:

```text
completed
rejected
failed
```

The database rejects invalid transitions even if application code attempts them.

## Audit integrity

`audit_events` is append-only. Updates and deletes are rejected by database triggers.

Audit records should contain operational facts rather than secrets, tokens, credentials, or raw sensitive payloads.

## Idempotency

Execution idempotency lives in `workflow_operations`. Each side effect of a
workflow gets one row, claimed before the effect is attempted:

- `workflow_operations_idempotency_key_uidx` — unique on
  `{workflow_run_id}:{operation_type}`
- `workflow_operations_run_type_uidx` — unique on
  `(workflow_run_id, operation_type)`

A second executor loses the insert and reads the existing row, so a retry skips
what already succeeded. A row still `pending` means a previous attempt was
interrupted in flight; the backend reports that as `IN_DOUBT` and refuses to
resend without an explicit operator override.

Inbound email is deduplicated by `email_messages (direction, message_id)`, which
is what makes IMAP polling safe to repeat and safe to restart.

`workflow_runs.idempotency_key` remains available for a future
caller-supplied key; it is not required by the current contract.

## Execution and email tables

`workflow_operations` — one durable record per external side effect
(`email_send`, `task_create`, `crm_update`) with status, attempts, provider id,
error and result.

`email_messages` — outbound and inbound mail. Outbound rows carry the
`Message-ID` we minted and the workflow/account that sent it. Inbound rows carry
the threading headers, the correlation verdict
(`confirmed_reply` / `possible_reply` / `unrelated`) and the reason for it.

`accounts.followups_paused`, `followups_paused_reason`, `last_reply_at` — set
when a confirmed reply is detected, checked before every send.

`followup_packages.validation_issues`, `blocked` — the validation outcome
travels with the package the human reviews; `blocked` prevents approval.

## Important execution limitation

Database constraints cannot make a multi-system operation atomic across Zoho Mail, an external CRM, and Postgres.

The backend therefore must treat external execution as a distributed workflow and eventually add idempotency keys, provider operation IDs, durable execution records, and reconciliation handling.

## Security model

The current schema follows a server-only access pattern:

```text
React
  -> FastAPI
      -> Supabase service role
          -> PostgreSQL + RLS
```

The service-role key must remain exclusively on the backend. Supabase documents that service-role credentials bypass RLS and must not be exposed to browsers.

## Verification

At minimum, verify:

```bash
supabase db lint
supabase db reset
pytest
```

For a production deployment, also validate the database against the backend integration suite and the evaluation cases before pushing the migration.
