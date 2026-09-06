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
import type {
  ActionItem,
  EvidenceItem,
  ExecutionResponse,
  WorkflowDetail,
} from "../types";

/**
 * The review screen is the human half of the safety model, so it has to make
 * three things impossible to miss: what the AI claims, what evidence supports
 * it, and exactly what will happen on approval.
 */
export function Review({
  workflowId,
  onBack,
}: {
  workflowId: string;
  onBack: () => void;
}) {
  const [detail, setDetail] = useState<WorkflowDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [subject, setSubject] = useState("");
  const [body, setBody] = useState("");
  const [result, setResult] = useState<ExecutionResponse | null>(null);

  async function load() {
    try {
      const data = await api.getWorkflow(workflowId);
      setDetail(data);
      setSubject(data.package?.email_subject ?? "");
      setBody(data.package?.email_body ?? "");
      setError(null);
    } catch (err) {
      setError((err as Error).message);
    }
  }

  useEffect(() => {
    void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [workflowId]);

  async function act(fn: () => Promise<unknown>) {
    setBusy(true);
    setError(null);
    try {
      await fn();
      await load();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  if (!detail) {
    return (
      <div className="space-y-4">
        <Button onClick={onBack}>← Back</Button>
        <ErrorBanner message={error} />
        {!error && <Empty>Loading…</Empty>}
      </div>
    );
  }

  const pkg = detail.package;
  const extraction = pkg?.structured_output;
  const issues = pkg?.validation_issues ?? [];
  const critical = issues.filter((i) => i.severity === "critical");
  const status = detail.workflow.status;
  const paused = detail.account?.followups_paused ?? false;
  const canApprove = status === "awaiting_approval" && !pkg?.blocked;
  const edited =
    subject !== pkg?.email_subject || body !== pkg?.email_body;

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <Button onClick={onBack}>← Back</Button>
        <div className="flex items-center gap-2">
          <Badge tone={statusTone(status)}>{status}</Badge>
          {detail.workflow.latency_ms != null && (
            <span className="text-xs text-slate-400">
              {detail.workflow.latency_ms} ms
            </span>
          )}
        </div>
      </div>

      <ErrorBanner message={error} />

      {paused && (
        <div className="rounded-md border border-amber-300 bg-amber-50 px-3 py-2 text-sm text-amber-900">
          <strong>Follow-ups are paused for this account.</strong>{" "}
          {detail.account?.followups_paused_reason}
          {detail.account && (
            <div className="mt-2">
              <Button
                onClick={() =>
                  act(() =>
                    api.setFollowupPause(
                      detail.account!.id,
                      false,
                      "Operator resumed after reviewing the reply",
                    ),
                  )
                }
                disabled={busy}
              >
                Resume follow-ups
              </Button>
            </div>
          )}
        </div>
      )}

      {pkg?.blocked && (
        <div className="rounded-md border border-rose-300 bg-rose-50 px-3 py-2 text-sm text-rose-900">
          <strong>Approval blocked.</strong> Validation found content the meeting
          notes do not support. Edit the email below to remove it, then save.
        </div>
      )}

      {issues.length > 0 && (
        <Card title={`Validation (${critical.length} critical, ${issues.length - critical.length} warnings)`}>
          <ul className="space-y-2">
            {issues.map((issue, index) => (
              <li key={index} className="flex gap-2 text-sm">
                <Badge tone={issue.severity === "critical" ? "bad" : "warn"}>
                  {issue.code}
                </Badge>
                <span className="text-slate-600">
                  {issue.message}
                  {issue.item && (
                    <em className="ml-1 text-slate-400">“{issue.item}”</em>
                  )}
                </span>
              </li>
            ))}
          </ul>
        </Card>
      )}

      {detail.replies.length > 0 && (
        <Card title="Inbound replies">
          {detail.replies.map((reply) => (
            <div key={reply.id} className="border-b border-slate-100 py-2 last:border-0">
              <div className="flex items-center gap-2">
                <Badge tone={reply.correlation === "confirmed_reply" ? "good" : "warn"}>
                  {reply.correlation}
                </Badge>
                <span className="text-sm text-slate-700">{reply.from_address}</span>
              </div>
              <p className="text-xs text-slate-500">{reply.correlation_reason}</p>
            </div>
          ))}
        </Card>
      )}

      {extraction && (
        <>
          <Card title="Meeting summary">
            <p className="text-sm text-slate-700">{extraction.meeting_summary}</p>
          </Card>

          <ItemList title="Decisions" items={extraction.decisions} />
          <ItemList title="Client actions" items={extraction.client_actions} />
          <ItemList title="Internal actions" items={extraction.internal_actions} />

          <div className="grid gap-4 md:grid-cols-2">
            <Card title="Open questions">
              {extraction.open_questions.length ? (
                <ul className="list-disc space-y-1 pl-5 text-sm text-slate-700">
                  {extraction.open_questions.map((q, i) => (
                    <li key={i}>{q}</li>
                  ))}
                </ul>
              ) : (
                <Empty>None raised.</Empty>
              )}
            </Card>
            <Card title="Risks">
              {extraction.risks.length ? (
                <ul className="list-disc space-y-1 pl-5 text-sm text-slate-700">
                  {extraction.risks.map((r, i) => (
                    <li key={i}>{r}</li>
                  ))}
                </ul>
              ) : (
                <Empty>None raised.</Empty>
              )}
            </Card>
          </div>

          <Card title="CRM update">
            <dl className="space-y-1 text-sm">
              <Row label="Summary" value={extraction.crm_update.summary} />
              <Row label="Next step" value={extraction.crm_update.next_step} />
              <Row
                label="Next follow-up"
                value={extraction.crm_update.next_followup_date}
              />
            </dl>
          </Card>
        </>
      )}

      <Card
        title="Customer email"
        actions={
          status === "awaiting_approval" && (
            <Button
              onClick={() =>
                act(() =>
                  api.editPackage(workflowId, {
                    email_subject: subject,
                    email_body: body,
                  }),
                )
              }
              disabled={busy || !edited}
            >
              Save edits & re-validate
            </Button>
          )
        }
      >
        <div className="space-y-3">
          <Field label="Subject">
            <input
              className={inputClass}
              value={subject}
              onChange={(e) => setSubject(e.target.value)}
              disabled={status !== "awaiting_approval"}
            />
          </Field>
          <Field label="Body">
            <textarea
              className={`${inputClass} h-56 font-mono text-xs`}
              value={body}
              onChange={(e) => setBody(e.target.value)}
              disabled={status !== "awaiting_approval"}
            />
          </Field>
        </div>
      </Card>

      {status === "awaiting_approval" && (
        <Card title="Approve">
          <p className="mb-3 text-sm text-slate-600">
            Approving will <strong>send this email to {detail.account?.contact_email}</strong>
            , create {extraction?.internal_actions.length ?? 0} internal task(s),
            and record a <strong>simulated</strong> CRM update (no external CRM
            is connected).
          </p>
          <div className="flex gap-2">
            <Button
              variant="primary"
              disabled={busy || !canApprove || edited}
              onClick={() =>
                act(async () => {
                  setResult(await api.approve(workflowId, true));
                })
              }
            >
              Approve &amp; send
            </Button>
            <Button
              variant="danger"
              disabled={busy}
              onClick={() => act(() => api.approve(workflowId, false))}
            >
              Reject
            </Button>
          </div>
          {edited && (
            <p className="mt-2 text-xs text-amber-700">
              Save your edits before approving.
            </p>
          )}
        </Card>
      )}

      {status === "failed" && (
        <Card title="Recovery">
          <p className="mb-2 text-sm text-slate-600">
            {detail.workflow.failure_message}
          </p>
          <p className="mb-3 text-xs text-slate-500">
            Retrying re-runs only the actions that have not already succeeded.
          </p>
          <Button
            disabled={busy}
            onClick={() =>
              act(async () => {
                setResult(await api.retry(workflowId));
              })
            }
          >
            Retry execution
          </Button>
        </Card>
      )}

      {result && (
        <Card title="Execution result">
          <ul className="space-y-1 text-sm">
            {result.operations.map((op) => (
              <li key={op.operation_type} className="flex items-center gap-2">
                <Badge tone={op.status === "succeeded" ? "good" : "bad"}>
                  {op.status}
                </Badge>
                <span>{op.operation_type}</span>
                {op.skipped && (
                  <span className="text-xs text-slate-400">
                    (already done — not repeated)
                  </span>
                )}
                {op.error && <span className="text-xs text-rose-600">{op.error}</span>}
              </li>
            ))}
          </ul>
        </Card>
      )}

      <Card title="Operations">
        {detail.operations.length ? (
          <table className="w-full text-left text-sm">
            <thead className="text-xs uppercase text-slate-400">
              <tr>
                <th className="py-1">Action</th>
                <th>Status</th>
                <th>Attempts</th>
                <th>Provider id</th>
              </tr>
            </thead>
            <tbody>
              {detail.operations.map((op) => (
                <tr key={op.id} className="border-t border-slate-100">
                  <td className="py-1">{op.operation_type}</td>
                  <td>
                    <Badge tone={op.status === "succeeded" ? "good" : op.status === "failed" ? "bad" : "warn"}>
                      {op.status}
                    </Badge>
                  </td>
                  <td>{op.attempts}</td>
                  <td className="max-w-40 truncate font-mono text-xs text-slate-500">
                    {op.provider_id ?? "—"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <Empty>No external action has been attempted.</Empty>
        )}
      </Card>

      <Card title="Audit trail">
        <ol className="space-y-2">
          {detail.audit_events.map((event) => (
            <li key={event.id} className="text-sm">
              <span className="font-mono text-xs text-slate-400">
                {new Date(event.created_at).toLocaleString()}
              </span>{" "}
              <Badge>{event.event_type}</Badge>{" "}
              <span className="text-slate-600">{event.message}</span>
            </li>
          ))}
        </ol>
      </Card>
    </div>
  );
}

function ItemList({
  title,
  items,
}: {
  title: string;
  items: (EvidenceItem | ActionItem)[];
}) {
  return (
    <Card title={`${title} (${items.length})`}>
      {items.length === 0 ? (
        <Empty>None extracted.</Empty>
      ) : (
        <ul className="space-y-3">
          {items.map((item, index) => {
            const action = item as ActionItem;
            return (
              <li key={index} className="border-l-2 border-slate-200 pl-3">
                <p className="text-sm font-medium text-slate-800">{item.text}</p>
                <p className="mt-1 text-xs text-slate-500">
                  Evidence: <em>“{item.evidence}”</em>
                </p>
                <div className="mt-1 flex flex-wrap gap-2 text-xs">
                  <Badge tone={item.confidence >= 0.8 ? "good" : "warn"}>
                    confidence {(item.confidence * 100).toFixed(0)}%
                  </Badge>
                  {"owner" in item && (
                    <Badge tone={action.owner ? "neutral" : "warn"}>
                      owner: {action.owner ?? "unknown"}
                    </Badge>
                  )}
                  {"due_date" in item && (
                    <Badge tone={action.due_date ? "neutral" : "warn"}>
                      due: {action.due_date ?? "none stated"}
                    </Badge>
                  )}
                </div>
              </li>
            );
          })}
        </ul>
      )}
    </Card>
  );
}

function Row({ label, value }: { label: string; value: string | null }) {
  return (
    <div className="flex gap-2">
      <dt className="w-32 shrink-0 text-slate-400">{label}</dt>
      <dd className="text-slate-700">{value ?? "—"}</dd>
    </div>
  );
}
