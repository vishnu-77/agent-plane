/**
 * TypeScript client for agent-plane.
 *
 * Mirrors the Python SDK contract. A developer holds one credential - a
 * Project API Key (`ap_live_…` / `ap_test_…`) - and works through a task:
 *
 *   import { AgentPlane } from "@agent-plane/sdk";
 *
 *   const ap = new AgentPlane();                      // AGENTPLANE_API_KEY / AGENTPLANE_URL
 *   const task = await ap.task("fix-staging-checkout");
 *   const d = await task.authorize("deployment.restart", "staging/checkout");
 *   if (d.proceed) await restart();
 *
 * `authorize()` asks before a side effect; `report()` tells agent-plane what a
 * tool did. Both carry the task, so the decision and its evidence are
 * attributable to the thing someone actually asked for.
 *
 * A decision only blocks when the connector can block. `binding` says whether
 * this decision actually stopped anything, and `enforcement` says what the
 * reporting connector is capable of. For an SDK caller enforcement is
 * advisory: agent-plane answers, your code decides. Nothing here may be read
 * as agent-plane having blocked an action it cannot reach.
 *
 * Any inconsistent or unexpected response throws `AuthorizationProtocolError`;
 * HTTP and network failures throw too. None of those may be treated as
 * permission.
 *
 * Uses the global `fetch` (Node 18+, browsers, edge runtimes). No dependencies.
 */

export type DecisionKind = "allow" | "deny" | "approval_required" | "quarantine" | "simulate";
export type ApprovalStatus = "pending" | "approved" | "rejected" | "consumed" | "expired";

/**
 * Project runtime mode. `observe` records and never blocks; `govern` returns
 * the real decision but leaves execution to the caller; `enforce` binds, but
 * only on connectors that can actually block.
 */
export type RuntimeMode = "observe" | "govern" | "enforce";

/**
 * What the reporting connector is capable of, declared per integration kind.
 * `advisory` means agent-plane can only answer - it cannot stop the action.
 */
export type EnforcementLevel = "full" | "partial" | "advisory";

/** The server rejects a larger batch; refuse before spending a round trip. */
export const MAX_BATCH = 50;

const DEFAULT_URL = "http://127.0.0.1:8000";
const MODES: string[] = ["observe", "govern", "enforce"];
const ENFORCEMENT_LEVELS: string[] = ["full", "partial", "advisory"];

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
  /** the agent the decision was attributed to; "" when the server did not say */
  agent: string;
  /** true only for an explicit ALLOW */
  allowed: boolean;
  needsApproval: boolean;
  quarantined: boolean;
  /** false when the project's mode did not enforce (observe / govern) */
  enforced: boolean;
  /** what enforce mode would have returned, when this one did not enforce */
  wouldBe: string | null;
  /** the decision is information, not an outcome: nothing was stopped */
  advisory: boolean;
  /** the project's runtime mode, when the server reported it */
  mode: RuntimeMode | null;
  /** what the reporting connector can do; null when the server did not say */
  enforcement: EnforcementLevel | null;
  /**
   * Whether this decision actually binds - true only when the project enforces
   * *and* the connector can block. False means agent-plane recorded the
   * decision and nothing else; honouring it is the caller's job. Never report
   * an action as blocked unless this is true.
   */
  binding: boolean;
  /** ALLOW, or an observe-mode SIMULATE: the executor may proceed */
  proceed: boolean;
  consequence: Record<string, unknown>;
  explanation: string[];
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
  /** caller-declared impact; the server derives its own from the resource catalog */
  impact?: "reversible" | "irreversible";
  /** provenance attached to the audit record (parent_evidence_id, conversation_id, ...) */
  context?: Record<string, string>;
}

/**
 * One action an agent is about to take, or has just taken. Either name the
 * canonical `action` and `resource`, or hand over the raw `tool` and
 * `arguments` and let the server normalize them.
 */
