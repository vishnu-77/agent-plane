# Framework adapters

An advanced surface, for your own code. If you run a coding agent or an MCP
client, use a [connector](../connectors.md) instead - nothing is wrapped.

`agentplane.adapters` puts the authorize-before-execute check in front of a
tool once. Every adapter is the same rule: derive the canonical resource from
the call's arguments in **your** code, call `plane.authorize`, run the tool
only on ALLOW. No framework is imported by the SDK; nothing is installed for
you.

Outcomes are uniform:

| Decision | Adapter behaviour |
| --- | --- |
| ALLOW (or observe-mode SIMULATE) | tool runs; decision available as `last_decision` |
| APPROVAL_REQUIRED | `ApprovalRequired` raised with `decision.approval_id`; or, with `wait_for_approval=<seconds>`, the adapter polls and resumes |
| DENY / protocol error / HTTP or transport error | `NotAuthorized` (or the underlying exception) raised; tool never runs |

`task` may be a string or a zero-argument callable returning the current task
id, so one wrapped tool serves many tasks.

## Plain functions

```python
from agentplane import AgentPlane
from agentplane.adapters import govern

plane = AgentPlane(api_key="ap_live_...", url="http://127.0.0.1:8000")

@govern(plane, task=lambda: current_task_id(), action="branch.delete",
        resource="github://acme/repo/branches/{branch}")
def delete_branch(branch: str) -> str:
    return github.delete_branch(branch)
```

`resource` is a format string over the function's bound arguments, or a
callable receiving them. `context=` accepts a mapping or a callable for
provenance.

## Custom loops

```python
from agentplane.adapters import governed_dispatch

dispatch = governed_dispatch(
    plane, task="cleanup",
    actions={"delete_branch": "branch.delete", "list_branches": "branch.list"},
    resources={"delete_branch": "github://acme/repo/branches/{branch}",
               "list_branches": "github://acme/repo"},
    handlers={"delete_branch": github.delete_branch, "list_branches": github.list_branches},
)
result = dispatch(tool_call.name, tool_call.arguments)   # KeyError for unmapped tools
```

## LangChain and CrewAI

```python
from agentplane.adapters import langchain_tool, crewai_tool

governed = langchain_tool(search_tool, plane, task="research", action="kb.search",
                          resource="kb/{input}")
agent = create_react_agent(llm, [governed])
```

The wrapper proxies attribute access (`name`, `description`, `args_schema`)
to the original tool and authorizes on `invoke`, `run`, `_run`, and
`__call__`. The resource template is formatted from the tool's input mapping,
or `{input}` for single-string tools. `crewai_tool` is the same function.

## OpenAI Agents SDK

```python
from agentplane.adapters import openai_agents_guard

guard = openai_agents_guard(plane, task=lambda: run_context.task,
                            actions={"delete_branch": "branch.delete"},
                            resources={"delete_branch": "github://acme/repo/branches/{branch}"})

async def before_tool(tool_name: str, arguments: dict) -> None:
    guard(tool_name, arguments)     # raises NotAuthorized / ApprovalRequired to block
```

Call it from whatever pre-execution hook your version of the SDK exposes
(tool guardrail, `on_tool_start`, or a wrapping `FunctionTool`). Unmapped
tools are refused with `TOOL_NOT_GOVERNED`.

## TypeScript

```ts
import { govern } from "@agent-plane/sdk";
const restart = govern(plane, { task, action: "deployment.restart",
  resource: (svc: string) => `staging/${svc}` }, restartService);
```

Vercel AI SDK: call `plane.authorize` inside each tool's `execute`, or wrap
`execute` with `govern`.

## Resource derivation matters

Leases match resources with glob patterns. `staging/*` matches
`staging/../production/x` textually, so the resource string must be built
from validated, canonical arguments in your executor, never from raw model
output. Lease templates apply the same rule to variables.
