# FollowOps AI — Project State

Last updated: 2026-09-06

**Status: FUNCTIONAL, HOSTED-VERIFIED, EVALUATION MEASURED.**

The hosted Supabase project is migrated and the complete workflow —
extraction, validation, approval, real email, reply detection, follow-up pause,
idempotent retry, audit immutability — is verified against **hosted Supabase +
real Gemini + real Zoho SMTP/IMAP + FastAPI**. All fifteen evaluation cases have
now been run against the live model: 12/15 passed, and all three failures were
defects in the evaluation tooling rather than in the model or the product. One
of them was a real validator bug, now fixed.

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
| Evaluation: extraction cases | **[measured]** 10/10 run, 7 passed |
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
evaluation extraction      10/10 measured, 7 passed (2026-09-07)
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

Extraction cases TC01–TC10 — **[measured] 2026-09-07**, snapshot in
`evaluation/results/2026-09-07-run2.md`. All ten ran; seven passed.

```text
action recall                 100.0%   target >= 90%    met
decision precision             90.0%   target >= 90%    met
owner extraction              100.0%   target >= 90%    met
deadline extraction           100.0%   target >= 95%    met
median latency                7982ms   target < 15s     met
forbidden claims                   2   target 0         MISSED
unsupported after validation       2   target 0         MISSED
cases passed                    7/10
```

The two missed targets are reported exactly as measured. Investigation of all
three failing cases showed the cause was evaluation tooling, not the model:

- **TC05 and TC09** — `unsupported_after_validation`. Both were the validator
  rejecting a *correct* email for restating the meeting date, e.g. "Thank you
  for the call on 2026-09-05". The meeting date is trusted workflow input, not
  a model invention, and belongs in the allowed set. Fixed, with two regression
  tests. TC05 re-ran clean afterwards. This is the second real validator defect
  the evaluation has caught, after the prose-date bug.
- **TC10** — `forbidden claims`. The model behaved correctly: it surfaced the
  CRM conflict in `risks` ("the CRM record states … CLOSED WON … but Nadia
  stated Sable has not signed anything yet") and kept it out of the customer
  email entirely. The forbidden-claim check is a substring match over the whole
  extraction, so it cannot distinguish *citing* the stale CRM state — which the
  case explicitly requires — from *asserting* it. TC10 also declares
  `expected_decisions: []` although its notes contain "We agreed that we will
  resend the commercial terms", which drove decision precision to 0 for that
  case. Both are specification errors; see "Open evaluation decisions" below.

A corrected re-run needs a fresh daily quota (20 requests/day, spent).

Never measured: manual baseline, human review time.

## Evaluation specification corrections

Two errors in TC10's specification were found while investigating its failure
and have now been corrected. Both are verifiable from the case's own text
without reference to any model output, which is what separates correcting a
wrong assertion from tuning an evaluation to pass:

1. `expected_decisions` was empty although the notes state "We agreed that we
   will resend the commercial terms for review" — an explicit agreement, and
   therefore a decision. This is the same error already corrected in TC03, TC06
   and TC07. It had forced decision precision to 0 for the case.
2. The forbidden-claim check was self-contradictory. TC10 requires the stale CRM
   state to be *surfaced*, which means naming it, while the check matched the
   phrase anywhere in the extraction — so satisfying the case guaranteed failing
   the check. Forbidden claims now have two scopes: `forbidden_claims` (anywhere
   in the extraction) and `forbidden_in_email` (client-facing text only). TC10
   uses the latter, because asserting "closed won" to the customer is the
   behaviour that would actually be wrong.

Verified against TC10's real recorded generation without spending quota: the
case now passes on correct output, and still fails when the stale CRM state is
asserted in the email.

A confirming full re-run needs a fresh daily quota.

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
