# FollowOps AI

An AI-assisted operating system for the work that happens **after** a customer
meeting: turning raw notes into a validated follow-up package, getting a human
to approve it, executing the external actions safely, and detecting when the
prospect replies.

```text
Meeting notes + account context
              ↓
        AI extraction            (Gemini, structured output)
              ↓
   Grounding + validation        (deterministic code)
              ↓
      Follow-up package
              ↓
       Human approval            (mandatory gate)
              ↓
     External execution
   ┌──────────┼───────────┐
   ↓          ↓           ↓
 Email      Tasks        CRM
   └──────────┼───────────┘
              ↓
          Audit log
              ↓
   IMAP reply detection  →  pause further follow-ups
```

The governing principle:

```text
LLM proposes → code validates → human approves → tools execute → system audits
```

The LLM is never the authority for an external side effect.

## What makes this more than "an LLM writes an email"

| Concern | How it is handled |
|---|---|
| Hallucinated commitments | Every decision/action must quote evidence that literally occurs in the notes. Ungrounded items are removed and flagged; a package with a critical issue **cannot be approved** until edited. |
| Invented owners and dates | Owners not named in the notes are cleared. Deadlines not traceable to the notes or an unambiguous relative cue are cleared. Dates in the email are checked as calendar dates against validated deadlines. |
| Prompt injection | Notes are fenced as untrusted data, forged delimiters are stripped, injection markers are surfaced to the operator — and, decisively, the model has no tool access. |
| Approval bypass | The database transition trigger and the service both require `awaiting_approval → approved → executing`. Tested. |
| Duplicate emails on retry | Each side effect is a claimed, durable operation row with a unique key. A retry skips what already succeeded. |
| Crash mid-send | Reported `IN_DOUBT` and never auto-resent, because SMTP cannot be asked "did you accept this?". |
| Prospect already replied | IMAP polling correlates inbound mail to outbound Message-IDs and pauses the account. The pause is re-checked immediately before the send. |
| Fake CRM success | CRM runs in `simulated` mode and says so in the API and the UI. |

## Stack

- **Frontend** — React 19, Vite 7, Tailwind 4, TypeScript
- **Backend** — Python 3.13, FastAPI, Pydantic v2
- **AI** — Gemini via the official `google-genai` SDK, structured output, temperature 0
- **Database** — Supabase / PostgreSQL, RLS, server-only access
- **Email** — Zoho Mail SMTP (outbound) and IMAP (inbound reply detection)
- **Scheduling** — APScheduler, one coalescing job
- **Testing** — pytest, pytest-asyncio, ruff

## Quick start

### 1. Database

Apply both migrations to your Supabase project, in order:

```text
supabase/migrations/20260905000100_initial_schema.sql
supabase/migrations/20260906000100_execution_and_email.sql
```

With the Supabase CLI:

```bash
supabase link --project-ref <ref>
supabase db push
```

Without the CLI, paste each file into the Supabase dashboard SQL editor in
order. Optionally run `supabase/seed.sql` for a demo account.

### 2. Backend

```bash
cd backend
python -m venv .venv && .venv/Scripts/activate     # Windows
pip install -r requirements.txt
cp .env.example .env                                # then fill it in
uvicorn app.main:app --reload
```

Open http://localhost:8000/docs.

### 3. Frontend

```bash
cd frontend
npm install
cp .env.example .env
npm run dev
```

Open http://localhost:5173 and sign in with the `OPERATOR_API_KEY` value from
`backend/.env`.

### Docker

```bash
docker compose up --build
```

## Operator flow

1. Create an account (company, contact, contact email, context).
2. Paste meeting notes, set the meeting date, generate.
3. Review the package: summary, decisions, client/internal actions with their
   supporting evidence, open questions, risks, CRM update, email draft, and any
   validation issues.
4. Edit the email if needed — edits are re-validated.
5. Approve or reject. The approve button states exactly what will happen.
6. See per-operation results, the audit trail, and any detected replies.

## Verification

```bash
cd backend && python -m pytest -q          # unit + HTTP contract tests
cd backend && python -m ruff check app tests
cd frontend && npm run build               # includes strict tsc
python evaluation/run_eval.py --offline    # deterministic safety cases
python evaluation/run_eval.py              # + Gemini extraction cases
```

## Evaluation

15 cases: TC01–TC10 probe the model (missing deadlines, ambiguous owners,
rejected proposals, superseded decisions, relative dates, contradictions,
prompt injection, junk notes, CRM conflicts); TC11–TC15 assert deterministic
system behaviour (reply detection, unrelated mail, duplicate processing, SMTP
failure, approval bypass).

Results are written to `evaluation/results/`. Unmeasured metrics are reported
as unmeasured — see `docs/evaluation.md` and `PROGRESS.md` for the current
measured state and its caveats.

## Documentation

| Document | Contents |
|---|---|
| `docs/architecture.md` | Components, data flow, boundaries |
| `docs/workflow.md` | State machine, idempotency, execution contract |
| `docs/email-integration.md` | Zoho SMTP/IMAP, reply correlation, pausing |
| `docs/evaluation.md` | Methodology, cases, metrics |
| `docs/runbook.md` | Setup, operation, failure recovery |
| `docs/security.md` | Trust boundaries, secrets, injection, authz |
| `docs/api-contracts.md` | HTTP contract |
| `docs/case-study.md` | Problem, solution, results, limitations |
| `docs/demo-script.md` | Five-minute demo |
| `PROGRESS.md` | Current state, known issues, next steps |

## Known limitations

- The CRM integration is simulated; no external CRM is written to.
- A crash between SMTP acceptance and the local write leaves the operation
  `IN_DOUBT`, requiring an operator decision. SMTP offers no way to resolve this
  automatically.
- Authentication is a single shared operator token, not per-user identity.
- Reply correlation uses a layered heuristic; weak evidence yields
  `possible_reply` for human review rather than an automatic pause.
