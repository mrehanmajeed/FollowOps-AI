create extension if not exists pgcrypto;

create type public.workflow_status as enum (
    'created',
    'processing',
    'validating',
    'awaiting_approval',
    'approved',
    'executing',
    'completed',
    'rejected',
    'failed'
);

create type public.task_status as enum (
    'open',
    'in_progress',
    'completed',
    'cancelled'
);

create table public.accounts (
    id uuid primary key default gen_random_uuid(),
    company_name text not null,
    contact_name text,
    contact_email text,
    account_context text,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    constraint accounts_company_name_length check (char_length(company_name) between 1 and 200),
    constraint accounts_contact_name_length check (
        contact_name is null or char_length(contact_name) <= 200
    ),
    constraint accounts_contact_email_format check (
        contact_email is null
        or contact_email ~* '^[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}$'
    ),
    constraint accounts_context_length check (
        account_context is null or char_length(account_context) <= 10000
    )
);

create table public.meetings (
    id uuid primary key default gen_random_uuid(),
    account_id uuid not null references public.accounts(id) on delete cascade,
    meeting_date date not null,
    notes text not null,
    created_at timestamptz not null default now(),
    constraint meetings_notes_length check (char_length(notes) between 10 and 50000)
);

create table public.workflow_runs (
    id uuid primary key default gen_random_uuid(),
    meeting_id uuid not null references public.meetings(id) on delete cascade,
    status public.workflow_status not null default 'created',
    model text not null,
    latency_ms integer,
    estimated_cost numeric(12,6),
    overall_confidence numeric(5,4),
    idempotency_key text,
    failure_code text,
    failure_message text,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    started_at timestamptz,
    completed_at timestamptz,
    constraint workflow_runs_model_length check (char_length(model) between 1 and 200),
    constraint workflow_runs_latency_nonnegative check (
        latency_ms is null or latency_ms >= 0
    ),
    constraint workflow_runs_cost_nonnegative check (
        estimated_cost is null or estimated_cost >= 0
    ),
    constraint workflow_runs_confidence_range check (
        overall_confidence is null or overall_confidence between 0 and 1
    ),
    constraint workflow_runs_failure_message_length check (
        failure_message is null or char_length(failure_message) <= 5000
    )
);

create unique index workflow_runs_idempotency_key_uidx
on public.workflow_runs(idempotency_key)
where idempotency_key is not null;

create table public.followup_packages (
    id uuid primary key default gen_random_uuid(),
    workflow_run_id uuid not null unique references public.workflow_runs(id) on delete cascade,
    summary text not null,
    structured_output jsonb not null,
    email_subject text not null,
    email_body text not null,
    approved boolean not null default false,
    edited boolean not null default false,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    constraint followup_packages_summary_length check (char_length(summary) <= 20000),
    constraint followup_packages_subject_length check (
        char_length(email_subject) between 1 and 200
    ),
    constraint followup_packages_body_length check (char_length(email_body) between 1 and 50000),
    constraint followup_packages_structured_output_object check (
        jsonb_typeof(structured_output) = 'object'
    )
);

create table public.tasks (
    id uuid primary key default gen_random_uuid(),
    account_id uuid not null references public.accounts(id) on delete cascade,
    workflow_run_id uuid not null references public.workflow_runs(id) on delete cascade,
    title text not null,
    owner text,
    due_date date,
    status public.task_status not null default 'open',
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    constraint tasks_title_length check (char_length(title) between 1 and 1000),
    constraint tasks_owner_length check (owner is null or char_length(owner) <= 200)
);

create table public.audit_events (
    id bigint generated always as identity primary key,
    workflow_run_id uuid not null references public.workflow_runs(id) on delete cascade,
    event_type text not null,
    message text not null,
    metadata jsonb not null default '{}'::jsonb,
    created_at timestamptz not null default now(),
    constraint audit_events_event_type_length check (
        char_length(event_type) between 1 and 100
    ),
    constraint audit_events_message_length check (
        char_length(message) between 1 and 5000
    ),
    constraint audit_events_metadata_object check (
        jsonb_typeof(metadata) = 'object'
    )
);

