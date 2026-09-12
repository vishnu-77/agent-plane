"""Sign-up, sign-in, projects, API keys, integrations, and rules.

This is the surface the console talks to. A human never handles a token here:
the browser gets a signed, httponly session cookie and every subsequent call
is scoped to a project they belong to.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Query, Request, Response

from agent_plane.accounts.models import (
    DEFAULT_COLLECTION,
    INTEGRATION_CATALOG,
    OPTIONAL_COLLECTION,
    Project,
    User,
)
from agent_plane.accounts.security import (
    SESSION_COOKIE,
    password_problems,
    read_session,
    sign_session,
)
from agent_plane.accounts.store import AccountError
from agent_plane.authority.lease import action_matches
from agent_plane.gateway.authz import resolve_operator
from agent_plane.rules import suggest_rule
from agent_plane.rules.yaml_io import RulesYamlError, dump_rules, load_rules

accounts_router = APIRouter(tags=["accounts"])


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _store(request: Request):
    accounts = getattr(request.app.state, "accounts", None)
    if accounts is None:
        raise HTTPException(status_code=404, detail="accounts are not enabled")
    return accounts


def _current_user(request: Request) -> User:
    settings = request.app.state.settings
    payload = read_session(request.cookies.get(SESSION_COOKIE), settings.api_key_secret)
    user = _store(request).user(str(payload["sub"])) if payload and payload.get("sub") else None
    if user is None:
        raise HTTPException(status_code=401, detail="Sign in to continue")
    return user


def _set_session(request: Request, response: Response, user: User) -> None:
    settings = request.app.state.settings
    cookie = sign_session({"sub": user.id}, settings.api_key_secret, settings.session_ttl_seconds)
    response.set_cookie(SESSION_COOKIE, cookie, httponly=True, samesite="lax",
                        secure=settings.cookies_secure, max_age=settings.session_ttl_seconds, path="/")


def _project_or_403(request: Request, user: User, project_id: str) -> Project:
    project = _store(request).project(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Unknown project")
    if project.id not in {p.id for p in _store(request).projects_for(user.id)}:
        raise HTTPException(status_code=403, detail="You do not have access to that project")
    return project


def _project_view(request: Request, project: Project) -> dict[str, Any]:
    accounts = _store(request)
    integrations = accounts.integrations(project.id)
    rules = request.app.state.rules.list(project.id)
    return {
        **project.model_dump(mode="json"),
        "keys": len([k for k in accounts.keys(project.id) if k.status == "active"]),
        "integrations": len(integrations),
        "connected": len([i for i in integrations if i.status == "connected"]),
        "rules": len(rules),
    }


def _audit(request: Request, *, action: str, detail: str, tenant: str = "-") -> None:
    request.app.state.audit.record({
        "decision_id": f"acct_{datetime.now(UTC).timestamp():.0f}", "user_id": "console",
        "tenant": tenant, "model_requested": f"account:{action}", "model_used": action,
        "data_classification": "", "decision": "admin_action", "reason": detail, "rules_matched": [],
    })


# --------------------------------------------------------------------------- #
# auth
# --------------------------------------------------------------------------- #
@accounts_router.get("/v1/auth/state")
async def auth_state(request: Request) -> dict[str, Any]:
    """What the sign-in screen needs before anyone has an account."""
    settings = request.app.state.settings
    accounts = _store(request)
    users = accounts.user_count()
    return {
        "users": users,
        "signup_open": settings.signup_mode == "open" or (settings.signup_mode == "first_user" and users == 0),
        "first_run": users == 0,
        "demo_available": settings.demo_enabled,
        "demo_token": settings.demo_token if settings.demo_enabled else None,
    }


@accounts_router.post("/v1/auth/signup")
async def signup(request: Request, response: Response, body: dict[str, Any]) -> dict[str, Any]:
    settings = request.app.state.settings
    accounts = _store(request)
    users = accounts.user_count()
    open_signup = settings.signup_mode == "open" or (settings.signup_mode == "first_user" and users == 0)
    if not open_signup:
        raise HTTPException(status_code=403, detail="Sign-up is closed on this deployment")
    email = str(body.get("email") or "").strip()
    password = str(body.get("password") or "")
    if "@" not in email or len(email) < 5:
        raise HTTPException(status_code=400, detail="Enter a valid email address")
    problems = password_problems(password)
    if problems:
        raise HTTPException(status_code=400, detail=f"Password {problems[0]}")
    try:
        user = accounts.create_user(email=email, password=password, name=str(body.get("name") or ""))
    except AccountError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    workspace = accounts.create_workspace(name=str(body.get("workspace") or f"{user.name}'s workspace"), owner=user.id)
    _set_session(request, response, user)
    _audit(request, action="signup", detail=f"user {user.id} created workspace {workspace.id}")
    return {"user": user.model_dump(mode="json"), "workspace": workspace.model_dump(mode="json")}


@accounts_router.post("/v1/auth/login")
async def login(request: Request, response: Response, body: dict[str, Any]) -> dict[str, Any]:
    user = _store(request).authenticate(str(body.get("email") or ""), str(body.get("password") or ""))
    if user is None:
        raise HTTPException(status_code=401, detail="That email and password do not match")
    _set_session(request, response, user)
    return {"user": user.model_dump(mode="json")}


@accounts_router.post("/v1/auth/logout")
async def logout(response: Response) -> dict[str, Any]:
    response.delete_cookie(SESSION_COOKIE, path="/")
    return {"signed_out": True}


@accounts_router.get("/v1/auth/me")
async def me(request: Request) -> dict[str, Any]:
    user = _current_user(request)
    accounts = _store(request)
    workspaces = accounts.workspaces_for(user.id)
    projects = [_project_view(request, p) for p in accounts.projects_for(user.id)]
    return {
        "user": user.model_dump(mode="json"),
        "workspaces": [w.model_dump(mode="json") for w in workspaces],
        "projects": projects,
        # The console uses this to decide whether to show onboarding.
        "onboarded": any(p["connected"] for p in projects),
    }


@accounts_router.post("/v1/auth/exchange")
async def exchange(request: Request, body: dict[str, Any] | None = None,
                   authorization: str | None = Header(default=None),
                   x_api_key: str | None = Header(default=None)) -> dict[str, Any]:
    """Project API Key -> a short-lived runtime identity for one agent session.

    Connectors call this once at start-up. The developer never mints a token;
    agent-plane resolves the project, registers the integration, and hands back
    the identifiers the connector should attach to everything it reports.
    """
    body = body or {}
    from agent_plane.gateway.context import resolve_request

    ctx = resolve_request(request, authorization=authorization, x_api_key=x_api_key, body=body)
    if ctx.project is None:
        raise HTTPException(status_code=401, detail="A Project API Key is required")
    ctx.requires("ingest")
    kind = ctx.integration or str(body.get("integration") or "custom")
    if kind not in INTEGRATION_CATALOG:
        kind = "custom"
    accounts = _store(request)
    integration = accounts.upsert_integration(
        project_id=ctx.project.id, kind=kind,
        name=str(body.get("name") or INTEGRATION_CATALOG[kind]["label"]),
        host=ctx.host, config={"version": body.get("version")} if body.get("version") else None)
    session_id = ctx.session or f"ses_{datetime.now(UTC).timestamp():.0f}"
    catalog = INTEGRATION_CATALOG[kind]
    return {
        "project": {"id": ctx.project.id, "name": ctx.project.name, "mode": ctx.project.mode},
        "integration": {"id": integration.id, "kind": kind,
                        "observation": catalog["observation"], "enforcement": catalog["enforcement"]},
        "agent": ctx.agent,
        "session": session_id,
        "collect": {k: v for k, v in ctx.project.collection.items()},
        # Everything the connector should send back with each action.
        "report_to": "/v1/events/action",
        "authorize_at": "/v1/authorize",
    }


# --------------------------------------------------------------------------- #
# projects
# --------------------------------------------------------------------------- #
@accounts_router.get("/v1/projects")
async def list_projects(request: Request) -> dict[str, Any]:
    user = _current_user(request)
    return {"projects": [_project_view(request, p) for p in _store(request).projects_for(user.id)]}


@accounts_router.post("/v1/projects")
async def create_project(request: Request, body: dict[str, Any]) -> dict[str, Any]:
    user = _current_user(request)
    accounts = _store(request)
    workspaces = accounts.workspaces_for(user.id)
    workspace_id = str(body.get("workspace") or (workspaces[0].id if workspaces else ""))
    if not workspace_id:
        workspace_id = accounts.create_workspace(name=f"{user.name}'s workspace", owner=user.id).id
    elif workspace_id not in {w.id for w in workspaces}:
        raise HTTPException(status_code=403, detail="You do not have access to that workspace")
    name = str(body.get("name") or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="Give the project a name")
    mode = str(body.get("mode") or "observe")
    if mode not in ("observe", "govern", "enforce"):
        raise HTTPException(status_code=400, detail="mode must be observe, govern, or enforce")
    project = accounts.create_project(workspace_id=workspace_id, name=name, created_by=user.id, mode=mode)
    _audit(request, action="project.create", detail=f"{project.id} ({mode})", tenant=project.id)
    return {"project": _project_view(request, project)}


@accounts_router.get("/v1/projects/{project_id}")
async def get_project(request: Request, project_id: str) -> dict[str, Any]:
    project = _project_or_403(request, _current_user(request), project_id)
    return {"project": _project_view(request, project),
            "collection_fields": {"defaults": DEFAULT_COLLECTION, "optional": list(OPTIONAL_COLLECTION)}}


@accounts_router.patch("/v1/projects/{project_id}")
async def update_project(request: Request, project_id: str, body: dict[str, Any]) -> dict[str, Any]:
    user = _current_user(request)
    _project_or_403(request, user, project_id)
    if "mode" in body and body["mode"] not in ("observe", "govern", "enforce"):
        raise HTTPException(status_code=400, detail="mode must be observe, govern, or enforce")
    if "collection" in body and not isinstance(body["collection"], dict):
        raise HTTPException(status_code=400, detail="collection must be an object of booleans")
    project = _store(request).update_project(project_id, **body)
    if "mode" in body:
        _audit(request, action="project.mode", detail=f"{project_id} -> {project.mode}", tenant=project_id)
    return {"project": _project_view(request, project)}


@accounts_router.delete("/v1/projects/{project_id}")
async def delete_project(request: Request, project_id: str) -> dict[str, Any]:
    user = _current_user(request)
    project = _project_or_403(request, user, project_id)
    if project.demo:
        raise HTTPException(status_code=400, detail="The demo project cannot be deleted")
    _store(request).delete_project(project_id)
    request.app.state.agent_registry.reset_tenant(project_id)
    _audit(request, action="project.delete", detail=project_id, tenant=project_id)
    return {"deleted": True}


@accounts_router.get("/v1/workspaces/{workspace_id}/members")
async def members(request: Request, workspace_id: str) -> dict[str, Any]:
    user = _current_user(request)
    accounts = _store(request)
    if workspace_id not in {w.id for w in accounts.workspaces_for(user.id)}:
        raise HTTPException(status_code=403, detail="You do not have access to that workspace")
    return {"members": accounts.members(workspace_id), "your_role": accounts.role_of(workspace_id, user.id)}


# --------------------------------------------------------------------------- #
# api keys
# --------------------------------------------------------------------------- #
@accounts_router.get("/v1/api-keys")
async def list_keys(request: Request, project: str = Query(...)) -> dict[str, Any]:
    _project_or_403(request, _current_user(request), project)
    keys = _store(request).keys(project)
    return {"keys": [{**k.model_dump(mode="json"), "masked": k.masked, "status": k.status} for k in keys]}


@accounts_router.post("/v1/api-keys")
async def create_key(request: Request, body: dict[str, Any]) -> dict[str, Any]:
    user = _current_user(request)
    project_id = str(body.get("project") or "")
    _project_or_403(request, user, project_id)
    environment = str(body.get("environment") or "live")
    if environment not in ("live", "test", "mgmt"):
        raise HTTPException(status_code=400, detail="environment must be live, test, or mgmt")
    expires_at = None
    if body.get("expires_in_days"):
        expires_at = datetime.now(UTC) + timedelta(days=int(body["expires_in_days"]))
    key, plaintext = _store(request).create_key(
        project_id=project_id, name=str(body.get("name") or "api key"), created_by=user.id,
        environment=environment, expires_at=expires_at)
    _audit(request, action="key.create", detail=f"{key.id} ({environment})", tenant=project_id)
    # The only time the secret is ever returned.
    return {"key": {**key.model_dump(mode="json"), "masked": key.masked, "status": key.status},
            "secret": plaintext}


@accounts_router.post("/v1/api-keys/{key_id}/rotate")
async def rotate_key(request: Request, key_id: str) -> dict[str, Any]:
    user = _current_user(request)
    accounts = _store(request)
    existing = accounts.key(key_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="Unknown key")
    _project_or_403(request, user, existing.project_id)
    key, plaintext = accounts.rotate_key(key_id, created_by=user.id)
    _audit(request, action="key.rotate", detail=f"{key_id} -> {key.id}", tenant=existing.project_id)
    return {"key": {**key.model_dump(mode="json"), "masked": key.masked, "status": key.status},
            "secret": plaintext, "revoked": key_id}


@accounts_router.delete("/v1/api-keys/{key_id}")
async def revoke_key(request: Request, key_id: str) -> dict[str, Any]:
    user = _current_user(request)
    accounts = _store(request)
    existing = accounts.key(key_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="Unknown key")
    _project_or_403(request, user, existing.project_id)
    key = accounts.revoke_key(key_id)
    _audit(request, action="key.revoke", detail=key_id, tenant=existing.project_id)
    return {"key": {**key.model_dump(mode="json"), "masked": key.masked, "status": key.status}}


# --------------------------------------------------------------------------- #
# integrations
# --------------------------------------------------------------------------- #
@accounts_router.get("/v1/integrations")
async def list_integrations(request: Request, project: str = Query(...),
                            x_admin_token: str | None = Header(default=None),
                            x_demo_token: str | None = Header(default=None)) -> dict[str, Any]:
    scope = resolve_operator(request, x_admin_token, x_demo_token, project=project)
    project_id = scope.restrict(project) or project
    items = []
    for integration in _store(request).integrations(project_id):
        catalog = integration.catalog
        items.append({**integration.model_dump(mode="json"), "label": catalog["label"],
                      "observation": catalog["observation"], "enforcement": catalog["enforcement"],
                      "summary": catalog["summary"], "enforcement_note": catalog["enforcement_note"]})
    return {"integrations": items,
            "catalog": [{"kind": k, **v} for k, v in INTEGRATION_CATALOG.items()]}


@accounts_router.post("/v1/integrations")
async def create_integration(request: Request, body: dict[str, Any]) -> dict[str, Any]:
    user = _current_user(request)
    project_id = str(body.get("project") or "")
    _project_or_403(request, user, project_id)
    kind = str(body.get("kind") or "custom")
    if kind not in INTEGRATION_CATALOG:
        raise HTTPException(status_code=400, detail="Unknown integration kind")
    integration = _store(request).upsert_integration(
        project_id=project_id, kind=kind, name=str(body.get("name") or INTEGRATION_CATALOG[kind]["label"]),
        host=body.get("host"), config=body.get("config") or {})
    return {"integration": integration.model_dump(mode="json")}


@accounts_router.delete("/v1/integrations/{integration_id}")
async def delete_integration(request: Request, integration_id: str,
                             project: str = Query(...)) -> dict[str, Any]:
    _project_or_403(request, _current_user(request), project)
    _store(request).delete_integration(integration_id)
    return {"deleted": True}


# --------------------------------------------------------------------------- #
# rules
# --------------------------------------------------------------------------- #
def _rule_view(rule) -> dict[str, Any]:
    return {**rule.model_dump(mode="json"), "summary": rule.summary(), "scope_label": rule.scope.label()}


# What a coding agent actually does, in the order someone thinks about it. The
# catalog is declared in operator order, which puts deployments and metrics at
# the top of a screen whose first reader is governing a coding agent.
_ACTION_NAMESPACE_ORDER = ["filesystem", "tests", "git", "repository", "shell",
                           "package", "branch", "network"]


def _action_order(option: dict[str, Any]) -> int:
    """Sort key only. The sort is stable, so the catalog's own order survives
    inside each group."""
    namespace = str(option["action"]).split(".", 1)[0]
    if namespace in _ACTION_NAMESPACE_ORDER:
        return _ACTION_NAMESPACE_ORDER.index(namespace)
    return len(_ACTION_NAMESPACE_ORDER)


@accounts_router.get("/v1/rules")
async def list_rules(request: Request, project: str = Query(...),
                     x_admin_token: str | None = Header(default=None),
                     x_demo_token: str | None = Header(default=None)) -> dict[str, Any]:
    scope = resolve_operator(request, x_admin_token, x_demo_token, project=project)
    project_id = scope.restrict(project) or project
    rules = request.app.state.rules.list(project_id)
    catalog = request.app.state.catalog
    options = [{"action": a.pattern, "label": a.label or a.pattern,
                "class": a.consequence_class, "effect": a.effect}
               for a in catalog.actions if "*" not in a.pattern]
    return {
        "rules": [_rule_view(r) for r in rules],
        # The action vocabulary the rule editor offers, with plain labels,
        # ordered so the person writing a rule sees what their agents do first.
        "actions": sorted(options, key=_action_order),
        "templates": request.app.state.rule_templates,
    }


@accounts_router.post("/v1/rules")
async def create_rule(request: Request, body: dict[str, Any]) -> dict[str, Any]:
    user = _current_user(request)
    project_id = str(body.get("project") or body.get("project_id") or "")
    _project_or_403(request, user, project_id)
    rule = request.app.state.rules.create(project_id, body, created_by=user.id)
    _audit(request, action="rule.create", detail=f"{rule.id} {rule.summary()}", tenant=project_id)
    return {"rule": _rule_view(rule)}


@accounts_router.patch("/v1/rules/{rule_id}")
async def update_rule(request: Request, rule_id: str, body: dict[str, Any]) -> dict[str, Any]:
    user = _current_user(request)
    existing = request.app.state.rules.get(rule_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="Unknown rule")
    _project_or_403(request, user, existing.project_id)
    rule = request.app.state.rules.update(rule_id, body)
    _audit(request, action="rule.update", detail=f"{rule_id} {rule.summary()}", tenant=existing.project_id)
    return {"rule": _rule_view(rule)}


@accounts_router.delete("/v1/rules/{rule_id}")
async def delete_rule(request: Request, rule_id: str) -> dict[str, Any]:
    user = _current_user(request)
    existing = request.app.state.rules.get(rule_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="Unknown rule")
    _project_or_403(request, user, existing.project_id)
    request.app.state.rules.delete(rule_id)
    _audit(request, action="rule.delete", detail=rule_id, tenant=existing.project_id)
    return {"deleted": True}


@accounts_router.get("/v1/rules/export")
async def export_rules(request: Request, project: str = Query(...),
                       x_admin_token: str | None = Header(default=None),
                       x_demo_token: str | None = Header(default=None)) -> dict[str, Any]:
    """This project's permissions as YAML, ready to commit next to the code."""
    scope = resolve_operator(request, x_admin_token, x_demo_token, project=project)
    project_id = scope.restrict(project) or project
    rules = request.app.state.rules.list(project_id)
    return {"project": project_id, "count": len(rules), "yaml": dump_rules(rules)}


