// Typed client for the agent-plane API.
//
// The console authenticates as a human: a signed, httponly session cookie set
// by /v1/auth/login. No token is held in JavaScript and none is pasted into
// the UI. DEMO mode is the one exception - it reads the isolated demo project
// with a viewer token the server hands out publicly.

export type Mode = "observe" | "govern" | "enforce";
export type Outcome = "allow" | "deny" | "approval_required" | "quarantine" | "simulate";
export type Source = "live" | "demo";

export class ApiError extends Error {
  constructor(public readonly status: number, message: string) {
    super(message);
  }
}

let demoToken: string | null = null;
export function setDemoToken(token: string | null) {
  demoToken = token;
}

export async function api<T>(path: string, init: { method?: string; body?: unknown; source?: Source; timeoutMs?: number } = {}): Promise<T> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), init.timeoutMs ?? 15000);
  const headers: Record<string, string> = {};
  if (init.body !== undefined) headers["Content-Type"] = "application/json";
  if (init.source === "demo" && demoToken) headers["X-Demo-Token"] = demoToken;
  try {
    const res = await fetch(path, {
      method: init.method ?? "GET",
      headers,
      body: init.body === undefined ? undefined : JSON.stringify(init.body),
      credentials: "same-origin",
      cache: "no-store",
      signal: controller.signal,
    });
    const text = await res.text();
    let data: unknown = null;
    try {
      data = text ? JSON.parse(text) : null;
    } catch {
      data = null;
    }
    if (!res.ok) {
      const detail = (data as { detail?: unknown } | null)?.detail;
      const message = typeof detail === "string" ? detail : `Something went wrong (${res.status})`;
      throw new ApiError(res.status, message);
    }
    return data as T;
  } catch (e) {
    if (e instanceof ApiError) throw e;
    throw new ApiError(0, e instanceof Error && e.name === "AbortError" ? "Timed out" : "Cannot reach agent-plane");
  } finally {
    clearTimeout(timer);
  }
}

// --------------------------------------------------------------------------- //
// accounts
// --------------------------------------------------------------------------- //
export interface User { id: string; email: string; name: string; created_at: string; sso?: boolean }
export interface Workspace { id: string; name: string; slug: string }
export interface Project {
  id: string; workspace_id: string; name: string; slug: string; mode: Mode; demo: boolean;
  collection: Record<string, boolean>; created_at: string;
  keys: number; integrations: number; connected: number; rules: number;
}
export interface AuthState {
  users: number; signup_open: boolean; first_run: boolean; demo_available: boolean;
  demo_token: string | null; sso_available?: boolean; password_login?: boolean;
}
export interface Me { user: User; workspaces: Workspace[]; projects: Project[]; onboarded: boolean }

export interface ApiKey {
  id: string; project_id: string; name: string; masked: string; environment: "live" | "test" | "mgmt";
  status: "active" | "revoked" | "expired"; created_at: string; last_used_at: string | null;
  expires_at: string | null; scopes: string[];
}

export interface Integration {
  id: string; project_id: string; kind: string; name: string; label: string; host: string | null;
  status: "pending" | "connected" | "stale"; created_at: string; last_seen_at: string | null;
  agents: string[]; actions: number;
  observation: string; enforcement: string; summary: string; enforcement_note: string;
}
export interface CatalogEntry {
  kind: string; label: string; observation: string; enforcement: string;
  summary: string; enforcement_note: string; connect: string;
}

// --------------------------------------------------------------------------- //
// runtime
// --------------------------------------------------------------------------- //
export interface Origin {
  kind: string; ref?: string | null; text?: string | null; created_by?: string | null;
  parent_task?: string | null; parent_agent?: string | null;
}

export interface DecisionSummary {
  decision_id: string; created_at: string; tenant: string; agent: string; task: string;
  action: string; resource: string; outcome: Outcome; reason: string; would_be: string | null;
  enforced: boolean; lease: string | null; edge: string; impact: string | null;
  environment: string | null; approval_id: string | null;
}

export interface Consequence {
  action: string; resource: string; consequence_class: string; scope: string; effect: string;
  direct_effect: string; environment: string; criticality: string; customer_facing: boolean;
  reversibility: string; persistence: string; protected: boolean; downstream: string[];
  blast_radius: number; environments: string[]; impact: string; business: string;
  resource_profile: string | null; action_profile: string | null; summary: string[];
}

export interface LineageLink {
  lease: string; subject?: string; task?: string; actions?: string[]; resources?: string[];
  protected_resources?: string[]; require_approval?: string[]; origin?: Record<string, unknown>;
  parent_lease?: string | null; revoked?: boolean; missing?: boolean;
  permitted_consequence?: Record<string, unknown>; maximum_impact?: string; expires_at?: string | null;
}

