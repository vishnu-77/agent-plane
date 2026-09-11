# Quickstart

Five minutes from a clean checkout to an approved, audited action. No model
provider keys are needed.

## 1. Run the service

```bash
python -m venv .venv && source .venv/bin/activate     # PowerShell: .venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]" -e ./sdk/python
python -c "import secrets; print('JWT_SECRET=' + secrets.token_urlsafe(32)); print('ADMIN_TOKEN=' + secrets.token_urlsafe(32)); print('AUDIT_SIGNING_KEY=' + secrets.token_urlsafe(32))" >> .env
agentplane serve --host 127.0.0.1 --port 8000
```

`http://127.0.0.1:8000/console` shows the console, `/docs` the OpenAPI UI.
Leases and audit evidence persist in `audit.db` (SQLite) between restarts.

## 2. Issue a lease from a template

The trusted backend does this with the admin token. `repair-service` is one of
the shipped templates in `config/lease-templates.yaml`.

```python
import os
from agentplane import AgentPlaneAdmin

admin = AgentPlaneAdmin("http://127.0.0.1:8000", os.environ["ADMIN_TOKEN"])
lease = admin.issue_from_template(
    "repair-service", subject="devops-agent", task="fix-checkout",
    variables={"env": "staging", "service": "checkout"})
print(lease.id, lease.resources, lease.require_approval)
# lease-repair-service-… ['staging/checkout'] ['deployment.restart']
```

## 3. Authorize an action

The executor holds the agent's bearer token (an HS256 JWT signed with
`JWT_SECRET` in `jwt_claims` mode).

```python
import jwt
from agentplane import AgentPlane

token = jwt.encode({"sub": "operator-42", "tenant": "acme", "agent_id": "devops-agent",
                    "allowed_tools": ["deployment"]}, os.environ["JWT_SECRET"], algorithm="HS256")
plane = AgentPlane("http://127.0.0.1:8000", token)

print(plane.authorize(task="fix-checkout", action="deployment.read", resource="staging/checkout"))
# allow / ACTION_WITHIN_TASK_AUTHORITY
print(plane.authorize(task="fix-checkout", action="deployment.read", resource="production/checkout"))
# deny / RESOURCE_OUTSIDE_DELEGATED_SCOPE
pending = plane.authorize(task="fix-checkout", action="deployment.restart", resource="staging/checkout")
print(pending.decision, pending.approval_id)
# approval_required apr_…
```

## 4. Approve and resume

Approve from the console (Live mode, **Pending Approvals** panel) or with
the admin client, then resume from the executor:

```python
admin.approve(pending.approval_id, note="verified with on-call", decided_by="alice")
resumed = plane.wait_for_approval(pending, timeout=60)
print(resumed.decision, resumed.reason)     # allow ACTION_APPROVED
print(plane.authorize(task="fix-checkout", action="deployment.restart",
                      resource="staging/checkout", approval=pending.approval_id).reason)
# APPROVAL_ALREADY_USED  - an approval authorises exactly one execution
```

## 5. Narrow or revoke while the agent runs

```python
admin.shrink_lease(lease.id, actions=["deployment.read"])   # restart is gone immediately
admin.revoke_lease(lease.id)                                 # everything is gone
```

## 6. Inspect the evidence

```python
for event in admin.audit(limit=10):
    print(event["decision_id"], event["decision"], event["reason"])
```

Or open the console, choose **Live**, paste the admin token, and select any
event: the execution chain, lease snapshot, approval id, and provenance
context are shown per decision.

## Next

- Wrap your real tools once with the [framework adapters](integration/frameworks.md).
- Prove the executor never runs without ALLOW with the [conformance kit](integration/conformance.md).
- Move to Postgres and more than one replica with the [deployment guide](deployment.md).
- Switch identity to signed delegation before production: [SECURITY.md](../SECURITY.md).
