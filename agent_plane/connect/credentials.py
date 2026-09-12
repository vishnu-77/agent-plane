"""Where a connector keeps its Project API Key.

Not in the editor's settings file, which people commit by accident. In
``~/.agentplane/credentials.json``, owner-readable only, one entry per
(url, integration) so one machine can serve several projects.
"""
from __future__ import annotations

import json
import os
import stat
from dataclasses import dataclass
from pathlib import Path

DEFAULT_URL = "http://127.0.0.1:8000"
ENV_KEY = "AGENTPLANE_API_KEY"
ENV_URL = "AGENTPLANE_URL"


def credentials_path() -> Path:
    override = os.environ.get("AGENTPLANE_HOME")
    return (Path(override) if override else Path.home() / ".agentplane") / "credentials.json"


@dataclass
class Credentials:
    url: str
    key: str
    integration: str = "custom"
    project: str | None = None
    agent: str | None = None

    def masked(self) -> str:
        return f"{self.key[:8]}{'*' * 12}{self.key[-4:]}" if len(self.key) > 12 else "****"


def _read_all(path: Path) -> dict[str, dict]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return {}
    return data if isinstance(data, dict) else {}


def save_credentials(creds: Credentials) -> Path:
    path = credentials_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    entries = _read_all(path)
    entries[f"{creds.url}#{creds.integration}"] = {
        "url": creds.url, "key": creds.key, "integration": creds.integration,
        "project": creds.project, "agent": creds.agent,
    }
    path.write_text(json.dumps(entries, indent=2) + "\n", encoding="utf-8")
    try:
        # Owner read/write only. Best effort: Windows ignores the group/other bits.
        path.chmod(stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass
    return path


def load_credentials(integration: str | None = None, url: str | None = None) -> Credentials | None:
    """Environment first (CI and containers), then the credentials file."""
    env_key = os.environ.get(ENV_KEY)
    if env_key:
        return Credentials(url=url or os.environ.get(ENV_URL, DEFAULT_URL), key=env_key,
                           integration=integration or "custom")
    entries = _read_all(credentials_path())
    if not entries:
        return None
    for entry in entries.values():
        if integration and entry.get("integration") != integration:
            continue
        if url and entry.get("url") != url:
            continue
        return Credentials(url=entry["url"], key=entry["key"],
                           integration=entry.get("integration", "custom"),
                           project=entry.get("project"), agent=entry.get("agent"))
    return None


def forget(integration: str, url: str | None = None) -> bool:
    """Forget an integration's stored credential.

    ``url`` narrows it to one control plane. Without it every entry for the
    integration goes: someone who connected to a non-default URL and then runs
    `disconnect` must not be told there was nothing to disconnect while the
    credential is still on disk.
    """
    path = credentials_path()
    entries = _read_all(path)
    doomed = [name for name, entry in entries.items()
              if entry.get("integration") == integration and (url is None or entry.get("url") == url)]
    if not doomed:
        return False
    for name in doomed:
        entries.pop(name, None)
    path.write_text(json.dumps(entries, indent=2) + chr(10), encoding="utf-8")
    return True
