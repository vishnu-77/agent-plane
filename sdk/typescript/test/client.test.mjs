// Runs with: node --experimental-strip-types --test test/
// No server needed: a fetch stub answers each request.
import { test } from "node:test";
import assert from "node:assert/strict";
import {
  AgentPlane, AgentPlaneAdmin, AuthorizationProtocolError, HttpError, MAX_BATCH, NotAuthorized, Task, govern,
} from "../src/index.ts";

function fetchStub(handler) {
  const calls = [];
  const fetchImpl = async (url, init) => {
    calls.push({ url: String(url), init });
    const out = handler(String(url), init);
    const status = out.status ?? 200;
    const body = typeof out.body === "string" ? out.body : JSON.stringify(out.body ?? {});
    return new Response(body, { status, headers: { "content-type": "application/json" } });
  };
  return { fetchImpl, calls };
}

const OK = { decision: "allow", reason: "ACTION_WITHIN_TASK_AUTHORITY", lease: "lease-1", evidence_id: "az_1" };

/** A client whose only credential is a Project API Key, as a developer holds it. */
function keyed(fetchImpl, opts = {}) {
  return new AgentPlane({ apiKey: "ap_test_key", url: "https://plane.test", fetch: fetchImpl, ...opts });
}

test("authorize parses top-level and detail-wrapped decisions", async () => {
  for (const [status, decision, wrapped] of [[200, "allow", false], [202, "approval_required", true], [403, "deny", true]]) {
    const payload = { ...OK, decision, approval_id: decision === "approval_required" ? "apr_1" : undefined };
    const { fetchImpl, calls } = fetchStub(() => ({ status, body: wrapped ? { detail: payload } : payload }));
    const plane = new AgentPlane("https://plane.test/", "tok", { fetch: fetchImpl });
    const d = await plane.authorize({ task: "t", action: "a.b", resource: "r" });
    assert.equal(d.decision, decision);
    assert.equal(d.allowed, decision === "allow");
    assert.equal(d.needsApproval, decision === "approval_required");
    assert.equal(d.evidenceId, "az_1");
    assert.equal(calls[0].url, "https://plane.test/v1/authorize");
    assert.equal(calls[0].init.headers.Authorization, "Bearer tok");
  }
});

test("inconsistent responses fail closed", async () => {
  const bad = [
    [200, {}], [200, []], [200, { detail: "x" }], [200, { decision: "allow", reason: "OK" }],
    [202, OK], [403, OK], [200, { ...OK, reason: "" }], [200, { ...OK, evidence_id: 1 }],
    [200, { ...OK, lease: {} }], [200, { ...OK, approval_id: 5 }],
  ];
  for (const [status, body] of bad) {
    const { fetchImpl } = fetchStub(() => ({ status, body }));
    const plane = new AgentPlane("https://plane.test", "tok", { fetch: fetchImpl });
    await assert.rejects(plane.authorize({ task: "t", action: "a", resource: "r" }), AuthorizationProtocolError, JSON.stringify([status, body]));
  }
  const { fetchImpl } = fetchStub(() => ({ status: 200, body: "not json" }));
  await assert.rejects(new AgentPlane("https://plane.test", "tok", { fetch: fetchImpl }).authorize({ task: "t", action: "a", resource: "r" }), AuthorizationProtocolError);
});

test("http errors propagate as HttpError", async () => {
  for (const status of [401, 404, 429, 500]) {
    const { fetchImpl } = fetchStub(() => ({ status, body: { error: "x" } }));
    await assert.rejects(new AgentPlane("https://plane.test", "tok", { fetch: fetchImpl }).authorize({ task: "t", action: "a", resource: "r" }), (e) => e instanceof HttpError && e.status === status);
  }
});