create index meetings_account_id_idx
on public.meetings(account_id);

create index meetings_meeting_date_idx
on public.meetings(meeting_date desc);

create index workflow_runs_meeting_id_idx
on public.workflow_runs(meeting_id);

create index workflow_runs_status_idx
on public.workflow_runs(status);

create index workflow_runs_created_at_idx
on public.workflow_runs(created_at desc);

create index followup_packages_created_at_idx
on public.followup_packages(created_at desc);

create index tasks_account_id_status_idx
on public.tasks(account_id, status);

create index tasks_workflow_run_id_idx
on public.tasks(workflow_run_id);

create index tasks_due_date_idx
on public.tasks(due_date)
where status in ('open', 'in_progress');

create index audit_events_workflow_run_id_created_at_idx
on public.audit_events(workflow_run_id, created_at desc);

create or replace function public.set_updated_at()
returns trigger
language plpgsql
as $$
begin
    new.updated_at = now();
    return new;
end;
$$;

create trigger accounts_set_updated_at
before update on public.accounts
for each row
execute function public.set_updated_at();

create trigger workflow_runs_set_updated_at
before update on public.workflow_runs
for each row
execute function public.set_updated_at();

create trigger followup_packages_set_updated_at
before update on public.followup_packages
for each row
execute function public.set_updated_at();

create trigger tasks_set_updated_at
before update on public.tasks
for each row
execute function public.set_updated_at();

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

    return new;
end;
$$;

create trigger workflow_runs_enforce_transition
before update of status on public.workflow_runs
for each row
execute function public.enforce_workflow_transition();

create or replace function public.prevent_audit_mutation()
returns trigger
language plpgsql
as $$
begin
    raise exception 'audit_events is append-only'
        using errcode = '42501';
end;
$$;

create trigger audit_events_no_update
before update on public.audit_events
for each row
execute function public.prevent_audit_mutation();

create trigger audit_events_no_delete
before delete on public.audit_events
for each row
execute function public.prevent_audit_mutation();

create or replace function public.validate_task_account_consistency()
returns trigger
language plpgsql
as $$
declare
    workflow_account_id uuid;
begin
    select m.account_id
    into workflow_account_id
    from public.workflow_runs wr
    join public.meetings m on m.id = wr.meeting_id
    where wr.id = new.workflow_run_id;

    if workflow_account_id is null then
        raise exception 'Workflow run does not resolve to an account'
            using errcode = '23503';
    end if;

    if workflow_account_id <> new.account_id then
        raise exception 'Task account_id does not match workflow account'
            using errcode = '23514';
    end if;

    return new;
end;
$$;

create trigger tasks_validate_account
before insert or update on public.tasks
for each row
execute function public.validate_task_account_consistency();

alter table public.accounts enable row level security;
alter table public.meetings enable row level security;
alter table public.workflow_runs enable row level security;
alter table public.followup_packages enable row level security;
alter table public.tasks enable row level security;
alter table public.audit_events enable row level security;

revoke all on table public.accounts from anon, authenticated;
revoke all on table public.meetings from anon, authenticated;
revoke all on table public.workflow_runs from anon, authenticated;
revoke all on table public.followup_packages from anon, authenticated;
revoke all on table public.tasks from anon, authenticated;
revoke all on table public.audit_events from anon, authenticated;

grant select, insert, update, delete on table public.accounts to service_role;
grant select, insert, update, delete on table public.meetings to service_role;
grant select, insert, update, delete on table public.workflow_runs to service_role;
grant select, insert, update, delete on table public.followup_packages to service_role;
grant select, insert, update, delete on table public.tasks to service_role;
grant select, insert on table public.audit_events to service_role;

grant usage, select on sequence public.audit_events_id_seq to service_role;
