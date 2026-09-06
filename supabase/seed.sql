insert into public.accounts (
    id,
    company_name,
    contact_name,
    contact_email,
    account_context
) values (
    '10000000-0000-0000-0000-000000000001',
    'Acme AI Labs',
    'Sarah Khan',
    'sarah@example.com',
    'Existing customer evaluating an AI workflow automation engagement.'
);

insert into public.meetings (
    id,
    account_id,
    meeting_date,
    notes
) values (
    '20000000-0000-0000-0000-000000000001',
    '10000000-0000-0000-0000-000000000001',
    '2026-09-05',
    'The customer agreed that we will prepare a proposal. Sarah will review the proposal next week. We will schedule a technical workshop after the proposal review.'
);