@accounts_router.post("/v1/rules/import")
async def import_rules(request: Request, body: dict[str, Any],
                       x_admin_token: str | None = Header(default=None)) -> dict[str, Any]:
    """Apply a permissions file.

    ``merge`` (the default) creates what is new and updates what a rule of the
    same name already says, leaving anything the file does not mention alone.
    ``replace`` makes the file the whole truth for this project and deletes the
    rest, which is what config-as-code usually wants and is never the default
    because it throws away rules someone wrote in the console.
    """
    # A console session, or a management key: applying a permissions file from
    # CI is the point of having the file, and a pipeline has no cookie.
    requested = str(body.get("project") or body.get("project_id") or "")
    scope = resolve_operator(request, x_admin_token, project=requested)
    scope.require_write()
    project_id = scope.restrict(requested) or requested
    if scope.user_id is not None:
        _project_or_403(request, _current_user(request), project_id)
    elif request.app.state.accounts.project(project_id) is None:
        raise HTTPException(status_code=404, detail="Unknown project")
    author = scope.user_id or scope.source
    mode = str(body.get("mode") or "merge")
    if mode not in ("merge", "replace"):
        raise HTTPException(status_code=400, detail="mode must be 'merge' or 'replace'")
    try:
        payloads = load_rules(str(body.get("yaml") or ""))
    except RulesYamlError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    store = request.app.state.rules
    existing = {rule.name: rule for rule in store.list(project_id)}
    created, updated = [], []
    for payload in payloads:
        current = existing.get(payload["name"])
        if current is None:
            created.append(store.create(project_id, payload, created_by=author).id)
        else:
            store.update(current.id, payload)
            updated.append(current.id)

    deleted: list[str] = []
    if mode == "replace":
        keep = {p["name"] for p in payloads}
        for name, rule in existing.items():
            if name not in keep:
                store.delete(rule.id)
                deleted.append(rule.id)

    _audit(request, action="rule.import",
           detail=f"{mode}: {len(created)} created, {len(updated)} updated, {len(deleted)} deleted",
           tenant=project_id)
    return {"created": created, "updated": updated, "deleted": deleted,
            "rules": [_rule_view(r) for r in store.list(project_id)]}


