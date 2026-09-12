# agent-plane Python SDK

HTTP client for the agent-plane authorization service. The install name is
`agent-plane-sdk`; the Python import is `agentplane`. It does not install or
start the server. Its only runtime dependency is HTTPX.

```bash
python -m pip install agent-plane-sdk          # from your package index
python -m pip install ./sdk/python             # from this checkout
```

## Connect with a project API key

Create a project in the console and generate a key. That key is the whole
credential: there is no JWT to mint and no lease to provision first.

```bash
export AGENTPLANE_API_KEY=ap_live_...
export AGENTPLANE_URL=http://127.0.0.1:8000
```

## Report what your agent does

```python
from agentplane import AgentPlane

with AgentPlane() as ap:                        # reads the two variables above
    task = ap.task("fix-authentication-tests", origin={"kind": "prompt", "text": prompt})
    task.report(tool="Read", arguments={"file_path": "src/auth.ts"})
    task.report(tool="Edit", arguments={"file_path": "src/auth.ts"})
```

Reporting is enough to see activity, the authority behind it, and what each
action would cause. In Observe nothing is blocked.

## Authorize before execution

Ask before a side effect, and execute only when `decision.allowed` is true.

```python
import os
from agentplane import AgentPlane

with AgentPlane(api_key=os.environ["AGENTPLANE_API_KEY"]) as ap:
    task = ap.task("fix-staging-checkout")
    decision = task.authorize(
        "deployment.restart", "staging/checkout",
        context={"conversation_id": thread_id, "origin": "human"},  # optional provenance
    )
    if decision.allowed:
        restart()                                   # ALLOW
    elif decision.needs_approval:
        resumed = ap.wait_for_approval(decision, timeout=600)
        if resumed.allowed:
            restart()                               # ACTION_APPROVED, exactly once
    else:
        log.warning("%s %s", decision.reason, decision.evidence_id)   # DENY
```

`decision.proceed` is true for an explicit ALLOW and for Observe mode's
SIMULATE, where nothing is enforced. In Govern the decision is real, so
`proceed` is false even though agent-plane does not block; your code decides
what to do with it. `decision.allowed` stays strict: ALLOW only.

Deployments that mint their own identity tokens can still pass one:
`AgentPlane(url, agent_jwt)`.

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

Advanced, and not part of getting started: rules in the console cover the
normal case. This is for issuing authority for a single task from your own
backend.

```python
from agentplane import AgentPlaneAdmin

# A management key (ap_mgmt_..., scoped to one project) or the deployment's
# ADMIN_TOKEN. Either way this is not an agent credential.
admin = AgentPlaneAdmin(os.environ["AGENTPLANE_URL"], os.environ["AGENTPLANE_MGMT_KEY"])
lease = admin.issue_from_template("repair-service", subject="devops-agent",
                                  task="fix-staging-checkout", tenant="acme",
                                  variables={"env": "staging", "service": "checkout"})
for req in admin.list_approvals():
    admin.approve(req.id, note="verified with on-call", decided_by="alice")
admin.shrink_lease(lease.id, actions=["deployment.read"])
admin.revoke_lease(lease.id)
events = admin.audit(limit=20)
```

Keep this credential in your trusted backend. Never ship it to an agent.

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
