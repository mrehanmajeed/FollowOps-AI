# Zoho Mail Integration

FollowOps AI uses one Zoho mailbox for both directions:

```text
SMTP  →  send approved follow-up email
IMAP  →  read inbound mail, detect prospect replies, pause follow-ups
```

Both adapters speak plain RFC-compliant SMTP/IMAP, so the configured host
decides the provider. The variable names say Zoho because that is the
configured integration.

## Configuration

```env
ZOHO_SMTP_HOST=smtp.zoho.com
ZOHO_SMTP_PORT=587
ZOHO_SMTP_USER=<the real Zoho mailbox address>
ZOHO_SMTP_PASSWORD=<Zoho app-specific password>
ZOHO_USE_TLS=true

ZOHO_IMAP_ENABLED=true
ZOHO_IMAP_HOST=imap.zoho.com
ZOHO_IMAP_PORT=993
ZOHO_IMAP_USER=            # blank = use the SMTP user
ZOHO_IMAP_PASSWORD=        # blank = use the SMTP password
ZOHO_IMAP_USE_SSL=true
ZOHO_IMAP_MAILBOX=INBOX
ZOHO_IMAP_POLL_INTERVAL_SECONDS=120
ZOHO_IMAP_LOOKBACK_DAYS=14
ZOHO_IMAP_MAX_MESSAGES_PER_POLL=50
```

Notes:

- Port 587 uses STARTTLS; port 465 uses implicit TLS. The adapter picks based
  on the port.
- Regional Zoho accounts use `smtp.zoho.eu` / `imap.zoho.eu` (and `.in`,
  `.com.au`). Set both to the same region as the account.
- Use an **app-specific password**, not the account password, whenever the
  account has two-factor authentication enabled.
- IMAP and SMTP may share the app password unless the account requires
  otherwise. Leaving the IMAP credentials blank makes one mailbox serve both.
- Zoho reports a rejected IMAP login as a generic `[ALERT] Internal error`. The
  adapter translates that into an actionable message rather than passing it
  through.
- The mailbox address must belong to the Zoho account. A mailbox hosted
  elsewhere cannot authenticate against `smtp.zoho.com`.

Verify without side effects:

```http
POST /api/v1/integrations/smtp/verify   # authenticates, sends nothing
POST /api/v1/integrations/imap/verify   # authenticates, lists folders, read-only
```

## Outbound: threading metadata

FollowOps mints the `Message-ID` itself so it can be persisted **before** the
send. If the process dies mid-send, the system still knows a message with that
id may exist in the world.

Each outbound email is recorded in `email_messages`:

| Column | Purpose |
|---|---|
| `message_id` | The `Message-ID` header we set |
| `workflow_run_id` | Which workflow sent it |
| `account_id` | Which account it went to |
| `to_address` | Recipient |
| `subject`, `normalized_subject` | Correlation fallback |
| `sent_at` | When it was handed to Zoho |
| `correlation` | `outbound` |

`In-Reply-To` and `References` are set when the send is itself a reply in an
existing thread.

## Inbound: polling

A single APScheduler job owned by the FastAPI lifespan:

- `max_instances=1` — a slow poll cannot overlap itself.
- `coalesce=True` — a backlog of missed ticks collapses into one run after a
  restart rather than a stampede.
- Blocking `imaplib` work runs in a thread, so a poll never blocks a request.
- An IMAP failure is captured into the poll summary and logged; the next tick
  retries. A Zoho outage degrades reply detection without taking down the API.

The mailbox is opened **read-only**. FollowOps never sets flags, moves or
deletes mail. Deduplication therefore does not rely on `\Seen`; it relies on the
unique index `email_messages (direction, message_id)`. A lookback window
(`ZOHO_IMAP_LOOKBACK_DAYS`) bounds the search, and reprocessing the same
message is a no-op, which makes the poller safe to restart at any point.

Only headers are fetched (`BODY.PEEK[HEADER]`). Message bodies are not read
into the application and are never logged.

Manual run:

```http
POST /api/v1/integrations/imap/poll
```

## Reply correlation

