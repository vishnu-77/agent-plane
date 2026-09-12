# Executor conformance kit

The only property that makes agent-plane a control rather than a suggestion:
**the real action runs after an explicit ALLOW and never otherwise.** The
classic integration bug is trusting an HTTP status, a truthy body, or a
caught exception. `agentplane.testing` catches all of those before you
connect real side effects.

## Use it

```python
from agentplane import AgentPlane
from agentplane.testing import check_executor

def build(plane: AgentPlane, execute):
    """Return a zero-arg callable that performs ONE governed action through
    `plane` and calls `execute()` only when allowed."""
    def run():
        d = plane.authorize(task="t", action="deployment.restart", resource="staging/x")
        if d.allowed:
            execute()
    return run

def test_executor_fails_closed():
    check_executor(build)
```

`check_executor` constructs a mock-backed client per case, calls your builder,
runs the executor, and records whether `execute` was called. It raises
`ConformanceFailure` with a per-case report; pass `raise_on_failure=False` to
get the `ConformanceReport` instead.

The builder should exercise your real wrapper. With the adapters:

```python
from agentplane.adapters import govern

def build(plane, execute):
    @govern(plane, task="t", action="deployment.restart", resource="staging/x")
    def restart():
        execute()
    return restart
```

## Cases

| Case | Server behaviour | Must execute |
| --- | --- | --- |
| `allow` | 200 with a consistent ALLOW | yes, exactly once |
| `deny` | 403, decision wrapped in `detail` | no |
| `approval_required` | 202 with `approval_id` | no |
| `http_500`, `http_401`, `http_404`, `http_429` | error statuses | no |
| `http_200_wrong_decision` | 200 whose body says `deny` | no |
| `http_200_no_evidence` | 200 without `evidence_id` | no |
| `http_200_empty_object`, `http_200_list`, `http_200_not_json`, `http_204_no_content` | malformed bodies | no |
| `transport_failure` | connection dropped | no |

Pass `cases=` to run a subset, or build your own `Case` objects.

## Mock client

`agentplane.testing.mock_plane(handler)` returns an `AgentPlane` whose HTTP
calls are answered by your `httpx.Request -> httpx.Response` function, for
unit tests that need canned decisions without a server.

## TypeScript

The TypeScript SDK ships the same decision parser; drive it with a `fetch`
stub (see `sdk/typescript/test/client.test.mjs`) and assert your executor
under the same cases.
