from __future__ import annotations

from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    if text.count(old) != 1:
        raise RuntimeError(f"{path}: expected one marker, found {text.count(old)}: {old[:80]!r}")
    p.write_text(text.replace(old, new, 1), encoding="utf-8")


def insert_after(path: str, marker: str, text: str) -> None:
    replace_once(path, marker, marker + text)


# ---------------------------------------------------------------------------
# FastAPI wiring + cacheable static shell
# ---------------------------------------------------------------------------
insert_after(
    "agent_plane/main.py",
    "from agent_plane.config import Settings, get_settings\n",
    "from agent_plane.context import build_context_store\nfrom agent_plane.context.router import context_router\n",
)
insert_after(
    "agent_plane/main.py",
    "from agent_plane.gateway.broker import broker_router\n",
    "from agent_plane.gateway.console_api import console_api_router\n",
)
replace_once(
    "agent_plane/main.py",
    "        app.state.agent_registry = build_registry(settings)\n        app.state.accounts = build_account_store(settings)\n",
    "        app.state.agent_registry = build_registry(settings)\n        app.state.context_store = build_context_store(settings)\n        app.state.accounts = build_account_store(settings)\n",
)
replace_once(
    "agent_plane/main.py",
    '''    @app.get("/", include_in_schema=False)\n    async def root() -> RedirectResponse:\n        return RedirectResponse("/console")\n''',
    '''    @app.get("/", include_in_schema=False)\n    async def root() -> Response:\n        # The shell is user-independent: cache it at the CDN so a Vercel cold\n        # start does not sit in front of every landing-page navigation. The\n        # browser still revalidates; hashed JS/CSS carry the long immutable cache.\n        index = files("agent_plane.console") / "dist" / "index.html"\n        if index.is_file():\n            return HTMLResponse(\n                index.read_text(encoding="utf-8"),\n                headers={"Cache-Control": "public, max-age=0, s-maxage=300, stale-while-revalidate=86400"},\n            )\n        return RedirectResponse("/console")\n''',
)
replace_once(
    "agent_plane/main.py",
    '            return HTMLResponse(dist_index.read_text(encoding="utf-8"))\n',
    '            return HTMLResponse(dist_index.read_text(encoding="utf-8"), headers={"Cache-Control": "public, max-age=0, s-maxage=300, stale-while-revalidate=86400"})\n',
)
replace_once(
    "agent_plane/main.py",
    '            return HTMLResponse(dist_index.read_text(encoding="utf-8"))\n        raise HTTPException(status_code=404, detail="not found")\n',
    '            return HTMLResponse(dist_index.read_text(encoding="utf-8"), headers={"Cache-Control": "public, max-age=0, s-maxage=300, stale-while-revalidate=86400"})\n        raise HTTPException(status_code=404, detail="not found")\n',
)
replace_once(
    "agent_plane/main.py",
    "    app.include_router(registry_router)\n    app.include_router(accounts_router)\n",
    "    app.include_router(registry_router)\n    app.include_router(context_router)\n    app.include_router(console_api_router)\n    app.include_router(accounts_router)\n",
)