export interface ActionEvent {
  task?: string;
  tool?: string;
  action?: string;
  resource?: string;
  arguments?: Record<string, unknown>;
  repository?: string;
  branch?: string;
  impact?: "reversible" | "irreversible";
  approval?: string;
  context?: Record<string, string>;
  /** where the task came from; provenance, never permission */
  origin?: Record<string, unknown>;
  agent?: string;
  integration?: string;
  session?: string;
  host?: string;
}

export interface SessionInfo {
  session: string;
  agent: string;
  project: string;
  mode: RuntimeMode | null;
}

export interface ClientOptions {
  /** Project API Key. Falls back to AGENTPLANE_API_KEY. */
  apiKey?: string;
  /** runtime base URL. Falls back to AGENTPLANE_URL, then localhost. */
  url?: string;
  /** agent identifier reported with every call (X-Agent-Id) */
  agent?: string;
  /** integration kind; decides the enforcement level the server reports back */
  integration?: string;
  /** custom fetch (tests, polyfills) */
  fetch?: typeof fetch;
  /** per-request timeout in ms (default 10s) */
  timeoutMs?: number;
}

const EXPECTED: Record<number, DecisionKind[]> = {
  200: ["allow", "simulate"], 202: ["approval_required"], 403: ["deny"], 423: ["quarantine"],
};

type Json = Record<string, unknown>;

function isRecord(v: unknown): v is Json {
  return typeof v === "object" && v !== null && !Array.isArray(v);
}

/**
 * Read an environment variable without depending on @types/node: the same
 * bundle runs in browsers and edge runtimes, where `process` may not exist.
 */
function env(name: string): string | undefined {
  const proc = (globalThis as { process?: { env?: Record<string, string | undefined> } }).process;
  return proc?.env?.[name];
}

function asMode(v: unknown): RuntimeMode | null {
  return typeof v === "string" && MODES.includes(v) ? (v as RuntimeMode) : null;
}

function asEnforcement(v: unknown): EnforcementLevel | null {
  return typeof v === "string" && ENFORCEMENT_LEVELS.includes(v) ? (v as EnforcementLevel) : null;
}

/**
 * Validate and shape one decision payload, whichever edge produced it.
 *
 * `fallback` supplies what the caller asked about; `/v1/events/action` echoes
 * its own normalized task, action and resource, and those win, because they
 * are what was actually decided on.
 */
