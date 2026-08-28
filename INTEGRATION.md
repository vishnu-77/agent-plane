# Integrating agent-plane into an existing product

**Short answer to "is it plug-and-play": for model calls, yes - genuinely
zero code change (it's an OpenAI-compatible `base_url` swap). For tool calls,
RAG, and task-scoped authorization, it's "one wrapper function per call site,"
not zero-code - there's no universal interception point across arbitrary
agent frameworks yet (that's what the MCP adapter in
[`ROADMAP.md`](ROADMAP.md) v0.2 closes). This document is the real, honest
path for both.**

Everything below was run against a live `agentplane serve` process while
writing this doc (`examples/verify_deployment.py` is the script that did it -
run it against your own deployment to confirm the wiring).

---

## 0. Deploy it

Pick one - all three run the identical code:

```bash
# A. From source / pip install (zero external deps: SQLite + in-memory)
pip install agent-plane
agentplane init                 # scaffolds policies/ + config/ into the cwd
cp .env.example .env            # set JWT_SECRET, ADMIN_TOKEN at minimum
agentplane serve --port 8000

# B. Docker
docker build -t agent-plane .
docker run -p 8000:8000 --env-file .env agent-plane

# C. Docker Compose with Postgres + Redis (for real concurrency/scale)
docker compose up --build
```

Confirm it's live: `curl localhost:8000/healthz` → `{"status":"ok", ...}`.

---

## 1. Identity: how your product tells agent-plane who's calling

Every request carries `Authorization: Bearer <token>`. Two modes
(`IDENTITY_MODE` env var, no code change to switch):

- **`jwt_claims`** (default) - your backend mints an HS256 JWT with your own
  `JWT_SECRET` and the caller's claims are trusted as-is. Fastest path to
  integrate; the agent (or your backend, on its behalf) asserts its own scope.
- **`delegation`** - production mode. agent-plane verifies an Ed25519-signed
  delegation from a trusted issuer; the token can't assert its own tools/scope.

Start with `jwt_claims` while integrating, move to `delegation` before
production (checklist in [`SECURITY.md`](SECURITY.md)).

```python
# Your backend, minting a token for one agent invocation (jwt_claims mode)
import jwt, time

token = jwt.encode({
    "sub": "user-42",                 # the human/account this agent acts for
    "tenant": "acme-corp",
    "department": "support",           # policies can match on this
    "agent_id": "support-bot",
    "allowed_tools": ["search_kb", "draft_email"],  # capability manifest
    "clearance": "internal",
    "iat": int(time.time()),
}, JWT_SECRET, algorithm="HS256")
```

```ts
// TypeScript equivalent (jsonwebtoken)
import jwt from "jsonwebtoken";
const token = jwt.sign(
  { sub: "user-42", tenant: "acme-corp", department: "support",
    agent_id: "support-bot", allowed_tools: ["search_kb", "draft_email"] },
  process.env.JWT_SECRET!, { algorithm: "HS256" }
);
```

---

## 2. Model calls - the actually-zero-code integration

Any code that talks to an OpenAI-compatible endpoint works unchanged by
pointing `base_url` at agent-plane instead of the provider directly. Nothing
else in your app needs to know agent-plane exists.

```python
# Before
client = OpenAI(api_key=OPENAI_KEY)

# After - same SDK, same call sites, everything downstream is now governed
client = OpenAI(base_url="http://localhost:8000/v1", api_key=your_minted_jwt)
resp = client.chat.completions.create(model="gpt-4.1", messages=[...])
```

```ts
// Vercel AI SDK / OpenAI SDK (TypeScript) - identical swap
import OpenAI from "openai";
const client = new OpenAI({ baseURL: "http://localhost:8000/v1", apiKey: jwt });
```

```python
# LangChain - same swap via openai_api_base
from langchain_openai import ChatOpenAI
llm = ChatOpenAI(model="gpt-4.1", openai_api_base="http://localhost:8000/v1",
                  openai_api_key=jwt)
```

What you get for free at every call site: policy-based allow/deny/redact,
per-user token quota, provider routing + fallback, and a signed audit record -
inspect it via the `x_control_plane` block in the response, or `GET /v1/audit`.

---

## 3. Tool calls - one wrapper function

There's no framework-agnostic interception point for arbitrary tool-calling
code (yet - MCP adapter is the closest thing, see §7). The integration is one
function that every tool-call site routes through instead of calling the tool
directly.

```python
import httpx

def call_tool(token: str, tool: str, arguments: dict) -> dict:
    r = httpx.post("http://localhost:8000/v1/tools/invoke",
                    headers={"Authorization": f"Bearer {token}"},
                    json={"tool": tool, "arguments": arguments})
    if r.status_code == 202:
        raise NeedsHumanApproval(r.json()["detail"])
    r.raise_for_status()          # 403 -> denied_by_policy, 404 -> unknown_tool
    return r.json()["result"]
```

Framework wiring - swap the framework's tool-execution step for this call:

```python
# LangChain: wrap each @tool's body
from langchain_core.tools import tool

@tool
def search_kb(q: str) -> str:
    """Search the internal knowledge base."""
    return call_tool(current_token(), "search_kb", {"q": q})
```

```python
# CrewAI / a custom agent loop: same idea at the dispatch point
def dispatch(action, token):
    return call_tool(token, action.tool_name, action.arguments)
```

The broker executes with its **own** credential (`config/tools.yaml`,
`secret_env`) - your agent's token never needs to hold the real API key.

---

## 4. RAG retrieval - one wrapper function

```python
def retrieve(token: str, source: str, query: str, top_k: int = 5) -> list[dict]:
    r = httpx.post("http://localhost:8000/v1/retrieve",
                    headers={"Authorization": f"Bearer {token}"},
                    json={"source": source, "query": query, "top_k": top_k})
    r.raise_for_status()
    return r.json()["results"]   # already filtered - relevance is not permission
```

Swap this in wherever your RAG pipeline currently calls the vector store
directly, with sources/documents declared in `config/knowledge.yaml`
(access metadata: tenant, department, classification, ACL).

---

## 5. Task-scoped authorization - the new primitive (`/v1/authorize`)

Use this where an agent is about to take a **real-world, side-effecting
action** - infra changes, financial transactions, external API writes,
deleting anything - and "the agent generically has this credential" isn't
authorization enough. See [`spec/authority-lease.md`](spec/authority-lease.md)
for the full object and reason-code reference.

**Integration shape, three steps:**

```python
from agentplane import AgentPlane   # sdk/python - pip install -e sdk/python for now

plane = AgentPlane("http://localhost:8000", token)

# Step 1 - at task/session start, issue (or your orchestrator issues) a lease
# scoping what THIS task may do. Admin-token gated - your backend does this,
# not the agent.
httpx.post("http://localhost:8000/v1/leases",
    headers={"X-Admin-Token": ADMIN_TOKEN},
    json={
        "id": f"lease-{session_id}", "task": task_id, "agent": "devops-agent",
        "resources": ["staging/*"], "actions": ["deployment.read", "deployment.restart"],
        "protected_resources": ["production/*"],
        "expires_at": (now + timedelta(hours=1)).isoformat(),
    })

# Step 2 - before EVERY proposed action, ask (this is the actual enforcement point)
decision = plane.authorize(task=task_id, action="deployment.restart", resource="staging/checkout")

# Step 3 - only execute if allowed; the reason code tells you (and the agent) why not
if decision.allowed:
    do_the_real_thing()
elif decision.decision == "approval_required":
    queue_for_human(decision)
else:
    tell_the_agent_why(decision.reason)   # e.g. RESOURCE_OUTSIDE_DELEGATED_SCOPE
```

Model the `action`/`resource` naming after your own domain (`branch.delete` +
`github://org/repo`, `payment.refund` + `stripe://acct_x/charge_y`, whatever
your agent's actions actually are) - agent-plane doesn't need to know your
schema, only that leases and requests agree on the strings.

```ts
// No TS SDK yet (see ROADMAP.md v0.2) - it's four lines of fetch either way.
async function authorize(task: string, action: string, resource: string) {
  const r = await fetch("http://localhost:8000/v1/authorize", {
    method: "POST",
    headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
    body: JSON.stringify({ task, action, resource }),
  });
  return r.json();   // { decision, reason, lease, evidence_id } on 200/202/403 alike
}
```

Full runnable example: [`examples/devops-agent/demo.py`](examples/devops-agent/demo.py).

---

## 6. Agent-to-agent handoff

If your product lets one agent spin up or delegate to another, don't just
copy the parent's token - mint an attenuated child so the sub-agent can never
end up with more authority than its parent (requires `IDENTITY_MODE=delegation`):

```python
r = httpx.post("http://localhost:8000/v1/agents/delegate",
    headers={"Authorization": f"Bearer {parent_token}"},
    json={"agent": "child-agent", "scope": {"tools": ["search"], "clearance": "internal"}, "ttl": 600})
child_token = r.json()["token"]   # scope is a verified subset of the parent's, or 403
```

---

## 7. Deployment topology

Three ways to place it, same code either way - pick per environment:

```
SDK / direct HTTP call         Gateway / reverse proxy         Sidecar (k8s)
  Your app ──► agent-plane       Your app ──► agent-plane ──►     ┌── Pod ──┐
        (calls it directly,          (sits in front of an          │ App ──►│──► agent-plane ──► tools
         same as any HTTP API)        internal service mesh)       └────────┘
```

Start with direct SDK/HTTP calls (§2-§6 above) - it's the fastest to wire up
and what every example in this doc uses. Gateway/sidecar are the same
container, just placed differently in your network; nothing in `agent_plane/`
changes.

---

## 8. Checklist: adding this to an existing product

1. Deploy agent-plane (§0), confirm `curl /healthz`.
2. Decide identity mode; wire your backend to mint a token per agent
   invocation (§1).
3. Swap your model client's `base_url` (§2) - ship this alone first, it's
   zero-risk and immediately gives you policy + audit on every model call.
4. Wrap tool-execution call sites through `/v1/tools/invoke` (§3), one at a
   time, starting with your highest-risk tools.
5. Wrap RAG retrieval through `/v1/retrieve` (§4) if you have a knowledge base.
6. For any agent that takes real-world side-effecting actions, add
   `/v1/authorize` at the decision point (§5) - this is the one that actually
   stops "capability != authority" incidents.
7. Run `python examples/verify_deployment.py <your-url>` against your
   deployment to confirm every edge is live.
8. Before production: `IDENTITY_MODE=delegation`, rotate `JWT_SECRET` /
   `AUDIT_SIGNING_KEY` off their defaults, set a strong `ADMIN_TOKEN` (or
   leave the admin API disabled) - full checklist in
   [`SECURITY.md`](SECURITY.md). The server **refuses to start** in
   `ENVIRONMENT=production` with default secrets, so you can't skip this by
   accident.

---

## 9. Troubleshooting

| Response | Meaning | Fix |
|---|---|---|
| `401 Missing or malformed Authorization header` | No/bad bearer token | Mint and send a JWT (§1) |
| `403 denied_by_policy` | A `policies/*.yaml` rule matched and denied | Check `reason` + `rules_matched` in the response; edit the policy |
| `403 { "denied_tools": [...] }` | Actor's `allowed_tools` doesn't cover this tool | Widen the token's capability manifest, or it's working as intended |
| `403 { "reason": "RESOURCE_OUTSIDE_DELEGATED_SCOPE" \| "RESOURCE_PROTECTED" \| ... }` | `/v1/authorize` denial - see [`spec/authority-lease.md`](spec/authority-lease.md) reason-code table | Issue/widen the lease, or it's working as intended |
| `202 approval_required` | Policy or lease requires a human | Not an error - surface it as a pending approval |
| `404 unknown_tool` / `lease not found` | Not in `config/tools.yaml` / no such lease id | Add it, or check the id |
| `404` on `/v1/agents/delegate` | A2A is disabled | Set `IDENTITY_MODE=delegation` + `DELEGATION_SIGNING_KEY` |
| `502 upstream_error: OPENAI_API_KEY not configured` | Request passed governance, failed at the real provider call | Set the provider key - this confirms the pipeline is wired correctly |

---

## What's genuinely NOT plug-and-play yet

Being direct about the limits, per [`ROADMAP.md`](ROADMAP.md):

- **No framework middleware/plugin** for LangChain/LangGraph/CrewAI - you
  write the one-line wrapper per call site (§3-§5). An MCP adapter (v0.2)
  would let MCP-based agents get tool/task governance without touching their
  code, since MCP already centralizes tool dispatch - that's not built yet.
- **No TypeScript SDK** - raw `fetch` (four lines, shown in §5) covers it,
  just without the typed wrapper the Python SDK gives you.
- **Lease delegation isn't enforced** - `child_authority` is parsed but a
  sub-agent can't yet be issued an attenuated *lease* the way it can an
  attenuated *identity* (§6).

None of these block integration today; they're where the wrapper-per-call-site
approach in §3-§5 eventually gets replaced with zero-code interception.
