-- FollowOps AI — execution idempotency, outbound/inbound email, reply pausing.
--
-- Rationale: Zoho SMTP, an external CRM and PostgreSQL cannot participate in one
-- atomic transaction. Execution is therefore modelled as a set of individually
-- claimable, individually retryable operations with durable outcomes.

create type public.operation_status as enum (
    'pending',
    'succeeded',
    'failed'
);

create type public.email_direction as enum (
    'outbound',
    'inbound'
);

-- How confident the correlator is that an inbound message replies to one of our
-- outbound emails. 'possible_reply' never stops a sequence on its own.
create type public.email_correlation as enum (
    'outbound',
    'confirmed_reply',
    'possible_reply',
    'unrelated'
);

-- ---------------------------------------------------------------------------
-- Per-side-effect execution records
-- ---------------------------------------------------------------------------

create table public.workflow_operations (
    id uuid primary key default gen_random_uuid(),
    workflow_run_id uuid not null references public.workflow_runs(id) on delete cascade,
    operation_type text not null,
    idempotency_key text not null,
    status public.operation_status not null default 'pending',
    attempts integer not null default 0,
    provider_id text,
    error text,
    result jsonb not null default '{}'::jsonb,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    completed_at timestamptz,
    constraint workflow_operations_type_valid check (
        operation_type in ('email_send', 'task_create', 'crm_update')
    ),
    constraint workflow_operations_key_length check (
        char_length(idempotency_key) between 1 and 200
    ),
    constraint workflow_operations_error_length check (
        error is null or char_length(error) <= 5000
    ),
    constraint workflow_operations_result_object check (
        jsonb_typeof(result) = 'object'
    )
);

-- The claim: a second executor inserting the same key loses, so exactly one
-- caller may perform each side effect.
create unique index workflow_operations_idempotency_key_uidx
on public.workflow_operations(idempotency_key);

create unique index workflow_operations_run_type_uidx
on public.workflow_operations(workflow_run_id, operation_type);

create index workflow_operations_status_idx
on public.workflow_operations(status);

create trigger workflow_operations_set_updated_at
before update on public.workflow_operations
for each row
execute function public.set_updated_at();

-- ---------------------------------------------------------------------------
-- Outbound and inbound email
-- ---------------------------------------------------------------------------

create table public.email_messages (
    id uuid primary key default gen_random_uuid(),
    direction public.email_direction not null,
    message_id text,
    in_reply_to text,
    references_header text,
    account_id uuid references public.accounts(id) on delete set null,
    workflow_run_id uuid references public.workflow_runs(id) on delete set null,
    from_address text,
    to_address text,
    subject text,
    normalized_subject text,
    mailbox text,
    imap_uid text,
    correlation public.email_correlation not null default 'unrelated',
    correlation_reason text,
    sent_at timestamptz,
    received_at timestamptz,
    created_at timestamptz not null default now(),
    constraint email_messages_subject_length check (
        subject is null or char_length(subject) <= 2000
    ),
    constraint email_messages_reason_length check (
        correlation_reason is null or char_length(correlation_reason) <= 1000
    ),
    constraint email_messages_outbound_has_workflow check (
        direction = 'inbound' or workflow_run_id is not null
    )
);

-- Idempotent IMAP processing: re-reading the same message is a no-op.
create unique index email_messages_direction_message_id_uidx
on public.email_messages(direction, message_id)
where message_id is not null;

create index email_messages_in_reply_to_idx
on public.email_messages(in_reply_to)
where in_reply_to is not null;

create index email_messages_workflow_run_id_idx
on public.email_messages(workflow_run_id);

create index email_messages_account_id_idx
on public.email_messages(account_id);

create index email_messages_normalized_subject_idx
on public.email_messages(normalized_subject)
where normalized_subject is not null;

create index email_messages_correlation_idx
on public.email_messages(correlation);

-- ---------------------------------------------------------------------------
-- Reply-driven follow-up pausing
-- ---------------------------------------------------------------------------

alter table public.accounts
    add column followups_paused boolean not null default false,
    add column followups_paused_reason text,
    add column last_reply_at timestamptz,
    add constraint accounts_pause_reason_length check (
        followups_paused_reason is null
        or char_length(followups_paused_reason) <= 1000
    );

-- ---------------------------------------------------------------------------
-- Allow a failed execution to be retried. Generation failures are not
-- retryable in place: those require a new workflow run.
-- ---------------------------------------------------------------------------

create or replace function public.enforce_workflow_transition()
returns trigger
language plpgsql
as $$
begin
    if old.status = new.status then
        return new;
    end if;

    if not (
        (old.status = 'created' and new.status = 'processing')
        or (old.status = 'processing' and new.status = 'validating')
        or (old.status = 'processing' and new.status = 'awaiting_approval')
        or (old.status = 'processing' and new.status = 'failed')
        or (old.status = 'validating' and new.status = 'awaiting_approval')
        or (old.status = 'validating' and new.status = 'failed')
        or (old.status = 'awaiting_approval' and new.status = 'approved')
        or (old.status = 'awaiting_approval' and new.status = 'rejected')
        or (old.status = 'awaiting_approval' and new.status = 'failed')
        or (old.status = 'approved' and new.status = 'executing')
        or (old.status = 'approved' and new.status = 'failed')
        or (old.status = 'executing' and new.status = 'completed')
        or (old.status = 'executing' and new.status = 'failed')
        -- retry of a partially executed workflow; operations are idempotent
        or (old.status = 'failed' and new.status = 'executing')
    ) then
        raise exception 'Invalid workflow transition: % -> %', old.status, new.status
            using errcode = '22023';
    end if;

    if new.status in ('processing', 'validating', 'approved', 'executing')
       and new.started_at is null then
        new.started_at = now();
    end if;

    if new.status = 'completed' and new.completed_at is null then
        new.completed_at = now();
    end if;

    if new.status = 'failed' and new.failure_message is null then
        new.failure_message = 'Workflow execution failed';
    end if;

    -- A retry clears the previous failure so the record reflects the attempt
    -- currently in flight.
    if new.status = 'executing' then
        new.failure_code = null;
        new.failure_message = null;
    end if;

    return new;
end;
$$;

-- ---------------------------------------------------------------------------
-- Access model: server-only, unchanged.
-- ---------------------------------------------------------------------------

alter table public.workflow_operations enable row level security;
alter table public.email_messages enable row level security;

revoke all on table public.workflow_operations from anon, authenticated;
revoke all on table public.email_messages from anon, authenticated;

grant select, insert, update on table public.workflow_operations to service_role;
grant select, insert, update on table public.email_messages to service_role;

-- ---------------------------------------------------------------------------
-- Validation outcome travels with the package the human reviews.
-- ---------------------------------------------------------------------------

alter table public.followup_packages
    add column validation_issues jsonb not null default '[]'::jsonb,
    add column blocked boolean not null default false,
    add constraint followup_packages_validation_issues_array check (
        jsonb_typeof(validation_issues) = 'array'
    );