function decisionOf(
  payload: unknown,
  fallback: { task: string; action: string; resource: string },
): AuthorityDecision {
  if (
    !isRecord(payload) ||
    typeof payload.decision !== "string" ||
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
  const enforced = payload.enforced === undefined ? true : payload.enforced === true;
  if (decision === "simulate" && enforced) throw new AuthorizationProtocolError("Inconsistent simulate decision");
  const consequence = isRecord(payload.consequence) ? payload.consequence : {};
  const explanation = Array.isArray(payload.explanation) ? payload.explanation.map(String) : [];
  return {
    decision,
    reason: payload.reason,
    lease: (payload.lease as string | undefined) ?? null,
    evidenceId: payload.evidence_id,
    approvalId: (payload.approval_id as string | undefined) ?? null,
    context,
    task: typeof payload.task === "string" && payload.task ? payload.task : fallback.task,
    action: typeof payload.action === "string" && payload.action ? payload.action : fallback.action,
    resource: typeof payload.resource === "string" && payload.resource ? payload.resource : fallback.resource,
    agent: typeof payload.agent === "string" ? payload.agent : "",
    allowed: decision === "allow",
    needsApproval: decision === "approval_required",
    quarantined: decision === "quarantine",
    enforced,
    wouldBe: (payload.would_be as string | undefined) ?? null,
    advisory: payload.advisory === true,
    mode: asMode(payload.mode),
    enforcement: asEnforcement(payload.enforcement),
    // Default false. A missing field must never read as "agent-plane blocked
    // this"; only an explicit true from the server binds.
    binding: payload.binding === true,
    proceed: decision === "allow" || (decision === "simulate" && !enforced),
    consequence,
    explanation,
  };
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
  if (!isRecord(payload) || !expected.includes(payload.decision as DecisionKind)) {
    throw new AuthorizationProtocolError("Inconsistent authorization decision");
  }
  return decisionOf(payload, input);
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

interface Resolved {
  baseUrl: string;
  headers: Record<string, string>;
  fetch?: typeof fetch;
  timeoutMs?: number;
}

abstract class BaseClient {
  protected readonly baseUrl: string;
  protected readonly fetchImpl: typeof fetch;
  protected readonly timeoutMs: number;
  protected readonly headers: Record<string, string>;

  constructor(cfg: Resolved) {
    this.headers = cfg.headers;
    this.baseUrl = cfg.baseUrl.replace(/\/+$/, "");
    this.fetchImpl = cfg.fetch ?? globalThis.fetch;
    this.timeoutMs = cfg.timeoutMs ?? 10_000;
    if (!this.fetchImpl) throw new Error("No fetch implementation available; pass options.fetch");
  }

  protected call(method: string, path: string, body?: unknown) {
    const init: RequestInit = { method, headers: { ...this.headers, "content-type": "application/json" } };
    if (body !== undefined) init.body = JSON.stringify(body);
    return request(this.fetchImpl, this.timeoutMs, this.baseUrl + path, init);
  }
}

/**
 * Resolve the executor credential and endpoint. Kept outside the constructor
 * so `super()` stays the first statement.
 */
function executorConfig(
  baseUrlOrOptions: string | ClientOptions | undefined,
  token: string | undefined,
  options: ClientOptions,
): Resolved {
  const opts = typeof baseUrlOrOptions === "object" && baseUrlOrOptions !== null ? baseUrlOrOptions : options;
  const baseUrl = typeof baseUrlOrOptions === "string" ? baseUrlOrOptions : undefined;
  const credential = opts.apiKey ?? token ?? env("AGENTPLANE_API_KEY");
  if (!credential) {
    throw new Error(
      "No credential. Pass apiKey: … or set AGENTPLANE_API_KEY " +
      "(get one from Integrations in the console).",
    );
  }
  const integration = opts.integration ?? "custom";
  const headers: Record<string, string> = {
    Authorization: `Bearer ${credential}`,
    "X-Integration": integration,
  };
  if (opts.agent) headers["X-Agent-Id"] = opts.agent;
  return {
    baseUrl: opts.url ?? baseUrl ?? env("AGENTPLANE_URL") ?? DEFAULT_URL,
    headers,
    fetch: opts.fetch,
    timeoutMs: opts.timeoutMs,
  };
}

/**
 * One unit of work an agent is doing, and the authority it acts under.
 * Obtained from `ap.task(name)`, which also registers the task's provenance.
 */
export class Task {
  readonly plane: AgentPlane;
  readonly name: string;
  readonly origin: Record<string, unknown>;

  constructor(plane: AgentPlane, name: string, origin: Record<string, unknown> = {}) {
    this.plane = plane;
    this.name = name;
    this.origin = origin;
  }

  /** Ask before the side effect; execute only when the decision says proceed. */
  authorize(
    action: string,
    resource: string,
    opts: Omit<AuthorizeInput, "task" | "action" | "resource"> = {},
  ): Promise<AuthorityDecision> {
    return this.plane.authorize({ ...opts, task: this.name, action, resource });
  }

  /** Report one action taken under this task. */
  report(event: Omit<ActionEvent, "task"> = {}): Promise<AuthorityDecision> {
    return this.plane.report({ ...event, task: this.name });
  }

  /** Report up to MAX_BATCH actions taken under this task, in one round trip. */
  reportBatch(events: Array<Omit<ActionEvent, "task">>): Promise<AuthorityDecision[]> {
    return this.plane.reportBatch(events.map((e) => ({ ...e, task: this.name })));
  }

  /** Wait out an approval_required decision raised by this task. */
  waitForApproval(
    decision: AuthorityDecision,
    opts: { timeoutMs?: number; intervalMs?: number } = {},
  ): Promise<AuthorityDecision> {
    return this.plane.waitForApproval(decision, opts);
  }
}

/** Executor-side client, authenticated with a Project API Key. */
export class AgentPlane extends BaseClient {
  /** agent identifier sent with every call, or null when the server names one */
  readonly agent: string | null;
  readonly integration: string;

  /** `new AgentPlane()` / `new AgentPlane({ apiKey, url })` - the normal form. */
  constructor(options?: ClientOptions);
  /** Legacy form, for deployments that mint their own identity tokens. */
  constructor(baseUrl: string, token: string, options?: ClientOptions);
  constructor(baseUrlOrOptions?: string | ClientOptions, token?: string, options: ClientOptions = {}) {
    super(executorConfig(baseUrlOrOptions, token, options));
    this.agent = this.headers["X-Agent-Id"] ?? null;
    this.integration = this.headers["X-Integration"] ?? "custom";
  }

  // -- authorization ---------------------------------------------------------- //

  /** Ask whether `action` on `resource` is authorised for `task`. */
  async authorize(input: AuthorizeInput): Promise<AuthorityDecision> {
    const body: Json = { task: input.task, action: input.action, resource: input.resource };
    if (input.approval) body.approval = input.approval;
    if (input.impact) body.impact = input.impact;
    if (input.context && Object.keys(input.context).length) body.context = input.context;
    const { status, text } = await this.call("POST", "/v1/authorize", body);
    return parseDecision(status, text, input);
  }

  // -- tasks ------------------------------------------------------------------ //

  /**
   * Start (or resume) a task and hand back a handle you can authorize and
   * report against. Registering provenance is a convenience, not a
   * precondition for asking permission, so a rejected registration is
   * swallowed rather than allowed to stop the work.
   */
  async task(name: string, opts: { origin?: Record<string, unknown> } = {}): Promise<Task> {
    try {
      await this.registerTask(name, opts.origin ?? {});
    } catch (err) {
      if (!(err instanceof HttpError)) throw err;
    }
    return new Task(this, name, opts.origin ?? {});
  }

  /** Record where a task came from. Prompts are provenance, never permission. */
  async registerTask(task: string, origin: Record<string, unknown> = {}): Promise<Record<string, unknown>> {
    const { status, text } = await this.call("POST", "/v1/tasks", { task, origin });
    return (parseJson(status, text).task as Record<string, unknown>) ?? {};
  }

  // -- reporting -------------------------------------------------------------- //

  /**
   * Report one action. The server normalizes the tool name and arguments into
   * a canonical action and resource, decides, records the evidence, and
   * answers. Read `binding` before describing anything as blocked.
   */
  async report(event: ActionEvent): Promise<AuthorityDecision> {
    const { status, text } = await this.call("POST", "/v1/events/action", this.eventBody(event));
    return decisionOf(parseJson(status, text), fallbackFor(event));
  }

  /** Report up to MAX_BATCH actions in one round trip; one decision each, in order. */
  async reportBatch(events: ActionEvent[]): Promise<AuthorityDecision[]> {
    if (events.length > MAX_BATCH) {
      throw new RangeError(`a batch carries at most ${MAX_BATCH} events (got ${events.length})`);
    }
    const { status, text } = await this.call("POST", "/v1/events/action", {
      events: events.map((e) => this.eventBody(e)),
    });
    const results = parseJson(status, text).results;
    // One decision per event, positionally: a short or reordered array would
    // silently attribute a decision to the wrong action.
    if (!Array.isArray(results) || results.length !== events.length) {
      throw new AuthorizationProtocolError("Batch response does not match the batch sent");
    }
    return results.map((r, i) => decisionOf(r, fallbackFor(events[i] as ActionEvent)));
  }

  /** Announce an agent session. Optional: reporting actions creates one anyway. */
  async startSession(opts: { task?: string; origin?: Record<string, unknown> } = {}): Promise<SessionInfo> {
    const body: Json = { integration: this.integration };
    if (opts.task) body.task = opts.task;
    if (opts.origin) body.origin = opts.origin;
    if (this.agent) body.agent = this.agent;
    const { status, text } = await this.call("POST", "/v1/sessions", body);
    const payload = parseJson(status, text);
    return {
      session: String(payload.session ?? ""),
      agent: String(payload.agent ?? ""),
      project: String(payload.project ?? ""),
      mode: asMode(payload.mode),
    };
  }

  private eventBody(event: ActionEvent): Json {
    const body: Json = { ...event };
    if (this.agent && body.agent === undefined) body.agent = this.agent;
    if (body.integration === undefined) body.integration = this.integration;
    return body;
  }

  // -- approvals -------------------------------------------------------------- //

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

  // -- delegation ------------------------------------------------------------- //

  /** Mint an attenuated child lease for `agent` (lease-holder self-service). */
  async delegate(leaseId: string, body: { agent: string } & Partial<Omit<Lease, "id" | "task" | "subject" | "revoked">> & { id?: string }): Promise<Lease> {
    const { status, text } = await this.call("POST", `/v1/leases/${encodeURIComponent(leaseId)}/delegate`, body);
    return asLease(parseJson(status, text).lease);
  }
}

function fallbackFor(event: ActionEvent): { task: string; action: string; resource: string } {
  return {
    task: event.task ?? "",
    action: event.action ?? event.tool ?? "",
    resource: event.resource ?? "",
  };
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
    super({
      baseUrl, headers: { "X-Admin-Token": adminToken },
      fetch: opts.fetch, timeoutMs: opts.timeoutMs,
    });
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

  async agents(opts: { tenant?: string } = {}): Promise<Array<Record<string, unknown>>> {
    const q = opts.tenant ? `?tenant=${encodeURIComponent(opts.tenant)}` : "";
    const { status, text } = await this.call("GET", `/v1/agents${q}`);
    return (parseJson(status, text).agents as Array<Record<string, unknown>>) ?? [];
  }

  async decisions(opts: { tenant?: string; limit?: number } = {}): Promise<Array<Record<string, unknown>>> {
    const params = new URLSearchParams({ limit: String(opts.limit ?? 50) });
    if (opts.tenant) params.set("tenant", opts.tenant);
    const { status, text } = await this.call("GET", `/v1/decisions?${params}`);
    return (parseJson(status, text).decisions as Array<Record<string, unknown>>) ?? [];
  }

  async decision(decisionId: string): Promise<Record<string, unknown>> {
    const { status, text } = await this.call("GET", `/v1/decisions/${encodeURIComponent(decisionId)}`);
    return parseJson(status, text);
  }

  async setMode(mode: RuntimeMode, tenant?: string): Promise<Record<string, unknown>> {
    const { status, text } = await this.call("PUT", "/admin/mode", tenant ? { mode, tenant } : { mode });
    return parseJson(status, text);
  }

  async audit(opts: { limit?: number } = {}): Promise<Array<Record<string, unknown>>> {
    const { status, text } = await this.call("GET", `/v1/audit?limit=${opts.limit ?? 50}`);
    return (parseJson(status, text).events as Array<Record<string, unknown>>) ?? [];
  }
}

/**
 * Wrap an async function so agent-plane is consulted before every call.
 * `resource` derives the canonical resource from the call's arguments.
 * The wrapped function runs only when the decision says proceed; anything
 * else throws NotAuthorized. This is how an SDK caller chooses to make an
 * advisory decision binding on itself - agent-plane cannot do it for you.
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
    if (!decision.proceed) {
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
