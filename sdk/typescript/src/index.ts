/**
 * TypeScript client for agent-plane.
 *
 * Mirrors the Python SDK contract exactly: `authorize()` returns a decision
 * and the caller executes the real action only when `decision.allowed` is
 * true. Any inconsistent or unexpected response throws
 * `AuthorizationProtocolError`; HTTP and network failures throw too. None of
 * those may be treated as permission.
 *
 *   import { AgentPlane } from "@agent-plane/sdk";
 *   const plane = new AgentPlane(process.env.AGENT_PLANE_URL!, agentToken);
 *   const d = await plane.authorize({ task, action: "deployment.restart", resource: "staging/checkout" });
 *   if (d.allowed) await restart();
 *   else if (d.needsApproval) { const r = await plane.waitForApproval(d, { timeoutMs: 600_000 }); ... }
 *
 * Uses the global `fetch` (Node 18+, browsers, edge runtimes). No dependencies.
 */

export type DecisionKind = "allow" | "deny" | "approval_required";
export type ApprovalStatus = "pending" | "approved" | "rejected" | "consumed" | "expired";

export class AuthorizationProtocolError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "AuthorizationProtocolError";
  }
}

export class HttpError extends Error {
  readonly status: number;
  readonly body: string;
  constructor(status: number, body: string) {
    super(`agent-plane responded ${status}`);
    this.name = "HttpError";
    this.status = status;
    this.body = body;
  }
}

export class ApprovalTimeout extends Error {
  readonly approvalId: string;
  constructor(approvalId: string) {
    super(`approval ${approvalId} still pending`);
    this.name = "ApprovalTimeout";
    this.approvalId = approvalId;
  }
}

export interface AuthorityDecision {
  decision: DecisionKind;
  reason: string;
  lease: string | null;
  evidenceId: string;
  approvalId: string | null;
  context: Record<string, string>;
  task: string;
  action: string;
  resource: string;
  /** true only for an explicit ALLOW */
  allowed: boolean;
  needsApproval: boolean;
}

export interface ApprovalRequest {
  id: string;
  status: ApprovalStatus;
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
}

export interface AuthorizeInput {
  task: string;
  action: string;
  resource: string;
  /** resume a previously raised approval request */
  approval?: string;
  /** provenance attached to the audit record (parent_evidence_id, conversation_id, ...) */
  context?: Record<string, string>;
}

export interface ClientOptions {
  /** custom fetch (tests, polyfills) */
  fetch?: typeof fetch;
  /** per-request timeout in ms (default 10s) */
  timeoutMs?: number;
}

const EXPECTED: Record<number, DecisionKind> = { 200: "allow", 202: "approval_required", 403: "deny" };

type Json = Record<string, unknown>;

function isRecord(v: unknown): v is Json {
  return typeof v === "object" && v !== null && !Array.isArray(v);
}

function parseDecision(status: number, text: string, input: AuthorizeInput): AuthorityDecision {
  const expected = EXPECTED[status];
  if (!expected) throw new HttpError(status, text);
  let body: unknown;
  try {
    body = JSON.parse(text);
  } catch {
    throw new AuthorizationProtocolError("Invalid JSON from agent-plane");
  }
  // FastAPI wraps 202/403 in "detail"; HTTP success is not permission.
  const payload = isRecord(body) && isRecord(body.detail) ? body.detail : body;
  if (
    !isRecord(payload) ||
    payload.decision !== expected ||
    typeof payload.reason !== "string" || !payload.reason ||
    typeof payload.evidence_id !== "string" || !payload.evidence_id ||
    (payload.lease != null && typeof payload.lease !== "string") ||
    (payload.approval_id != null && typeof payload.approval_id !== "string")
  ) {
    throw new AuthorizationProtocolError("Inconsistent authorization decision");
  }
  const rawContext = payload.context;
  if (rawContext != null && !isRecord(rawContext)) {
    throw new AuthorizationProtocolError("Inconsistent authorization context");
  }
  const context: Record<string, string> = {};
  for (const [k, v] of Object.entries(rawContext ?? {})) context[k] = String(v);
  const decision = payload.decision as DecisionKind;
  return {
    decision,
    reason: payload.reason,
    lease: (payload.lease as string | undefined) ?? null,
    evidenceId: payload.evidence_id,
    approvalId: (payload.approval_id as string | undefined) ?? null,
    context,
    task: input.task,
    action: input.action,
    resource: input.resource,
    allowed: decision === "allow",
    needsApproval: decision === "approval_required",
  };
}

async function request(
  fetchImpl: typeof fetch, timeoutMs: number, url: string, init: RequestInit,
): Promise<{ status: number; text: string }> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const res = await fetchImpl(url, { ...init, signal: controller.signal });
    return { status: res.status, text: await res.text() };
  } finally {
    clearTimeout(timer);
  }
}

