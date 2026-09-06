# Security and Trust Model

## Trust Boundaries

### Trusted

- Backend application code
- Server-side secrets
- Deterministic validators
- Database constraints and triggers

### Untrusted

- Meeting notes
- Account context free text
- LLM-generated content
- Browser input
- **Inbound email** — headers, subjects and addresses from anyone

## Prompt Injection

Meeting notes are customer-supplied and may contain instructions. They are
treated as data through four layers:

1. **Structural** — the model has no tool access. It cannot send email, write
   the CRM, create tasks or change workflow state. Nothing it outputs becomes
   an action without passing validation and a human. This is the actual
   boundary; the rest is defence in depth.
2. **Fencing** — untrusted text is wrapped in `<meeting_notes>` /
   `<account_context>` delimiters, and forged delimiters inside the input are
   neutralised so the input cannot close the block early and appear to speak as
   the system.
3. **Instruction** — the system rules state that content inside those blocks is
   data, and that instruction-like text should be reported as a risk.
4. **Validation** — output is checked against the notes regardless of what the
   model was told. An injected "we agreed a 40% discount" has no supporting
   evidence, so it is removed and flagged critical.

The operator is also shown a `prompt_injection_detected` warning when the notes
contain instruction markers, so a human knows the input was adversarial.

Evaluation case TC08 exercises this end to end.

## External Side Effects

Sending email, creating tasks and updating the CRM all require:

```text
validated package (no critical issues)
+
explicit human approval
+
account not paused by a confirmed reply
```

The approval gate is enforced by the database transition trigger as well as the
service, so application-level bugs alone cannot bypass it.

## Credentials

Server-side only, held as `pydantic.SecretStr`:

- Gemini API key
- Supabase secret (service-role) key
- Zoho SMTP password
- Zoho IMAP password
- Operator API key

Rules, all currently satisfied:

- No secret is hardcoded or committed. `.env` is gitignored; `.env.example`
  contains names only.
- No secret is logged. SMTP/IMAP exceptions are classified into messages rather
  than echoed, so provider text cannot carry a credential into logs or the
  database.
- No secret is returned by an API response. A regression test asserts the
  integration status response contains no `password`, `secret`, `api_key` or
  `service_role` substring.
- No secret reaches the frontend bundle. The browser holds only the operator
  token the human typed.
- The Supabase secret key is never exposed to the browser; it bypasses RLS.

## Authorization

The MVP has one operator role, authenticated by a shared bearer token
(`X-API-Key` or `Authorization: Bearer`), compared with `secrets.compare_digest`.
Every `/api/v1` route requires it; `/health` does not.

This is deliberately narrow, and it is the main hardening gap. Production needs:

- Per-user identity (Supabase Auth JWT verification)
- Account-scoped access
- A distinct role for approval rights
- Approver identity recorded in the audit trail
- Least-privilege integration credentials

`require_operator` is the seam where that replacement goes.

## Database

- RLS enabled on every application table.
- `anon` and `authenticated` roles revoked on all application tables; the Data
  API is not a path to this data.
- `service_role` granted only what the backend needs — notably `select, insert`
  on `audit_events`, with update, delete and truncate explicitly **revoked**.
  The revoke is necessary rather than implied: Supabase confers blanket
  privileges on tables created in `public`, so granting a narrower set does not
  remove what is already there. This was found by the enforcement suite, which
  now guards it.
- `audit_events` is append-only, enforced by triggers that reject updates and
  deletes.
- Workflow transitions are enforced by a trigger, so an illegal state change
  fails in the database.
- Idempotency is enforced by unique indexes, not by application checks alone.

## Logging

Logged: workflow id, state, operation type, latency, error category, message
id, correlation verdict and its reason, sender **domain**, processing outcome.

Not logged: API keys, passwords, email bodies, full recipient lists, or raw
sensitive customer data.

## Data Minimization

Only the account context and notes needed for the workflow are sent to the
model. IMAP fetches headers only (`BODY.PEEK[HEADER]`) — message bodies are
never read into the application. The mailbox is opened read-only, so FollowOps
cannot alter or delete a human's mail.

## Security Acceptance Criteria

| Criterion | Status |
|---|---|
| 0 approval bypasses | Enforced in service + DB trigger; TC15 passes |
| 0 exposed service-role credentials | Backend-only; asserted by test |
| 0 credentials in source control | `.env` gitignored, `.env.example` names only |
| 0 duplicate external side effects | Unique-key operation claims; TC13 passes |
| All external actions tied to a workflow id | `workflow_operations.workflow_run_id`, not null |
| All critical failures observable | `failed` state + audit event recording what succeeded |
| Injected instructions never executed | No tool access; TC08 passes, and verified live (notes carrying "confirm a 40% discount" produced no such claim) |
| Browser roles cannot read application data | `anon`/`authenticated` hold no privileges on any of the 8 tables; verified |
| Audit trail cannot be rewritten | Append-only triggers **and** revoked grants; verified |
