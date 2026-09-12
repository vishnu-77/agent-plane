"""Framework adapters: put the authorize-before-execute check in front of a
tool once, instead of at every call site.

Every adapter is built on :func:`govern`, which wraps any callable so that
``plane.authorize(task, action, resource)`` runs first and the callable runs
only on ALLOW. The resource is derived from the call's arguments by a
function you supply, so the canonical resource is decided by your code, not
by the model.

    from agentplane import AgentPlane
    from agentplane.adapters import govern

    plane = AgentPlane(url, token)

    @govern(plane, task=lambda: current_task(), action="branch.delete",
            resource=lambda branch: f"github://acme/repo/branches/{branch}")
    def delete_branch(branch: str) -> str:
        return github.delete_branch(branch)

What happens on each outcome:

* ALLOW - the callable runs; the decision is available on the raised error /
  return value via ``last_decision``.
* APPROVAL_REQUIRED - :class:`ApprovalRequired` is raised carrying the
  decision (and its ``approval_id``). If ``wait_for_approval`` seconds is
  set, the adapter instead polls and resumes, then runs on approval.
* DENY, protocol error, HTTP error, transport error - :class:`NotAuthorized`
  (or the underlying exception) is raised; the callable never runs.

Framework helpers:

* :func:`langchain_tool` - wrap a LangChain ``BaseTool`` (duck-typed: any
  object with ``name`` and ``invoke``/``run``/``_run``).
* :func:`crewai_tool` - same shape for CrewAI tools.
* :func:`openai_agents_guard` - a ``tool_guardrail``-style callable for the
  OpenAI Agents SDK.
* :func:`governed_dispatch` - one function for custom agent loops.

No framework is imported here; nothing is installed on your behalf.
"""
from __future__ import annotations

import functools
import inspect
from collections.abc import Callable, Mapping
from typing import Any

from agentplane import AgentPlane, ApprovalTimeout, AuthorityDecision

__all__ = [
    "ApprovalRequired",
    "NotAuthorized",
    "crewai_tool",
    "govern",
    "governed_dispatch",
    "langchain_tool",
    "openai_agents_guard",
]

ResourceFn = Callable[..., str]
TaskFn = str | Callable[[], str]


class NotAuthorized(PermissionError):
    """The action was denied (or could not be verified). Do not execute."""

    def __init__(self, decision: AuthorityDecision):
        super().__init__(f"{decision.decision.upper()}: {decision.reason} "
                         f"({decision.action} -> {decision.resource}; evidence {decision.evidence_id})")
        self.decision = decision


class ApprovalRequired(NotAuthorized):
    """A human must approve first. ``decision.approval_id`` identifies the request."""


def _resolve_task(task: TaskFn) -> str:
    return task() if callable(task) else task


def _resolve_resource(resource: str | ResourceFn, args: tuple, kwargs: dict) -> str:
    if callable(resource):
        return resource(*args, **kwargs)
    return resource.format(*args, **kwargs) if ("{" in resource) else resource


def _bound_kwargs(fn: Callable, args: tuple, kwargs: dict) -> dict[str, Any]:
    try:
        bound = inspect.signature(fn).bind_partial(*args, **kwargs)
        bound.apply_defaults()
        return dict(bound.arguments)
    except (TypeError, ValueError):
        return dict(kwargs)


def authorize_call(
    plane: AgentPlane, *, task: TaskFn, action: str, resource: str,
    context: Mapping[str, str] | None = None, wait_for_approval: float | None = None,
) -> AuthorityDecision:
    """Run the check and turn anything but ALLOW into an exception."""
    decision = plane.authorize(task=_resolve_task(task), action=action, resource=resource,
                               context=dict(context) if context else None)
    if decision.needs_approval and wait_for_approval:
        try:
            decision = plane.wait_for_approval(decision, timeout=wait_for_approval)
        except ApprovalTimeout:
            raise ApprovalRequired(decision) from None
    if decision.proceed:
        return decision
    if decision.needs_approval:
        raise ApprovalRequired(decision)
    raise NotAuthorized(decision)