# ---------------------------------------------------------------------------
# One-request auth boot + register connected integration as context
# ---------------------------------------------------------------------------
replace_once(
    "agent_plane/gateway/accounts_router.py",
    '''@accounts_router.get("/v1/auth/state")\nasync def auth_state(request: Request) -> dict[str, Any]:\n    """What the sign-in screen needs before anyone has an account."""\n    settings = request.app.state.settings\n    accounts = _store(request)\n    users = accounts.user_count()\n    return {\n        "users": users,\n        "signup_open": settings.signup_mode == "open" or (settings.signup_mode == "first_user" and users == 0),\n        "first_run": users == 0,\n        "demo_available": settings.demo_enabled,\n        "demo_token": settings.demo_token if settings.demo_enabled else None,\n        "sso_available": settings.oidc_enabled,\n        "password_login": settings.password_login_enabled,\n    }\n''',
    '''def _auth_state_payload(request: Request) -> dict[str, Any]:\n    settings = request.app.state.settings\n    accounts = _store(request)\n    users = accounts.user_count()\n    return {\n        "users": users,\n        "signup_open": settings.signup_mode == "open" or (settings.signup_mode == "first_user" and users == 0),\n        "first_run": users == 0,\n        "demo_available": settings.demo_enabled,\n        "demo_token": settings.demo_token if settings.demo_enabled else None,\n        "sso_available": settings.oidc_enabled,\n        "password_login": settings.password_login_enabled,\n    }\n\n\ndef _me_payload(request: Request, user: User) -> dict[str, Any]:\n    accounts = _store(request)\n    workspaces = accounts.workspaces_for(user.id)\n    projects = [_project_view(request, p) for p in accounts.projects_for(user.id)]\n    return {\n        "user": user.model_dump(mode="json"),\n        "workspaces": [w.model_dump(mode="json") for w in workspaces],\n        "projects": projects,\n        "onboarded": any(p["connected"] for p in projects),\n    }\n\n\n@accounts_router.get("/v1/auth/state")\nasync def auth_state(request: Request) -> dict[str, Any]:\n    """What the sign-in screen needs before anyone has an account."""\n    return _auth_state_payload(request)\n\n\n@accounts_router.get("/v1/auth/bootstrap")\nasync def auth_bootstrap(request: Request) -> dict[str, Any]:\n    """One cold-start-friendly request for auth state plus the current session."""\n    state = _auth_state_payload(request)\n    try:\n        user = _current_user(request)\n    except HTTPException as exc:\n        if exc.status_code != 401:\n            raise\n        return {"state": state, "me": None}\n    return {"state": state, "me": _me_payload(request, user)}\n''',
)
replace_once(
    "agent_plane/gateway/accounts_router.py",
    '''@accounts_router.get("/v1/auth/me")\nasync def me(request: Request) -> dict[str, Any]:\n    user = _current_user(request)\n    accounts = _store(request)\n    workspaces = accounts.workspaces_for(user.id)\n    projects = [_project_view(request, p) for p in accounts.projects_for(user.id)]\n    return {\n        "user": user.model_dump(mode="json"),\n        "workspaces": [w.model_dump(mode="json") for w in workspaces],\n        "projects": projects,\n        # The console uses this to decide whether to show onboarding.\n        "onboarded": any(p["connected"] for p in projects),\n    }\n''',
    '''@accounts_router.get("/v1/auth/me")\nasync def me(request: Request) -> dict[str, Any]:\n    return _me_payload(request, _current_user(request))\n''',
)
replace_once(
    "agent_plane/gateway/accounts_router.py",
    '''    integration = _store(request).upsert_integration(\n        project_id=project_id, kind=kind, name=str(body.get("name") or INTEGRATION_CATALOG[kind]["label"]),\n        host=body.get("host"), config=body.get("config") or {})\n    return {"integration": integration.model_dump(mode="json")}\n''',
    '''    integration = _store(request).upsert_integration(\n        project_id=project_id, kind=kind, name=str(body.get("name") or INTEGRATION_CATALOG[kind]["label"]),\n        host=body.get("host"), config=body.get("config") or {})\n    context_kind = "mcp" if kind == "mcp" else ("harness" if kind in {"claude-code", "codex", "cursor", "langgraph"} else "tool")\n    request.app.state.context_store.register_many(project_id, [{\n        "kind": context_kind, "name": integration.name,\n        "source": f"integration://{kind}/{integration.id}", "trust": "project-bound",\n        "influence": "high" if context_kind in {"mcp", "harness"} else "medium",\n        "metadata": {"integration_id": integration.id, "integration_kind": kind, "host": integration.host},\n    }])\n    return {"integration": integration.model_dump(mode="json")}\n''',
)
replace_once(
    "agent_plane/gateway/accounts_router.py",
    '''    integration = accounts.upsert_integration(\n        project_id=ctx.project.id, kind=kind,\n        name=str(body.get("name") or INTEGRATION_CATALOG[kind]["label"]),\n        host=ctx.host, config={"version": body.get("version")} if body.get("version") else None)\n    session_id = ctx.session or f"ses_{datetime.now(UTC).timestamp():.0f}"\n''',
    '''    integration = accounts.upsert_integration(\n        project_id=ctx.project.id, kind=kind,\n        name=str(body.get("name") or INTEGRATION_CATALOG[kind]["label"]),\n        host=ctx.host, config={"version": body.get("version")} if body.get("version") else None)\n    context_kind = "mcp" if kind == "mcp" else ("harness" if kind in {"claude-code", "codex", "cursor", "langgraph"} else "tool")\n    request.app.state.context_store.register_many(ctx.project.id, [{\n        "kind": context_kind, "name": integration.name,\n        "source": f"integration://{kind}/{integration.id}", "trust": "project-bound",\n        "influence": "high" if context_kind in {"mcp", "harness"} else "medium",\n        "metadata": {"integration_id": integration.id, "integration_kind": kind, "host": ctx.host},\n    }], agent=ctx.agent)\n    session_id = ctx.session or f"ses_{datetime.now(UTC).timestamp():.0f}"\n''',
)

# ---------------------------------------------------------------------------
# Event context registration + decision lineage
# ---------------------------------------------------------------------------
replace_once(
    "agent_plane/gateway/events_router.py",
    '''    if project.collects("tool_arguments") and isinstance(event.get("arguments"), dict):\n        context.setdefault("arguments_recorded", "true")\n\n    actor = ctx.actor.model_copy(update={"agent_id": agent})\n''',
    '''    if project.collects("tool_arguments") and isinstance(event.get("arguments"), dict):\n        context.setdefault("arguments_recorded", "true")\n\n    raw_assets = event.get("context_assets")\n    assets = [a for a in raw_assets if isinstance(a, dict)] if isinstance(raw_assets, list) else []\n    tool_name = str(event.get("tool") or action)[:200]\n    assets.append({\n        "kind": "mcp" if integration == "mcp" else "tool",\n        "name": tool_name,\n        "source": f"{integration}://{tool_name}",\n        "trust": "project-bound" if ctx.integration else "unknown",\n        "influence": "high",\n        "capabilities": [action],\n        "metadata": {"integration": integration},\n    })\n    registered_context = request.app.state.context_store.register_many(\n        project.id, assets, agent=agent, task=task)\n\n    actor = ctx.actor.model_copy(update={"agent_id": agent})\n''',
)
replace_once(
    "agent_plane/gateway/events_router.py",
    '''    request.app.state.accounts.touch_integration(\n        project_id=project.id, kind=integration, host=ctx.host, agent=agent)\n\n    catalog = INTEGRATION_CATALOG[integration]\n''',
    '''    request.app.state.context_store.link_decision(\n        project.id, result.decision_id, [a.id for a in registered_context], task=task, agent=agent)\n\n    request.app.state.accounts.touch_integration(\n        project_id=project.id, kind=integration, host=ctx.host, agent=agent)\n\n    catalog = INTEGRATION_CATALOG[integration]\n''',
)
replace_once(
    "agent_plane/gateway/events_router.py",
    '''        "binding": bool(result.enforced and enforcement in ("full", "partial")),\n    })\n''',
    '''        "binding": bool(result.enforced and enforcement in ("full", "partial")),\n        "context_assets": [a.id for a in registered_context],\n    })\n''',
)