export interface Trace {
  schema: string; decision_id: string; edge: string; mode: string;
  identity: { agent: string; user: string; tenant: string; application: string | null; verified: boolean; identity_mode: string; declared_capabilities: string[] };
  task: { id: string; origin: Origin | Record<string, never>; agents: string[] };
  authority: {
    lease: string | null; actions: string[]; resources: string[]; protected_resources: string[];
    require_approval: string[]; permitted_consequence: Record<string, unknown>;
    maximum_impact: string | null; expires_at: string | null; origin: Record<string, unknown>;
    lineage: LineageLink[]; lineage_permits_action: boolean;
  };
  action: { name: string; declared_impact: string; effect: string | null };
  resource: { name: string; scope: "within" | "outside" | "protected" };
  consequence: Consequence | null;
  decision: { outcome: string; reason: string; reasons: string[]; enforced: boolean; would_be: string | null; approval_id: string | null };
  explanation: string[];
  context: Record<string, string>;
}

export interface ApprovalRequest {
  id: string; status: string; tenant: string; subject: string; task: string; action: string;
  resource: string; lease_id: string | null; evidence_id: string; created_at: string;
  expires_at: string | null; decided_at: string | null; decided_by: string | null; note: string | null;
}

export interface DecisionDetail {
  decision_id: string;
  event: { decision_id: string; created_at: string | null; event_hash: string; prev_hash: string | null; signature: string; policy_version: string | null };
  trace: Trace | null;
  related: Array<{ kind: string; approval: ApprovalRequest | null }>;
  receipts: Array<{ decision_id: string; created_at: string | null; model_requested: string; reason: string }>;
}

export interface Agent {
  id: string; tenant: string; application: string; framework: string | null;
  status: "active" | "idle" | "quarantined"; first_seen: string; last_seen: string;
  parent_agent: string | null; origin: Origin | null; current_task: string | null;
  tasks: string[]; sessions: string[]; declared_capabilities: string[];
  requested_authority: Record<string, number>; exercised_authority: Record<string, number>;
  denied_authority: Record<string, number>; resources: Record<string, number>;
  decisions: Record<string, number>;
  last_action: { action: string; resource: string; outcome: string; decision_id: string; at: string; edge: string } | null;
  granted_authority: string[]; active_lease: string | null; leases: string[];
  quarantine_note?: string | null;
}

export interface AgentDetail extends Omit<Agent, "leases"> {
  leases: Array<Record<string, unknown>>;
  lineage: Record<string, LineageLink[]>;
  drift: { declared: string[]; granted: string[]; observed: string[]; undeclared: string[]; ungranted: string[]; unused_grants?: string[] };
  tasks_detail: Array<{ id: string; origin: Origin; decisions: Record<string, number>; observed_actions: Record<string, number>; resources: string[] }>;
  recent_decisions: Array<{ decision_id: string; decision: string; reason: string; model_requested: string; model_used: string | null; created_at: string | null }>;
  children: string[];
}

export interface Rule {
  id: string; project_id: string; name: string; enabled: boolean; source: string;
  scope: { agents: string[]; integrations: string[]; environments: string[] };
  scope_label: string; summary: string;
  allow: string[]; ask: string[]; never: string[];
  resources: string[]; protected_resources: string[];
  permitted_consequence: Record<string, unknown>; created_at: string; updated_at: string;
}

export interface ActionOption { action: string; label: string; class: string | null; effect: string }
export interface RuleTemplate {
  key: string; name: string; description: string;
  allow: string[]; ask: string[]; never: string[];
  resources?: string[]; protected_resources?: string[]; scope?: Record<string, string[]>;
  permitted_consequence?: Record<string, unknown>;
}

export interface SystemState {
  mode: Mode; identity_mode: string; environment: string; authority_store: string;
  policy_version: string | null; tenant: string | null; demo: boolean;
  agents: number; active_agents: number; quarantined: number; tasks: number;
  decisions: Record<string, number>; pending_approvals: number; leases: number;
  audit_head: string | null;
}

// --------------------------------------------------------------------------- //
// calls
// --------------------------------------------------------------------------- //
const q = (params: Record<string, string | number | undefined | null>) =>
  Object.entries(params).filter(([, v]) => v !== undefined && v !== null && v !== "")
    .map(([k, v]) => `${k}=${encodeURIComponent(String(v))}`).join("&");

