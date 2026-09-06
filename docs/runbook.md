# Operations Runbook

## Prerequisites

- Python 3.13+
- Node 20+
- A Supabase project
- A Gemini API key
- A Zoho mailbox with IMAP/SMTP access enabled and an app-specific password

## Database Setup

### Local stack (recommended for development)

Needs Docker running. No Supabase account required:

```bash
npx supabase start          # applies all migrations + seed
npx supabase stop           # when finished
```

Copy the printed API URL and secret key into `backend/.env`.

### Hosted project

Apply the three migrations in filename order:

```text
supabase/migrations/20260905000100_initial_schema.sql
supabase/migrations/20260906000100_execution_and_email.sql
supabase/migrations/20260906000200_audit_least_privilege.sql
supabase/migrations/20260906000300_email_messages_cascade.sql
```

```bash
export SUPABASE_ACCESS_TOKEN=<personal access token>   # dashboard > account > tokens
npx supabase link --project-ref <ref>
npx supabase migration list        # confirm the target project
npx supabase db push --dry-run     # review the plan
npx supabase db push
```

`supabase db reset --linked` destroys hosted data. Do not run it.

### Verifying the schema enforces its rules

Two suites, because a hosted project has no psql connection:

```bash
# Local: 40 checks via direct SQL, inside a rolled-back transaction.
docker exec -i supabase_db_followops-ai   psql -U postgres -d postgres -f - < supabase/tests/enforcement.sql

# Hosted (or local): 24 checks through PostgREST, using only the secret key.
cd backend && python ../evaluation/hosted_enforcement.py
```

Between them they assert the approval gate, terminal states, the retry path,
operation idempotency, inbound deduplication, append-only audit, task/account
consistency, data validation, and that browser roles hold no privileges.

The hosted script creates a fixture and removes it. It deliberately writes no
audit event: `audit_events` is append-only, so a workflow run that has one can
never be deleted.

Verify the schema is live:

```bash
curl -s -H "X-API-Key: $OPERATOR_API_KEY" http://localhost:8000/api/v1/accounts
```

An empty array means the schema exists. `PGRST205 Could not find the table` means
the migrations have not been applied.

## Backend

```bash
cd backend
python -m venv .venv
.venv\Scripts\activate        # Windows
pip install -r requirements.txt
cp .env.example .env          # then fill it in
uvicorn app.main:app --reload
```

`/docs`, `/redoc`, `/health`.

Generate the operator token:

