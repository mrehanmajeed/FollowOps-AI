-- Narrow service_role on the append-only audit table.
--
-- Supabase grants blanket privileges to service_role on tables created in
-- `public`, so the earlier migration's `grant select, insert` did not actually
-- remove the update/delete rights that grant had already conferred. The
-- append-only triggers still rejected mutations, but the privilege was there,
-- which is weaker than least privilege and contradicts the documented model.
--
-- Belt and braces on purpose: the trigger stops the operation, the grant stops
-- it being attempted at all.

revoke update, delete, truncate on table public.audit_events from service_role;

-- The backend only ever reads and appends.
grant select, insert on table public.audit_events to service_role;
