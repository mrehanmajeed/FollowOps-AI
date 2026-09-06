begin;

select plan(20);

select has_table('public', 'accounts');
select has_table('public', 'meetings');
select has_table('public', 'workflow_runs');
select has_table('public', 'followup_packages');
select has_table('public', 'tasks');
select has_table('public', 'audit_events');
select has_table('public', 'workflow_operations');
select has_table('public', 'email_messages');

select has_index('public', 'workflow_runs', 'workflow_runs_idempotency_key_uidx');
select has_index('public', 'workflow_runs', 'workflow_runs_status_idx');
select has_index('public', 'audit_events', 'audit_events_workflow_run_id_created_at_idx');

-- Execution idempotency: one claim per side effect.
select has_index(
    'public', 'workflow_operations', 'workflow_operations_idempotency_key_uidx'
);
select has_index(
    'public', 'workflow_operations', 'workflow_operations_run_type_uidx'
);

-- Inbound mail deduplication: reprocessing the same message is a no-op.
select has_index(
    'public', 'email_messages', 'email_messages_direction_message_id_uidx'
);

select has_type('public', 'workflow_status');
select has_type('public', 'task_status');
select has_type('public', 'operation_status');
select has_type('public', 'email_direction');
select has_type('public', 'email_correlation');

select has_function('public', 'enforce_workflow_transition');

select * from finish();

rollback;
