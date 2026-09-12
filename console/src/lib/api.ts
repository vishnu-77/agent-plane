// Typed client for the agent-plane API. LIVE uses the operator token
// (X-Admin-Token); DEMO uses the demo viewer token (X-Demo-Token) and only
// ever sees the isolated demo tenant. Tokens stay in memory.

export type Mode = "live" | "demo";

export interface Credentials {
  admin: string;
  demo: string;
}

export interface SystemState {
  mode: "observe" | "enforce";
  enforcement_default: string;
  identity_mode: string;
  environment: string;
  authority_store: string;
  policy_version: string | null;
  tenant: string | null;
  demo: boolean;
  agents: number;
  active_agents: number;
  quarantined: number;
  tasks: number;
  decisions: Record<string, number>;
  pending_approvals: number;
  leases: number;
  audit_head: string | null;
}

export interface Origin {
  kind: string;
  ref?: string | null;
  text?: string | null;
  created_by?: string | null;
  parent_task?: string | null;
  parent_agent?: string | null;
}

export interface AgentSummary {
  id: string;
  tenant: string;
  application: string;
  framework: string | null;
  status: "active" | "idle" | "quarantined";
  first_seen: string;
  last_seen: string;
  parent_agent: string | null;
  origin: Origin | null;
  current_task: string | null;
  tasks: string[];
  sessions: string[];
  declared_capabilities: string[];
  requested_authority: Record<string, number>;
  exercised_authority: Record<string, number>;
  denied_authority: Record<string, number>;
  resources: Record<string, number>;
  decisions: Record<string, number>;
  last_action: { action: string; resource: string; outcome: string; decision_id: string; at: string; edge: string } | null;
  granted_authority: string[];
  active_lease: string | null;
  leases: string[];
  delegated_authority: string[];
  quarantined_by?: string | null;
  quarantine_note?: string | null;
}

export interface Lease {
  id: string;
  task: string;
  subject: string;
  tenant: string;
  resources: string[];
  actions: string[];
  protected_resources: string[];
  max_uses: Record<string, number>;
  require_approval: string[];
  expires_at: string | null;
  maximum_impact: string;
  child_authority: string;
  revoked: boolean;
  parent_lease: string | null;
  origin: Record<string, unknown>;
  permitted_consequence: Record<string, unknown>;
  status?: string;
  uses?: Record<string, number>;
}

export interface LineageLink {
  lease: string;
  subject?: string;
  tenant?: string;
  task?: string;
  actions?: string[];
  resources?: string[];
  protected_resources?: string[];
  require_approval?: string[];
  permitted_consequence?: Record<string, unknown>;
  maximum_impact?: string;
  expires_at?: string | null;
  revoked?: boolean;
  origin?: Record<string, unknown>;
  parent_lease?: string | null;
  missing?: boolean;
}

export interface AgentDetail extends AgentSummary {
  leases: Lease[] & string[];
  lineage: Record<string, LineageLink[]>;
  drift: { declared: string[]; granted: string[]; observed: string[]; undeclared: string[]; ungranted: string[]; unused_grants?: string[] };
  tasks_detail: TaskRecord[];
  recent_decisions: AuditEvent[];
  children: string[];
}

export interface TaskRecord {
  id: string;
  tenant: string;
  origin: Origin;
  agents: string[];
  leases: string[];
  status: string;
  created_at: string;
  last_activity: string;
  decisions: Record<string, number>;
  observed_actions: Record<string, number>;
  resources: string[];
  leases_detail?: Lease[];
  lineage?: Record<string, LineageLink[]>;
}

export interface Consequence {
  action: string;
  resource: string;
  effect: string;
  direct_effect: string;
  environment: string;
  criticality: string;
  customer_facing: boolean;
  reversibility: string;
  persistence: string;
  protected: boolean;
  downstream: string[];
  blast_radius: number;
  environments: string[];
  impact: string;
  business: string;
  resource_profile: string | null;
  action_profile: string | null;
  summary: string[];
}

export interface Trace {
  schema: string;
  decision_id: string;
  edge: string;
  mode: string;
  identity: { agent: string; user: string; tenant: string; application: string | null; verified: boolean; identity_mode: string; declared_capabilities: string[] };
  task: { id: string; origin: Origin | Record<string, never>; agents: string[] };
  authority: { lease: string | null; actions: string[]; resources: string[]; protected_resources: string[]; require_approval: string[]; permitted_consequence: Record<string, unknown>; maximum_impact: string | null; expires_at: string | null; origin: Record<string, unknown>; lineage: LineageLink[]; lineage_permits_action: boolean };
  action: { name: string; declared_impact: string; effect: string | null };
  resource: { name: string; scope: "within" | "outside" | "protected" };
  consequence: Consequence | null;
  decision: { outcome: string; reason: string; reasons: string[]; enforced: boolean; would_be: string | null; approval_id: string | null };
  explanation: string[];
  context: Record<string, string>;
}