test("waitForApproval polls then resumes", async () => {
  let polls = 0;
  const { fetchImpl, calls } = fetchStub((url, init) => {
    if (url.endsWith("/v1/approvals/apr_1")) {
      polls += 1;
      return { body: { id: "apr_1", status: polls < 2 ? "pending" : "approved" } };
    }
    const body = JSON.parse(init.body);
    if (body.approval === "apr_1") return { status: 200, body: { ...OK, reason: "ACTION_APPROVED", approval_id: "apr_1" } };
    return { status: 202, body: { detail: { ...OK, decision: "approval_required", reason: "ACTION_REQUIRES_APPROVAL", approval_id: "apr_1" } } };
  });
  const plane = keyed(fetchImpl);
  const first = await plane.authorize({ task: "t", action: "a", resource: "r" });
  assert.equal(first.approvalId, "apr_1");
  const resumed = await plane.waitForApproval(first, { timeoutMs: 1000, intervalMs: 1 });
  assert.equal(resumed.allowed, true);
  assert.equal(resumed.reason, "ACTION_APPROVED");
  assert.equal(polls, 2);
  assert.equal(JSON.parse(calls.at(-1).init.body).approval, "apr_1");

  // The same wait, reached through a task handle.
  polls = 0;
  const viaTask = await new Task(plane, "t").waitForApproval(first, { timeoutMs: 1000, intervalMs: 1 });
  assert.equal(viaTask.reason, "ACTION_APPROVED");
});

test("govern runs the function only on ALLOW", async () => {
  let mode = "allow";
  const { fetchImpl } = fetchStub(() => mode === "allow"
    ? { status: 200, body: OK }
    : { status: 403, body: { detail: { ...OK, decision: "deny", reason: "RESOURCE_PROTECTED" } } });
  const plane = keyed(fetchImpl);
  const ran = [];
  const restart = govern(plane, { task: "t", action: "deployment.restart", resource: (svc) => `staging/${svc}` }, async (svc) => { ran.push(svc); return "ok"; });
  assert.equal(await restart("cart"), "ok");
  mode = "deny";
  await assert.rejects(restart("locked"), (e) => e instanceof NotAuthorized && e.decision.reason === "RESOURCE_PROTECTED");
  assert.deepEqual(ran, ["cart"]);
});

// --------------------------------------------------------------------------- //
// Project API Key construction
// --------------------------------------------------------------------------- //

test("a Project API Key is read from options, then the environment", async () => {
  const { fetchImpl, calls } = fetchStub(() => ({ body: OK }));
  const plane = keyed(fetchImpl, { agent: "release-bot", integration: "langgraph" });
  await plane.authorize({ task: "t", action: "a", resource: "r" });
  assert.equal(calls[0].url, "https://plane.test/v1/authorize");
  assert.equal(calls[0].init.headers.Authorization, "Bearer ap_test_key");
  assert.equal(calls[0].init.headers["X-Agent-Id"], "release-bot");
  assert.equal(calls[0].init.headers["X-Integration"], "langgraph");

  const saved = { key: process.env.AGENTPLANE_API_KEY, url: process.env.AGENTPLANE_URL };
  process.env.AGENTPLANE_API_KEY = "ap_live_env";
  process.env.AGENTPLANE_URL = "https://env.plane.test/";
  try {
    const stub = fetchStub(() => ({ body: OK }));
    const fromEnv = new AgentPlane({ fetch: stub.fetchImpl });
    await fromEnv.authorize({ task: "t", action: "a", resource: "r" });
    assert.equal(stub.calls[0].url, "https://env.plane.test/v1/authorize");
    assert.equal(stub.calls[0].init.headers.Authorization, "Bearer ap_live_env");
    assert.equal(fromEnv.integration, "custom");
    assert.equal(fromEnv.agent, null);
  } finally {
    if (saved.key === undefined) delete process.env.AGENTPLANE_API_KEY; else process.env.AGENTPLANE_API_KEY = saved.key;
    if (saved.url === undefined) delete process.env.AGENTPLANE_URL; else process.env.AGENTPLANE_URL = saved.url;
  }
});

test("no credential anywhere is a construction error, not a silent unauthenticated client", () => {
  const saved = process.env.AGENTPLANE_API_KEY;
  delete process.env.AGENTPLANE_API_KEY;
  try {
    assert.throws(() => new AgentPlane({ url: "https://plane.test" }), /AGENTPLANE_API_KEY/);
  } finally {
    if (saved !== undefined) process.env.AGENTPLANE_API_KEY = saved;
  }
});

// --------------------------------------------------------------------------- //
// Tasks, reporting, batches
// --------------------------------------------------------------------------- //

const REPORTED = {
  ...OK, enforced: true, mode: "enforce", explanation: ["within the task's authority"],
  action: "filesystem.write", resource: "workspace/src/auth.ts", task: "fix-auth", agent: "agent",
  enforcement: "advisory", binding: false,
};

