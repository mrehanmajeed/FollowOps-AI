-- Behavioural checks: does the database actually refuse what it must refuse?
--
-- The other test files assert that objects exist. This one asserts they work.
-- Run against a database with the migrations applied:
--
--   docker exec -i supabase_db_followops-ai \
--     psql -U postgres -d postgres -f - < supabase/tests/enforcement.sql
--
-- Everything happens inside a transaction that is rolled back.

begin;

create temp table results (name text, passed boolean, detail text);

create or replace function assert_fails(label text, stmt text)
returns void language plpgsql as $$
begin
    execute stmt;
    insert into results values (label, false, 'statement unexpectedly succeeded');
exception when others then
    insert into results values (label, true, left(sqlerrm, 70));
end;
$$;

create or replace function assert_works(label text, stmt text)
returns void language plpgsql as $$
begin
    execute stmt;
    insert into results values (label, true, 'ok');
exception when others then
    insert into results values (label, false, left(sqlerrm, 70));
end;
$$;

-- Fixtures ------------------------------------------------------------------

insert into public.accounts (id, company_name, contact_email)
values ('99000000-0000-0000-0000-000000000001', 'Enforcement Test', 'e2e@example.com');

insert into public.meetings (id, account_id, meeting_date, notes)
values (
    '99000000-0000-0000-0000-000000000002',
    '99000000-0000-0000-0000-000000000001',
    '2026-09-05',
    'Sarah confirmed that we will prepare a commercial proposal.'
);

insert into public.workflow_runs (id, meeting_id, status, model)
values (
    '99000000-0000-0000-0000-000000000003',
    '99000000-0000-0000-0000-000000000002',
    'awaiting_approval',
    'gemini-2.5-flash'
);

-- 1. The approval gate -------------------------------------------------------

select assert_fails(
    'awaiting_approval -> completed is rejected',
    $$update public.workflow_runs set status='completed'
      where id='99000000-0000-0000-0000-000000000003'$$
);

select assert_fails(
    'awaiting_approval -> executing is rejected (must pass through approved)',
    $$update public.workflow_runs set status='executing'
      where id='99000000-0000-0000-0000-000000000003'$$
);

select assert_works(
    'awaiting_approval -> approved is allowed',
    $$update public.workflow_runs set status='approved'
      where id='99000000-0000-0000-0000-000000000003'$$
);

select assert_works(
    'approved -> executing is allowed',
    $$update public.workflow_runs set status='executing'
      where id='99000000-0000-0000-0000-000000000003'$$
);

select assert_works(
    'executing -> completed is allowed',
    $$update public.workflow_runs set status='completed'
      where id='99000000-0000-0000-0000-000000000003'$$
);

select assert_fails(
    'completed is terminal',
    $$update public.workflow_runs set status='executing'
      where id='99000000-0000-0000-0000-000000000003'$$
);

-- 2. Retry path --------------------------------------------------------------

insert into public.workflow_runs (id, meeting_id, status, model)
values (
    '99000000-0000-0000-0000-000000000004',
    '99000000-0000-0000-0000-000000000002',
    'awaiting_approval',
    'gemini-2.5-flash'
);
update public.workflow_runs set status='approved'
where id='99000000-0000-0000-0000-000000000004';
update public.workflow_runs set status='executing'
where id='99000000-0000-0000-0000-000000000004';
update public.workflow_runs set status='failed'
where id='99000000-0000-0000-0000-000000000004';

select assert_works(
    'failed -> executing is allowed (idempotent retry)',
    $$update public.workflow_runs set status='executing'
      where id='99000000-0000-0000-0000-000000000004'$$
);

-- completed_at / started_at are maintained by the trigger
insert into results
select 'started_at set automatically', started_at is not null, coalesce(started_at::text,'null')
from public.workflow_runs where id='99000000-0000-0000-0000-000000000004';

insert into results
select 'completed_at set automatically', completed_at is not null, coalesce(completed_at::text,'null')
from public.workflow_runs where id='99000000-0000-0000-0000-000000000003';

-- 3. Execution idempotency ---------------------------------------------------

insert into public.workflow_operations
    (workflow_run_id, operation_type, idempotency_key, status)
values
    ('99000000-0000-0000-0000-000000000003', 'email_send',
     '99000000-0000-0000-0000-000000000003:email_send', 'succeeded');

select assert_fails(
    'duplicate operation idempotency key is rejected',
    $$insert into public.workflow_operations
        (workflow_run_id, operation_type, idempotency_key, status)
      values ('99000000-0000-0000-0000-000000000003', 'email_send',
              '99000000-0000-0000-0000-000000000003:email_send', 'pending')$$
);

