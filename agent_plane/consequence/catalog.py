"""Resource and action profiles, and the structured consequence they imply.

The catalog is operator-declared YAML (``config/resources.yaml``):

    resources:
      - pattern: "production/*"
        environment: production
        criticality: critical
        customer_facing: true
        reversibility: recoverable
        persistence: durable
        depends_on: []            # what this resource needs
        dependents: ["billing/*"] # what breaks downstream if it changes
        protected: false
        description: production workloads
    actions:
      - pattern: "deployment.restart"
        effect: restart
        severity: medium
        reversibility: recoverable
        direct_effect: "workload restarts; in-flight requests are dropped"

Evaluation is deterministic and explainable: every field on the resulting
:class:`Consequence` names the profile it came from. Nothing here is a risk
score; the dimensions stay separate so a lease can bound each one.
"""
from __future__ import annotations

import fnmatch
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

Impact = Literal["none", "low", "medium", "high", "critical"]
Reversibility = Literal["reversible", "recoverable", "irreversible"]
Persistence = Literal["transient", "durable", "permanent"]
Effect = Literal["read", "list", "create", "mutate", "restart", "delete", "execute", "send", "export"]
# The consequence *class*: a small closed vocabulary describing the kind of
# effect, independent of which resource it lands on. Deterministic by design:
# no scoring, no inference, no model in the path.
ConsequenceClass = Literal[
    "read_only", "workspace_mutation", "repository_mutation", "destructive_resource_change",
    "service_interruption", "configuration_change", "credential_access", "data_egress",
    "external_communication", "code_execution", "dependency_change",
]
# How far one action reaches on its own, before downstream dependents.
Scope = Literal["none", "single_file", "workspace", "single_service", "repository", "account", "organisation"]

IMPACT_RANK: dict[str, int] = {"none": 0, "low": 1, "medium": 2, "high": 3, "critical": 4}
REVERSIBILITY_RANK: dict[str, int] = {"reversible": 0, "recoverable": 1, "irreversible": 2}
PERSISTENCE_RANK: dict[str, int] = {"transient": 0, "durable": 1, "permanent": 2}
_EFFECT_SEVERITY: dict[str, str] = {
    "read": "none", "list": "none", "create": "low", "mutate": "medium", "restart": "medium",
    "execute": "medium", "send": "medium", "export": "high", "delete": "high",
}
_EFFECT_CLASS: dict[str, str] = {
    "read": "read_only", "list": "read_only", "create": "workspace_mutation",
    "mutate": "workspace_mutation", "restart": "service_interruption",
    "delete": "destructive_resource_change", "execute": "code_execution",
    "send": "external_communication", "export": "data_egress",
}
_EFFECT_SCOPE: dict[str, str] = {
    "read": "none", "list": "none", "create": "single_file", "mutate": "single_file",
    "restart": "single_service", "delete": "single_service", "execute": "workspace",
    "send": "organisation", "export": "organisation",
}
_DEFAULT_TEMPLATE = "config/resources.yaml"


class ResourceProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pattern: str
    environment: str = "unknown"
    criticality: Impact = "medium"
    customer_facing: bool = False
    reversibility: Reversibility = "recoverable"
    persistence: Persistence = "durable"
    depends_on: list[str] = Field(default_factory=list)
    dependents: list[str] = Field(default_factory=list)
    protected: bool = False
    description: str = ""
    business: str = ""


class ActionProfile(BaseModel):
    """What an action does, before we know which resource it lands on."""

    model_config = ConfigDict(extra="forbid")

    pattern: str
    effect: Effect = "mutate"
    severity: Impact | None = None          # overrides the effect's default severity
    reversibility: Reversibility | None = None
    persistence: Persistence | None = None
    direct_effect: str = ""
    # The deterministic registry entry, per spec: class / scope / reversible /
    # environment_sensitive. Written either flat or under a `consequence:` block.
    consequence_class: ConsequenceClass | None = None
    scope: Scope | None = None
    environment_sensitive: bool = False
    label: str = ""                         # human phrasing for the UI ("Push git changes")

    @model_validator(mode="before")
    @classmethod
    def _accept_consequence_block(cls, data: Any) -> Any:
        """Allow the documented nested form:

            git.push:
              consequence:
                class: repository_mutation
                reversible: true
                scope: repository
        """
        if not isinstance(data, dict):
            return data
        block = data.pop("consequence", None)
        if isinstance(block, dict):
            data = dict(data)
            if "class" in block and "consequence_class" not in data:
                data["consequence_class"] = block["class"]
            if "scope" in block and "scope" not in data:
                data["scope"] = block["scope"]
            if "environment_sensitive" in block:
                data["environment_sensitive"] = block["environment_sensitive"]
            if "reversible" in block and "reversibility" not in data:
                data["reversibility"] = "reversible" if block["reversible"] else "irreversible"
        return data


