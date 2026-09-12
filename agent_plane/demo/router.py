"""Demo endpoints (enabled by DEMO_ENABLED).

    GET  /demo/scenarios                       the three scenarios, described
    POST /demo/reset                           clear the demo tenant and simulated targets
    POST /demo/scenarios/{name}/run            run all steps (or {"steps": [0, 1]})
    GET  /demo/targets                         simulated target state

Runs are rate-limited by the global per-client limiter and touch only the
isolated ``demo`` tenant. Any caller may run them: that is the point of a
hosted demo. Reads of what they produced go through the registry and
decision endpoints with X-Demo-Token.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request

demo_router = APIRouter(prefix="/demo", tags=["demo"])


def _harness(request: Request):
    harness = getattr(request.app.state, "demo", None)
    if harness is None:
        raise HTTPException(status_code=404, detail="demo mode is disabled")
    return harness


@demo_router.get("/scenarios")
async def list_scenarios(request: Request) -> dict[str, Any]:
    harness = _harness(request)
    return {"scenarios": harness.describe(), "tenant": "demo",
            "token_header": "X-Demo-Token", "token": request.app.state.settings.demo_token}


@demo_router.post("/reset")
async def reset(request: Request) -> dict[str, Any]:
    return _harness(request).reset()


@demo_router.post("/scenarios/{name}/run")
async def run_scenario(request: Request, name: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
    harness = _harness(request)
    body = body or {}
    steps = body.get("steps")
    if steps is not None and (not isinstance(steps, list) or not all(isinstance(s, int) for s in steps)):
        raise HTTPException(status_code=400, detail="'steps' must be a list of step indexes")
    run_id = body.get("run_id")
    if run_id is not None and (not isinstance(run_id, str) or not run_id.isalnum() or len(run_id) > 12):
        raise HTTPException(status_code=400, detail="'run_id' must be a short alphanumeric string")
    try:
        return harness.run(name, steps=steps, run_id=run_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="unknown scenario") from None


@demo_router.get("/targets")
async def targets(request: Request) -> dict[str, Any]:
    harness = _harness(request)
    return {"deployments": harness.targets.deployments, "branches": harness.targets.branches,
            "log": harness.targets.log[-50:]}
