"""Permissions as YAML: the same rules, in a file you can review and commit.

The console is the fast path, but a rule is a decision a team makes, and
decisions belong in version control next to the code they govern. This module
is the whole of that support:

    dump_rules(rules)   -> the YAML text for a project's rules
    load_rules(text)    -> validated rule payloads, ready for RuleStore

The shape is the one the Rules screen already speaks, and the one
``config/rule-templates.yaml`` is written in, so a template can be pasted into
a rules file unchanged:

    version: 1
    rules:
      - name: Coding agents
        scope:
          agents: ["claude-code"]
        allow: [filesystem.read, tests.execute]
        ask: [filesystem.write, git.push]
        never: [repository.delete]
        resources: ["workspace/*"]
        protected_resources: ["workspace/.env*"]

Nothing here decides anything. The file is compiled by the same path as a rule
written in the console: rules -> AuthorityLease -> the engine.
"""
from __future__ import annotations

from typing import Any

import yaml

from agent_plane.rules.store import AuthorityRule

FORMAT_VERSION = 1

HEADER = (
    "# agent-plane permissions.\n"
    "#\n"
    "# allow  runs without asking anyone\n"
    "# ask    runs only after a human approves it\n"
    "# never  is refused outright: no other rule, lease, or delegation\n"
    "#        can grant it back\n"
)

# Written in this order because it is the order someone reads a rule in.
_FIELD_ORDER = ["name", "scope", "allow", "ask", "never", "resources",
                "protected_resources", "max_uses", "permitted_consequence",
                "enabled", "order"]

_LIST_FIELDS = {"allow", "ask", "never", "resources", "protected_resources"}
_SCOPE_FIELDS = {"agents", "integrations", "environments"}


class _Inline(list):
    """A list dumped on one line. An action list is easier to read across than
    down, and this is a file people read more often than they edit."""


yaml.SafeDumper.add_representer(
    _Inline,
    lambda dumper, data: dumper.represent_sequence("tag:yaml.org,2002:seq", data, flow_style=True),
)


class RulesYamlError(ValueError):
    """The document is not a rules file, or a rule in it is malformed."""


# --------------------------------------------------------------------------- #
# dump
# --------------------------------------------------------------------------- #
def _rule_document(rule: AuthorityRule) -> dict[str, Any]:
    """One rule, with everything left at its default omitted.

    A file people edit should show what someone chose, not every field the
    model happens to carry.
    """
    body = rule.model_dump(mode="json")
    out: dict[str, Any] = {}
    for field in _FIELD_ORDER:
        value = body.get(field)
        if field == "scope":
            scope = {k: _Inline(v) for k, v in (value or {}).items() if v and v != ["*"]}
            if scope:
                out["scope"] = scope
            continue
        if field == "resources" and value == ["*"]:
            continue
        if field == "enabled":
            if value is False:          # only worth writing when it is off
                out["enabled"] = False
            continue
        if field == "order" and value == 100:
            continue
        if value in (None, [], {}, ""):
            continue
        out[field] = _Inline(value) if field in _LIST_FIELDS else value
    return out


def dump_rules(rules: list[AuthorityRule]) -> str:
    """The YAML text for ``rules``. Stable output: same rules, same bytes."""
    document = {"version": FORMAT_VERSION,
                "rules": [_rule_document(r) for r in rules]}
    body = yaml.safe_dump(document, sort_keys=False, default_flow_style=False,
                          allow_unicode=True, width=100)
    return f"{HEADER}\n{body}"


# --------------------------------------------------------------------------- #
# load
# --------------------------------------------------------------------------- #
def _strings(value: Any, *, where: str) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):          # a single action, written without a list
        return [value]
    if not isinstance(value, list) or any(not isinstance(v, str) for v in value):
        raise RulesYamlError(f"{where} must be a list of strings")
    return [v.strip() for v in value if v.strip()]


def _rule_payload(raw: Any, *, index: int) -> dict[str, Any]:
    where = f"rules[{index}]"
    if not isinstance(raw, dict):
        raise RulesYamlError(f"{where} must be a mapping")

    name = str(raw.get("name") or "").strip()
    if not name:
        raise RulesYamlError(f"{where} needs a name")

    unknown = set(raw) - set(_FIELD_ORDER) - {"id", "source", "key", "description"}
    if unknown:
        # Silence here would mean a typo quietly granting nothing, or worse,
        # a "never" that was never read.
        raise RulesYamlError(f"{where} has unknown field(s): {', '.join(sorted(unknown))}")

    scope_raw = raw.get("scope") or {}
    if not isinstance(scope_raw, dict):
        raise RulesYamlError(f"{where}.scope must be a mapping")
    bad_scope = set(scope_raw) - _SCOPE_FIELDS
    if bad_scope:
        raise RulesYamlError(f"{where}.scope has unknown field(s): {', '.join(sorted(bad_scope))}")
    scope = {field: _strings(scope_raw.get(field), where=f"{where}.scope.{field}")
             for field in _SCOPE_FIELDS if scope_raw.get(field) is not None}

    payload: dict[str, Any] = {
        "name": name,
        "scope": scope,
        "enabled": bool(raw.get("enabled", True)),
        "source": str(raw.get("source") or "yaml"),
    }
    for field in _LIST_FIELDS:
        if raw.get(field) is not None:
            payload[field] = _strings(raw.get(field), where=f"{where}.{field}")
    if raw.get("id"):
        payload["id"] = str(raw["id"])
    if raw.get("order") is not None:
        try:
            payload["order"] = int(raw["order"])
        except (TypeError, ValueError) as exc:
            raise RulesYamlError(f"{where}.order must be a number") from exc
    for field in ("max_uses", "permitted_consequence"):
        value = raw.get(field)
        if value is not None:
            if not isinstance(value, dict):
                raise RulesYamlError(f"{where}.{field} must be a mapping")
            payload[field] = value

    if not (payload.get("allow") or payload.get("ask") or payload.get("never")):
        raise RulesYamlError(f"{where} grants and refuses nothing: give it allow, ask, or never")
    return payload


def load_rules(text: str) -> list[dict[str, Any]]:
    """Validated rule payloads from ``text``.

    Raises :class:`RulesYamlError` with a message naming the offending rule.
    A permissions file that is almost right is more dangerous than one that is
    obviously wrong, so this refuses anything it cannot read exactly.
    """
    try:
        document = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise RulesYamlError(f"not valid YAML: {exc}") from exc
    if document is None:
        return []
    if not isinstance(document, dict):
        raise RulesYamlError("the document must be a mapping with a 'rules' list")

    version = document.get("version", FORMAT_VERSION)
    if version != FORMAT_VERSION:
        raise RulesYamlError(f"unsupported version {version!r}; this build reads version {FORMAT_VERSION}")

    raw_rules = document.get("rules")
    if raw_rules is None:
        raise RulesYamlError("the document has no 'rules' list")
    if not isinstance(raw_rules, list):
        raise RulesYamlError("'rules' must be a list")

    payloads = [_rule_payload(raw, index=i) for i, raw in enumerate(raw_rules)]
    names = [p["name"] for p in payloads]
    duplicates = sorted({n for n in names if names.count(n) > 1})
    if duplicates:
        # Names are how an import matches an existing rule; two rules with one
        # name would make the result depend on ordering.
        raise RulesYamlError(f"duplicate rule name(s): {', '.join(duplicates)}")
    return payloads
