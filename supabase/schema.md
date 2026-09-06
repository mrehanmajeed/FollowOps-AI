# FollowOps AI — Database Schema

## Entity model

```text
accounts
   1
   |
   +----< meetings
              |
              1
              |
              +----< workflow_runs
                         |
                         +---- 1 followup_packages
                         |
                         +----< tasks
                         |
                         +----< audit_events
```

## accounts

Stores customer/account context used by the AI extraction workflow.

Key fields:

- `id`: UUID primary key
- `company_name`: required company identifier
- `contact_name`: optional client contact
- `contact_email`: optional validated email address
- `account_context`: bounded grounding context
- `created_at`, `updated_at`: lifecycle timestamps

## meetings

Stores source material for an AI workflow run.

Key fields:

- `id`: UUID primary key
- `account_id`: foreign key to `accounts`
- `meeting_date`: semantic reference date for relative dates
- `notes`: source text used for extraction
- `created_at`: ingestion timestamp

Deleting an account deletes its meetings.

## workflow_runs

Represents one execution of the FollowOps pipeline.

Key fields:

- `id`: UUID primary key
- `meeting_id`: foreign key to `meetings`
- `status`: controlled workflow state
- `model`: model identifier used for extraction
- `latency_ms`: measured model/workflow latency
- `estimated_cost`: optional estimated model cost
- `overall_confidence`: model-derived confidence retained for evaluation
- `idempotency_key`: future execution deduplication key
- `failure_code`, `failure_message`: operational failure details
- `started_at`, `completed_at`: execution lifecycle timestamps

The transition trigger prevents illegal state changes.

## followup_packages

Stores the validated AI output awaiting human approval or the package that was executed.

`structured_output` is JSONB so the database can retain the exact structured response while the backend schema remains the canonical semantic contract.

A workflow run can have at most one follow-up package.

## tasks

Stores internal action items created from approved workflow output.

A trigger verifies that `tasks.account_id` matches the account associated with the workflow run.

## audit_events

Stores append-only operational events.

The table deliberately does not expose update or delete privileges to the service role, and database triggers reject mutation attempts.

## workflow_operations

One row per external side effect of a workflow run. Written before the effect is
attempted and settled afterwards, so execution can be retried without repeating
what already succeeded.

Key fields: `operation_type` (`email_send` / `task_create` / `crm_update`),
`idempotency_key` (unique), `status` (`pending` / `succeeded` / `failed`),
`attempts`, `provider_id`, `error`, `result`.

A `pending` row after a crash means the outcome is unknown; the backend reports
`IN_DOUBT` rather than resending.

## email_messages

Outbound and inbound mail in one table, distinguished by `direction`.

Outbound rows record the `Message-ID` FollowOps minted, the recipient, the
subject and its normalized form, and the workflow/account that sent it — the
evidence a later reply is matched against.

Inbound rows record `in_reply_to`, `references_header`, sender, subject, the
IMAP uid and mailbox, plus `correlation` and `correlation_reason`.

`unique (direction, message_id)` makes IMAP processing idempotent.

## accounts: reply state

`followups_paused`, `followups_paused_reason` and `last_reply_at` are set when
a confirmed prospect reply is detected, and are checked before every send.

## Index strategy

Indexes target the access paths used by the backend:

- meetings by account and date
- workflow runs by meeting, status, and creation time
- follow-up packages by creation time
- tasks by account/status, workflow, and actionable due date
- audit events by workflow and creation time
- partial unique index for idempotency keys
