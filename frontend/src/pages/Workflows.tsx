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
  statusTone,
} from "../components/ui";
import type { Account, WorkflowSummary } from "../types";

export function Workflows({ onOpen }: { onOpen: (id: string) => void }) {
  const [accounts, setAccounts] = useState<Account[]>([]);
  const [workflows, setWorkflows] = useState<WorkflowSummary[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const [accountId, setAccountId] = useState("");
  const [meetingDate, setMeetingDate] = useState(
    new Date().toISOString().slice(0, 10),
  );
  const [notes, setNotes] = useState("");

  async function load() {
    try {
      const [accountList, workflowList] = await Promise.all([
        api.listAccounts(),
        api.listWorkflows(),
      ]);
      setAccounts(accountList);
      setWorkflows(workflowList);
      if (!accountId && accountList.length) setAccountId(accountList[0].id);
      setError(null);
    } catch (err) {
      setError((err as Error).message);
    }
  }

  useEffect(() => {
    void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function generate() {
    setBusy(true);
    setError(null);
    try {
      const created = await api.createWorkflow({
        account_id: accountId,
        meeting_date: meetingDate,
        notes,
      });
      setNotes("");
      onOpen(created.id);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  const selected = accounts.find((a) => a.id === accountId);

  return (
    <div className="space-y-4">
      <ErrorBanner message={error} />

      <Card title="New follow-up">
        {accounts.length === 0 ? (
          <Empty>Create an account first.</Empty>
        ) : (
          <div className="space-y-3">
            <div className="grid gap-3 md:grid-cols-2">
              <Field label="Account">
                <select
                  className={inputClass}
                  value={accountId}
                  onChange={(e) => setAccountId(e.target.value)}
                >
                  {accounts.map((account) => (
                    <option key={account.id} value={account.id}>
                      {account.company_name}
                      {account.followups_paused ? " (paused — replied)" : ""}
                    </option>
                  ))}
                </select>
              </Field>
              <Field
                label="Meeting date"
                hint="Relative dates in the notes are resolved against this date."
              >
                <input
                  type="date"
                  className={inputClass}
                  value={meetingDate}
                  onChange={(e) => setMeetingDate(e.target.value)}
                />
              </Field>
            </div>

            {selected && !selected.contact_email && (
              <p className="text-xs text-amber-700">
                This account has no contact email, so execution will be refused.
              </p>
            )}

            <Field label="Meeting notes" hint="Minimum 10 characters.">
              <textarea
                className={`${inputClass} h-48`}
                value={notes}
                onChange={(e) => setNotes(e.target.value)}
                placeholder="Paste the raw meeting notes here."
              />
            </Field>

            <Button
              variant="primary"
              onClick={generate}
              disabled={busy || notes.trim().length < 10 || !accountId}
            >
              {busy ? "Generating…" : "Generate follow-up"}
            </Button>
            <p className="text-xs text-slate-400">
              Generating never sends anything. You review and approve first.
            </p>
          </div>
        )}
      </Card>

      <Card title="Workflows" actions={<Button onClick={load}>Refresh</Button>}>
        {workflows.length === 0 ? (
          <Empty>No workflows yet.</Empty>
        ) : (
          <table className="w-full text-left text-sm">
            <thead className="text-xs uppercase text-slate-400">
              <tr>
                <th className="py-1">Created</th>
                <th>Status</th>
                <th>Latency</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {workflows.map((workflow) => (
                <tr key={workflow.id} className="border-t border-slate-100">
                  <td className="py-1.5">
                    {new Date(workflow.created_at).toLocaleString()}
                  </td>
                  <td>
                    <Badge tone={statusTone(workflow.status)}>
                      {workflow.status}
                    </Badge>
                  </td>
                  <td className="text-slate-500">
                    {workflow.latency_ms ? `${workflow.latency_ms} ms` : "—"}
                  </td>
                  <td className="text-right">
                    <Button onClick={() => onOpen(workflow.id)}>Review</Button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>
    </div>
  );
}
