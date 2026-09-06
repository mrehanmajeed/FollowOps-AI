-- Make a workflow with sent email deletable again.
--
-- `email_messages.workflow_run_id` was ON DELETE SET NULL, but the table also
-- carries `email_messages_outbound_has_workflow`, which requires an outbound
-- row to have one. Deleting a workflow therefore tried to null a column that
-- must not be null, and Postgres refused:
--
--   new row for relation "email_messages" violates check constraint
--   "email_messages_outbound_has_workflow"
--
-- The practical effect was that any account which had ever sent a follow-up
-- became permanently undeletable — a problem for a data-deletion request, and
-- the reason test fixtures could not be cleaned up.
--
-- An email record belongs to the workflow that sent it, so cascade is the
-- coherent behaviour: delete the workflow, delete its email records with it.
-- The account link stays SET NULL, since an inbound message can legitimately
-- outlive the account it was matched to.

alter table public.email_messages
    drop constraint email_messages_workflow_run_id_fkey;

alter table public.email_messages
    add constraint email_messages_workflow_run_id_fkey
        foreign key (workflow_run_id)
        references public.workflow_runs(id)
        on delete cascade;
