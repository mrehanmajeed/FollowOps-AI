import type {
  Account,
  ExecutionResponse,
  IntegrationStatus,
  EmailMessage,
  PollSummary,
  VerifyResult,
  WorkflowDetail,
  WorkflowSummary,
} from "../types";

const BASE_URL =
  import.meta.env.VITE_API_URL ?? "http://localhost:8000/api/v1";

const TOKEN_KEY = "followops.operatorToken";

export function getToken(): string {
  return (
    localStorage.getItem(TOKEN_KEY) ??
    import.meta.env.VITE_OPERATOR_API_KEY ??
    ""
  );
}

export function setToken(token: string): void {
  localStorage.setItem(TOKEN_KEY, token.trim());
}

export function clearToken(): void {
  localStorage.removeItem(TOKEN_KEY);
}

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
  }
}

async function request<T>(
  path: string,
  options: RequestInit = {},
): Promise<T> {
  const response = await fetch(`${BASE_URL}${path}`, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      "X-API-Key": getToken(),
      ...(options.headers ?? {}),
    },
  });

  if (!response.ok) {
    let detail = `Request failed (${response.status})`;
    try {
      const body = await response.json();
      if (body?.detail) detail = String(body.detail);
    } catch {
      // Non-JSON error body; keep the status-based message.
    }
    throw new ApiError(detail, response.status);
  }

  return response.status === 204 ? (undefined as T) : await response.json();
}

export const api = {
  listAccounts: () => request<Account[]>("/accounts"),

  createAccount: (payload: {
    company_name: string;
    contact_name?: string | null;
    contact_email?: string | null;
    account_context?: string | null;
  }) =>
    request<Account>("/accounts", {
      method: "POST",
      body: JSON.stringify(payload),
    }),

  setFollowupPause: (accountId: string, paused: boolean, reason?: string) =>
    request<Account>(`/accounts/${accountId}/followups`, {
      method: "POST",
      body: JSON.stringify({ paused, reason: reason ?? null }),
    }),

  listWorkflows: () => request<WorkflowSummary[]>("/workflows"),

  getWorkflow: (id: string) => request<WorkflowDetail>(`/workflows/${id}`),

  createWorkflow: (payload: {
    account_id: string;
    meeting_date: string;
    notes: string;
  }) =>
    request<{ id: string; status: string; blocked: boolean }>("/workflows", {
      method: "POST",
      body: JSON.stringify(payload),
    }),

  editPackage: (id: string, payload: { email_subject?: string; email_body?: string }) =>
    request<unknown>(`/workflows/${id}/package`, {
      method: "PATCH",
      body: JSON.stringify(payload),
    }),

  approve: (id: string, approved: boolean) =>
    request<ExecutionResponse>(`/workflows/${id}/approve`, {
      method: "POST",
      body: JSON.stringify({ approved }),
    }),

  retry: (id: string, force = false) =>
    request<ExecutionResponse>(`/workflows/${id}/retry?force=${force}`, {
      method: "POST",
    }),

  integrations: () => request<IntegrationStatus>("/integrations"),

  verifySmtp: () => request<VerifyResult>("/integrations/smtp/verify", { method: "POST" }),

  verifyImap: () => request<VerifyResult>("/integrations/imap/verify", { method: "POST" }),

  pollNow: () => request<PollSummary>("/integrations/imap/poll", { method: "POST" }),

  listReplies: () => request<EmailMessage[]>("/integrations/replies"),
};
