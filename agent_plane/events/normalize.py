"""Raw tool invocation -> canonical (action, resource).

Coding agents report tools with their own names: ``Bash``, ``Edit``,
``str_replace_editor``, ``shell``. Authority is written against canonical
actions (``shell.execute``, ``filesystem.write``), so the connector's job is
to say what happened and this module's job is to say what that means.

A connector that already knows the canonical action sends it directly and
nothing here overrides it. Everything is derived from the tool name and the
argument *shape* - never from file contents or prompt text, which the
default collection policy does not even send.
"""
from __future__ import annotations

import posixpath
import re
import shlex
from typing import Any
from urllib.parse import urlsplit

# tool name (lowercased) -> canonical action
TOOL_ACTIONS: dict[str, str] = {
    # Claude Code / Codex / editors
    "bash": "shell.execute",
    "shell": "shell.execute",
    "run_command": "shell.execute",
    "terminal": "shell.execute",
    "execute": "shell.execute",
    "read": "filesystem.read",
    "read_file": "filesystem.read",
    "view": "filesystem.read",
    "cat": "filesystem.read",
    "glob": "filesystem.read",
    "grep": "filesystem.read",
    "search": "filesystem.read",
    "ls": "filesystem.read",
    "write": "filesystem.write",
    "write_file": "filesystem.write",
    "edit": "filesystem.write",
    "multiedit": "filesystem.write",
    "apply_patch": "filesystem.write",
    "str_replace_editor": "filesystem.write",
    "notebookedit": "filesystem.write",
    "create_file": "filesystem.write",
    "delete_file": "filesystem.delete",
    "remove_file": "filesystem.delete",
    "webfetch": "network.access",
    "websearch": "network.access",
    "fetch": "network.access",
    "http_request": "network.access",
}

# first token(s) of a shell command -> canonical action
COMMAND_ACTIONS: list[tuple[tuple[str, ...], str]] = [
    (("git", "push"), "git.push"),
    (("git", "commit"), "git.commit"),
    (("git", "clone"), "repository.read"),
    (("git", "branch", "-d"), "branch.delete"),
    (("git", "branch", "-D"), "branch.delete"),
    (("gh", "repo", "delete"), "repository.delete"),
    (("npm", "install"), "package.install"),
    (("npm", "i"), "package.install"),
    (("pnpm", "add"), "package.install"),
    (("yarn", "add"), "package.install"),
    (("pip", "install"), "package.install"),
    (("uv", "add"), "package.install"),
    (("poetry", "add"), "package.install"),
    (("npm", "test"), "tests.execute"),
    (("pytest",), "tests.execute"),
    (("jest",), "tests.execute"),
    (("vitest",), "tests.execute"),
    (("go", "test"), "tests.execute"),
    (("cargo", "test"), "tests.execute"),
    (("kubectl", "rollout"), "deployment.restart"),
    (("kubectl", "delete"), "deployment.delete"),
    (("terraform", "apply"), "config.write"),
    (("curl",), "network.access"),
    (("wget",), "network.access"),
]

_SECRET_FILE = re.compile(r"(^|/)\.env|(^|/)(id_rsa|id_ed25519)|\.pem$|credentials(\.json)?$", re.I)
_ARG_PATH_KEYS = ("file_path", "path", "filename", "file", "notebook_path", "target_file")
_ARG_COMMAND_KEYS = ("command", "cmd", "script", "input")
_ARG_URL_KEYS = ("url", "endpoint", "uri")


_HOME_PREFIX = re.compile(r"^(users|home)/[^/]+/", re.I)
_MAX_ABSOLUTE_SEGMENTS = 4