function parseJson(status: number, text: string): Json {
  if (status < 200 || status >= 300) throw new HttpError(status, text);
  let body: unknown;
  try {
    body = JSON.parse(text);
  } catch {
    throw new AuthorizationProtocolError("Invalid JSON from agent-plane");
  }
  if (!isRecord(body)) throw new AuthorizationProtocolError("Unexpected payload shape");
  return body;
}

function asApproval(body: unknown): ApprovalRequest {
  if (!isRecord(body) || typeof body.id !== "string" || typeof body.status !== "string") {
    throw new AuthorizationProtocolError("Invalid approval payload");
  }
  return body as unknown as ApprovalRequest;
}

function asLease(body: unknown): Lease {
  if (!isRecord(body) || typeof body.id !== "string") {
    throw new AuthorizationProtocolError("Invalid lease payload");
  }
  return body as unknown as Lease;
}

abstract class BaseClient {
  protected readonly baseUrl: string;
  protected readonly fetchImpl: typeof fetch;
  protected readonly timeoutMs: number;
  protected readonly headers: Record<string, string>;

  constructor(baseUrl: string, headers: Record<string, string>, opts: ClientOptions = {}) {
    this.headers = headers;
    this.baseUrl = baseUrl.replace(/\/+$/, "");
    this.fetchImpl = opts.fetch ?? globalThis.fetch;
    this.timeoutMs = opts.timeoutMs ?? 10_000;
    if (!this.fetchImpl) throw new Error("No fetch implementation available; pass options.fetch");
  }

  protected call(method: string, path: string, body?: unknown) {
    const init: RequestInit = { method, headers: { ...this.headers, "content-type": "application/json" } };
    if (body !== undefined) init.body = JSON.stringify(body);
    return request(this.fetchImpl, this.timeoutMs, this.baseUrl + path, init);
  }
}

/** Executor-side client, authenticated with the agent bearer token. */
export class AgentPlane extends BaseClient {
  constructor(baseUrl: string, token: string, opts: ClientOptions = {}) {
    super(baseUrl, { Authorization: `Bearer ${token}` }, opts);
  }

  /** Ask whether `action` on `resource` is authorised for `task`. Execute only on `allowed`. */
  async authorize(input: AuthorizeInput): Promise<AuthorityDecision> {
    const body: Json = { task: input.task, action: input.action, resource: input.resource };
    if (input.approval) body.approval = input.approval;
    if (input.context && Object.keys(input.context).length) body.context = input.context;
    const { status, text } = await this.call("POST", "/v1/authorize", body);
    return parseDecision(status, text, input);
  }

  async getApproval(approvalId: string): Promise<ApprovalRequest> {
    const { status, text } = await this.call("GET", `/v1/approvals/${encodeURIComponent(approvalId)}`);
    return asApproval(parseJson(status, text));
  }

  /**
   * Poll an approval_required decision until decided, then resume it. Resolves
   * to the resumed decision; rejects with ApprovalTimeout if still pending.
   */
  async waitForApproval(
    decision: AuthorityDecision,
    opts: { timeoutMs?: number; intervalMs?: number } = {},
  ): Promise<AuthorityDecision> {
    if (!decision.needsApproval || !decision.approvalId) return decision;
    const timeoutMs = opts.timeoutMs ?? 300_000;
    const intervalMs = opts.intervalMs ?? 2_000;
    const deadline = Date.now() + timeoutMs;
    for (;;) {
      const req = await this.getApproval(decision.approvalId);
      if (req.status !== "pending") {
        return this.authorize({
          task: decision.task, action: decision.action, resource: decision.resource,
          approval: decision.approvalId, context: decision.context,
        });
      }
      if (Date.now() >= deadline) throw new ApprovalTimeout(decision.approvalId);
      await new Promise((r) => setTimeout(r, Math.min(intervalMs, Math.max(0, deadline - Date.now()))));
    }
  }

  /** Mint an attenuated child lease for `agent` (lease-holder self-service). */
  async delegate(leaseId: string, body: { agent: string } & Partial<Omit<Lease, "id" | "task" | "subject" | "revoked">> & { id?: string }): Promise<Lease> {
    const { status, text } = await this.call("POST", `/v1/leases/${encodeURIComponent(leaseId)}/delegate`, body);
    return asLease(parseJson(status, text).lease);
  }
}

export interface IssueLeaseInput {
  id: string;
  subject: string;
  task: string;
  /** must match the agent token's tenant claim (default "default") */
  tenant?: string;
  resources: string[];
  actions: string[];
  protected_resources?: string[];
  require_approval?: string[];
  max_uses?: Record<string, number>;
  expires_at?: string | null;
  maximum_impact?: string;
  child_authority?: string;
}

/** Backend / operator client, authenticated with ADMIN_TOKEN. Never give this to an agent. */
export class AgentPlaneAdmin extends BaseClient {
  constructor(baseUrl: string, adminToken: string, opts: ClientOptions = {}) {
    super(baseUrl, { "X-Admin-Token": adminToken }, opts);
  }

