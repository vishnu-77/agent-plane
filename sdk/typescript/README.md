# @agent-plane/sdk

TypeScript client for agent-plane. Zero dependencies; uses the global `fetch`
(Node 18+, browsers, edge runtimes). Same contract as the Python SDK.

## Install

```bash
npm install @agent-plane/sdk        # once published
# or from this checkout
npm install ./sdk/typescript && npm --prefix sdk/typescript run build
```

## Credentials

One credential: a **Project API Key**, created under Integrations in the
console. It looks like `ap_live_…` (or `ap_test_…` for the test environment)
and is scoped to a single project. You do not mint JWTs, and you never give an
agent the admin token.

```ts
import { AgentPlane } from "@agent-plane/sdk";

// from the environment: AGENTPLANE_API_KEY, AGENTPLANE_URL
const ap = new AgentPlane();

// or explicitly
const ap = new AgentPlane({ apiKey: "ap_live_…", url: "https://plane.acme.com" });
```

Construction throws if no key is found, rather than producing a client that
silently reports nothing.

## A decision only blocks when the connector can block

agent-plane answers; whether the answer *stops* anything depends on where the
action is happening. The SDK is an advisory connector: your code asks, and your
code decides whether to honour the answer. Every result carries both facts:

| field | meaning |
| --- | --- |
| `enforcement` | what this connector is capable of - `full`, `partial`, or `advisory` |
| `binding` | whether this specific decision actually stopped anything |

`binding` is true only when the project is in `enforce` mode **and** the
connector can block (an MCP gateway or model gateway, where agent-plane holds
the upstream credential). For an SDK caller it is false: the decision was
evaluated and recorded, and nothing was prevented. Never tell a user an action
was blocked unless `binding` is true.

`binding` defaults to `false` when a server does not report it, so an older or
unexpected response can never be mistaken for enforcement.

## Runtime modes

A project runs in one of three modes. `decision.mode` reports which one
produced the answer.

| mode | what comes back | what happened |
| --- | --- | --- |
| `observe` | `simulate`, with `enforced: false` and `wouldBe` set | recorded only; nothing is ever refused |
| `govern` | the real decision, with `advisory: true` | the verdict is real, execution is still yours |
| `enforce` | the real decision | binds wherever the connector can block |

`decision.proceed` folds this together: true for an explicit `allow`, and for
an observe-mode `simulate` (where refusing would be pretending to enforce).
`decision.allowed` stays strict - `allow` and nothing else.

## Ask before acting, report what happened

```ts
import { AgentPlane } from "@agent-plane/sdk";

const ap = new AgentPlane({ agent: "release-bot", integration: "langgraph" });
const task = await ap.task("fix-staging-checkout", {
  origin: { kind: "prompt", created_by: "dana@acme.com" },
});

const decision = await task.authorize("deployment.restart", "staging/checkout");
if (decision.proceed) {
  await restartService("staging/checkout");
  await task.report({ action: "deployment.restart", resource: "staging/checkout" });
} else if (decision.needsApproval) {
  const resumed = await task.waitForApproval(decision, { timeoutMs: 600_000 });
  if (resumed.allowed) await restartService("staging/checkout");
} else {
  // Say what is true: agent-plane refused, this code stopped.
  console.warn(`${decision.reason} (evidence ${decision.evidenceId})`);
  console.warn(decision.explanation.join("\n"));
}
```

`task.report()` also takes a raw tool call; the server normalizes the tool name
and arguments into a canonical action and resource:

```ts
await task.report({ tool: "Write", arguments: { file_path: "src/auth.ts" } });
```

## Report a batch

Up to `MAX_BATCH` (50) events in one round trip. One decision comes back per
event, in the order sent; a larger batch throws `RangeError` before the
request is made.

```ts
import { MAX_BATCH } from "@agent-plane/sdk";

const decisions = await task.reportBatch([
  { tool: "Read", arguments: { file_path: "src/auth.ts" } },
  { tool: "Write", arguments: { file_path: "src/auth.ts" } },
  { tool: "Bash", arguments: { command: "npm test" } },
]);
for (const d of decisions) {
  if (!d.proceed) console.warn(d.action, d.resource, d.reason, `binding=${d.binding}`);
}
```

## Wrap a function once

`govern()` is how an SDK caller chooses to make an advisory decision binding on
itself: the wrapped function runs only when the decision says proceed.

```ts
import { govern } from "@agent-plane/sdk";

const deleteBranch = govern(
  ap,
  { task: () => currentTask(), action: "branch.delete",
    resource: (branch: string) => `github://acme/repo/branches/${branch}` },
  async (branch: string) => github.deleteBranch(branch),
);
await deleteBranch("stale-fix");   // throws NotAuthorized unless the decision says proceed
```

## Errors

Inconsistent or malformed replies throw `AuthorizationProtocolError`; non-2xx
statuses other than 202/403/423 throw `HttpError`; network failures and
timeouts reject. All of these must stop execution - an unexpected response is
never permission.

## Operator / backend client

`AgentPlaneAdmin` is separate, and authenticates with `ADMIN_TOKEN` rather than
a Project API Key. It issues, shrinks and revokes leases and works the approval
queue. Never ship `ADMIN_TOKEN` to an agent or a browser.

```ts
import { AgentPlaneAdmin } from "@agent-plane/sdk";

const admin = new AgentPlaneAdmin(url, process.env.ADMIN_TOKEN!);
const lease = await admin.issueFromTemplate("repair-service", {
  subject: "release-bot", task: "fix-staging-checkout", tenant: "acme",
  variables: { env: "staging", service: "checkout" },
});
for (const req of await admin.listApprovals()) await admin.approve(req.id, { note: "ok" });
await admin.shrinkLease(lease.id, { actions: ["deployment.read"] });
await admin.revokeLease(lease.id);
```

## Develop

```bash
npm install
npm run typecheck
npm run build
npm test
```
