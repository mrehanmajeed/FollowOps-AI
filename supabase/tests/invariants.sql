begin;

select plan(16);

select col_not_null('public', 'accounts', 'company_name');
select col_not_null('public', 'accounts', 'followups_paused');
select col_not_null('public', 'meetings', 'account_id');
select col_not_null('public', 'meetings', 'notes');
select col_not_null('public', 'workflow_runs', 'meeting_id');
select col_not_null('public', 'workflow_runs', 'status');
select col_not_null('public', 'followup_packages', 'workflow_run_id');
select col_not_null('public', 'followup_packages', 'structured_output');
select col_not_null('public', 'followup_packages', 'validation_issues');
select col_not_null('public', 'followup_packages', 'blocked');
select col_not_null('public', 'tasks', 'workflow_run_id');
select col_not_null('public', 'audit_events', 'workflow_run_id');
select col_not_null('public', 'audit_events', 'metadata');
select col_not_null('public', 'workflow_operations', 'workflow_run_id');
select col_not_null('public', 'workflow_operations', 'idempotency_key');
select col_not_null('public', 'email_messages', 'direction');

select * from finish();

rollback;
