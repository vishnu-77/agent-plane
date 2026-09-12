# Approvals

An action can be inside an agent's authority and still need a human. A rule's
**ASK FIRST** list compiles to a lease's `require_approval`; a lease can name
those actions directly; a policy file can also demand approval for a brokered
tool. Either way the decision is `APPROVAL_REQUIRED` (HTTP 202 on
`/v1/authorize`), and that decision opens an **approval request** the runtime
tracks for you.

**Only in enforce mode.** In `observe` the decision comes back as `simulate`;
in `govern` it comes back as `approval_required` with `enforced: false` and no
`approval_id`. There is nothing to approve, because nothing was stopped. See
[modes.md](../modes.md).

## Lifecycle

```text
POST /v1/authorize  ──202──►  approval request (pending)
                                   │
        operator: POST /v1/approvals/{id}/approve | reject
                                   │
POST /v1/authorize {"approval": id} ──► ALLOW / ACTION_APPROVED   (once)
                                       DENY  / APPROVAL_REJECTED | _EXPIRED | _ALREADY_USED | _MISMATCH
```

- The request records tenant, subject, task, action, resource, lease id, the
  evidence id of the 202 decision, and any provenance `context` you sent.
- It expires after `APPROVAL_TTL_SECONDS` (default one hour) and never
  outlives the lease.
- An approved request is **consumed atomically** on resume: two executors
  resuming the same approval cannot both run.
- A revoked, expired, or narrowed lease wins over a granted approval; resume
  re-evaluates the lease first.
- A resume must repeat the identical task, action, and resource, or it is
  refused with `APPROVAL_MISMATCH` and the approval is not spent.
- Every step is on the signed audit chain: the 202, the operator decision
  (`APPROVAL_GRANTED` / `APPROVAL_REJECTED` with `decided_by` and note), and
  the resumed decision.

## Executor side

```python
d = plane.authorize(task=task, action="deployment.restart", resource="staging/checkout")
if d.needs_approval:
    d = plane.wait_for_approval(d, timeout=900)     # polls GET /v1/approvals/{id}, then resumes
if d.allowed:
    restart()
```

`wait_for_approval` raises `ApprovalTimeout` if nothing happens; the request
stays open, so a later worker can resume it with the stored `approval_id`.
The [adapters](frameworks.md) raise `ApprovalRequired` (carrying the id) or
block with `wait_for_approval=<seconds>`.

Direct HTTP: repeat the same `POST /v1/authorize` body with
`"approval": "<id>"`.

## Operator side

- Console: approve or reject with an optional note, authenticated by your
  session cookie. No token is pasted into the UI.
- API: `GET /v1/approvals?status=pending&tenant=<project>`,
  `POST /v1/approvals/{id}/approve`, `POST /v1/approvals/{id}/reject` with
  `{"note": "...", "decided_by": "..."}`. These accept a console session, a
  management key (`ap_mgmt_…`), or `ADMIN_TOKEN`, all in `X-Admin-Token`.
- SDK: `AgentPlaneAdmin.list_approvals()`, `.approve()`, `.reject()`.

## Webhook

Set `APPROVAL_WEBHOOK_URL` to be notified instead of polling. Each event is a
JSON POST:

```json
{"schema": "agent-plane.approval.v1", "event": "approval.requested",
 "emitted_at": "…", "approval": { "id": "apr_…", "status": "pending", "task": "…", "action": "…", "resource": "…", "subject": "…", "lease_id": "…", "evidence_id": "az_…", "expires_at": "…", "context": {} }}
```

Headers: `X-AgentPlane-Event` and `X-AgentPlane-Signature: sha256=<hex>`,
an HMAC-SHA256 of the raw body with `AUDIT_SIGNING_KEY`. Verify before acting
(`agent_plane.approvals.notify.verify_signature` shows how). Delivery is
best-effort from a background thread and never affects the decision; the
queue endpoint remains the source of truth. Typical receivers post to Slack
or open a ticket, and call `/approve` from a button handler.

## MCP gateway

The gateway path raises the same requests. A `tools/call` that is admitted as
APPROVAL REQUIRED returns the `approval_id` in its `_meta["agent-plane"]`
evidence; the client resumes by calling the same tool with the same arguments
and `_meta["agent-plane/approval-id"]`. The approval is bound to the exact
argument digest.