class Consequence(BaseModel):
    """What allowing ``action`` on ``resource`` can cause.

    Every field is derived from declared profiles, never inferred by a model.
    ``consequence_class`` plus ``scope`` answer "what kind of thing is this";
    the resource context answers "and where does it land".
    """

    action: str
    resource: str
    consequence_class: str = "workspace_mutation"
    scope: str = "single_file"
    effect: Effect
    direct_effect: str
    environment: str
    criticality: Impact
    customer_facing: bool
    reversibility: Reversibility
    persistence: Persistence
    protected: bool
    downstream: list[str] = Field(default_factory=list)   # resources reachable via dependents
    blast_radius: int = 0                                  # resources affected incl. this one
    environments: list[str] = Field(default_factory=list) # environments in the blast radius
    impact: Impact = "low"
    business: str = ""
    resource_profile: str | None = None
    action_profile: str | None = None
    summary: list[str] = Field(default_factory=list)      # short human-readable lines

    @property
    def mutating(self) -> bool:
        return self.effect not in ("read", "list")

    def registry_shape(self) -> dict[str, Any]:
        """The compact deterministic record: class, environment, scope, reversibility."""
        return {
            "class": self.consequence_class,
            "environment": self.environment,
            "scope": self.scope,
            "reversibility": self.reversibility,
            "protected_resource": self.protected,
        }


class ConsequenceCatalog:
    def __init__(self, resources: list[ResourceProfile] | None = None,
                 actions: list[ActionProfile] | None = None):
        self.resources = list(resources or [])
        self.actions = list(actions or [])

    # -- lookups ------------------------------------------------------------ #
    def resource_profile(self, resource: str) -> ResourceProfile | None:
        # Most specific (longest) matching pattern wins.
        matches = [p for p in self.resources if fnmatch.fnmatchcase(resource, p.pattern)]
        return max(matches, key=lambda p: len(p.pattern)) if matches else None

    def action_profile(self, action: str) -> ActionProfile | None:
        matches = [p for p in self.actions if fnmatch.fnmatchcase(action, p.pattern)]
        return max(matches, key=lambda p: len(p.pattern)) if matches else None

    def matching_resources(self, pattern: str) -> list[ResourceProfile]:
        return [p for p in self.resources if fnmatch.fnmatchcase(p.pattern, pattern)
                or fnmatch.fnmatchcase(pattern, p.pattern)]

    def dependents_of(self, resource: str, *, max_depth: int = 4) -> list[str]:
        """Resources that transitively depend on ``resource`` (breadth-first)."""
        seen: list[str] = []
        frontier = [resource]
        for _ in range(max_depth):
            next_frontier: list[str] = []
            for current in frontier:
                profile = self.resource_profile(current)
                if profile is None:
                    continue
                for dep in profile.dependents:
                    if dep not in seen and dep != resource:
                        seen.append(dep)
                        next_frontier.append(dep)
                # Anything that declares this resource (or its pattern) upstream.
                for other in self.resources:
                    if other.pattern in seen or other.pattern == profile.pattern:
                        continue
                    if any(fnmatch.fnmatchcase(current, up) or fnmatch.fnmatchcase(profile.pattern, up)
                           for up in other.depends_on):
                        seen.append(other.pattern)
                        next_frontier.append(other.pattern)
            frontier = next_frontier
            if not frontier:
                break
        return seen

    # -- evaluation ----------------------------------------------------------- #
    def evaluate(self, action: str, resource: str) -> Consequence:
        rp = self.resource_profile(resource)
        ap = self.action_profile(action)
        effect: str = ap.effect if ap else _infer_effect(action)
        severity = (ap.severity if ap and ap.severity else _EFFECT_SEVERITY.get(effect, "medium"))
        criticality = rp.criticality if rp else "medium"
        environment = rp.environment if rp else _infer_environment(resource)
        customer_facing = rp.customer_facing if rp else False
        reversibility = (ap.reversibility if ap and ap.reversibility else
                         rp.reversibility if rp else "recoverable")
        persistence = (ap.persistence if ap and ap.persistence else
                       rp.persistence if rp else "durable")
        protected = bool(rp.protected) if rp else False
        mutating = effect not in ("read", "list")

        downstream = self.dependents_of(resource) if mutating else []
        blast = 1 + len(downstream) if mutating else 0
        environments = sorted({environment, *[
            (self.resource_profile(d) or ResourceProfile(pattern=d)).environment for d in downstream
        ]}) if mutating else []

        consequence_class = (ap.consequence_class if ap and ap.consequence_class
                             else _EFFECT_CLASS.get(effect, "workspace_mutation"))
        scope = ap.scope if ap and ap.scope else _EFFECT_SCOPE.get(effect, "single_file")
        # Impact = the worse of what the action does and where it does it, only
        # for mutating effects; a read of production is still a read.
        if mutating:
            impact_rank = max(IMPACT_RANK[severity], IMPACT_RANK[criticality])
            if reversibility == "irreversible":
                impact_rank = max(impact_rank, IMPACT_RANK["high"])
            if protected:
                impact_rank = max(impact_rank, IMPACT_RANK["high"])
            if customer_facing and impact_rank < IMPACT_RANK["medium"]:
                impact_rank = IMPACT_RANK["medium"]
        elif consequence_class in ("credential_access", "data_egress"):
            # A read that removes a secret from its boundary is not a "low impact
            # read": holding the credential is equivalent to using it.
            impact_rank = max(IMPACT_RANK[severity], IMPACT_RANK[criticality])
        else:
            impact_rank = IMPACT_RANK["none"] if criticality in ("none", "low") else IMPACT_RANK["low"]
        impact = next(k for k, v in IMPACT_RANK.items() if v == impact_rank)

        direct = ap.direct_effect if ap and ap.direct_effect else _default_direct_effect(effect, resource)
        summary = [direct]
        if mutating:
            summary.append(consequence_class.replace("_", " "))
            if environment != "unknown":
                summary.append(f"{environment} mutation")
            if customer_facing:
                summary.append("customer-facing workload")
            if downstream:
                summary.append(f"{len(downstream)} downstream resource(s) affected")
            summary.append(f"{reversibility} · {persistence}")
            if protected:
                summary.append("protected resource")
        else:
            summary.append("no state change")

        return Consequence(
            action=action, resource=resource, consequence_class=consequence_class, scope=scope,
            effect=effect, direct_effect=direct,
            environment=environment, criticality=criticality, customer_facing=customer_facing,
            reversibility=reversibility, persistence=persistence, protected=protected,
            downstream=downstream, blast_radius=blast, environments=environments,
            impact=impact, business=(rp.business if rp else ""),
            resource_profile=rp.pattern if rp else None,
            action_profile=ap.pattern if ap else None, summary=summary,
        )


