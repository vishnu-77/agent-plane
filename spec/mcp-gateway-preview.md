# MCP enforcement preview: implementation and user flow

An opt-in gateway that combines task-authority admission and dispatch for
explicitly configured MCP tools. Since 0.4 its authority state (leases, use
counters, the request-key ledger, approvals) lives in the shared SQL store, so
it runs in `ENVIRONMENT=production` and across replicas. Operational limits are
described below; the operator's path is [docs/integration/mcp-gateway.md](../docs/integration/mcp-gateway.md).

## Run the complete user flow

From the repository root, with the virtual environment activated:

```bash
python -m pip install -e ".[dev,mcp]" -e ./sdk/python
python examples/mcp_gateway_demo.py --serve
```

The demo starts two local processes, creates a task lease and trusted mapping,
mints separate agent and upstream credentials, and makes three requests using the
official MCP client. It does not contact GitHub or change a real repository.

1. Open **http://127.0.0.1:8780/flow**.
2. Enter the **Local console ADMIN_TOKEN** printed by the command.
3. Select each of the five steps: Connect, Bind, Request, Decide, Inspect.
4. Switch between the recorded ALLOW, APPROVAL REQUIRED, and DENY outcomes.
5. Open **http://127.0.0.1:8780/console**, choose Live, and enter the same token.
   Select the ALLOW event and inspect its correlated execution receipts.

The browser makes read-only requests. Neither page issues authority or executes
tools. The command-line MCP client generated the evidence before the pages read it.

| Client request | Authority result | Actual mock upstream calls |
| --- | --- | --- |
| `repo.branches` | ALLOW | `list_branches` called once; completion recorded. |
| `repo.delete_branch`, branch `stale-fix` | APPROVAL REQUIRED | Zero calls. No approval queue is implied. |
| `repo.delete_branch`, branch `main` | DENY / `RESOURCE_PROTECTED` | Zero calls. |

The command prints the temporary directory containing the YAML configuration,
SQLite evidence, process logs, and credential-free `results.json`. Secrets are
generated per run and passed to child processes through their environments.
The local admin token is printed for the operator only when `--serve` is used.
Ctrl+C stops both processes. Omit `--serve` for a self-contained smoke test that
stops after verification. Use `--port` and `--upstream-port` if the defaults are busy.

## What is implemented

- `/mcp` uses official Python MCP SDK **2.2.0**, pinned in the `[mcp]` extra.
  This preview accepts protocol **2026-07-28** only. Unsupported versions are
  rejected explicitly; older client compatibility is not advertised.
- The gateway acts as an MCP server to a client and an MCP client to one configured
  remote upstream. Discovery follows up to ten pages with at most 500 tools.
- Operator mappings fix the upstream name, public tool name, action, input schema,
  and resource template. The server validates arguments before policy and again
  after redaction, then derives the canonical resource that is actually admitted.
- A configured `(tenant, agent)` binding fixes one task and lease. Each binding
  must have its own lease. Explicit bearer identity and nonempty capabilities are
  required. Incoming admin credentials are never forwarded upstream.
- `tools/list` is a non-consuming candidate filter. Calling any tool still checks
  capability, policy, binding, current lease, protected resources and use limits.
- Only ALLOW reserves a use and proceeds toward dispatch on this gateway path.
  Existing `/v1/authorize` consumption behavior remains compatible, including
  its legacy approval-attempt consumption.
- Required decision evidence is persisted before use reservation and dispatch.
  Separate signed audit events record dispatch start, upstream completion/error,
  or uncertain outcome. A generic timeout or cancellation after dispatch is
  **outcome unknown**, never proof that the external action did not happen.
- Local admissions and lease mutations serialize through the lease store lock.
  Protected-resource override is independent of lease insertion order. The
  narrowing endpoint holds the same lock across its read/check/update operation.
- Request sizes, upstream response bytes, deadlines, active HTTP requests, and
  execution concurrency are bounded. Compressed upstream responses are rejected
  so the response-byte bound cannot be bypassed by decompression.
- `/flow` presents real evidence through five steps. The existing console groups
  execution receipts under the selected decision, preserves the recorded task,
  shows admission-time lease scope separately from fetched current context, and
  includes original receipts in JSON exports.

