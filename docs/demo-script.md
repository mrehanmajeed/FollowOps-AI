# Five-Minute Demo Script

## 0:00–0:40 — Problem

Show a typical customer meeting note.

Explain:

> After a customer meeting, someone has to manually identify decisions, actions, deadlines, CRM updates, and write the follow-up email.

Highlight the risk:

> The workflow is repetitive, but an invented customer commitment is much more serious than a typo.

## 0:40–2:10 — Live Workflow

Create or select an account.

Enter meeting notes.

Start the workflow.

Show:

- Meeting summary
- Decisions
- Client actions
- Internal actions
- Open questions
- CRM update
- Email draft
- Evidence

Explain that Gemini produces structured output, while application code validates the output before approval.

## 2:10–3:10 — Safety Case

Use a meeting note containing one of:

- Missing deadline
- Ambiguous owner
- Rejected proposal
- Prompt injection

Show that the system does not blindly execute the generated result.

Emphasize:

```text
AI output -> validation -> human approval -> execution
```

## 3:10–3:50 — Execution Safety and Replies

Approve a package and show the Operations table: each side effect with its own
status and provider id.

Then show recovery:

> Email sent, task creation failed. The workflow is `failed`, and the audit
> event records both what failed and what already succeeded. Retrying re-runs
> only the task — the customer does not get a second email.

Then show reply detection on the Integrations tab:

- Poll the mailbox
- Show an inbound message with its correlation verdict **and the reason**
- Show the account paused, and that execution now refuses to send

Emphasize:

```text
Postgres cannot roll back an email Zoho already accepted.
So execution is claimed per side effect, not wrapped in a transaction.
```

## 3:50–4:20 — Evaluation

Show the evaluation dashboard or result table.

Present:

- Action recall
- Decision precision
- Unsupported commitments
- Deadline extraction
- Human review time
- Latency
- Approval bypass

Show at least one failed case and explain the mitigation. The strongest one is
real: the evaluation caught the validator blocking a correct package because it
compared date strings instead of calendar dates. Show the fix and its
regression tests.

State plainly which numbers are measured and which are not.

## 4:20–5:00 — Handoff

Show:

- README
- Runbook
- Architecture
- Evaluation package

Explain:

> A non-developer can operate the workflow because the system exposes a bounded review-and-approve process rather than requiring direct model interaction.

Finish with limitations and next steps.

## Demo Rule

Never claim an evaluation result that has not actually been measured.