def _clean_path(value: str, workspace_root: str | None = None) -> str:
    """A workspace-relative, forward-slash path.

    Absolute local paths are a privacy problem as much as a tidiness one: a
    file path carries the developer's username and directory layout into
    shared evidence. If the connector tells us the workspace root we cut
    there; otherwise we drop the home prefix and keep only the tail.
    """
    path = value.replace("\\", "/").strip()
    root = (workspace_root or "").replace("\\", "/").strip().rstrip("/")
    absolute = path.startswith("/") or bool(re.match(r"^[A-Za-z]:/", path))
    if root and path.lower().startswith(root.lower() + "/"):
        path, absolute = path[len(root) + 1:], False
    else:
        for marker in ("/workspace/", "/repo/", "/src/"):
            if marker in path:
                path, absolute = path[path.index(marker) + 1:], False
                break
    path = re.sub(r"^[A-Za-z]:/", "", path).lstrip("/")
    path = _HOME_PREFIX.sub("", path)
    parts = [p for p in path.split("/") if p not in ("", ".", "..")]
    if absolute and len(parts) > _MAX_ABSOLUTE_SEGMENTS:
        parts = parts[-_MAX_ABSOLUTE_SEGMENTS:]
    return posixpath.join(*parts) if parts else "unknown"


def _repo_resource(repo: str | None, ref: str | None) -> str:
    base = f"github://{repo}" if repo else "repository/current"
    return f"{base}/branches/{ref}" if ref else base


def _command_action(command: str) -> tuple[str | None, str | None]:
    try:
        tokens = [t for t in shlex.split(command, posix=True) if not t.startswith("-")] or command.split()
    except ValueError:
        tokens = command.split()
    lowered = [t.lower() for t in tokens]
    for prefix, action in COMMAND_ACTIONS:
        if lowered[: len(prefix)] == [p.lower() for p in prefix]:
            return action, tokens[len(prefix)] if len(tokens) > len(prefix) else None
    return None, tokens[0] if tokens else None


def normalize_action(
    *,
    action: str | None = None,
    tool: str | None = None,
    resource: str | None = None,
    arguments: dict[str, Any] | None = None,
    repository: str | None = None,
    branch: str | None = None,
    workspace_root: str | None = None,
) -> tuple[str, str]:
    """Return ``(action, resource)``, both canonical.

    An explicit ``action`` always wins; an explicit ``resource`` always wins.
    Otherwise the tool name and argument shape decide.
    """
    arguments = arguments or {}
    resolved_action = (action or "").strip()
    resolved_resource = (resource or "").strip()

    tool_key = (tool or "").strip().lower().replace("-", "").replace(" ", "")
    command = next((str(arguments[k]) for k in _ARG_COMMAND_KEYS if isinstance(arguments.get(k), str)), None)
    path = next((str(arguments[k]) for k in _ARG_PATH_KEYS if isinstance(arguments.get(k), str)), None)
    url = next((str(arguments[k]) for k in _ARG_URL_KEYS if isinstance(arguments.get(k), str)), None)

    if not resolved_action:
        resolved_action = TOOL_ACTIONS.get(tool_key, "")
    # A shell tool is only as specific as the command it runs.
    if command and (not resolved_action or resolved_action == "shell.execute"):
        derived, _ = _command_action(command)
        if derived:
            resolved_action = derived
    if not resolved_action:
        resolved_action = f"{tool_key or 'tool'}.invoke"

    if not resolved_resource:
        if resolved_action in ("git.push", "git.commit", "branch.delete", "repository.delete",
                               "repository.read"):
            resolved_resource = _repo_resource(repository, branch if resolved_action != "repository.delete" else None)
        elif resolved_action == "package.install" and command:
            _, target = _command_action(command)
            resolved_resource = f"package/{target}" if target else "package/unknown"
        elif resolved_action == "network.access":
            host = urlsplit(url).hostname if url else None
            resolved_resource = f"network/{host or 'unknown'}"
        elif resolved_action.startswith("filesystem.") and path:
            cleaned = _clean_path(path, workspace_root)
            resolved_resource = ("workspace/.env" if _SECRET_FILE.search(cleaned)
                                 else f"workspace/{cleaned}")
        elif resolved_action in ("shell.execute", "tests.execute") and command:
            try:
                head = shlex.split(command, posix=True)[0]
            except (ValueError, IndexError):
                head = command.split()[0] if command.split() else "command"
            resolved_resource = f"shell/{posixpath.basename(head)}"
        elif path:
            resolved_resource = f"workspace/{_clean_path(path, workspace_root)}"
        else:
            resolved_resource = f"{resolved_action.split('.', 1)[0]}/unknown"

    return resolved_action, resolved_resource[:400]
