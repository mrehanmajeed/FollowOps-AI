export interface Account {
  id: string;
  company_name: string;
  contact_name: string | null;
  contact_email: string | null;
  account_context: string | null;
  created_at: string;
  followups_paused: boolean;
  followups_paused_reason: string | null;
  last_reply_at: string | null;
}

export interface ValidationIssue {
  severity: "critical" | "warning";
  code: string;
  message: string;
  item: string | null;
}

export interface EvidenceItem {
  text: string;
  evidence: string;
  confidence: number;
}

export interface ActionItem extends EvidenceItem {
  owner: string | null;
  due_date: string | null;
}

export interface Extraction {
  meeting_summary: string;
  decisions: EvidenceItem[];
  client_actions: ActionItem[];
  internal_actions: ActionItem[];
  open_questions: string[];
  risks: string[];
  crm_update: {
    summary: string;
    next_step: string | null;
    next_followup_date: string | null;
  };
  email: { subject: string; body: string };
  overall_confidence: number;
}

export interface FollowupPackage {
  id: string;
  workflow_run_id: string;
  summary: string;
  structured_output: Extraction;
  email_subject: string;
  email_body: string;
  approved: boolean;
  edited: boolean;
  blocked: boolean;
  validation_issues: ValidationIssue[];
}

export interface WorkflowSummary {
  id: string;
  meeting_id: string;
  status: string;
  model: string;
  latency_ms: number | null;
  overall_confidence: number | null;
  failure_message: string | null;
  created_at: string;
  completed_at: string | null;
}

export interface WorkflowOperation {
  id: string;
  operation_type: string;
  status: "pending" | "succeeded" | "failed";
  attempts: number;
  provider_id: string | null;
  error: string | null;
  completed_at: string | null;
}

export interface AuditEvent {
  id: number;
  event_type: string;
  message: string;
  metadata: Record<string, unknown>;
  created_at: string;
}

export interface EmailMessage {
  id: string;
  direction: "inbound" | "outbound";
  message_id: string | null;
  from_address: string | null;
  to_address: string | null;
  subject: string | null;
  correlation: "outbound" | "confirmed_reply" | "possible_reply" | "unrelated";
  correlation_reason: string | null;
  workflow_run_id: string | null;
  account_id: string | null;
  received_at: string | null;
  created_at: string;
}

export interface WorkflowDetail {
  workflow: WorkflowSummary & { failure_message: string | null };
  meeting: { id: string; notes: string; meeting_date: string } | null;
  account: Account | null;
  package: FollowupPackage | null;
  operations: WorkflowOperation[];
  audit_events: AuditEvent[];
  replies: EmailMessage[];
}

export interface ExecutionResponse {
  workflow_run_id: string;
  status: string;
  email_sent: boolean;
  tasks_created: number;
  crm_updated: boolean;
  email_message_id: string | null;
  crm_mode: string;
  operations: {
    operation_type: string;
    status: string;
    skipped: boolean;
    provider_id: string | null;
    error: string | null;
  }[];
}

export interface IntegrationStatus {
  gemini: { model: string; configured: boolean };
  supabase: { url: string; configured: boolean };
  smtp: { host: string; port: number; user: string; tls: boolean };
  imap: {
    enabled: boolean;
    host: string;
    port: number;
    ssl: boolean;
    user: string;
    mailbox: string;
    poll_interval_seconds: number;
    polling: boolean;
    last_poll: PollSummary | null;
  };
  crm: { mode: string; real_integration: boolean };
}

export interface PollSummary {
  fetched: number;
  processed: number;
  duplicates: number;
  confirmed_replies: number;
  possible_replies: number;
  unrelated: number;
  paused_accounts: number;
  errors: string[];
  ran_at: string;
}

export interface VerifyResult {
  ok: boolean;
  host?: string;
  error?: string;
  mailbox?: string;
  message_count?: number;
  folders?: string[];
}
