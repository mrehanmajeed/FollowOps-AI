# FollowOps AI — Project State

Last updated: 2026-09-06

**Status: FUNCTIONAL, HOSTED-VERIFIED, WITH ONE MEASUREMENT GAP.**

The hosted Supabase project is migrated and the complete workflow —
extraction, validation, approval, real email, reply detection, follow-up pause,
idempotent retry, audit immutability — is verified against **hosted Supabase +
real Gemini + real Zoho SMTP/IMAP + FastAPI**. The only thing not achieved is a
full re-measurement of the ten model-extraction evaluation cases, because the
Gemini free tier allows 20 requests per day and that budget is spent.

Legend used below: **[hosted]** verified against the live Supabase project ·
**[local]** verified against the local Supabase stack (identical migrations) ·
**[measured]** a number produced by a run · **[unmeasured]** not measured, and
not estimated.

## Current status

| Area | State |
|---|---|
| Hosted Supabase schema | **[hosted]** 4/4 migrations applied, history in sync |
| Backend architecture | Working |
| Gemini extraction | **[hosted]** working; quota-limited for bulk evaluation |
| Grounding validation | **[hosted]** working; injection payload not obeyed |
| Workflow state machine | **[hosted]** trigger enforces the approval gate |
| Execution idempotency | **[hosted]** duplicate claims rejected by the database |
| Zoho SMTP | **[hosted]** real emails sent and delivered |
| Zoho IMAP + reply detection | **[hosted]** real reply correlated via In-Reply-To |
| Follow-up pausing | **[hosted]** pause set by reply; execution refused |
| Audit immutability | **[hosted]** update and delete both rejected |
| Operator authentication | **[hosted]** 401 / 401 / 200 against the live API |
| Frontend operator console | Builds clean; contract verified against hosted responses |
| Evaluation: system cases | **[measured]** 5/5 |
| Evaluation: extraction cases | **[unmeasured]** — quota |
| CRM | Simulated, labelled as such everywhere |

## Verification evidence

```text
supabase migration list    4/4 local == remote (hosted project <project-ref>)
hosted enforcement         24/24 passed   (evaluation/hosted_enforcement.py)
local db enforcement       40/40 passed   (supabase/tests/enforcement.sql)
pytest                     74 passed
ruff (app, tests, eval)    clean
frontend build             successful (tsc strict + vite)
frontend type contract     8/8 response shapes match src/types against hosted data
docker compose config      valid
evaluation system cases    5/5 passed (TC11-TC15)
evaluation extraction      unmeasured (Gemini daily quota)
live API vs hosted         health 200; auth 401/401/200; no secrets in responses
SMTP verify (via API)      ok — smtp.zoho.com
IMAP verify (via API)      ok — imap.zoho.com, INBOX, 10 folders
end-to-end (hosted)        stages 1-6 all passed, incl. one real delivered email
reply loop (hosted)        10/10 passed, incl. audit immutability
pause guard (hosted)       execution refused, 0 emails, 0 operations claimed
```

## Hosted deployment

```text
Project ref:       <project-ref>  ("followops-ai", ACTIVE_HEALTHY, ap-south-1)
Postgres:          17.6.1.166
Migrations:        20260905000100_initial_schema
                   20260906000100_execution_and_email
                   20260906000200_audit_least_privilege
                   20260906000300_email_messages_cascade
Migration history: local == remote for all four
Tables:            accounts, meetings, workflow_runs, followup_packages, tasks,
                   audit_events, workflow_operations, email_messages   (8/8 reachable)
RLS:               enabled on all 8 (asserted by the migrations; behaviourally
                   confirmed locally, where anon/authenticated hold no privileges)
Backend connects:  yes, via SUPABASE_URL + server-side secret key
```

`supabase db reset --linked` was never run. No hosted data was dropped.

Structural introspection of the hosted catalog was not possible: the Management
API `/database/query` endpoint returns 403 for this token, and `supabase db
diff` fails with an internal CLI error (`LegacyMigraDiffError … unsupported or
invalid secret format`). Instead the hosted schema was verified
**behaviourally** — 24 checks that make the database refuse what it must refuse
— which is stronger evidence than a catalog listing.

## What the hosted end-to-end run proved

```text
meeting notes (with an embedded prompt injection)
   -> Gemini extraction               40923 ms  (slow: retrying through rate limits)
   -> validation                      injection not obeyed, no "40% discount"
   -> awaiting_approval               0 operations recorded, nothing sent
   -> execute() before approval       REFUSED (ValidationError)
   -> human approval                  approved -> executing -> completed
   -> Zoho SMTP                       1 email delivered, Message-ID persisted
   -> tasks                           2 internal tasks created
   -> CRM                             recorded as simulated
   -> retry of a completed workflow   REFUSED, no duplicate send
   -> audit trail                     workflow_ready/approved/crm_update/completed
   -> reply sent from the mailbox
   -> IMAP poll                       correlated via in_reply_to, first poll
   -> account                         followups_paused = true, last_reply_at set
   -> reply_detected audit event      written
   -> second poll                     0 new, 5 duplicates (idempotent)
   -> audit event UPDATE              REJECTED (append-only)
   -> audit event DELETE              REJECTED (append-only)
   -> pending follow-up, paused acct  REFUSED: 0 emails, 0 operations claimed
```

