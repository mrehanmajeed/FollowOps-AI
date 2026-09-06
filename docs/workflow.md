# Workflow Contract

## State Machine

```text
CREATED
   |
   v
PROCESSING ---------> FAILED
   |
   v
AWAITING_APPROVAL --> REJECTED
   |              \
   |               -> FAILED
   v
APPROVED ----------> FAILED
   |
   v
EXECUTING
   |
   +-----------+
   |           |
   v           v
COMPLETED    FAILED
                |
                v
            EXECUTING   (retry; operations are idempotent)
```

## State Rules

| Current | Allowed next |
|---|---|
| `created` | `processing` |
| `processing` | `validating`, `awaiting_approval`, `failed` |
| `validating` | `awaiting_approval`, `failed` |
| `awaiting_approval` | `approved`, `rejected`, `failed` |
| `approved` | `executing`, `failed` |
| `executing` | `completed`, `failed` |
| `failed` | `executing` (retry only) |
| `completed` | none |
| `rejected` | none |

`completed` and `rejected` are terminal. `failed` is recoverable **only** into
`executing`, which is the retry path for a partially executed workflow; a
generation failure is not retried in place, it needs a new run.

The transition table is enforced in two places:

1. `public.enforce_workflow_transition`, a PostgreSQL trigger. The database
   rejects an illegal transition even if application code attempts it.
2. `WorkflowService`, which checks status before every consequential step.

The API must never permit:

```text
awaiting_approval -> executing
```

Execution passes through `approved`. That is the approval gate, and it is a
database constraint rather than a convention.

## Creation Flow

1. Validate the account exists.
2. Persist the meeting.
3. Create the workflow run (`processing`).
4. Invoke Gemini (structured output, temperature 0, untrusted input fenced).
5. Validate: schema → evidence grounding → owners → deadlines → CRM → email.
6. Persist the follow-up package with its validation issues and `blocked` flag.
7. Move to `awaiting_approval`.
8. Record `workflow_ready`.

Any failure marks the workflow `failed` and performs no external action.

## Validation Outcome

Validation sanitizes as well as judges:

| Finding | Severity | Effect |
|---|---|---|
| Evidence not present in the notes | critical | Item removed from the package |
| Deadline earlier than the meeting date | critical | Deadline cleared |
| Email states a date nothing supports | critical | Recorded; package blocked |
| Owner not named in the notes | warning | Owner cleared |
| Deadline not traceable to the notes | warning | Deadline cleared |
| CRM next step shares no vocabulary with the notes | warning | Recorded |
| Email references no validated item | warning | Recorded |
| Injection markers in the notes | warning | Recorded for the operator |

Any critical issue sets `followup_packages.blocked = true`, and approval is
refused while it stands. The operator clears it by editing the package, which
re-runs validation on the edited content.

Warnings do not block. They are review signals, shown next to the item.

## Approval Flow

1. Load the workflow; require `awaiting_approval`.
2. On rejection: move to `rejected`, audit, stop. No side effects.
3. On approval: refuse if the package is `blocked`.
4. Move to `approved`, audit `workflow_approved`.
5. Execute.

## Execution Contract

Execution is a distributed operation across Zoho SMTP, the task store and the
CRM. PostgreSQL cannot roll back an email Zoho already accepted, so execution
is not modelled as a transaction. Each side effect is claimed and settled
independently:

```text
claim -> perform -> settle
```

The claim is a row in `workflow_operations` with a unique key
`{workflow_run_id}:{operation_type}`. A second executor loses the insert and
reads the existing record instead:

| Existing status | Outcome |
|---|---|
| `succeeded` | Skip; report the stored result and provider id |
| `failed` | Retry; increment `attempts` |
| `pending` | **IN_DOUBT** — a previous attempt was interrupted in flight |

`IN_DOUBT` is the honest answer to the hard case: the process died between
handing the mail to Zoho and recording the outcome, and SMTP has no API to ask
"did you accept this message?". Guessing either way is wrong, so the operation
is surfaced to the operator and never auto-resent. `?force=true` overrides it
after the operator has checked the mailbox.

Order of operations:

1. **Email** — verify approval, verify the account is not paused (re-read
   immediately before the send), persist the outbound `Message-ID`, send,
   record the provider id.
2. **Internal tasks** — guarded twice: by the operation record and by an
   existing-title check per workflow.
3. **CRM** — simulated; records the proposed update in the audit log and
   reports `simulated: true`.

If every operation succeeds the workflow becomes `completed` and the package is
marked approved. Otherwise it becomes `failed` with a failure message, and the
audit event records **both** what failed and what already succeeded — without
that, a retry is guesswork.

## Safety Invariants

- No external execution before approval.
- No action without evidence quoted from the notes.
- No invented owner, deadline, or email date.
- No instruction embedded in meeting notes is treated as a system instruction.
- No automated follow-up after a confirmed prospect reply, without an explicit
  resume.
- One approved workflow yields at most one customer email.
- Every failed workflow is observable and states what already happened.
- Every external execution is auditable and carries its provider id.