  async issueLease(input: IssueLeaseInput): Promise<Lease> {
    const { status, text } = await this.call("POST", "/v1/leases", {
      tenant: "default", protected_resources: [], require_approval: [], max_uses: {},
      maximum_impact: "reversible", child_authority: "none", ...input,
    });
    return asLease(parseJson(status, text).lease);
  }

  async issueFromTemplate(template: string, input: { subject: string; task: string; tenant?: string; variables?: Record<string, string>; id?: string }): Promise<Lease> {
    const { status, text } = await this.call("POST", "/v1/leases/from-template", { template, tenant: "default", variables: {}, ...input });
    return asLease(parseJson(status, text).lease);
  }

  async listTemplates(): Promise<Array<Record<string, unknown>>> {
    const { status, text } = await this.call("GET", "/v1/lease-templates");
    return (parseJson(status, text).templates as Array<Record<string, unknown>>) ?? [];
  }

  async getLease(leaseId: string): Promise<Lease> {
    const { status, text } = await this.call("GET", `/v1/leases/${encodeURIComponent(leaseId)}`);
    return asLease(parseJson(status, text));
  }

  async shrinkLease(leaseId: string, narrower: Partial<Omit<Lease, "id" | "task" | "subject" | "revoked">>): Promise<Lease> {
    const { status, text } = await this.call("PATCH", `/v1/leases/${encodeURIComponent(leaseId)}`, narrower);
    return asLease(parseJson(status, text).lease);
  }

  async revokeLease(leaseId: string): Promise<void> {
    const { status, text } = await this.call("DELETE", `/v1/leases/${encodeURIComponent(leaseId)}`);
    parseJson(status, text);
  }

  async listApprovals(opts: { status?: ApprovalStatus | "all"; tenant?: string; limit?: number } = {}): Promise<ApprovalRequest[]> {
    const params = new URLSearchParams({ status: opts.status ?? "pending", limit: String(opts.limit ?? 100) });
    if (opts.tenant) params.set("tenant", opts.tenant);
    const { status, text } = await this.call("GET", `/v1/approvals?${params}`);
    return ((parseJson(status, text).approvals as unknown[]) ?? []).map(asApproval);
  }

  async getApproval(approvalId: string): Promise<ApprovalRequest> {
    const { status, text } = await this.call("GET", `/v1/approvals/${encodeURIComponent(approvalId)}`);
    return asApproval(parseJson(status, text));
  }

  approve(approvalId: string, opts: { note?: string; decidedBy?: string } = {}) {
    return this.decide(approvalId, "approve", opts);
  }

  reject(approvalId: string, opts: { note?: string; decidedBy?: string } = {}) {
    return this.decide(approvalId, "reject", opts);
  }

  private async decide(approvalId: string, verb: "approve" | "reject", opts: { note?: string; decidedBy?: string }): Promise<ApprovalRequest> {
    const body: Json = {};
    if (opts.note !== undefined) body.note = opts.note;
    if (opts.decidedBy !== undefined) body.decided_by = opts.decidedBy;
    const { status, text } = await this.call("POST", `/v1/approvals/${encodeURIComponent(approvalId)}/${verb}`, body);
    return asApproval(parseJson(status, text).approval);
  }

  async audit(opts: { limit?: number } = {}): Promise<Array<Record<string, unknown>>> {
    const { status, text } = await this.call("GET", `/v1/audit?limit=${opts.limit ?? 50}`);
    return (parseJson(status, text).events as Array<Record<string, unknown>>) ?? [];
  }
}

/**
 * Wrap an async function so agent-plane is consulted before every call.
 * `resource` derives the canonical resource from the call's arguments.
 * Throws on anything but ALLOW; the wrapped function never runs otherwise.
 */
export function govern<A extends unknown[], R>(
  plane: AgentPlane,
  spec: { task: string | (() => string); action: string; resource: (...args: A) => string; context?: (...args: A) => Record<string, string>; waitForApprovalMs?: number },
  fn: (...args: A) => Promise<R> | R,
): (...args: A) => Promise<R> {
  return async (...args: A): Promise<R> => {
    const task = typeof spec.task === "function" ? spec.task() : spec.task;
    let decision = await plane.authorize({ task, action: spec.action, resource: spec.resource(...args), context: spec.context?.(...args) });
    if (decision.needsApproval && spec.waitForApprovalMs) {
      decision = await plane.waitForApproval(decision, { timeoutMs: spec.waitForApprovalMs });
    }
    if (!decision.allowed) {
      const err = new NotAuthorized(decision);
      throw err;
    }
    return fn(...args);
  };
}

export class NotAuthorized extends Error {
  readonly decision: AuthorityDecision;
  constructor(decision: AuthorityDecision) {
    super(`${decision.decision.toUpperCase()}: ${decision.reason} (${decision.action} -> ${decision.resource}; evidence ${decision.evidenceId})`);
    this.name = decision.needsApproval ? "ApprovalRequired" : "NotAuthorized";
    this.decision = decision;
  }
}