select assert_fails(
    'second operation of the same type on one workflow is rejected',
    $$insert into public.workflow_operations
        (workflow_run_id, operation_type, idempotency_key, status)
      values ('99000000-0000-0000-0000-000000000003', 'email_send',
              'some-other-key', 'pending')$$
);

select assert_fails(
    'unknown operation type is rejected',
    $$insert into public.workflow_operations
        (workflow_run_id, operation_type, idempotency_key, status)
      values ('99000000-0000-0000-0000-000000000003', 'delete_everything',
              'k2', 'pending')$$
);

-- 4. Inbound email deduplication --------------------------------------------

insert into public.email_messages (direction, message_id, from_address)
values ('inbound', 'dup-test@example.com', 'sarah@example.com');

select assert_fails(
    'duplicate inbound message_id is rejected',
    $$insert into public.email_messages (direction, message_id, from_address)
      values ('inbound', 'dup-test@example.com', 'sarah@example.com')$$
);

select assert_works(
    'the same message_id in the other direction is allowed',
    $$insert into public.email_messages
        (direction, message_id, workflow_run_id, to_address)
      values ('outbound', 'dup-test@example.com',
              '99000000-0000-0000-0000-000000000003', 'sarah@example.com')$$
);

select assert_fails(
    'outbound email without a workflow is rejected',
    $$insert into public.email_messages (direction, message_id, to_address)
      values ('outbound', 'orphan@example.com', 'sarah@example.com')$$
);

-- 5. Append-only audit -------------------------------------------------------

insert into public.audit_events (workflow_run_id, event_type, message)
values ('99000000-0000-0000-0000-000000000003', 'test_event', 'created');

select assert_fails(
    'audit events cannot be updated',
    $$update public.audit_events set message='tampered'
      where event_type='test_event'$$
);

select assert_fails(
    'audit events cannot be deleted',
    $$delete from public.audit_events where event_type='test_event'$$
);

-- 6. Task / account consistency ---------------------------------------------

select assert_fails(
    'a task cannot be attached to the wrong account',
    $$insert into public.tasks (account_id, workflow_run_id, title)
      values ('10000000-0000-0000-0000-000000000001',
              '99000000-0000-0000-0000-000000000003', 'wrong account')$$
);

select assert_works(
    'a task on the correct account is accepted',
    $$insert into public.tasks (account_id, workflow_run_id, title)
      values ('99000000-0000-0000-0000-000000000001',
              '99000000-0000-0000-0000-000000000003', 'right account')$$
);

-- 7. Data validation ---------------------------------------------------------

select assert_fails(
    'confidence outside 0..1 is rejected',
    $$insert into public.workflow_runs (meeting_id, status, model, overall_confidence)
      values ('99000000-0000-0000-0000-000000000002', 'created', 'm', 1.5)$$
);

select assert_fails(
    'notes shorter than the minimum are rejected',
    $$insert into public.meetings (account_id, meeting_date, notes)
      values ('99000000-0000-0000-0000-000000000001', '2026-09-05', 'short')$$
);

select assert_fails(
    'a malformed contact email is rejected',
    $$insert into public.accounts (company_name, contact_email)
      values ('Bad', 'not-an-email')$$
);

-- 8. Data API is closed to browser roles ------------------------------------

insert into results
select
    'anon has no privileges on '||t,
    not has_table_privilege('anon', 'public.'||t, 'SELECT'),
    'select'
from unnest(array['accounts','meetings','workflow_runs','followup_packages',
                  'tasks','audit_events','workflow_operations','email_messages']) as t;

insert into results
select
    'authenticated has no privileges on '||t,
    not has_table_privilege('authenticated', 'public.'||t, 'SELECT'),
    'select'
from unnest(array['accounts','meetings','workflow_runs','followup_packages',
                  'tasks','audit_events','workflow_operations','email_messages']) as t;

insert into results
select 'service_role cannot delete audit_events',
       not has_table_privilege('service_role', 'public.audit_events', 'DELETE'),
       'delete';

insert into results
select 'service_role cannot update audit_events',
       not has_table_privilege('service_role', 'public.audit_events', 'UPDATE'),
       'update';

-- Report ---------------------------------------------------------------------

select
    case when passed then 'PASS' else 'FAIL' end as result,
    name,
    case when passed then '' else detail end as detail
from results
order by passed, name;

select
    count(*) filter (where passed) as passed,
    count(*) filter (where not passed) as failed,
    count(*) as total
from results;

rollback;
