import { useEffect, useState } from "react";
import { api } from "../api/client";
import {
  Badge,
  Button,
  Card,
  Empty,
  ErrorBanner,
  Field,
  inputClass,
} from "../components/ui";
import type { Account } from "../types";

export function Accounts() {
  const [accounts, setAccounts] = useState<Account[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [form, setForm] = useState({
    company_name: "",
    contact_name: "",
    contact_email: "",
    account_context: "",
  });

  async function load() {
    try {
      setAccounts(await api.listAccounts());
      setError(null);
    } catch (err) {
      setError((err as Error).message);
    }
  }

  useEffect(() => {
    void load();
  }, []);

  async function create() {
    setBusy(true);
    setError(null);
    try {
      await api.createAccount({
        company_name: form.company_name,
        contact_name: form.contact_name || null,
        contact_email: form.contact_email || null,
        account_context: form.account_context || null,
      });
      setForm({
        company_name: "",
        contact_name: "",
        contact_email: "",
        account_context: "",
      });
      await load();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function togglePause(account: Account) {
    setBusy(true);
    try {
      await api.setFollowupPause(
        account.id,
        !account.followups_paused,
        account.followups_paused ? "Operator resumed" : "Operator paused",
      );
      await load();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-4">
      <ErrorBanner message={error} />

      <Card title="New account">
        <div className="grid gap-3 md:grid-cols-2">
          <Field label="Company name">
            <input
              className={inputClass}
              value={form.company_name}
              onChange={(e) => setForm({ ...form, company_name: e.target.value })}
            />
          </Field>
          <Field label="Contact name">
            <input
              className={inputClass}
              value={form.contact_name}
              onChange={(e) => setForm({ ...form, contact_name: e.target.value })}
            />
          </Field>
          <Field
            label="Contact email"
            hint="Follow-up emails are sent here. Without it, execution is refused."
          >
            <input
              className={inputClass}
              value={form.contact_email}
              onChange={(e) => setForm({ ...form, contact_email: e.target.value })}
            />
          </Field>
          <Field
            label="Account context"
            hint="Treated as untrusted data, same as meeting notes."
          >
            <textarea
              className={inputClass}
              value={form.account_context}
              onChange={(e) =>
                setForm({ ...form, account_context: e.target.value })
              }
            />
          </Field>
        </div>
        <div className="mt-3">
          <Button
            variant="primary"
            onClick={create}
            disabled={busy || !form.company_name.trim()}
          >
            Create account
          </Button>
        </div>
      </Card>

      <Card title="Accounts" actions={<Button onClick={load}>Refresh</Button>}>
        {accounts.length === 0 ? (
          <Empty>No accounts yet.</Empty>
        ) : (
          <ul className="divide-y divide-slate-100">
            {accounts.map((account) => (
              <li
                key={account.id}
                className="flex items-start justify-between gap-4 py-3"
              >
                <div>
                  <p className="text-sm font-medium text-slate-800">
                    {account.company_name}
                  </p>
                  <p className="text-xs text-slate-500">
                    {account.contact_name ?? "no contact"} ·{" "}
                    {account.contact_email ?? "no email"}
                  </p>
                  {account.followups_paused && (
                    <p className="mt-1 text-xs text-amber-700">
                      {account.followups_paused_reason}
                    </p>
                  )}
                </div>
                <div className="flex shrink-0 items-center gap-2">
                  <Badge tone={account.followups_paused ? "warn" : "good"}>
                    {account.followups_paused ? "paused" : "active"}
                  </Badge>
                  <Button onClick={() => togglePause(account)} disabled={busy}>
                    {account.followups_paused ? "Resume" : "Pause"}
                  </Button>
                </div>
              </li>
            ))}
          </ul>
        )}
      </Card>
    </div>
  );
}