export interface DecisionSummary {
  decision_id: string;
  created_at: string;
  tenant: string;
  agent: string;
  task: string;
  action: string;
  resource: string;
  outcome: string;
  reason: string;
  would_be: string | null;
  enforced: boolean;
  lease: string | null;
  edge: string;
  impact: string | null;
  environment: string | null;
  approval_id: string | null;
}

export interface AuditEvent {
  decision_id: string;
  created_at: string | null;
  user_id: string;
  tenant: string;
  agent_id: string | null;
  model_requested: string;
  model_used: string | null;
  decision: string;
  reason: string;
  policy_version: string | null;
  rules_matched: string[];
  obligations_applied: unknown[];
  prev_hash: string | null;
  event_hash: string;
  signature: string;
}

export interface DecisionDetail {
  decision_id: string;
  event: AuditEvent;
  trace: Trace | null;
  related: Array<{ kind: string; approval: ApprovalRequest | null }>;
  receipts: AuditEvent[];
}

export interface ApprovalRequest {
  id: string;
  status: string;
  tenant: string;
  subject: string;
  task: string;
  action: string;
  resource: string;
  lease_id: string | null;
  evidence_id: string;
  created_at: string;
  expires_at: string | null;
  decided_at: string | null;
  decided_by: string | null;
  note: string | null;
  context: Record<string, string>;
}

export interface ResourceEntry {
  resource: string;
  touches: number;
  agents: string[];
  tenant: string;
  profile: Record<string, unknown> | null;
  downstream: string[];
}

export interface Scenario {
  name: string;
  title: string;
  prompt: string;
  task: string;
  agent: string;
  framework: string;
  created_by: string;
  summary: string;
  authority: { actions: string[]; resources: string[]; protected_resources: string[]; permitted_consequence: Record<string, unknown> };
  steps: Array<{ index: number; agent: string; action: string; resource: string; expected: string; note: string; spawns: string | null }>;
}

export interface ScenarioRun {
  scenario: string;
  title: string;
  prompt: string;
  task: string;
  agent: string;
  run_id: string;
  lease: string;
  children: Record<string, string>;
  steps: Array<{ index: number; agent: string; action: string; resource: string; note: string; expected: string; outcome: string; reason: string; decision_id: string; lease: string | null; matches_expected: boolean; explanation: string[]; consequence: Record<string, unknown> | null; executed: unknown; lineage: LineageLink[] }>;
  summary: string;
  targets: { deployments: Record<string, { status: string; restarts: number }>; branches: string[] };
}

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

export function headersFor(mode: Mode, creds: Credentials, extra: Record<string, string> = {}): Record<string, string> {
  const h: Record<string, string> = { ...extra };
  if (mode === "demo" && creds.demo) h["X-Demo-Token"] = creds.demo;
  if (mode === "live" && creds.admin) h["X-Admin-Token"] = creds.admin;
  return h;
}

export interface ApiOptions {
  mode: Mode;
  creds: Credentials;
  method?: string;
  body?: string;
  headers?: Record<string, string>;
  timeoutMs?: number;
}

export async function api<T>(path: string, init: ApiOptions): Promise<T> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), init.timeoutMs ?? 15000);
  try {
    const res = await fetch(path, {
      method: init.method ?? "GET",
      body: init.body,
      headers: headersFor(init.mode, init.creds, { ...(init.body ? { "Content-Type": "application/json" } : {}), ...(init.headers ?? {}) }),
      cache: "no-store",
      signal: controller.signal,
    });
    const text = await res.text();
    let data: unknown = null;
    try {
      data = text ? JSON.parse(text) : null;
    } catch {
      data = { error: "unexpected response" };
    }
    if (!res.ok) {
      const d = data as { detail?: unknown; error?: string } | null;
      const msg = typeof d?.detail === "string" ? d.detail : d?.error ?? `HTTP ${res.status}`;
      throw new ApiError(res.status, msg);
    }
    return data as T;
  } catch (e) {
    if (e instanceof ApiError) throw e;
    throw new ApiError(0, e instanceof Error && e.name === "AbortError" ? "timed out" : "unreachable");
  } finally {
    clearTimeout(timer);
  }
}

export function tenantQuery(mode: Mode, tenant: string | null): string {
  if (mode === "demo") return "?tenant=demo";
  return tenant ? `?tenant=${encodeURIComponent(tenant)}` : "";
}
