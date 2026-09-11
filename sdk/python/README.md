# agent-plane Python SDK

HTTP client for the agent-plane authorization service. The install name is
`agent-plane-sdk`; the Python import is `agentplane`. It does not install or
start the server. Its only runtime dependency is HTTPX.

```bash
python -m pip install agent-plane-sdk          # from your package index
python -m pip install ./sdk/python             # from this checkout
```

## Authorize before execution

Your backend supplies a trusted agent bearer token and provisions the matching
task AuthorityLease on the server. Execute the real action only when
`decision.allowed` is true.

```python
import os
from agentplane import AgentPlane

with AgentPlane(os.environ["AGENT_PLANE_URL"], os.environ["AGENT_TOKEN"]) as plane:
    decision = plane.authorize(
        task="fix-staging-checkout",
        action="deployment.restart",
        resource="staging/checkout",
        context={"conversation_id": thread_id, "origin": "human"},  # optional provenance
    )
    if decision.allowed:
        restart()                                   # ALLOW
    elif decision.needs_approval:
        resumed = plane.wait_for_approval(decision, timeout=600)
        if resumed.allowed:
            restart()                               # ACTION_APPROVED, exactly once
    else:
        log.warning("%s %s", decision.reason, decision.evidence_id)   # DENY
```

`AuthorityDecision` exposes `decision`, `reason`, `lease`, `evidence_id`,
`approval_id`, `context`, and the properties `allowed` (true only for ALLOW)
and `needs_approval`.

Inconsistent statuses, missing evidence, and malformed payloads raise
`AuthorizationProtocolError`. Authentication and server errors raise HTTPX
errors. Transport failures propagate. All of these must stop execution. The
client never retries authorization automatically because a check may consume
a lease use count.

## Wrap tools once with adapters

```python
from agentplane.adapters import govern, governed_dispatch, langchain_tool

@govern(plane, task=lambda: current_task(), action="branch.delete",
        resource="github://acme/repo/branches/{branch}")
def delete_branch(branch: str) -> str:
    return github.delete_branch(branch)      # runs only after ALLOW

dispatch = governed_dispatch(plane, task="cleanup",
    actions={"delete_branch": "branch.delete"},
    resources={"delete_branch": "github://acme/repo/branches/{branch}"},
    handlers={"delete_branch": github.delete_branch})

tool = langchain_tool(my_langchain_tool, plane, task="cleanup",
                      action="branch.delete", resource="github://acme/repo/branches/{branch}")
```

Anything but ALLOW raises `NotAuthorized` (or its subclass `ApprovalRequired`,
which carries the approval id). Pass `wait_for_approval=<seconds>` to block
on a human decision instead. `crewai_tool` and `openai_agents_guard` follow
the same shape. See `docs/integration/frameworks.md`.

## Backend / operator client

```python
from agentplane import AgentPlaneAdmin

admin = AgentPlaneAdmin(os.environ["AGENT_PLANE_URL"], os.environ["ADMIN_TOKEN"])
lease = admin.issue_from_template("repair-service", subject="devops-agent",
                                  task="fix-staging-checkout",
                                  variables={"env": "staging", "service": "checkout"})
for req in admin.list_approvals():
    admin.approve(req.id, note="verified with on-call", decided_by="alice")
admin.shrink_lease(lease.id, actions=["deployment.read"])
admin.revoke_lease(lease.id)
events = admin.audit(limit=20)
```

The admin token is not an agent credential. Keep it in your trusted backend.

## Prove your executor fails closed

```python
from agentplane.testing import check_executor

def build(plane, execute):
    def run():
        d = plane.authorize(task="t", action="deployment.restart", resource="staging/x")
        if d.allowed:
            execute()
    return run

def test_executor_conformance():
    check_executor(build)      # raises ConformanceFailure listing any case that executed
```

The kit replays ALLOW, DENY, APPROVAL_REQUIRED, 4xx/5xx, malformed JSON,
status/decision mismatches, and a dropped connection through an in-process
mock, and fails if the action ran in any case but a clean ALLOW.

## Build

`python -m build sdk/python` from the repository root. The server
distribution is `agent-plane` (imports as `agent_plane`) and is installed
separately.