`sender == contact_email` alone is **not** sufficient: a shared mailbox
receives newsletters, invoices and unrelated threads from people we email.
Evidence is layered, strongest first:

| # | Evidence | Verdict |
|---|---|---|
| 1 | `In-Reply-To` matches an outbound `Message-ID` | `confirmed_reply` |
| 2 | `References` contains an outbound `Message-ID` | `confirmed_reply` |
| 3 | Sender is the account contact **and** normalized subject matches an outbound subject | `confirmed_reply` |
| 4 | Sender is a known account contact, no thread/subject evidence | `possible_reply` |
| 5 | Subject matches an outbound thread, sender unknown | `possible_reply` |
| 6 | Nothing | `unrelated` |

Subject normalization strips any depth of `Re:` / `Fwd:` / `AW:` / `Re[2]:`
prefixes, lowercases and collapses whitespace.

Automated mail is never confirmed. A vacation auto-responder, a delivery
notification or a bounce may thread to our email, but a mail server
acknowledging delivery is not a prospect replying. Detected via
`Auto-Submitted`, `X-Autoreply`, `X-Autorespond`, `Precedence: bulk`,
`X-Failed-Recipients`, and sender patterns such as `mailer-daemon@`,
`postmaster@`, `no-reply@`. These are downgraded to `possible_reply`.

Every persisted row carries `correlation_reason` and the evidence list, so the
question "why was this message linked to that workflow?" always has an answer.

A message with no `Message-ID` is skipped: it cannot be deduplicated, so it
must not be processed.

## Reply → follow-up pausing

```text
Inbound reply
     ↓
Correlation
     ↓
Persist email_messages row
     ↓
confirmed_reply only:
     ├─ accounts.followups_paused = true
     ├─ accounts.followups_paused_reason, last_reply_at
     └─ audit_events: reply_detected
```

Only `confirmed_reply` pauses. A `possible_reply` is persisted and surfaced in
the UI for review — pausing on weak evidence would silently strand a sequence
with nobody being told.

Execution then refuses to send:

- **Pre-flight** — `execute()` raises `FollowupsPausedError` if the account is
  paused.
- **Immediately before the SMTP call** — the account row is re-read inside the
  claimed email operation, so a reply that lands *during* execution still stops
  the send. This closes the race:

```text
Scheduler/operator starts execution
            ↓
      Reply arrives, pause committed
            ↓
Email operation re-reads account → paused → operation fails, nothing sent
```

Resuming is an explicit human act:

```http
POST /api/v1/accounts/{account_id}/followups
{ "paused": false, "reason": "Reviewed the reply, sequence still relevant" }
```

`POST /workflows/{id}/retry?force=true` also overrides the pause. It is
documented as an override and requires the operator to have looked.

## Failure handling

| Failure | Behaviour |
|---|---|
| IMAP host unreachable | Poll summary records the error, next tick retries |
| IMAP auth rejected | Actionable message naming mailbox/app-password/IMAP-access as the likely cause |
| Malformed message | That message is skipped and logged; the poll continues |
| Message without `Message-ID` | Skipped (cannot be deduplicated) |
| Duplicate delivery | Unique index rejects it; counted as a duplicate |
| SMTP auth failure | Classified **permanent** — not retried in a loop |
| SMTP 4xx | Classified transient — retryable |
| SMTP 5xx / refused recipient | Classified permanent |
| Crash after SMTP accepted, before local write | Operation stays `pending` → reported `IN_DOUBT`, never auto-resent |

## Security

- SMTP and IMAP credentials are server-side only and stored as
  `pydantic.SecretStr`. They are never returned by an API response, never
  logged, and never reach the frontend bundle.
- `/api/v1/integrations` returns hosts, ports, user names and booleans — no
  passwords. A regression test asserts the response contains no
  `password` / `secret` / `api_key` / `service_role` substring.
- SMTP exception text is classified into a message rather than echoed, so a
  provider string can never carry a credential into the database or logs.
- Logged IMAP metadata: message id, correlation verdict, workflow/account id,
  processing outcome, sender **domain**. Not logged: message bodies, full
  recipient lists, credentials.
- All integration endpoints require the operator token.
