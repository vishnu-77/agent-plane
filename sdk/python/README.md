# agent-plane Python SDK

Lightweight HTTP client for the agent-plane authorization service. The install
name is `agent-plane-sdk`; the Python import is `agentplane`. This package does
not install or start the server. Its only runtime dependency is HTTPX.

## Install

From the repository root:

```bash
python -m pip install ./sdk/python
```

Or install the versioned `agent_plane_sdk-*.whl` supplied with a release.
Once a release is published to your package index, install `agent-plane-sdk`
from that index.

## Authorize before execution

Your backend supplies a trusted agent bearer token and provisions the matching
task AuthorityLease on the server. The admin token is not an agent credential.

```python
import os
from agentplane import AgentPlane

with AgentPlane(os.environ["AGENT_PLANE_URL"], os.environ["AGENT_TOKEN"]) as plane:
    decision = plane.authorize(
        task="fix-staging-checkout",
        action="deployment.restart",
        resource="staging/checkout",
    )
    print(decision.decision, decision.reason, decision.evidence_id)
    # Call your real tool only when decision.allowed is True.
```

`AuthorityDecision` exposes `decision`, `reason`, `lease`, `evidence_id`, and
`allowed`. `allowed` is true only for ALLOW. An approval-required response must
pause your workflow; it does not execute or queue approval automatically.

HTTP 200/202/403 decisions are parsed from top-level or `detail` response
objects. Inconsistent statuses, missing evidence, and malformed payloads raise
`AuthorizationProtocolError`. Authentication and server errors raise HTTPX
errors. Transport failures propagate. All of these must stop execution.

The client does not retry authorization automatically: checks may consume a
lease use count. Keep your target credentials inside a trusted executor that
the agent cannot bypass. Authorization and actual tool execution are separate.

Build this client independently with `python -m build sdk/python` from the
repository root. The server distribution is named `agent-plane` and imports as
`agent_plane`; it remains a separate installation.