def govern(
    plane: AgentPlane, *, task: TaskFn, action: str, resource: str | ResourceFn,
    context: Callable[..., Mapping[str, str]] | Mapping[str, str] | None = None,
    wait_for_approval: float | None = None,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Decorator: authorize with agent-plane before every call of the function.

    ``resource`` is either a format string over the function's bound arguments
    (``"staging/{service}"``) or a callable receiving the same arguments.
    ``task`` is a string or a zero-arg callable returning the current task id
    (so one wrapped tool can serve many tasks).
    """

    def decorate(fn: Callable[..., Any]) -> Callable[..., Any]:
        @functools.wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            bound = _bound_kwargs(fn, args, kwargs)
            res = resource(**bound) if callable(resource) else resource.format(**bound)
            ctx = context(**bound) if callable(context) else context
            decision = authorize_call(plane, task=task, action=action, resource=res,
                                      context=ctx, wait_for_approval=wait_for_approval)
            wrapper.last_decision = decision  # type: ignore[attr-defined]
            return fn(*args, **kwargs)

        wrapper.last_decision = None  # type: ignore[attr-defined]
        wrapper.agent_plane_action = action  # type: ignore[attr-defined]
        return wrapper

    return decorate


def governed_dispatch(
    plane: AgentPlane, *, task: TaskFn, actions: Mapping[str, str],
    resources: Mapping[str, str | ResourceFn], handlers: Mapping[str, Callable[..., Any]],
    wait_for_approval: float | None = None,
) -> Callable[[str, Mapping[str, Any]], Any]:
    """Build one ``dispatch(tool_name, arguments)`` for a custom agent loop.

    ``actions`` maps tool name -> action; ``resources`` maps tool name -> a
    format string or callable over the arguments; ``handlers`` maps tool name
    -> the real implementation. Unknown tools are refused before any check.
    """

    def dispatch(tool_name: str, arguments: Mapping[str, Any]) -> Any:
        if tool_name not in handlers or tool_name not in actions:
            raise KeyError(f"unknown or ungoverned tool: {tool_name}")
        res_spec = resources.get(tool_name, "")
        res = res_spec(**arguments) if callable(res_spec) else str(res_spec).format(**arguments)
        authorize_call(plane, task=task, action=actions[tool_name], resource=res,
                       wait_for_approval=wait_for_approval)
        return handlers[tool_name](**arguments)

    return dispatch


def _tool_invoke(tool: Any) -> Callable[..., Any]:
    for name in ("invoke", "run", "_run", "__call__"):
        fn = getattr(tool, name, None)
        if callable(fn):
            return fn
    raise TypeError("tool has no invoke/run/_run method")


class _GovernedTool:
    """Proxy that authorizes before delegating to the wrapped tool.

    Attribute access falls through to the wrapped tool so frameworks that
    read ``name``, ``description``, ``args_schema`` etc. keep working.
    """

    def __init__(self, tool: Any, plane: AgentPlane, *, task: TaskFn, action: str,
                 resource: str | ResourceFn, wait_for_approval: float | None):
        object.__setattr__(self, "_tool", tool)
        object.__setattr__(self, "_plane", plane)
        object.__setattr__(self, "_task", task)
        object.__setattr__(self, "_action", action)
        object.__setattr__(self, "_resource", resource)
        object.__setattr__(self, "_wait", wait_for_approval)
        object.__setattr__(self, "last_decision", None)

    def _check(self, arguments: Mapping[str, Any]) -> AuthorityDecision:
        res = self._resource(**arguments) if callable(self._resource) else str(self._resource).format(**arguments)
        decision = authorize_call(self._plane, task=self._task, action=self._action, resource=res,
                                  wait_for_approval=self._wait)
        object.__setattr__(self, "last_decision", decision)
        return decision

    @staticmethod
    def _arguments(args: tuple, kwargs: dict) -> Mapping[str, Any]:
        if args and isinstance(args[0], Mapping):
            return {**args[0], **kwargs}
        if args and isinstance(args[0], str):
            return {"input": args[0], **kwargs}
        return kwargs

    def invoke(self, *args: Any, **kwargs: Any) -> Any:
        self._check(self._arguments(args, kwargs))
        return _tool_invoke(self._tool)(*args, **kwargs)

    run = invoke
    _run = invoke
    __call__ = invoke

    def __getattr__(self, name: str) -> Any:
        return getattr(self._tool, name)

    def __setattr__(self, name: str, value: Any) -> None:
        setattr(self._tool, name, value)


def langchain_tool(tool: Any, plane: AgentPlane, *, task: TaskFn, action: str,
                   resource: str | ResourceFn, wait_for_approval: float | None = None) -> Any:
    """Wrap a LangChain tool so ``invoke``/``run`` authorize first.

    ``resource`` is formatted from the tool's input mapping (or ``{input}``
    for single-string tools). Pass the returned object to your agent in place
    of the original tool.
    """
    return _GovernedTool(tool, plane, task=task, action=action, resource=resource,
                         wait_for_approval=wait_for_approval)


crewai_tool = langchain_tool  # same duck-typed shape (`name`, `run`/`_run`)


def openai_agents_guard(plane: AgentPlane, *, task: TaskFn,
                        actions: Mapping[str, str], resources: Mapping[str, str | ResourceFn],
                        wait_for_approval: float | None = None) -> Callable[[str, Mapping[str, Any]], AuthorityDecision]:
    """A guard for the OpenAI Agents SDK (or any hook that sees tool name +
    arguments before execution). Call it from your tool guardrail / hook; it
    raises :class:`NotAuthorized` or :class:`ApprovalRequired` to block."""

    def guard(tool_name: str, arguments: Mapping[str, Any]) -> AuthorityDecision:
        if tool_name not in actions:
            raise NotAuthorized(AuthorityDecision(
                decision="deny", reason="TOOL_NOT_GOVERNED", lease=None, evidence_id="-",
                task=_resolve_task(task), action=tool_name, resource=""))
        spec = resources.get(tool_name, "")
        res = spec(**arguments) if callable(spec) else str(spec).format(**arguments)
        return authorize_call(plane, task=task, action=actions[tool_name], resource=res,
                              wait_for_approval=wait_for_approval)

    return guard