test("task() registers provenance and hands back a handle", async () => {
  const { fetchImpl, calls } = fetchStub((url) => url.endsWith("/v1/tasks")
    ? { body: { task: { id: "fix-auth" } } }
    : { body: REPORTED });
  const plane = keyed(fetchImpl);
  const task = await plane.task("fix-auth", { origin: { kind: "prompt", created_by: "dana" } });
  assert.ok(task instanceof Task);
  assert.equal(task.name, "fix-auth");
  assert.equal(calls[0].url, "https://plane.test/v1/tasks");
  assert.deepEqual(JSON.parse(calls[0].init.body), { task: "fix-auth", origin: { kind: "prompt", created_by: "dana" } });

  const d = await task.authorize("filesystem.write", "workspace/src/auth.ts");
  assert.equal(JSON.parse(calls[1].init.body).task, "fix-auth");
  assert.equal(d.allowed, true);
});

test("a rejected task registration does not stop the work", async () => {
  const { fetchImpl } = fetchStub((url) => url.endsWith("/v1/tasks")
    ? { status: 500, body: { error: "registry down" } }
    : { body: REPORTED });
  const task = await keyed(fetchImpl).task("fix-auth");
  assert.equal((await task.report({ tool: "Write" })).allowed, true);
});

test("report surfaces enforcement and binding as the server states them", async () => {
  const { fetchImpl, calls } = fetchStub(() => ({ body: REPORTED }));
  const plane = keyed(fetchImpl, { agent: "claude-code", integration: "claude-code" });
  const d = await plane.report({ task: "fix-auth", tool: "Write", arguments: { file_path: "src/auth.ts" } });

  assert.equal(calls[0].url, "https://plane.test/v1/events/action");
  const sent = JSON.parse(calls[0].init.body);
  assert.equal(sent.agent, "claude-code");
  assert.equal(sent.integration, "claude-code");
  // The server's normalized action and resource win over what was sent.
  assert.equal(d.action, "filesystem.write");
  assert.equal(d.resource, "workspace/src/auth.ts");
  assert.equal(d.enforcement, "advisory");
  assert.equal(d.binding, false);
  assert.equal(d.mode, "enforce");
  assert.deepEqual(d.explanation, ["within the task's authority"]);
});

test("binding is false unless the server explicitly says otherwise", async () => {
  for (const [payload, binding, enforcement] of [
    [{ ...REPORTED }, false, "advisory"],
    [{ ...REPORTED, binding: true, enforcement: "full" }, true, "full"],
    [{ ...REPORTED, binding: "yes", enforcement: "sort-of" }, false, null],
    [{ ...OK }, false, null],   // older server: says nothing about either
  ]) {
    const { fetchImpl } = fetchStub(() => ({ body: payload }));
    const d = await keyed(fetchImpl).report({ task: "t", tool: "Write" });
    assert.equal(d.binding, binding, JSON.stringify(payload));
    assert.equal(d.enforcement, enforcement, JSON.stringify(payload));
  }
});

test("an observe-mode deny comes back as a non-binding simulate the caller may proceed on", async () => {
  const simulated = {
    ...OK, decision: "simulate", reason: "RESOURCE_PROTECTED", enforced: false,
    would_be: "deny", advisory: true, mode: "observe", enforcement: "partial", binding: false,
  };
  const { fetchImpl } = fetchStub(() => ({ body: simulated }));
  const d = await keyed(fetchImpl).report({ task: "t", tool: "Bash" });
  assert.equal(d.allowed, false);
  assert.equal(d.proceed, true);
  assert.equal(d.enforced, false);
  assert.equal(d.advisory, true);
  assert.equal(d.wouldBe, "deny");
  assert.equal(d.binding, false);
});

test("a govern-mode deny is a real deny that still blocked nothing", async () => {
  const governed = {
    ...OK, decision: "deny", reason: "RESOURCE_PROTECTED", enforced: false,
    would_be: "deny", advisory: true, mode: "govern", enforcement: "advisory", binding: false,
  };
  const { fetchImpl } = fetchStub(() => ({ body: governed }));
  const d = await keyed(fetchImpl).report({ task: "t", tool: "Bash" });
  assert.equal(d.decision, "deny");
  assert.equal(d.proceed, false);
  assert.equal(d.binding, false);
});

