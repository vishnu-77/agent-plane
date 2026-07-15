"""Model registry + router.

Maps logical model ids -> (provider, upstream_model) with provider *tags* (used
by the policy engine to evaluate ``model_provider`` matches) and a fallback
chain. The router executes the call, walking the chain on upstream failure.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from agent_plane.config import Settings
from agent_plane.routing.providers import Provider, ProviderError, build_providers


@dataclass(frozen=True)
class ModelEntry:
    model_id: str
    provider: str
    upstream_model: str
    tags: frozenset[str]
    fallback: tuple[str, ...] = field(default_factory=tuple)


# Default registry. ``tags`` carry both the concrete provider and a sensitivity
# class ("external"/"internal") so policies can say model_provider: [external].
_DEFAULT_MODELS: list[ModelEntry] = [
    ModelEntry(
        model_id="gpt-4.1",
        provider="openai",
        upstream_model="gpt-4.1",
        tags=frozenset({"openai", "external"}),
        fallback=("gpt-4o-mini",),
    ),
    ModelEntry(
        model_id="gpt-4o-mini",
        provider="openai",
        upstream_model="gpt-4o-mini",
        tags=frozenset({"openai", "external"}),
    ),
    ModelEntry(
        model_id="claude-sonnet",
        provider="anthropic",
        upstream_model="claude-sonnet-4-6",
        tags=frozenset({"anthropic", "external"}),
    ),
    ModelEntry(
        model_id="azure-private-gpt4",
        provider="azure",
        upstream_model="gpt-4",  # Azure deployment name
        # Externally hosted but the approved private deployment: the finance
        # deny policy matches on "external", and its exception rescues this one
        # via the "azure_openai_private" tag.
        tags=frozenset({"azure", "azure_openai_private", "external"}),
    ),
]


_DEFAULT_MODELS_FILE = "config/models.yaml"


def load_model_entries(path: str) -> list[ModelEntry]:
    """Load a model catalog from YAML.

    Each entry: ``id``, ``provider``, ``upstream_model``, optional ``tags`` and
    ``fallback``. This is what lets an org onboard models/providers without code.
    """
    doc = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    raw = doc.get("models", doc if isinstance(doc, list) else [])
    entries: list[ModelEntry] = []
    for m in raw:
        entries.append(
            ModelEntry(
                model_id=m["id"],
                provider=m["provider"],
                upstream_model=m.get("upstream_model", m["id"]),
                tags=frozenset(m.get("tags", [])),
                fallback=tuple(m.get("fallback", ())),
            )
        )
    return entries


def _resolve_models(settings: Settings) -> list[ModelEntry]:
    path = settings.models_file or (
        _DEFAULT_MODELS_FILE if Path(_DEFAULT_MODELS_FILE).exists() else None
    )
    if path:
        return load_model_entries(path)
    return _DEFAULT_MODELS


class ModelRegistry:
    def __init__(self, settings: Settings, models: list[ModelEntry] | None = None):
        self._entries: dict[str, ModelEntry] = {
            m.model_id: m for m in (models or _resolve_models(settings))
        }
        self._providers: dict[str, Provider] = build_providers(settings)

    # --- lookups used by the policy engine + router ----------------------
    def provider_tags(self, model_id: str) -> set[str]:
        entry = self._entries.get(model_id)
        return set(entry.tags) if entry else set()

    def resolve(self, model_id: str) -> ModelEntry | None:
        return self._entries.get(model_id)

    def list_models(self) -> list[str]:
        return list(self._entries.keys())

    def _chain(self, model_id: str) -> list[ModelEntry]:
        chain: list[ModelEntry] = []
        seen: set[str] = set()
        queue = [model_id]
        while queue:
            mid = queue.pop(0)
            if mid in seen:
                continue
            seen.add(mid)
            entry = self._entries.get(mid)
            if entry:
                chain.append(entry)
                queue.extend(entry.fallback)
        return chain

    # --- execution -------------------------------------------------------
    async def route(
        self, model_id: str, body: dict[str, Any]
    ) -> tuple[dict[str, Any], str]:
        """Call the model, falling back on upstream failure.

        Returns (openai_response, model_id_used). Raises ProviderError if the
        whole chain fails, or LookupError if the model is unknown.
        """
        chain = self._chain(model_id)
        if not chain:
            raise LookupError(f"Unknown model: {model_id}")

        last_error: ProviderError | None = None
        for entry in chain:
            provider = self._providers.get(entry.provider)
            if provider is None:
                continue
            try:
                resp = await provider.chat(body, entry.upstream_model)
                return resp, entry.model_id
            except ProviderError as exc:
                last_error = exc
                continue
        raise last_error or ProviderError(f"No usable provider for {model_id}")