# ---------------------------------------------------------------------------
# RAG: preserve existing authorization, attach source/doc provenance afterward
# ---------------------------------------------------------------------------
replace_once(
    "agent_plane/gateway/retrieval.py",
    '''    try:\n        actor = resolve_identity(authorization, settings, request.app.state.revocations)\n    except IdentityError as exc:\n        raise HTTPException(status_code=401, detail=str(exc)) from exc\n\n    # 2. Source-level decision (the deterministic engine can gate a whole source).\n''',
    '''    try:\n        actor = resolve_identity(authorization, settings, request.app.state.revocations)\n    except IdentityError as exc:\n        raise HTTPException(status_code=401, detail=str(exc)) from exc\n\n    source_spec = knowledge.get(source)\n    source_assets = request.app.state.context_store.register_many(actor.tenant, [{\n        "kind": "knowledge", "name": source, "source": f"rag://{source}",\n        "trust": "internal", "influence": "high",\n        "metadata": {"source_type": source_spec.type if source_spec else "unknown",\n                     "document_count": len(source_spec.documents) if source_spec else 0},\n    }], agent=actor.agent_id)\n\n    # 2. Source-level decision (the deterministic engine can gate a whole source).\n''',
)
replace_once(
    "agent_plane/gateway/retrieval.py",
    '''    if decision.decision == DecisionAction.DENY:\n        record()\n''',
    '''    if decision.decision == DecisionAction.DENY:\n        request.app.state.context_store.link_decision(\n            actor.tenant, decision.decision_id, [a.id for a in source_assets], agent=actor.agent_id)\n        record()\n''',
)
replace_once(
    "agent_plane/gateway/retrieval.py",
    '''    if decision.decision == DecisionAction.APPROVAL_REQUIRED:\n        record()\n''',
    '''    if decision.decision == DecisionAction.APPROVAL_REQUIRED:\n        request.app.state.context_store.link_decision(\n            actor.tenant, decision.decision_id, [a.id for a in source_assets], agent=actor.agent_id)\n        record()\n''',
)
replace_once(
    "agent_plane/gateway/retrieval.py",
    '''    record(redactions)\n    request.app.state.usage.record(\n''',
    '''    doc_assets = request.app.state.context_store.register_many(actor.tenant, [{\n        "kind": "knowledge",\n        "name": str(item.get("id") or "document"),\n        "source": f"rag://{source}/{item.get('id')}",\n        "trust": "internal",\n        "influence": "medium",\n        "metadata": {"classification": item.get("classification"), "retrieval_score": item.get("score")},\n    } for item in results], agent=actor.agent_id)\n    context_asset_ids = [a.id for a in source_assets + doc_assets]\n    request.app.state.context_store.link_decision(\n        actor.tenant, decision.decision_id, context_asset_ids, agent=actor.agent_id)\n\n    record(redactions)\n    request.app.state.usage.record(\n''',
)
replace_once(
    "agent_plane/gateway/retrieval.py",
    '''            "filtered_by_authorization": filtered_n,\n        },\n''',
    '''            "filtered_by_authorization": filtered_n,\n            "context_assets": context_asset_ids,\n        },\n''',
)

# ---------------------------------------------------------------------------
# Decision detail exposes the context lineage to the ACG
# ---------------------------------------------------------------------------
replace_once(
    "agent_plane/registry/router.py",
    '''    receipts = [e for e in request.app.state.audit.query(tenant=event["tenant"], limit=300)\n                if any(isinstance(o, dict) and o.get("schema") == "agent-plane.gateway.v1"\n                       and o.get("admission_id") == decision_id and o.get("phase") == "execution"\n                       for o in e.get("obligations_applied") or [])]\n    return {"decision_id": decision_id, "event": event, "trace": trace, "related": related, "receipts": receipts}\n''',
    '''    receipts = [e for e in request.app.state.audit.query(tenant=event["tenant"], limit=300)\n                if any(isinstance(o, dict) and o.get("schema") == "agent-plane.gateway.v1"\n                       and o.get("admission_id") == decision_id and o.get("phase") == "execution"\n                       for o in e.get("obligations_applied") or [])]\n    context_lineage = request.app.state.context_store.lineage(event["tenant"], decision_id)\n    context_assets = []\n    if context_lineage is not None:\n        context_assets = [request.app.state.context_store.asset(event["tenant"], asset_id)\n                          for asset_id in context_lineage.asset_ids]\n    return {\n        "decision_id": decision_id, "event": event, "trace": trace, "related": related, "receipts": receipts,\n        "context_lineage": {\n            "lineage": context_lineage.model_dump(mode="json") if context_lineage else None,\n            "assets": [asset.public() for asset in context_assets if asset is not None],\n        },\n    }\n''',
)