def _infer_effect(action: str) -> str:
    verb = action.rsplit(".", 1)[-1].lower()
    for key in ("read", "list", "get", "describe", "search", "view"):
        if verb.startswith(key):
            return "list" if key == "list" else "read"
    for key, effect in (("delete", "delete"), ("remove", "delete"), ("destroy", "delete"),
                        ("drop", "delete"), ("restart", "restart"), ("send", "send"),
                        ("email", "send"), ("export", "export"), ("create", "create"),
                        ("write", "mutate"), ("update", "mutate"), ("execute", "execute"),
                        ("run", "execute"), ("deploy", "mutate")):
        if verb.startswith(key):
            return effect
    return "mutate"


def _infer_environment(resource: str) -> str:
    head = resource.split("/", 1)[0].split("://", 1)[-1].lower()
    for env in ("production", "prod", "staging", "stage", "development", "dev", "test", "sandbox"):
        if head.startswith(env):
            return {"prod": "production", "stage": "staging", "dev": "development"}.get(env, env)
    return "unknown"


def _default_direct_effect(effect: str, resource: str) -> str:
    return {
        "read": f"reads {resource}; no state change",
        "list": f"lists {resource}; no state change",
        "create": f"creates state under {resource}",
        "mutate": f"modifies {resource}",
        "restart": f"restarts {resource}; in-flight work is interrupted",
        "delete": f"deletes {resource}",
        "execute": f"executes against {resource}",
        "send": f"sends from {resource} to an external party",
        "export": f"exports data from {resource}",
    }[effect]


def load_catalog(path: str) -> ConsequenceCatalog:
    doc = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    return ConsequenceCatalog(
        [ResourceProfile.model_validate(r) for r in doc.get("resources", [])],
        [ActionProfile.model_validate(a) for a in doc.get("actions", [])],
    )


def build_consequence_catalog(settings: Any) -> ConsequenceCatalog:
    path: str | None = getattr(settings, "resources_file", None) or (
        _DEFAULT_TEMPLATE if Path(_DEFAULT_TEMPLATE).exists() else None
    )
    if path is None:
        from agent_plane.defaults import default_config_file

        default = default_config_file("resources.yaml")
        path = str(default) if default.exists() else None
    return load_catalog(path) if path else ConsequenceCatalog()
