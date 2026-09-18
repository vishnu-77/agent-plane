"""Authority Contracts (0.8.x, see spec/authority-contract.md and
agent_plane/authority/contract.py): the human-readable policy surface that
compiles into the existing AuthorityLease - no second authorization engine.

    POST /v1/contracts                    create/version a contract
    GET  /v1/contracts/{id}                latest version
    GET  /v1/contracts/{id}/history        every version (Phase 14: GRC audit)
    POST /v1/contracts/{id}/compile        compile -> issue a concrete lease for a task
"""
from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import ValidationError

from agent_plane.authority.contract import compile_contract, parse_contract
from agent_plane.gateway.authz import require_admin, resolve_operator

contracts_router = APIRouter()


def _readable(request: Request, tenant: str, x_admin_token: str | None,
             x_demo_token: str | None) -> None:
    """Contracts are read like any other project resource (any project
    member, the demo viewer, or an operator token) - reading "what
    governs this agent" is not the deployment-wide operation issuing or
    compiling one is, so this deliberately doesn't use require_admin."""
    scope = resolve_operator(request, x_admin_token, x_demo_token, project=tenant)
    if not scope.allows(tenant):
        raise HTTPException(status_code=403, detail="You do not have access to that project")


@contracts_router.post("/v1/contracts")
async def create_contract(request: Request, body: dict[str, Any],
                          x_admin_token: str | None = Header(default=None)) -> dict[str, Any]:
    try:
        contract = parse_contract(body or {})
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"invalid contract: {exc}") from exc
    except ValidationError as exc:
        raise HTTPException(status_code=400, detail=f"invalid contract: {exc.error_count()} error(s)") from exc
    require_admin(request, x_admin_token, tenant=contract.tenant)
    registry: Any = request.app.state.contracts
    existing = registry.latest(contract.tenant, contract.contract_id)
    if existing is not None and "version" not in (body or {}):
        # Auto-increment when the caller didn't pin one - the common case,
        # a developer iterating on the same contract_id.
        contract = contract.model_copy(update={"version": existing.version + 1,
                                                "supersedes": contract.supersedes or existing.fingerprint})
    registry.upsert(contract)
    return {"issued": True, "contract": contract.model_dump(mode="json"), "fingerprint": contract.fingerprint}


@contracts_router.get("/v1/agents/{agent_id}/contract")
async def get_agent_contract(request: Request, agent_id: str, tenant: str = "default",
                             x_admin_token: str | None = Header(default=None),
                             x_demo_token: str | None = Header(default=None)) -> dict[str, Any]:
    """The contract governing this agent, if any was issued with
    contract_id "<agent_id>-<domain>-starter" - the same naming
    convention agent_plane.domains.{software,cloud}'s suggested_contract()
    uses, and the same lookup agent_plane.mcp_control.server's
    current_contract tool performs. A REST sibling to that MCP tool, for
    the console."""
    _readable(request, tenant, x_admin_token, x_demo_token)
    registry = request.app.state.contracts
    for suffix in ("-software-starter", "-cloud-starter"):
        contract = registry.latest(tenant, f"{agent_id}{suffix}")
        if contract is not None:
            return {"found": True, "contract": contract.model_dump(mode="json"), "fingerprint": contract.fingerprint}
    return {"found": False, "contract": None, "fingerprint": None}


@contracts_router.get("/v1/contracts/{contract_id}")
async def get_contract(request: Request, contract_id: str, tenant: str = "default",
                       x_admin_token: str | None = Header(default=None),
                       x_demo_token: str | None = Header(default=None)) -> dict[str, Any]:
    _readable(request, tenant, x_admin_token, x_demo_token)
    contract = request.app.state.contracts.latest(tenant, contract_id)
    if contract is None:
        raise HTTPException(status_code=404, detail="contract not found")
    return {"contract": contract.model_dump(mode="json"), "fingerprint": contract.fingerprint}


@contracts_router.get("/v1/contracts/{contract_id}/history")
async def contract_history(request: Request, contract_id: str, tenant: str = "default",
                           x_admin_token: str | None = Header(default=None),
                           x_demo_token: str | None = Header(default=None)) -> dict[str, Any]:
    _readable(request, tenant, x_admin_token, x_demo_token)
    versions = request.app.state.contracts.history(tenant, contract_id)
    return {"versions": [{**c.model_dump(mode="json"), "fingerprint": c.fingerprint} for c in versions]}


@contracts_router.post("/v1/contracts/{contract_id}/compile")
async def compile_and_issue(request: Request, contract_id: str, body: dict[str, Any],
                            x_admin_token: str | None = Header(default=None)) -> dict[str, Any]:
    """Compile the contract's latest (or a pinned) version into a concrete,
    task-bound lease and issue it through the existing lease store - the
    evaluator never sees a contract, only the lease this produces."""
    body = body or {}
    tenant = body.get("tenant") or "default"
    task = body.get("task")
    if not task:
        raise HTTPException(status_code=400, detail="'task' is required")
    require_admin(request, x_admin_token, tenant=tenant)
    registry: Any = request.app.state.contracts
    version = body.get("version")
    contract = (
        next((c for c in registry.history(tenant, contract_id) if c.version == version), None)
        if version is not None else registry.latest(tenant, contract_id)
    )
    if contract is None:
        raise HTTPException(status_code=404, detail="contract not found")
    lease_id = body.get("lease_id") or f"lease-{contract_id}-{uuid.uuid4().hex[:8]}"
    lease = compile_contract(contract, lease_id=lease_id, task=task)
    request.app.state.leases.add(lease)
    registry_store = getattr(request.app.state, "agent_registry", None)
    if registry_store is not None:
        registry_store.attach_lease(tenant=lease.tenant, task=lease.task, agent=lease.subject, lease_id=lease.id)
    return {"issued": True, "lease": lease.model_dump(mode="json"),
            "compiled_from": {"contract_id": contract.contract_id, "version": contract.version,
                              "fingerprint": contract.fingerprint}}