# ---------------------------------------------------------------------------
# Coding-agent discovery: hashes only, never instruction contents
# ---------------------------------------------------------------------------
insert_after(
    "agent_plane/connect/hook.py",
    "PENDING_CACHE_MAX_AGE_SECONDS = 3600\n",
    "CONTEXT_SCAN_INTERVAL_SECONDS = 30\nCONTEXT_FILE_LIMIT = 64\n",
)
insert_after(
    "agent_plane/connect/hook.py",
    '''def _pending_cache_path() -> Path:\n    override = os.environ.get("AGENTPLANE_HOME")\n    return (Path(override) if override else Path.home() / ".agentplane") / "pending-confirmations.json"\n\n''',
    '''def _context_cache_path() -> Path:\n    override = os.environ.get("AGENTPLANE_HOME")\n    return (Path(override) if override else Path.home() / ".agentplane") / "context-discovery.json"\n\n\ndef _repo_root(cwd: str) -> Path:\n    try:\n        root = subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=cwd, timeout=2,\n                              capture_output=True, text=True, check=False).stdout.strip()\n        if root:\n            return Path(root)\n    except (OSError, subprocess.SubprocessError):\n        pass\n    return Path(cwd)\n\n\ndef _context_file_asset(root: Path, path: Path, kind: str) -> dict[str, Any] | None:\n    try:\n        data = path.read_bytes()\n        stat = path.stat()\n        rel = path.relative_to(root).as_posix()\n    except (OSError, ValueError):\n        return None\n    return {\n        "kind": kind,\n        "name": path.parent.name if kind == "skill" and path.name == "SKILL.md" else path.name,\n        "source": f"repo://{rel}",\n        "digest": f"sha256:{hashlib.sha256(data).hexdigest()}",\n        "trust": "repository",\n        "influence": "high",\n        "metadata": {"path": rel, "size": stat.st_size, "mtime_ns": stat.st_mtime_ns},\n    }\n\n\ndef _discover_context(cwd: str, integration: str) -> list[dict[str, Any]]:\n    """Bounded repository discovery for context that can shape a coding agent.\n\n    Only path/hash/metadata leave the machine. Instruction and skill contents\n    are deliberately not sent to the control plane.\n    """\n    root = _repo_root(cwd)\n    key = str(root)\n    now = time.time()\n    cache_path = _context_cache_path()\n    cache: dict[str, Any] = {}\n    try:\n        cache = json.loads(cache_path.read_text(encoding="utf-8")) if cache_path.exists() else {}\n    except (OSError, ValueError):\n        cache = {}\n    repos = cache.get("repos") if isinstance(cache.get("repos"), dict) else {}\n    cached = repos.get(key) if isinstance(repos.get(key), dict) else None\n    if cached and now - float(cached.get("at", 0)) < CONTEXT_SCAN_INTERVAL_SECONDS:\n        assets = cached.get("assets")\n        if isinstance(assets, list):\n            return [a for a in assets if isinstance(a, dict)]\n\n    assets: list[dict[str, Any]] = [{\n        "kind": "harness", "name": integration, "source": f"harness://{integration}",\n        "trust": "project-bound", "influence": "high",\n        "metadata": {"workspace": root.name},\n    }]\n    for rel in ("AGENTS.md", "CLAUDE.md", ".claude/CLAUDE.md"):\n        path = root / rel\n        if path.is_file():\n            asset = _context_file_asset(root, path, "instruction")\n            if asset:\n                assets.append(asset)\n\n    skill_paths: list[Path] = []\n    for base in (root / ".claude" / "skills", root / ".agents" / "skills", root / ".codex" / "skills"):\n        if base.is_dir():\n            skill_paths.extend(sorted(base.glob("*/SKILL.md")))\n    for path in skill_paths[:CONTEXT_FILE_LIMIT]:\n        asset = _context_file_asset(root, path, "skill")\n        if asset:\n            assets.append(asset)\n\n    repos[key] = {"at": now, "assets": assets}\n    cache["repos"] = repos\n    try:\n        cache_path.parent.mkdir(parents=True, exist_ok=True)\n        cache_path.write_text(json.dumps(cache), encoding="utf-8")\n    except OSError:\n        pass\n    return assets\n\n''',
)
replace_once(
    "agent_plane/connect/hook.py",
    '''    if raw.get("prompt") or raw.get("user_prompt"):\n        # Sent only as provenance; the server drops the text unless the project\n        # explicitly collects prompt content.\n        event["origin"] = {"kind": "prompt", "ref": raw.get("prompt_id") or session,\n                           "text": raw.get("prompt") or raw.get("user_prompt")}\n    return event\n''',
    '''    if raw.get("prompt") or raw.get("user_prompt"):\n        # Sent only as provenance; the server drops the text unless the project\n        # explicitly collects prompt content.\n        event["origin"] = {"kind": "prompt", "ref": raw.get("prompt_id") or session,\n                           "text": raw.get("prompt") or raw.get("user_prompt")}\n    event["context_assets"] = _discover_context(cwd, integration)\n    return event\n''',
)