test("reportBatch sends one request and maps results positionally", async () => {
  const { fetchImpl, calls } = fetchStub((url, init) => {
    const events = JSON.parse(init.body).events;
    return {
      body: {
        results: events.map((e, i) => ({
          ...REPORTED, action: `act.${i}`, resource: e.resource, evidence_id: `az_${i}`,
          binding: i === 0, enforcement: "full",
        })),
      },
    };
  });
  const plane = keyed(fetchImpl);
  const task = new Task(plane, "fix-auth");
  const out = await task.reportBatch([{ tool: "Write", resource: "a" }, { tool: "Bash", resource: "b" }]);

  assert.equal(calls.length, 1);
  const sent = JSON.parse(calls[0].init.body);
  assert.equal(sent.events.length, 2);
  assert.deepEqual(sent.events.map((e) => e.task), ["fix-auth", "fix-auth"]);
  assert.deepEqual(out.map((d) => d.evidenceId), ["az_0", "az_1"]);
  assert.deepEqual(out.map((d) => d.resource), ["a", "b"]);
  assert.deepEqual(out.map((d) => d.binding), [true, false]);
});

test("a batch is refused before the round trip, and a mismatched answer fails closed", async () => {
  const { fetchImpl, calls } = fetchStub(() => ({ body: { results: [REPORTED] } }));
  const plane = keyed(fetchImpl);
  const tooMany = Array.from({ length: MAX_BATCH + 1 }, () => ({ tool: "Write" }));
  await assert.rejects(plane.reportBatch(tooMany), RangeError);
  assert.equal(calls.length, 0);
  await assert.rejects(
    plane.reportBatch([{ tool: "Write" }, { tool: "Bash" }]),
    AuthorizationProtocolError,
  );
});

test("startSession announces the agent and reports the project mode", async () => {
  const { fetchImpl, calls } = fetchStub(() => ({ body: { session: "s1", agent: "release-bot", project: "proj_1", mode: "govern" } }));
  const plane = keyed(fetchImpl, { agent: "release-bot", integration: "langgraph" });
  const session = await plane.startSession({ task: "fix-auth" });
  assert.equal(calls[0].url, "https://plane.test/v1/sessions");
  assert.deepEqual(JSON.parse(calls[0].init.body), { integration: "langgraph", task: "fix-auth", agent: "release-bot" });
  assert.deepEqual(session, { session: "s1", agent: "release-bot", project: "proj_1", mode: "govern" });
});

test("admin client sends the admin header and parses leases/approvals", async () => {
  const { fetchImpl, calls } = fetchStub((url, init) => {
    if (url.endsWith("/v1/leases") && init.method === "POST") return { body: { issued: true, lease: { id: "lease-x", ...JSON.parse(init.body) } } };
    if (url.includes("/v1/leases/from-template")) return { body: { issued: true, lease: { id: "lease-t", resources: ["staging/cart"] } } };
    if (url.includes("/v1/approvals?")) return { body: { approvals: [{ id: "apr_1", status: "pending" }], count: 1 } };
    if (url.endsWith("/approve")) return { body: { approval: { id: "apr_1", status: "approved" } } };
    if (url.includes("/v1/audit")) return { body: { events: [{ decision: "allow" }] } };
    if (init.method === "DELETE") return { body: { revoked: true } };
    return { body: { id: "lease-x", revoked: false } };
  });
  const admin = new AgentPlaneAdmin("https://plane.test", "adm", { fetch: fetchImpl });
  const lease = await admin.issueLease({ id: "lease-x", subject: "s", task: "t", resources: ["a/*"], actions: ["x.y"] });
  assert.equal(lease.id, "lease-x");
  assert.equal(calls[0].init.headers["X-Admin-Token"], "adm");
  assert.deepEqual((await admin.issueFromTemplate("repair-service", { subject: "s", task: "t", variables: { env: "staging", service: "cart" } })).resources, ["staging/cart"]);
  assert.equal((await admin.listApprovals())[0].id, "apr_1");
  assert.equal((await admin.approve("apr_1", { note: "ok" })).status, "approved");
  assert.equal((await admin.audit({ limit: 1 }))[0].decision, "allow");
  await admin.revokeLease("lease-x");
  assert.equal((await admin.getLease("lease-x")).revoked, false);
});
