"""Outbound approval webhook.

When ``APPROVAL_WEBHOOK_URL`` is set, every approval lifecycle event
(``approval.requested``, ``approval.approved``, ``approval.rejected``) is
POSTed as JSON to that URL from a background thread. The body is signed with
HMAC-SHA256 over the raw bytes using ``AUDIT_SIGNING_KEY`` and sent as
``X-AgentPlane-Signature: sha256=<hex>``; receivers should verify it before
acting. Delivery is best-effort: a failed webhook never blocks or changes an
authorization decision, and pending approvals stay queryable at
``GET /v1/approvals``.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import threading
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import httpx

logger = logging.getLogger("agent_plane.approvals")

Sender = Callable[[str, bytes, dict[str, str]], None]


def _default_sender(url: str, body: bytes, headers: dict[str, str]) -> None:
    with httpx.Client(timeout=5.0, trust_env=False) as client:
        response = client.post(url, content=body, headers=headers)
        response.raise_for_status()


class ApprovalNotifier:
    def __init__(self, url: str | None, signing_key: str, *, sender: Sender | None = None,
                 synchronous: bool = False):
        self.url = url
        self._key = signing_key.encode()
        self._sender = sender or _default_sender
        self._synchronous = synchronous  # tests; production delivery is threaded

    @property
    def enabled(self) -> bool:
        return bool(self.url)

    def signature(self, body: bytes) -> str:
        return "sha256=" + hmac.new(self._key, body, hashlib.sha256).hexdigest()

    def emit(self, event: str, approval: dict[str, Any]) -> None:
        if not self.url:
            return
        payload = {
            "schema": "agent-plane.approval.v1",
            "event": event,
            "emitted_at": datetime.now(UTC).isoformat(),
            "approval": approval,
        }
        body = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        headers = {
            "Content-Type": "application/json",
            "X-AgentPlane-Event": event,
            "X-AgentPlane-Signature": self.signature(body),
        }

        def deliver() -> None:
            try:
                self._sender(self.url, body, headers)
            except Exception:  # noqa: BLE001 - never let delivery affect a decision
                logger.warning("approval webhook delivery failed event=%s id=%s",
                               event, approval.get("id"), exc_info=True)

        if self._synchronous:
            deliver()
        else:
            threading.Thread(target=deliver, name="approval-webhook", daemon=True).start()


def verify_signature(signing_key: str, body: bytes, header: str | None) -> bool:
    """Receiver-side helper: constant-time check of ``X-AgentPlane-Signature``."""
    if not header or not header.startswith("sha256="):
        return False
    expected = hmac.new(signing_key.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(header[len("sha256="):], expected)