# ---------------------------------------------------------------------------
# Console API types + single-request bootstrap + real context inventory
# ---------------------------------------------------------------------------
insert_after(
    "console/src/lib/api.ts",
    '''export interface Me { user: User; workspaces: Workspace[]; projects: Project[]; onboarded: boolean }\n''',
    '''export interface AuthBootstrap { state: AuthState; me: Me | null }\n''',
)
insert_after(
    "console/src/lib/api.ts",
    '''export interface DecisionDetail {\n  decision_id: string;\n  event: { decision_id: string; created_at: string | null; event_hash: string; prev_hash: string | null; signature: string; policy_version: string | null };\n  trace: Trace | null;\n  related: Array<{ kind: string; approval: ApprovalRequest | null }>;\n  receipts: Array<{ decision_id: string; created_at: string | null; model_requested: string; reason: string }>;\n}\n''',
    '''\nexport interface ContextAsset {\n  id: string; tenant: string; kind: string; name: string; source: string; digest: string; previous_digest: string | null;\n  version: number; change_count: number; trust: string; influence: "low" | "medium" | "high";\n  capabilities: string[]; agents: string[]; tasks: string[]; provenance: Record<string, unknown>; metadata: Record<string, unknown>;\n  risk: Record<string, number>; exposure_score: number; first_seen: string; last_seen: string; changed_at: string;\n}\nexport interface ContextLineagePayload {\n  lineage: { tenant: string; decision_id: string; asset_ids: string[]; task: string | null; agent: string | null; created_at: string } | null;\n  assets: ContextAsset[];\n}\n''',
)
replace_once(
    "console/src/lib/api.ts",
    '''  receipts: Array<{ decision_id: string; created_at: string | null; model_requested: string; reason: string }>;\n}\n\nexport interface ContextAsset''',
    '''  receipts: Array<{ decision_id: string; created_at: string | null; model_requested: string; reason: string }>;\n  context_lineage?: ContextLineagePayload;\n}\n\nexport interface ContextAsset''',
)
insert_after(
    "console/src/lib/api.ts",
    '''  me: () => api<Me>("/v1/auth/me"),\n''',
    '''  authBootstrap: () => api<AuthBootstrap>("/v1/auth/bootstrap"),\n''',
)
insert_after(
    "console/src/lib/api.ts",
    '''  suggestedRules: (project: string, source: Source = "live") =>\n    api<{ suggestions: Array<Record<string, unknown>> }>(`/v1/rules/suggested?${q({ project })}`, { source }),\n''',
    '''\n  bootstrap: (project: string, source: Source = "live") =>\n    api<{ system: SystemState; decisions: DecisionSummary[]; agents: Agent[]; approvals: ApprovalRequest[] }>(\n      `/v1/console/bootstrap?${q({ project, decision_limit: 80 })}`, { source }),\n  contextAssets: (project: string, source: Source = "live") =>\n    api<{ assets: ContextAsset[]; count: number }>(`/v1/context/assets?${q({ project })}`, { source }),\n  contextChanges: (project: string, source: Source = "live") =>\n    api<{ changes: ContextAsset[]; count: number }>(`/v1/context/changes?${q({ project })}`, { source }),\n''',
)

# ---------------------------------------------------------------------------
# Store: one auth request, one feed request, never overlap polling
# ---------------------------------------------------------------------------
insert_after(
    "console/src/lib/store.tsx",
    "  const generation = useRef(0);\n",
    "  const refreshing = useRef(false);\n",
)
replace_once(
    "console/src/lib/store.tsx",
    '''  useEffect(() => {\n    (async () => {\n      try {\n        const state = await Api.authState();\n        setAuthState(state);\n        if (state.demo_token) setDemoToken(state.demo_token);\n      } catch {\n        setAuthState(null);\n      }\n      await refreshAccount();\n      setReady(true);\n    })();\n  }, [refreshAccount]);\n''',
    '''  useEffect(() => {\n    (async () => {\n      try {\n        const boot = await Api.authBootstrap();\n        setAuthState(boot.state);\n        if (boot.state.demo_token) setDemoToken(boot.state.demo_token);\n        setMe(boot.me);\n        setSessionEnded(false);\n        if (boot.me) {\n          setProjectId((current) => boot.me!.projects.some((p) => p.id === current)\n            ? current\n            : (boot.me!.projects[0]?.id ?? null));\n        }\n      } catch {\n        setAuthState(null);\n        setMe(null);\n      } finally {\n        setReady(true);\n      }\n    })();\n  }, []);\n''',
)
replace_once(
    "console/src/lib/store.tsx",
    '''    const gen = ++generation.current;\n    setFeed((f) => ({ ...f, loading: true }));\n    const results = await Promise.allSettled([\n      Api.system(project.id, source),\n      Api.decisions(project.id, 120, source),\n      Api.agents(project.id, source),\n      Api.approvals(project.id, source),\n    ]);\n    if (gen !== generation.current) return;\n    const [system, decisions, agents, approvals] = results;\n    const failure = results.find((r) => r.status === "rejected") as PromiseRejectedResult | undefined;\n\n    // A dead session must end the session, not be retried every few seconds.\n    // Polling with a project the server will no longer talk about turned one\n    // expired cookie into an unbounded stream of 401s.\n    const unauthorized = source === "live" && results.some(\n      (r) => r.status === "rejected" && (r.reason as ApiError)?.status === 401);\n    if (unauthorized) {\n      setMe(null);\n      setFeed(EMPTY);\n      setSessionEnded(true);\n      return;\n    }\n    setFeed({\n      system: system.status === "fulfilled" ? system.value : null,\n      decisions: decisions.status === "fulfilled" ? decisions.value.decisions : [],\n      agents: agents.status === "fulfilled" ? agents.value.agents : [],\n      approvals: approvals.status === "fulfilled" ? approvals.value.approvals : [],\n      error: system.status === "rejected" ? (failure?.reason as ApiError)?.message ?? "Cannot reach agent-plane" : null,\n      loading: false,\n      updatedAt: Date.now(),\n    });\n''',
    '''    if (refreshing.current) return;\n    refreshing.current = true;\n    const gen = ++generation.current;\n    setFeed((f) => ({ ...f, loading: true }));\n    try {\n      const next = await Api.bootstrap(project.id, source);\n      if (gen !== generation.current) return;\n      setFeed({ system: next.system, decisions: next.decisions, agents: next.agents, approvals: next.approvals,\n        error: null, loading: false, updatedAt: Date.now() });\n    } catch (e) {\n      if (gen !== generation.current) return;\n      const error = e as ApiError;\n      if (source === "live" && error.status === 401) {\n        setMe(null);\n        setFeed(EMPTY);\n        setSessionEnded(true);\n        return;\n      }\n      setFeed((f) => ({ ...f, error: error.message ?? "Cannot reach agent-plane", loading: false }));\n    } finally {\n      refreshing.current = false;\n    }\n''',
)

