"""Device/browser authentication (Phase 17): removes manual key copying for
interactive developers.

    agentplane connect
          |
    POST /v1/device/start          (CLI, unauthenticated)
          |
    browser opens {verification_uri}
          |
    GET  /device?code=<user_code>   (human, must already have a console session)
          |
    POST /v1/device/approve         (human, session-cookie authenticated)
          |
    POST /v1/device/exchange        (CLI polls, unauthenticated)  ->  a real Project API Key

CI and workloads are unaffected - they keep using an explicit key with
`connect <target> --key ap_live_...`, unchanged.

Reuses the existing session-cookie auth (_current_user, _project_or_403)
and key minting (accounts.create_key) rather than inventing a parallel
identity/key system - this is a distribution convenience on top of the
same Project API Key model, not a new credential type.
"""
from __future__ import annotations

import secrets
import string
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse

from agent_plane.gateway.accounts_router import _current_user, _project_or_403, _store

device_router = APIRouter()

DEVICE_CODE_TTL_SECONDS = 600  # 10 minutes, matches the RFC 8628 device-flow convention
_USER_CODE_ALPHABET = string.ascii_uppercase.replace("O", "").replace("I", "")  # no 0/O, 1/I confusion


@dataclass
class DeviceCode:
    device_code: str
    user_code: str
    created_at: datetime
    expires_at: datetime
    status: Literal["pending", "approved", "denied", "consumed"] = "pending"
    project_id: str | None = None
    approved_by: str | None = None
    secret: str | None = None  # set on approval, cleared once exchanged (single-use)


class DeviceCodeStore:
    """In-memory, mirrors ContractRegistry/TrustDomainRegistry's precedent.

    ponytail: in-memory only - a device code is short-lived (10 min) by
    design, so losing pending codes on a restart is an acceptable "just
    run connect again", not a durability requirement worth a DB table for.
    """

    def __init__(self) -> None:
        self._by_device_code: dict[str, DeviceCode] = {}
        self._by_user_code: dict[str, str] = {}  # user_code -> device_code

    def start(self) -> DeviceCode:
        now = datetime.now(UTC)
        device_code = secrets.token_urlsafe(32)
        user_code = "-".join("".join(secrets.choice(_USER_CODE_ALPHABET) for _ in range(4)) for _ in range(2))
        record = DeviceCode(device_code=device_code, user_code=user_code, created_at=now,
                            expires_at=now + timedelta(seconds=DEVICE_CODE_TTL_SECONDS))
        self._by_device_code[device_code] = record
        self._by_user_code[user_code] = device_code
        return record

    def _expire_if_due(self, record: DeviceCode) -> DeviceCode:
        if record.status == "pending" and datetime.now(UTC) > record.expires_at:
            record.status = "denied"  # expired reads as denied to the CLI; distinguished by expires_at
        return record

    def by_user_code(self, user_code: str) -> DeviceCode | None:
        device_code = self._by_user_code.get(user_code.strip().upper())
        record = self._by_device_code.get(device_code) if device_code else None
        return self._expire_if_due(record) if record else None

    def by_device_code(self, device_code: str) -> DeviceCode | None:
        record = self._by_device_code.get(device_code)
        return self._expire_if_due(record) if record else None


def _view(record: DeviceCode) -> dict[str, Any]:
    return {"device_code": record.device_code, "user_code": record.user_code,
            "expires_in": max(0, int((record.expires_at - datetime.now(UTC)).total_seconds()))}


@device_router.post("/v1/device/start")
async def start_device(request: Request) -> dict[str, Any]:
    store: DeviceCodeStore = request.app.state.device_codes
    record = store.start()
    base = str(request.base_url).rstrip("/")
    return {**_view(record), "verification_uri": f"{base}/device?code={record.user_code}",
            "interval": 3}


