# @agent-plane/sdk

TypeScript client for the agent-plane authorization service. Zero
dependencies; uses the global `fetch` (Node 18+, browsers, edge runtimes).
Same contract as the Python SDK: **execute only on an explicit ALLOW**.

## Install

```bash
npm install @agent-plane/sdk        # once published
# or from this checkout
npm install ./sdk/typescript && npm --prefix sdk/typescript run build
```

## Authorize before execution

```ts
import { AgentPlane } from "@agent-plane/sdk";

const plane = new AgentPlane(process.env.AGENT_PLANE_URL!, agentToken);

const d = await plane.authorize({
  task: "fix-staging-checkout",
  action: "deployment.restart",
  resource: "staging/checkout",
  context: { conversation_id: threadId, origin: "human" },   // optional provenance
});

if (d.allowed) {
  await restart();                       // ALLOW: run the real action
} else if (d.needsApproval) {
  const resumed = await plane.waitForApproval(d, { timeoutMs: 600_000 });
  if (resumed.allowed) await restart();  // ACTION_APPROVED, exactly once
} else {
  console.warn(d.reason, d.evidenceId);  // DENY
}
```

`AuthorityDecision` carries `decision`, `reason`, `lease`, `evidenceId`,
`approvalId`, `context`, and the booleans `allowed` / `needsApproval`.
Inconsistent or malformed replies throw `AuthorizationProtocolError`; non-2xx
statuses other than 202/403 throw `HttpError`; network failures reject. All of
these must stop execution.

## Wrap a function once

```ts
import { govern } from "@agent-plane/sdk";

const deleteBranch = govern(
  plane,
  { task: () => currentTask(), action: "branch.delete",
    resource: (branch: string) => `github://acme/repo/branches/${branch}` },
  async (branch: string) => github.deleteBranch(branch),
);
await deleteBranch("stale-fix");   // throws NotAuthorized unless ALLOW
```

## Operator / backend client

```ts
import { AgentPlaneAdmin } from "@agent-plane/sdk";

const admin = new AgentPlaneAdmin(url, process.env.ADMIN_TOKEN!);
const lease = await admin.issueFromTemplate("repair-service", {
  subject: "devops-agent", task: "fix-staging-checkout", tenant: "acme",
  variables: { env: "staging", service: "checkout" },
});
for (const req of await admin.listApprovals()) await admin.approve(req.id, { note: "ok" });
await admin.shrinkLease(lease.id, { actions: ["deployment.read"] });
await admin.revokeLease(lease.id);
```

Never ship `ADMIN_TOKEN` to an agent or a browser.

## Test

```bash
node --experimental-strip-types --test test/
```