export const Api = {
  authState: () => api<AuthState>("/v1/auth/state"),
  signup: (body: { email: string; password: string; name?: string }) =>
    api<{ user: User; workspace: Workspace }>("/v1/auth/signup", { method: "POST", body }),
  login: (body: { email: string; password: string }) =>
    api<{ user: User }>("/v1/auth/login", { method: "POST", body }),
  logout: () => api<{ signed_out: boolean }>("/v1/auth/logout", { method: "POST" }),
  me: () => api<Me>("/v1/auth/me"),

  createProject: (body: { name: string; mode?: Mode }) => api<{ project: Project }>("/v1/projects", { method: "POST", body }),
  updateProject: (id: string, body: Record<string, unknown>) =>
    api<{ project: Project }>(`/v1/projects/${id}`, { method: "PATCH", body }),
  deleteProject: (id: string) => api<{ deleted: boolean }>(`/v1/projects/${id}`, { method: "DELETE" }),
  project: (id: string) => api<{ project: Project; collection_fields: { defaults: Record<string, boolean>; optional: string[] } }>(`/v1/projects/${id}`),
  members: (workspaceId: string) =>
    api<{ members: Array<{ user_id: string; role: string; email: string; name: string }>; your_role: string }>(`/v1/workspaces/${workspaceId}/members`),

  keys: (project: string) => api<{ keys: ApiKey[] }>(`/v1/api-keys?${q({ project })}`),
  createKey: (body: { project: string; name: string; environment?: string }) =>
    api<{ key: ApiKey; secret: string }>("/v1/api-keys", { method: "POST", body }),
  rotateKey: (id: string) => api<{ key: ApiKey; secret: string }>(`/v1/api-keys/${id}/rotate`, { method: "POST" }),
  revokeKey: (id: string) => api<{ key: ApiKey }>(`/v1/api-keys/${id}`, { method: "DELETE" }),

  integrations: (project: string, source: Source = "live") =>
    api<{ integrations: Integration[]; catalog: CatalogEntry[] }>(`/v1/integrations?${q({ project })}`, { source }),
  createIntegration: (body: { project: string; kind: string; name?: string }) =>
    api<{ integration: Integration }>("/v1/integrations", { method: "POST", body }),
  deleteIntegration: (id: string, project: string) =>
    api<{ deleted: boolean }>(`/v1/integrations/${id}?${q({ project })}`, { method: "DELETE" }),

  rules: (project: string, source: Source = "live") =>
    api<{ rules: Rule[]; actions: ActionOption[]; templates: RuleTemplate[] }>(`/v1/rules?${q({ project })}`, { source }),
  createRule: (body: Record<string, unknown>) => api<{ rule: Rule }>("/v1/rules", { method: "POST", body }),
  updateRule: (id: string, body: Record<string, unknown>) => api<{ rule: Rule }>(`/v1/rules/${id}`, { method: "PATCH", body }),
  deleteRule: (id: string) => api<{ deleted: boolean }>(`/v1/rules/${id}`, { method: "DELETE" }),
  exportRules: (project: string, source: Source = "live") =>
    api<{ project: string; count: number; yaml: string }>(`/v1/rules/export?${q({ project })}`, { source }),
  importRules: (project: string, yaml: string, mode: "merge" | "replace" = "merge") =>
    api<{ created: string[]; updated: string[]; deleted: string[]; rules: Rule[] }>(
      "/v1/rules/import", { method: "POST", body: { project, yaml, mode } }),
  suggestedRules: (project: string, source: Source = "live") =>
    api<{ suggestions: Array<Record<string, unknown>> }>(`/v1/rules/suggested?${q({ project })}`, { source }),

  system: (project: string, source: Source = "live") => api<SystemState>(`/v1/system?${q({ tenant: project })}`, { source }),
  decisions: (project: string, limit = 100, source: Source = "live") =>
    api<{ decisions: DecisionSummary[]; count: number }>(`/v1/decisions?${q({ tenant: project, limit })}`, { source }),
  decision: (id: string, source: Source = "live") => api<DecisionDetail>(`/v1/decisions/${id}`, { source }),
  agents: (project: string, source: Source = "live") =>
    api<{ agents: Agent[]; count: number }>(`/v1/agents?${q({ tenant: project })}`, { source }),
  agent: (id: string, project: string, source: Source = "live") =>
    api<AgentDetail>(`/v1/agents/${id}?${q({ tenant: project })}`, { source }),
  quarantine: (id: string, project: string, on: boolean) =>
    api<{ agent: Agent }>(`/v1/agents/${id}/quarantine?${q({ tenant: project })}`, { method: on ? "POST" : "DELETE", body: on ? { note: "held from the console" } : undefined }),
  approvals: (project: string, source: Source = "live") =>
    api<{ approvals: ApprovalRequest[]; count: number }>(`/v1/approvals?${q({ tenant: project, status: "pending", limit: 100 })}`, { source }),
  decideApproval: (id: string, verb: "approve" | "reject", note?: string) =>
    api<{ approval: ApprovalRequest }>(`/v1/approvals/${id}/${verb}`, { method: "POST", body: { note, decided_by: "console" } }),

  demoScenarios: () => api<{ scenarios: Array<{ name: string; title: string; prompt: string; summary: string }>; token: string }>("/demo/scenarios"),
  runScenario: (name: string, body: Record<string, unknown> = {}) =>
    api<Record<string, unknown>>(`/demo/scenarios/${name}/run`, { method: "POST", body }),
  resetDemo: () => api<{ reset: boolean }>("/demo/reset", { method: "POST" }),
};
