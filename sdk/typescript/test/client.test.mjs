// Runs with: node --experimental-strip-types --test test/
// No server needed: a fetch stub answers each request.
import { test } from "node:test";
import assert from "node:assert/strict";
import { AgentPlane, AgentPlaneAdmin, AuthorizationProtocolError, HttpError, NotAuthorized, govern } from "../src/index.ts";

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
  const plane = new AgentPlane("https://plane.test", "tok", { fetch: fetchImpl });
  const first = await plane.authorize({ task: "t", action: "a", resource: "r" });
  assert.equal(first.approvalId, "apr_1");
  const resumed = await plane.waitForApproval(first, { timeoutMs: 1000, intervalMs: 1 });
  assert.equal(resumed.allowed, true);
  assert.equal(resumed.reason, "ACTION_APPROVED");
  assert.equal(polls, 2);
  assert.equal(JSON.parse(calls.at(-1).init.body).approval, "apr_1");
});

test("govern runs the function only on ALLOW", async () => {
  let mode = "allow";
  const { fetchImpl } = fetchStub(() => mode === "allow"
    ? { status: 200, body: OK }
    : { status: 403, body: { detail: { ...OK, decision: "deny", reason: "RESOURCE_PROTECTED" } } });
  const plane = new AgentPlane("https://plane.test", "tok", { fetch: fetchImpl });
  const ran = [];
  const restart = govern(plane, { task: "t", action: "deployment.restart", resource: (svc) => `staging/${svc}` }, async (svc) => { ran.push(svc); return "ok"; });
  assert.equal(await restart("cart"), "ok");
  mode = "deny";
  await assert.rejects(restart("locked"), (e) => e instanceof NotAuthorized && e.decision.reason === "RESOURCE_PROTECTED");
  assert.deepEqual(ran, ["cart"]);
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