# ---------------------------------------------------------------------------
# Context UI: real registry, plus connected integration surfaces not yet seen
# ---------------------------------------------------------------------------
p = Path("console/src/pages/context.tsx")
p.write_text('''import { useEffect, useMemo, useState } from "react";\nimport { Api, type ContextAsset, type Integration } from "@/lib/api";\nimport { useStore } from "@/lib/store";\nimport { ago } from "@/lib/format";\nimport { Badge, Empty } from "@/components/ui";\n\nconst CATEGORY_ORDER = ["Instructions", "Skills", "Tools", "MCP", "Knowledge", "Memory", "Harness"] as const;\ntype Category = (typeof CATEGORY_ORDER)[number];\n\nfunction category(kind: string): Category {\n  if (kind === "instruction") return "Instructions";\n  if (kind === "skill") return "Skills";\n  if (kind === "mcp") return "MCP";\n  if (kind === "knowledge") return "Knowledge";\n  if (kind === "memory") return "Memory";\n  if (kind === "harness") return "Harness";\n  return "Tools";\n}\n\nfunction fallbackIntegration(item: Integration, tenant: string): ContextAsset {\n  const kind = item.kind === "mcp" ? "mcp" : ["claude-code", "codex", "cursor", "langgraph"].includes(item.kind) ? "harness" : "tool";\n  return {\n    id: `integration:${item.id}`, tenant, kind, name: item.label || item.name, source: item.host || item.kind, digest: "observed", previous_digest: null,\n    version: 1, change_count: 0, trust: item.status === "connected" ? "project-bound" : item.status, influence: kind === "mcp" || kind === "harness" ? "high" : "medium",\n    capabilities: [], agents: item.agents, tasks: [], provenance: {}, metadata: { integration_id: item.id }, risk: {},\n    exposure_score: kind === "mcp" ? 55 : kind === "harness" ? 45 : 30, first_seen: item.created_at, last_seen: item.last_seen_at || item.created_at, changed_at: item.created_at,\n  };\n}\n\nexport function ContextPage() {\n  const { project, source } = useStore();\n  const [registered, setRegistered] = useState<ContextAsset[]>([]);\n  const [integrations, setIntegrations] = useState<Integration[]>([]);\n  const [loading, setLoading] = useState(false);\n  const [error, setError] = useState<string | null>(null);\n\n  useEffect(() => {\n    if (!project) return;\n    let alive = true;\n    setLoading(true);\n    Promise.all([Api.contextAssets(project.id, source), Api.integrations(project.id, source)])\n      .then(([ctx, ints]) => { if (alive) { setRegistered(ctx.assets); setIntegrations(ints.integrations); setError(null); } })\n      .catch((e: Error) => alive && setError(e.message))\n      .finally(() => alive && setLoading(false));\n    return () => { alive = false; };\n  }, [project, source]);\n\n  const assets = useMemo(() => {\n    if (!project) return [];\n    const bySource = new Map(registered.map((a) => [a.source, a]));\n    const ids = new Set(registered.map((a) => String(a.metadata?.integration_id || "")));\n    const fallback = integrations.filter((i) => !ids.has(i.id)).map((i) => fallbackIntegration(i, project.id));\n    for (const a of fallback) if (!bySource.has(a.source)) bySource.set(a.source, a);\n    return [...bySource.values()].sort((a, b) => b.exposure_score - a.exposure_score);\n  }, [project, registered, integrations]);\n\n  const counts = Object.fromEntries(CATEGORY_ORDER.map((c) => [c, assets.filter((a) => category(a.kind) === c).length])) as Record<Category, number>;\n  const changed = assets.filter((a) => a.change_count > 0).length;\n\n  if (!project) return <Empty title="No project yet">Create a project before building its context inventory.</Empty>;\n\n  return (\n    <div className="mx-auto max-w-6xl px-4 py-6">\n      <div className="mb-5 flex flex-wrap items-end justify-between gap-3">\n        <div><div className="eyebrow">Context</div><h1 className="mt-1 text-lg font-medium tracking-tight">Influence surface</h1>\n          <p className="mt-1 max-w-2xl text-sm text-ink-2">What can shape an agent before it acts: repository instructions, skills, tools, MCP, RAG, memory and harnesses. Capability and influence never grant authority.</p></div>\n        <div className="font-mono text-2xs text-ink-2">{loading ? "discovering…" : `${assets.length} assets · ${changed} changed`}</div>\n      </div>\n      {error ? <div className="mb-4 rounded border border-deny/30 bg-deny-bg px-3 py-2 text-xs text-deny">{error}</div> : null}\n      <div className="mb-4 grid grid-cols-2 gap-3 sm:grid-cols-4 lg:grid-cols-7">\n        {CATEGORY_ORDER.map((c) => <div key={c} className="rounded border border-hairline bg-paper-raised px-3 py-3"><div className="eyebrow">{c}</div><div className="mt-2 font-mono text-xl">{counts[c]}</div><div className="mt-1 text-2xs text-ink-3">discovered</div></div>)}\n      </div>\n      <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_300px]">\n        <section className="panel overflow-hidden">\n          <div className="grid grid-cols-[92px_minmax(0,1fr)_105px_84px_62px] gap-3 border-b border-hairline bg-paper-sunk px-4 py-2 font-mono text-2xs uppercase tracking-[0.12em] text-ink-2"><span>Type</span><span>Name / source</span><span>Trust</span><span>Influence</span><span>Exposure</span></div>\n          {assets.length ? assets.map((asset) => <div key={asset.id} className="grid grid-cols-[92px_minmax(0,1fr)_105px_84px_62px] gap-3 border-b border-hairline px-4 py-3 text-sm last:border-b-0">\n            <span className="font-mono text-xs">{category(asset.kind)}</span>\n            <span className="min-w-0"><span className="block truncate font-medium">{asset.name}</span><span className="block truncate font-mono text-2xs text-ink-2">{asset.source}</span><span className="mt-1 block text-2xs text-ink-3">v{asset.version}{asset.change_count ? ` · changed ${asset.change_count}×` : ""} · seen {ago(asset.last_seen)}</span></span>\n            <span className="font-mono text-2xs text-ink-2">{asset.trust}</span><span><Badge tone={asset.influence === "high" ? "hold" : "neutral"}>{asset.influence}</Badge></span>\n            <span className="font-mono text-xs">{asset.exposure_score}</span>\n          </div>) : <div className="px-4 py-10 text-center"><div className="text-sm font-medium">No context assets discovered yet.</div><p className="mx-auto mt-2 max-w-lg text-sm text-ink-2">Connect a runtime and use it. Coding-agent hooks register AGENTS.md / CLAUDE.md / SKILL.md hashes; runtime actions, RAG and memory add their own lineage.</p></div>}\n        </section>\n        <aside className="space-y-3">\n          <div className="rounded border border-hairline bg-paper-raised p-4"><div className="eyebrow">Principle</div><div className="mt-2 text-sm font-medium">Capability ≠ Authority</div><p className="mt-1 text-xs leading-5 text-ink-2">Context may influence behaviour or expose capability. Access remains the explicit permission boundary.</p></div>\n          <div className="rounded border border-hairline bg-paper-raised p-4"><div className="eyebrow">Exposure score</div><p className="mt-2 text-xs leading-5 text-ink-2">0–100 ranks attention using provenance, integrity, influence, persistence, privilege amplification, external reach, sensitivity, propagation and reversibility. It never directly allows or blocks an action.</p></div>\n          <div className="rounded border border-hairline bg-paper-raised p-4"><div className="eyebrow">Privacy</div><p className="mt-2 text-xs leading-5 text-ink-2">Repository instruction and skill discovery sends path, hash and metadata — not file contents. RAG lineage records authorised document references, not a second copy of the knowledge base.</p></div>\n        </aside>\n      </div>\n    </div>\n  );\n}\n''', encoding="utf-8")

