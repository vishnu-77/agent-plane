"""Usage reporting endpoint — the data a billing system would meter on.

``GET /v1/usage`` returns the caller's tenant usage (calls + units per resource).
If a price book is configured, an estimated cost is attached — but this is
metering, not billing: no payment is taken here.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import yaml
from fastapi import APIRouter, Header, HTTPException, Request

from agent_plane.config import Settings
from agent_plane.gateway.identity import IdentityError, resolve_identity

usage_router = APIRouter()

_DEFAULT_PRICING_FILE = "config/pricing.yaml"


def _load_pricing(settings: Settings) -> dict[str, Any] | None:
    path = settings.pricing_file or (
        _DEFAULT_PRICING_FILE if Path(_DEFAULT_PRICING_FILE).exists() else None
    )
    if not path:
        return None
    doc = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    return doc.get("pricing", doc)


def _cost(item: dict[str, Any], pricing: dict[str, Any]) -> float:
    defaults = pricing.get("default", {})
    if item["edge"] == "model":
        rate = (pricing.get("models", {}).get(item["resource"], {})).get(
            "per_1k_units", defaults.get("model_per_1k_units", 0.0)
        )
        return round(item["units"] / 1000 * rate, 6)
    rate = (pricing.get("tools", {}).get(item["resource"], {})).get(
        "per_call", defaults.get("tool_per_call", 0.0)
    )
    return round(item["calls"] * rate, 6)


@usage_router.get("/v1/usage")
async def get_usage(
    request: Request,
    hours: int | None = None,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    settings: Settings = request.app.state.settings
    try:
        actor = resolve_identity(authorization, settings, request.app.state.revocations)
    except IdentityError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc

    since = None
    if hours:
        since = datetime.now(UTC) - timedelta(hours=hours)

    items = request.app.state.usage.summary(actor.tenant, since)
    pricing = _load_pricing(settings)
    totals = {"calls": sum(i["calls"] for i in items), "units": sum(i["units"] for i in items)}

    if pricing:
        for i in items:
            i["estimated_cost"] = _cost(i, pricing)
        totals["estimated_cost"] = round(sum(i["estimated_cost"] for i in items), 6)
        totals["currency"] = pricing.get("currency", "USD")

    return {
        "tenant": actor.tenant,
        "since": since.isoformat() if since else None,
        "items": items,
        "totals": totals,
    }