```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

## Frontend

```bash
cd frontend
npm install
cp .env.example .env
npm run dev
```

Sign in at http://localhost:5173 with the `OPERATOR_API_KEY` value.

## Required Environment Variables

```text
OPERATOR_API_KEY
GEMINI_API_KEY
GEMINI_MODEL
SUPABASE_URL
SUPABASE_SECRET_KEY          (alias: SUPABASE_SERVICE_ROLE_KEY)
ZOHO_SMTP_HOST / PORT / USER / PASSWORD
ZOHO_IMAP_HOST / PORT / MAILBOX      (USER/PASSWORD default to the SMTP ones)
```

Full list with comments: `backend/.env.example`.

## Verifying Integrations

All are safe: nothing is sent, nothing is modified.

```http
POST /api/v1/integrations/smtp/verify   # SMTP auth only
POST /api/v1/integrations/imap/verify   # IMAP auth + folder list, read-only
POST /api/v1/integrations/imap/poll     # one reply-detection pass
GET  /api/v1/integrations               # config + last poll summary
```

The Integrations tab in the console exposes the same actions.

## Normal Workflow

1. Create an account with a contact email.
2. Paste meeting notes, set the meeting date, generate.
3. Wait for `awaiting_approval`.
4. Review decisions and actions against their quoted evidence, and read the
   validation issues.
5. Edit the email if needed and save — this re-validates.
6. Approve or reject.
7. On approval, inspect the per-operation results and the audit trail.

## Failure Handling

### Gemini failure

Workflow becomes `failed`; nothing external happens. The service already
retries transient errors with backoff, and rate-limit errors with a longer
delay.

1. Check the API key and model name.
2. `429 RESOURCE_EXHAUSTED` means the plan quota is exhausted, not a bug — wait
   for the window to reset or raise the quota.
3. Start a new workflow. A generation failure is not retried in place.

### Validation blocked the package

Expected behaviour, not a fault: the model asserted something the notes do not
support.

1. Read the critical issues on the review screen.
2. Compare against the notes.
3. Edit the email to remove the unsupported content and save.
4. If the validator is wrong, that is a bug — add a regression test before
   changing the rule.

### SMTP failure

Workflow becomes `failed`; the email operation records the classified error.

1. `Authentication failed` → the mailbox user, the app-specific password, or
   SMTP access being disabled on the account. Verify with
   `POST /integrations/smtp/verify`.
2. Confirm host/port/TLS: 587 STARTTLS, 465 implicit TLS, matching region host.
3. Fix the cause, then `POST /workflows/{id}/retry`. The retry re-sends only if
   the email operation has not already succeeded.

### Partially executed workflow

For example the email succeeded but task creation failed.

1. Open the workflow. The Operations table shows each side effect's status and
   provider id.
2. Fix the underlying cause.
3. `POST /workflows/{id}/retry`. Succeeded operations are skipped — the
   customer will not receive a second email.

### Operation reported IN_DOUBT

A previous attempt was interrupted while in flight and its outcome is unknown.

1. **Check the Zoho Sent folder** for the recorded `Message-ID`.
2. If it was not sent: `POST /workflows/{id}/retry?force=true`.
3. If it was sent: leave it. Do not force; the workflow can be closed manually.

Never force without checking. Forcing is how a duplicate customer email
happens.

### IMAP failure

Reply detection degrades; the API keeps working. The poll summary records the
error and the next tick retries.

1. `POST /integrations/imap/verify`.
2. Zoho reports a bad login as `[ALERT] Internal error` — check the mailbox
   user, the app password, and that IMAP access is enabled in Zoho Mail
   settings.
3. Check the region host matches the account.

### Confirmed reply paused an account

Working as designed. Review the reply on the workflow or Integrations tab, then
either leave it paused or resume:

```http
POST /api/v1/accounts/{id}/followups   { "paused": false, "reason": "..." }
```

### Database failure

The API returns an application error with no secret in it. Workflow state stays
observable. `PGRST205` means the migrations were never applied.

## Verification Commands

```bash
cd backend && python -m pytest -q
cd backend && python -m ruff check app tests
cd frontend && npm run build
python evaluation/run_eval.py --offline
python evaluation/run_eval.py

docker exec -i supabase_db_followops-ai   psql -U postgres -d postgres -f - < supabase/tests/enforcement.sql
```

### End-to-end check

`evaluation/e2e_check.py` drives the real stack and prints PASS/FAIL per
assertion. It is staged so nothing leaves the building unless you ask:

```bash
cd backend
python ../evaluation/e2e_check.py                  # schema, extraction,
                                                   # approval gate, idempotency
python ../evaluation/e2e_check.py --send           # + one real email
python ../evaluation/e2e_check.py --send --reply   # + reply, IMAP, pause
```

`--send` mails the account contact, defaulting to the configured Zoho mailbox
(a controlled self-send). Use `--recipient` to point at another mailbox you own.
`--reply` needs about two minutes for delivery and polling.

## Production Hardening Checklist

Done in this iteration:

- [x] Execution idempotency with durable per-operation records
- [x] Retry-safe partial-failure recovery
- [x] Approval enforcement in the database, not only the service
- [x] Reply detection with follow-up pausing
- [x] Structured operational logging
- [x] Bounded retries with backoff for Gemini and classified SMTP errors
- [x] Append-only audit trail
- [x] An authentication boundary

Still required before production:

- [ ] Per-user identity and account-scoped authorization
- [ ] Rate limiting
- [ ] Monitoring and alerting on failed and in-doubt operations
- [ ] Secret rotation process
- [ ] A real CRM adapter
- [ ] Immutable audit retention policy
- [ ] Integration tests against a staging Supabase and mailbox

## Secret Handling

Never commit `.env`, log an API key, return a service-role credential, or place
SMTP credentials in frontend code. `.env` is gitignored; `.env.example` carries
names only.