@device_router.get("/device", response_class=HTMLResponse)
async def device_approval_page(request: Request, code: str = "") -> HTMLResponse:
    """Server-rendered, no build step - this is a one-screen approve/deny
    form, not a console feature, so it doesn't need the React app's design
    system. See the module docstring for why."""
    from agent_plane.accounts.security import SESSION_COOKIE, read_session
    settings = request.app.state.settings
    payload = read_session(request.cookies.get(SESSION_COOKIE), settings.api_key_secret)
    if not payload or not payload.get("sub"):
        return HTMLResponse(
            "<p>Sign in to the console first at <a href=\"/console\">/console</a>, "
            "then reopen this link.</p>", status_code=401)
    user = _store(request).user(str(payload["sub"]))
    if user is None:
        return HTMLResponse("<p>Session no longer valid. Sign in again at /console.</p>", status_code=401)
    store: DeviceCodeStore = request.app.state.device_codes
    record = store.by_user_code(code) if code else None
    if record is None or record.status != "pending":
        return HTMLResponse("<p>This connection code is invalid or has expired. "
                            "Run <code>agentplane connect</code> again.</p>", status_code=404)
    projects = _store(request).projects_for(user.id)
    options = "".join(f'<option value="{p.id}">{p.name}</option>' for p in projects)
    return HTMLResponse(f"""
<!doctype html><html><body style="font-family: system-ui; max-width: 32rem; margin: 4rem auto;">
<h1>Connect a new agent</h1>
<p>Code <b>{record.user_code}</b> wants to connect to Agent Plane.</p>
<form method="post" action="/v1/device/approve">
<input type="hidden" name="user_code" value="{record.user_code}">
<label>Project: <select name="project">{options}</select></label>
<button type="submit">Approve</button>
</form>
</body></html>""")


@device_router.post("/v1/device/approve")
async def approve_device(request: Request, body: dict[str, Any] | None = None) -> dict[str, Any]:
    user = _current_user(request)
    body = body or {}
    user_code = str(body.get("user_code") or "")
    project_id = str(body.get("project") or "")
    if not user_code or not project_id:
        raise HTTPException(status_code=400, detail="'user_code' and 'project' are required")
    _project_or_403(request, user, project_id)
    store: DeviceCodeStore = request.app.state.device_codes
    record = store.by_user_code(user_code)
    if record is None:
        raise HTTPException(status_code=404, detail="Unknown or expired code")
    if record.status != "pending":
        raise HTTPException(status_code=409, detail=f"Already {record.status}")
    key, plaintext = _store(request).create_key(
        project_id=project_id, name="agentplane connect (device)", created_by=user.id, environment="live")
    record.status, record.project_id, record.approved_by, record.secret = "approved", project_id, user.id, plaintext
    return {"approved": True, "project": project_id}


@device_router.post("/v1/device/deny")
async def deny_device(request: Request, body: dict[str, Any] | None = None) -> dict[str, Any]:
    _current_user(request)  # must be signed in, but any signed-in user may decline
    body = body or {}
    store: DeviceCodeStore = request.app.state.device_codes
    record = store.by_user_code(str((body or {}).get("user_code") or ""))
    if record is None:
        raise HTTPException(status_code=404, detail="Unknown or expired code")
    if record.status == "pending":
        record.status = "denied"
    return {"denied": True}


@device_router.post("/v1/device/exchange")
async def exchange_device(request: Request, body: dict[str, Any]) -> dict[str, Any]:
    """Polled by the CLI - no session, no API key, just the device_code it
    was handed by /v1/device/start. RFC 8628-flavored status vocabulary."""
    device_code = str((body or {}).get("device_code") or "")
    store: DeviceCodeStore = request.app.state.device_codes
    record = store.by_device_code(device_code)
    if record is None:
        raise HTTPException(status_code=404, detail={"error": "invalid_device_code"})
    if record.status == "pending":
        raise HTTPException(status_code=428, detail={"error": "authorization_pending"})
    if record.status == "denied":
        raise HTTPException(status_code=403, detail={"error": "access_denied"})
    if record.status == "consumed" or record.secret is None:
        raise HTTPException(status_code=410, detail={"error": "already_consumed"})
    secret, project_id = record.secret, record.project_id
    record.status, record.secret = "consumed", None  # single-use: never served twice
    return {"secret": secret, "project": project_id}