# ---------------------------------------------------------------------------
# ACG v2: context becomes upstream graph nodes
# ---------------------------------------------------------------------------
replace_once(
    "console/src/components/graph.tsx",
    'import type { Agent, Consequence, LineageLink, Trace } from "@/lib/api";\n',
    'import type { Agent, Consequence, ContextAsset, LineageLink, Trace } from "@/lib/api";\n',
)
replace_once(
    "console/src/components/graph.tsx",
    'export type Layer = "origin" | "task" | "agent" | "authority" | "action" | "resource" | "effect" | "downstream" | "consequence" | "decision";\n',
    'export type Layer = "context" | "origin" | "task" | "agent" | "authority" | "action" | "resource" | "effect" | "downstream" | "consequence" | "decision";\n',
)
replace_once(
    "console/src/components/graph.tsx",
    'export const LAYERS: Layer[] = ["origin", "task", "agent", "authority", "action", "resource", "effect", "downstream", "consequence", "decision"];\nconst BAND_OF: Record<Layer, 0 | 1> = { origin: 0, task: 0, agent: 0, authority: 0, action: 0, resource: 0, effect: 1, downstream: 1, consequence: 1, decision: 1 };\nconst LAYER_TITLE: Record<Layer, string> = {\n  origin: "ORIGIN",\n',
    'export const LAYERS: Layer[] = ["context", "origin", "task", "agent", "authority", "action", "resource", "effect", "downstream", "consequence", "decision"];\nconst BAND_OF: Record<Layer, 0 | 1> = { context: 0, origin: 0, task: 0, agent: 0, authority: 0, action: 0, resource: 0, effect: 1, downstream: 1, consequence: 1, decision: 1 };\nconst LAYER_TITLE: Record<Layer, string> = {\n  context: "CONTEXT",\n  origin: "ORIGIN",\n',
)
replace_once(
    "console/src/components/graph.tsx",
    'export function graphFromTrace(trace: Trace, siblings: Agent[] = [], opts: { lineageOnly?: boolean } = {}): Graph {\n',
    'export function graphFromTrace(trace: Trace, siblings: Agent[] = [], opts: { lineageOnly?: boolean; contextAssets?: ContextAsset[] } = {}): Graph {\n',
)
replace_once(
    "console/src/components/graph.tsx",
    '''  const task = add({ id: `task:${trace.task.id}`, layer: "task", label: trace.task.id, sub: trace.identity.tenant, onPath: true });\n  edges.push({ from: origin, to: task, kind: "lineage", onPath: true });\n\n  // Lineage: ancestors of the acting agent, in order, then the agent itself.\n''',
    '''  const task = add({ id: `task:${trace.task.id}`, layer: "task", label: trace.task.id, sub: trace.identity.tenant, onPath: true });\n  edges.push({ from: origin, to: task, kind: "lineage", onPath: true });\n  for (const asset of (opts.contextAssets ?? []).slice(0, 8)) {\n    const id = add({ id: `ctx:${asset.id}`, layer: "context", label: asset.name,\n      sub: `${asset.kind} · ${asset.trust} · exposure ${asset.exposure_score}`, onPath: true,\n      tone: asset.exposure_score >= 70 ? "hold" : "neutral", data: asset });\n    edges.push({ from: id, to: task, kind: "lineage", onPath: true, label: "influenced" });\n  }\n\n  // Lineage: ancestors of the acting agent, in order, then the agent itself.\n''',
)
replace_once(
    "console/src/pages/graph.tsx",
    '  const execution = useMemo(() => (trace ? graphFromTrace(trace, siblings) : null), [trace, siblings]);\n',
    '  const contextAssets = detail?.context_lineage?.assets ?? [];\n  const execution = useMemo(() => (trace ? graphFromTrace(trace, siblings, { contextAssets }) : null), [trace, siblings, contextAssets]);\n',
)
replace_once(
    "console/src/pages/graph.tsx",
    '''              <div><div className="eyebrow">Context captured</div><div className="mt-1">{Object.keys(trace.context ?? {}).length} field{Object.keys(trace.context ?? {}).length === 1 ? "" : "s"}</div></div>\n''',
    '''              <div><div className="eyebrow">Context lineage</div><div className="mt-1">{contextAssets.length} asset{contextAssets.length === 1 ? "" : "s"}</div></div>\n''',
)
replace_once(
    "console/src/pages/graph.tsx",
    '              <p className="text-xs leading-5 text-ink-2">Select any graph node to inspect it. Context assets will become upstream nodes as Context adapters register provenance.</p>\n',
    '              <p className="text-xs leading-5 text-ink-2">Select any graph node to inspect it. Context nodes show what could influence the task; authority remains a separate boundary downstream.</p>\n',
)
replace_once(
    "console/src/components/decision.tsx",
    '  const graph = useMemo(() => (trace ? graphFromTrace(trace, siblings) : null), [trace, siblings]);\n',
    '  const contextAssets = detail?.context_lineage?.assets ?? [];\n  const graph = useMemo(() => (trace ? graphFromTrace(trace, siblings, { contextAssets }) : null), [trace, siblings, contextAssets]);\n',
)

# ---------------------------------------------------------------------------
# Context-store query order + conservative memory declared impact
# ---------------------------------------------------------------------------
replace_once(
    "agent_plane/context/store.py",
    '''            stmt = select(AssetRow).order_by(AssetRow.updated_at.desc()).limit(limit)\n            if tenant is not None:\n                stmt = stmt.where(AssetRow.tenant == tenant)\n            rows = session.scalars(stmt).all()\n''',
    '''            stmt = select(AssetRow).order_by(AssetRow.updated_at.desc())\n            if tenant is not None:\n                stmt = stmt.where(AssetRow.tenant == tenant)\n            rows = session.scalars(stmt.limit(limit)).all()\n''',
)
replace_once(
    "agent_plane/context/router.py",
    '''        impact=str(body.get("impact") or ("reversible" if operation == "write" else "read")),\n''',
    '''        impact=str(body.get("impact") or "reversible"),\n''',
)

print("context runtime patch applied")