Correlation observed on the hosted project, including messages left in the
mailbox by the earlier local run:

```text
confirmed_reply  Re: Follow-up: Acme Pilot in Rot…   in_reply_to matched
possible_reply   Follow-up: Acme Rotterdam Pilot…    known contact, no outbound
possible_reply   Re: Follow-up: Acme Rotterdam Pi…   record in the hosted DB
```

The two `possible_reply` rows are exactly right: those messages belong to a
thread whose outbound record lives in the *local* database, so on hosted the
evidence is weak. They were recorded for review and did **not** pause anything.

## Defects found and fixed

From the initial audit:

1. **Approval could never execute.** The service moved
   `awaiting_approval → executing`, but the DB trigger requires
   `awaiting_approval → approved → executing`. Now confirmed on hosted: the
   database rejects the shortcut and accepts the correct path.
2. **The app could not start** — `.env` had `SUPABASE_SECRET_KEY`, settings
   demanded `SUPABASE_SERVICE_ROLE_KEY`. Accepts either.
3. **Supabase client too old for the key format** (2.11.0 rejects `sb_secret_…`).
4. **A failed workflow was permanently stuck** — added `failed → executing`.
5. **Retry would re-send the customer email** — added `workflow_operations`
   with claim/perform/settle and the `IN_DOUBT` state.
6. **Validator false positive on prose dates** — "September 6th" for a validated
   `2026-09-06` deadline blocked a correct package. Dates compared as calendar
   dates now.
7. **Email grounding check was unusable** — required verbatim text.
8. **Gemini errors were unreadable** — hid `429 RESOURCE_EXHAUSTED`.
9. **No authentication** — every endpoint was open.

Found while verifying:

10. **`service_role` could UPDATE and DELETE `audit_events`.** Supabase grants
    blanket privileges on new tables in `public`, so `grant select, insert`
    never removed what was already there. Fixed by an explicit revoke
    (`20260906000200`), and confirmed on hosted: both operations are rejected.
11. **Our own outbound email, echoed into the inbox, was scored as a confirmed
    reply.** Caught in a live run. A Bcc to self, a mailing list, or a provider
    filing sent mail in the inbox all produce this. The correlator now rejects
    any inbound message carrying a Message-ID we minted.
12. **An account that had ever sent email could never be deleted.**
    `email_messages.workflow_run_id` was `ON DELETE SET NULL` while a CHECK
    required outbound rows to have one, so deletion always failed with
    `email_messages_outbound_has_workflow`. This would have blocked any
    data-deletion request. Fixed by cascading
    (`20260906000300_email_messages_cascade`); verified on both databases.
13. **The scheduler leaked a Supabase client per poll tick.**
14. **Dead code** — `app/models/` (unimported, already drifted) and a stub
    `/evaluations/health` route.

## Evaluation state

System cases (deterministic) — **5/5 pass [measured]**:

| Case | Behaviour |
|---|---|
| TC11 | Confirmed reply correlates and stops follow-ups |
| TC12 | Unrelated mail, auto-responders, bounces and own echoes do not |
| TC13 | Duplicate IMAP processing creates one effect |
| TC14 | SMTP failure is retryable and never a false completion |
| TC15 | Unapproved workflow cannot send |

Database enforcement — **40/40 local, 24/24 hosted [measured]**.

Extraction cases TC01–TC10 — **[unmeasured]**. The last full pass
(`evaluation/results/2026-09-06-run1.md`) met the targets for action recall,
owner extraction, deadline extraction, forbidden claims and latency, and missed
on decision precision. Those numbers predate defect 6 and three
case-specification corrections, so they are stale and are **not** restated as
current. Re-run with `python evaluation/run_eval.py` once quota resets.

Never measured: manual baseline, human review time.

## Remaining limitations

- **Extraction metrics unmeasured** — Gemini free tier is 20 requests/day.
- **CRM is simulated** — no external CRM adapter exists; every surface says so.
- **`IN_DOUBT` needs a human** — SMTP cannot be asked whether it accepted a
  message, so an interrupted send is never auto-resent.
- **Authentication is one shared operator token**, not per-user identity.
- **Hosted catalog introspection unavailable** — Management API 403 and a CLI
  `db diff` bug; compensated by behavioural verification.
- **A workflow that has written an audit event cannot be deleted.** This is the
  append-only design working, but it means test fixtures persist. One such
  fixture remains on the hosted project, marked
  `failed / "Verification fixture: pause-guard test, never executed."`

## Architectural decisions

- **Execution is a distributed workflow, not a transaction.** Postgres cannot
  roll back an accepted email, so each side effect is claimed, performed and
  settled independently.
- **Validation sanitizes as well as judges.** Ungrounded items are removed and
  unsupported owners and deadlines cleared; critical findings block approval.
- **Only a confirmed reply pauses.** Weak evidence yields `possible_reply` and a
  review prompt — demonstrated live on hosted.
- **Our own Message-IDs are never replies.** Checked before any heuristic.
- **The mailbox is read-only.** Deduplication uses a database unique index, so
  polling is safe to repeat and restart.
- **Safety is enforced twice** — in the service and in the database — because a
  single layer is one bug away from failing.
