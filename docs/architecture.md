# Architecture

## System Overview

```text
React operator console
   |  (X-API-Key)
   v
FastAPI  ── auth boundary, request validation, error translation
   |
   v
Workflow Service ──────────────────────────────┐
   |                                           |
   +--> Gemini AI Service                      |
   |       structured JSON (Pydantic schema)   |
   |            |                              |
   |            v                              |
   |    Followup Validator                     |
   |      evidence grounding                   |
   |      owner / deadline checks              |
   |      email date checks                    |
   |      injection flagging                   |
   |            |                              |
   |            v                              |
   |    Follow-up package  (blocked?)          |
   |            |                              |
   |            v                              |
   |    ***  HUMAN APPROVAL GATE  ***          |
   |            |                              |
   |            v                              |
   +--> Execution Service (idempotent claims)  |
           |         |         |               |
           v         v         v               |
      Zoho SMTP   Tasks     CRM (simulated)    |
           |         |         |               |
           +---------+---------+               |
                     |                         |
                     v                         v
                Audit log  <-------------  Supabase
                                            (RLS, server-only)
                     ^
                     |
        Reply Processor  <--- APScheduler ---> Zoho IMAP (read-only)
                     |
                     v
        accounts.followups_paused
```

## Responsibilities

### Frontend

A non-developer console for: selecting an account, entering notes, generating a
package, reviewing decisions/actions **with their supporting evidence**, editing
the email, approving or rejecting, and seeing what execution actually did.

It holds no provider credential and never calls Gemini, Supabase or SMTP
directly. Its only secret is the operator token, entered by the human and kept
in that browser's `localStorage`.

### FastAPI

The application boundary: authentication, request validation, dependency
construction, error translation, stable response contracts. An unhandled
exception is translated to a generic 500 so a provider string can never leak.

### Workflow Service

Owns state transitions, AI invocation, validation, package persistence,
approval enforcement, execution orchestration, failure state, and audit
logging. It is the only component allowed to move a workflow between states.

### Gemini AI Service

Proposes structured content and nothing else. It receives account context,
meeting date and notes; it returns summary, decisions, client/internal actions,
open questions, risks, a CRM proposal, an email draft and confidence values.

It has **no tool access**. It cannot send email, write to the CRM, create a
task or change a workflow state. That is the structural reason prompt injection
cannot cause a side effect — the fencing and marker detection are defence in
depth, not the boundary itself.

Configuration: temperature 0, `response_schema` bound to the Pydantic model,
system rules separated from untrusted content, bounded retries with
quota-aware backoff, and a request timeout.

### Validation Layer

Deterministic code, no model involved. Checks required fields, evidence
grounding against the notes, owner support, deadline traceability, CRM
vocabulary overlap, and dates asserted in the customer email. It can reject a
result whose JSON is perfectly valid.

It also **sanitizes**: ungrounded items are removed and unsupported owners and
deadlines are cleared, so nothing unsupported can reach a task or the CRM even
if a human approves in a hurry. Critical findings block approval outright.

### Execution Service

Turns "run the side effects" into a set of individually claimed, individually
retryable operations with durable outcomes. See `workflow.md` for the
claim/perform/settle contract and the `IN_DOUBT` case.

### Supabase

PostgreSQL stores accounts, meetings, workflow runs, follow-up packages, tasks,
audit events, workflow operations and email messages. Integrity that matters is
enforced in the database, not only in application code: the workflow transition
trigger, the idempotency unique indexes, the append-only audit triggers, the
task/account consistency trigger, and RLS with server-only grants.

The secret key is backend-only.

### Zoho Mail

SMTP is an execution adapter called only after approval. IMAP is a read-only
reader feeding reply detection. See `email-integration.md`.

### Reply Processor and Scheduler

One coalescing APScheduler job polls IMAP, correlates inbound mail against
outbound `Message-ID`s, persists the verdict with its reason, and pauses
follow-ups on a confirmed reply.

### CRM Service

An integration boundary with no real provider behind it. `simulated` mode
records the proposed update in the audit log and labels itself simulated in the
API and the UI. Adding a provider means implementing one method; the workflow
contract does not change.

## Core Design Decision

The LLM is not the workflow controller:

```text
LLM proposes -> code validates -> human approves -> tools execute -> system audits
```

Each arrow is a place where a hallucination stops being harmless text and would
otherwise become an external effect. Putting deterministic code and a human at
those points is what bounds the blast radius.

## Trust and Failure Boundaries

- A Gemini failure never produces an external action.
- A validation failure never produces an external action.
- A rejected approval never produces an external action.
- A blocked package cannot be approved.
- A confirmed prospect reply stops the next send, re-checked immediately before
  the SMTP call.
- An execution failure produces a visible `failed` state that records both what
  failed and what already succeeded.
- A retry never repeats a side effect that already succeeded.

## Known Structural Limits

- SMTP acceptance and the local write are not atomic. A crash between them is
  reported `IN_DOUBT` rather than resolved by guessing.
- A single shared operator token is not per-user identity.
- Reply correlation is heuristic; weak evidence yields `possible_reply` for
  review rather than an automatic pause.

## Non-Goals

Autonomous negotiation, autonomous sending, autonomous pricing changes,
inferring contractual commitments, replacing the CRM, arbitrary enterprise
integrations, or a guarantee of zero hallucinations. The system optimizes for a
bounded, auditable operational workflow instead.