@accounts_router.get("/v1/rules/suggested")
async def suggested_rules(request: Request, project: str = Query(...),
                          agent: str | None = Query(default=None),
                          x_admin_token: str | None = Header(default=None),
                          x_demo_token: str | None = Header(default=None)) -> dict[str, Any]:
    """A reviewable rule drafted from what agents actually did (Observe -> Govern)."""
    scope = resolve_operator(request, x_admin_token, x_demo_token, project=project)
    project_id = scope.restrict(project) or project
    registry = request.app.state.agent_registry
    agents = [a for a in registry.agents(project_id) if agent is None or a.id == agent]
    if not agents:
        return {"suggestions": []}
    existing = request.app.state.rules.list(project_id, enabled_only=True)
    suggestions = []
    for record in agents:
        # Never re-propose what a rule already decides for this agent: a
        # suggestion is only useful for behaviour nobody has ruled on yet.
        covered = [pattern for rule in existing if rule.scope.covers_agent(record.id)
                   for pattern in (*rule.allow, *rule.ask, *rule.never)]
        observed = {action: count for action, count in record.requested_authority.items()
                    if not action_matches(covered, action)}
        if not observed:
            continue
        denied = {action: count for action, count in record.denied_authority.items()
                  if not action_matches(covered, action)}
        suggestions.append(suggest_rule(
            project_id=project_id, observed=observed,
            denied=denied, resources=list(record.resources), agent=record.id))
    return {"suggestions": suggestions}
