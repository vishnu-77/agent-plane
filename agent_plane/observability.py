"""Operational plumbing: structured logs and a dependency-free /metrics.

* ``LOG_FORMAT=json`` emits one JSON object per line (timestamp, level,
  logger, message, plus any ``extra`` fields) for log shippers.
* ``/metrics`` renders Prometheus text exposition from in-process counters.
  Counters are per replica; scrape every pod. No auth is applied here, so
  restrict the path at the ingress or network policy in production.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from collections import defaultdict
from datetime import UTC, datetime
from typing import Any

from starlette.responses import PlainTextResponse

_STANDARD_ATTRS = set(logging.LogRecord("", 0, "", 0, "", (), None).__dict__) | {"message", "asctime"}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key not in _STANDARD_ATTRS and not key.startswith("_"):
                payload[key] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def configure_logging(level_name: str, log_format: str) -> None:
    level = getattr(logging, level_name.upper(), logging.INFO)
    root = logging.getLogger()
    root.setLevel(level)
    if log_format == "json":
        handler = logging.StreamHandler()
        handler.setFormatter(JsonFormatter())
        root.handlers[:] = [handler]
    elif not root.handlers:
        logging.basicConfig(level=level, format="%(asctime)s %(levelname)s %(name)s %(message)s")


class Metrics:
    """Tiny counter/histogram registry rendered in Prometheus text format."""

    _BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0)

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._requests: dict[tuple[str, str, str], int] = defaultdict(int)
        self._decisions: dict[tuple[str, str, str], int] = defaultdict(int)
        self._latency: dict[str, list[float]] = defaultdict(lambda: [0.0] * (len(self._BUCKETS) + 1))
        self._latency_sum: dict[str, float] = defaultdict(float)
        self._latency_count: dict[str, int] = defaultdict(int)
        self.started = time.time()

    def observe_request(self, method: str, path: str, status: int, seconds: float) -> None:
        route = _route_label(path)
        with self._lock:
            self._requests[(method, route, str(status))] += 1
            buckets = self._latency[route]
            for i, bound in enumerate(self._BUCKETS):
                if seconds <= bound:
                    buckets[i] += 1
            buckets[-1] += 1  # +Inf
            self._latency_sum[route] += seconds
            self._latency_count[route] += 1

    def observe_decision(self, edge: str, decision: str, reason: str) -> None:
        with self._lock:
            self._decisions[(edge, decision, reason)] += 1

    def render(self) -> str:
        lines = [
            "# HELP agent_plane_uptime_seconds Seconds since this replica started.",
            "# TYPE agent_plane_uptime_seconds gauge",
            f"agent_plane_uptime_seconds {time.time() - self.started:.0f}",
            "# HELP agent_plane_http_requests_total HTTP requests by method, route, status.",
            "# TYPE agent_plane_http_requests_total counter",
        ]
        with self._lock:
            for (method, route, status), count in sorted(self._requests.items()):
                lines.append(
                    f'agent_plane_http_requests_total{{method="{method}",route="{route}",status="{status}"}} {count}'
                )
            lines += [
                "# HELP agent_plane_decisions_total Authority/policy decisions by edge, decision, reason.",
                "# TYPE agent_plane_decisions_total counter",
            ]
            for (edge, decision, reason), count in sorted(self._decisions.items()):
                lines.append(
                    f'agent_plane_decisions_total{{edge="{edge}",decision="{decision}",reason="{reason}"}} {count}'
                )
            lines += [
                "# HELP agent_plane_http_request_seconds Request latency by route.",
                "# TYPE agent_plane_http_request_seconds histogram",
            ]
            for route, buckets in sorted(self._latency.items()):
                for bound, count in zip(self._BUCKETS, buckets[:-1], strict=True):
                    lines.append(
                        f'agent_plane_http_request_seconds_bucket{{route="{route}",le="{bound}"}} {int(count)}'
                    )
                lines.append(
                    f'agent_plane_http_request_seconds_bucket{{route="{route}",le="+Inf"}} {int(buckets[-1])}'
                )
                lines.append(
                    f'agent_plane_http_request_seconds_sum{{route="{route}"}} {self._latency_sum[route]:.6f}'
                )
                lines.append(
                    f'agent_plane_http_request_seconds_count{{route="{route}"}} {self._latency_count[route]}'
                )
        return "\n".join(lines) + "\n"

    def response(self) -> PlainTextResponse:
        return PlainTextResponse(self.render(), media_type="text/plain; version=0.0.4; charset=utf-8")


def _route_label(path: str) -> str:
    """Collapse ids so label cardinality stays bounded."""
    parts = path.strip("/").split("/")
    out: list[str] = []
    for i, part in enumerate(parts):
        prev = parts[i - 1] if i else ""
        if prev in {"leases", "approvals", "agents"} and part not in {"from-template", "delegate"}:
            out.append("{id}")
        else:
            out.append(part)
    return "/" + "/".join(out)