Gateway records use a versioned `agent-plane.gateway.v1` envelope inside the
existing audit JSON field. This adds no SQL columns and preserves old audit rows.
Prompt origins and complete delegation ancestry remain unrecorded; the new
gateway does not infer missing provenance.

## Configure a different upstream

Use the generated `gateway.yaml` as a starting point, or inspect
[`configuration()` in the demo](../examples/mcp_gateway_demo.py). Set
`MCP_GATEWAY_FILE` to its absolute path before starting `agentplane serve`.
The gateway is disabled when this setting is empty.

The configuration contains:

| Field | Meaning |
| --- | --- |
| `upstream_url` | Fixed HTTPS MCP endpoint; HTTP allowed only for explicit localhost upstreams. No credentials, query, or fragment in the URL. |
| `upstream_secret_env` | Optional environment variable holding the upstream bearer secret. Startup fails if specified but missing. |
| `bindings` | Explicit tenant, agent, task, and lease ID. The matching lease is issued through the existing admin API or seeded YAML. |
| `tools` | Public name, upstream name, action, resource template, description, and local JSON Schema. Unknown tool names never dispatch. |
| `timeout_seconds` | Overall discovery/execution deadline; default 10 seconds. |
| `max_concurrency` | Active MCP request and execution bound; default 8. |
| `max_response_bytes` | Per-upstream HTTP response and final result bound; default 1 MB. |

Resource placeholders accept canonical single-segment strings, not arbitrary
paths, traversal, percent-encoded separators, or URLs. Schemas must reject extra
properties; remote schema references are unsupported. Published descriptions and
schemas come from operator configuration, not upstream prompt-like instructions.

The agent credential needs the public tool names for the existing policy engine
and the mapped action/namespace for authority evaluation. The demo uses
`branch`, `repo.branches`, and `repo.delete_branch`. Those capability claims do not
replace the task lease.

## Failure and retry semantics

The gateway does not automatically retry tool execution. A client may supply a
stable `_meta["agent-plane/request-id"]` for deduplication across every replica
sharing the authority store. Reusing
the same key for a completed request returns its previous result; changed
arguments or an in-progress/uncertain unresolved attempt do not execute again.
At most `max_request_keys` (default 10,000) keys are retained; capacity
exhaustion fails closed instead of evicting replay protection, and
`SqlLeaseStore.purge_requests()` reclaims reconciled keys. Without a key, each
call is a distinct attempt.

Deduplication, leases, counters, approvals, and revocation are durable in the SQL
authority store and shared by every replica. There is still no generic
exactly-once execution guarantee.
An audit failure after reservation may leave a spent use without execution;
reservations are not automatically refunded. Revocation prevents admissions after
its commit, not actions already admitted. Admissions are not durable, parent
budgets are not coordinated, and automatic outcome reconciliation is unavailable.

An APPROVAL REQUIRED admission opens an approval request and returns its id in
the evidence; the client resumes with `_meta["agent-plane/approval-id"]` and the
same arguments. `agentplane mcp discover` generates a reviewable mapping file
from an upstream's tool list. The gateway has no OAuth onboarding, Agent Key
registry, automatic client rewrite, or observe mode. Existing REST tool broker behavior remains separate; installing
this feature does not intercept arbitrary broker, shell, or model-tool traffic.

## Verification

```bash
python -m pytest -q
python examples/mcp_gateway_demo.py
```

The CI test matrix installs the MCP extra and runs both commands. Tests cover
authority ordering, non-consuming preview, concurrent caps, protected/expired/
revoked/out-of-scope authority, binding and argument checks, policy denial, audit
failure, deduplication, cancellation, protocol metadata, origin and body limits.

With Playwright installed as external tooling, run the existing console browser
suite. For the live demo pages, set `GATEWAY_ADMIN_TOKEN` to the token printed by
`--serve`, then run `node tests/gateway.browser.cjs`. It checks five steps, three
real outcomes, receipt grouping, mobile overflow, read-only requests, and
credential-free exports. Screenshots are written to the OS temporary directory.
