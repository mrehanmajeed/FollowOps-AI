import { useEffect, useState } from "react";
import { api } from "../api/client";
import { Badge, Button, Card, Empty, ErrorBanner } from "../components/ui";
import type {
  EmailMessage,
  IntegrationStatus,
  PollSummary,
  VerifyResult,
} from "../types";

/**
 * Shows what is actually connected. Nothing on this page reports a success it
 * did not observe: a verify button contacts the provider, and the CRM tile says
 * plainly that it is simulated.
 */
export function Integrations() {
  const [status, setStatus] = useState<IntegrationStatus | null>(null);
  const [replies, setReplies] = useState<EmailMessage[]>([]);
  const [smtp, setSmtp] = useState<VerifyResult | null>(null);
  const [imap, setImap] = useState<VerifyResult | null>(null);
  const [poll, setPoll] = useState<PollSummary | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function load() {
    try {
      const [statusData, replyData] = await Promise.all([
        api.integrations(),
        api.listReplies(),
      ]);
      setStatus(statusData);
      setReplies(replyData);
      setError(null);
    } catch (err) {
      setError((err as Error).message);
    }
  }

  useEffect(() => {
    void load();
  }, []);

  async function run<T>(fn: () => Promise<T>, set: (value: T) => void) {
    setBusy(true);
    setError(null);
    try {
      set(await fn());
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

      {status && (
        <div className="grid gap-4 md:grid-cols-2">
          <Card title="Gemini">
            <dl className="space-y-1 text-sm">
              <Row label="Model" value={status.gemini.model} />
              <Row label="Type" value="Real integration" />
            </dl>
          </Card>

          <Card title="Supabase">
            <dl className="space-y-1 text-sm">
              <Row label="URL" value={status.supabase.url} />
              <Row label="Type" value="Real integration" />
            </dl>
          </Card>

          <Card
            title="Zoho SMTP (outbound)"
            actions={
              <Button
                onClick={() => run(api.verifySmtp, setSmtp)}
                disabled={busy}
              >
                Verify
              </Button>
            }
          >
            <dl className="space-y-1 text-sm">
              <Row label="Host" value={`${status.smtp.host}:${status.smtp.port}`} />
              <Row label="User" value={status.smtp.user} />
              <Row label="TLS" value={status.smtp.tls ? "yes" : "no"} />
            </dl>
            {smtp && (
              <div className="mt-2">
                <Badge tone={smtp.ok ? "good" : "bad"}>
                  {smtp.ok ? "authenticated" : "failed"}
                </Badge>
                {smtp.error && (
                  <p className="mt-1 text-xs text-rose-700">{smtp.error}</p>
                )}
              </div>
            )}
          </Card>

          <Card
            title="Zoho IMAP (reply detection)"
            actions={
              <div className="flex gap-2">
                <Button onClick={() => run(api.verifyImap, setImap)} disabled={busy}>
                  Verify
                </Button>
                <Button onClick={() => run(api.pollNow, setPoll)} disabled={busy}>
                  Poll now
                </Button>
              </div>
            }
          >
            <dl className="space-y-1 text-sm">
              <Row label="Host" value={`${status.imap.host}:${status.imap.port}`} />
              <Row label="Mailbox" value={status.imap.mailbox} />
              <Row label="User" value={status.imap.user} />
              <Row
                label="Polling"
                value={
                  status.imap.enabled
                    ? `${status.imap.polling ? "running" : "stopped"} every ${status.imap.poll_interval_seconds}s`
                    : "disabled"
                }
              />
            </dl>
            {imap && (
              <div className="mt-2">
                <Badge tone={imap.ok ? "good" : "bad"}>
                  {imap.ok ? `connected (${imap.message_count} messages)` : "failed"}
                </Badge>
                {imap.error && (
                  <p className="mt-1 text-xs text-rose-700">{imap.error}</p>
                )}
              </div>
            )}
            {(poll ?? status.imap.last_poll) && (
              <PollLine summary={(poll ?? status.imap.last_poll)!} />
            )}
          </Card>

          <Card title="CRM">
            <Badge tone={status.crm.real_integration ? "good" : "warn"}>
              {status.crm.real_integration
                ? "REAL INTEGRATION"
                : "SIMULATED / DEMO"}
            </Badge>
            <p className="mt-2 text-xs text-slate-500">
              Mode: {status.crm.mode}. In simulated mode the proposed update is
              recorded in the audit log only — no external CRM is written to.
            </p>
          </Card>
        </div>
      )}

      <Card title="Inbound mail" actions={<Button onClick={load}>Refresh</Button>}>
        {replies.length === 0 ? (
          <Empty>No inbound messages processed yet.</Empty>
        ) : (
          <ul className="divide-y divide-slate-100">
            {replies.map((reply) => (
              <li key={reply.id} className="py-2">
                <div className="flex items-center gap-2">
                  <Badge tone={toneFor(reply.correlation)}>{reply.correlation}</Badge>
                  <span className="text-sm text-slate-700">
                    {reply.from_address}
                  </span>
                  <span className="text-xs text-slate-400">{reply.subject}</span>
                </div>
                <p className="mt-1 text-xs text-slate-500">
                  {reply.correlation_reason}
                </p>
              </li>
            ))}
          </ul>
        )}
      </Card>
    </div>
  );
}

function PollLine({ summary }: { summary: PollSummary }) {
  return (
    <p className="mt-2 text-xs text-slate-500">
      Last poll: {summary.fetched} fetched, {summary.processed} new,{" "}
      {summary.duplicates} duplicates, {summary.confirmed_replies} confirmed
      replies, {summary.paused_accounts} account(s) paused.
      {summary.errors.length > 0 && (
        <span className="text-rose-600"> Errors: {summary.errors.join("; ")}</span>
      )}
    </p>
  );
}

function toneFor(correlation: string) {
  if (correlation === "confirmed_reply") return "good" as const;
  if (correlation === "possible_reply") return "warn" as const;
  return "neutral" as const;
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex gap-2">
      <dt className="w-24 shrink-0 text-slate-400">{label}</dt>
      <dd className="truncate text-slate-700">{value}</dd>
    </div>
  );
}
