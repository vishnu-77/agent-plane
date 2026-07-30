"""Knowledge sources + identity-aware retrieval - the RAG authorization edge.

The core principle: **relevance is not permission.** Retrieval scores candidates,
then an ABAC/ACL filter drops every document the acting ``Actor`` is not authorized
for *before* anything is returned - so the model never sees data the principal
couldn't access. Config-driven (YAML), default-deny on unknown sources.

A ``local`` source carries inline documents (offline/dev); ``http`` (calling an
external retriever and re-authorizing on returned metadata) is the extension point.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from agent_plane.config import Settings
from agent_plane.schemas.canonical import Actor, DataClassification, max_classification

_DEFAULT_KNOWLEDGE_FILE = "config/knowledge.yaml"


def _classification(value: str | None) -> DataClassification:
    try:
        return DataClassification(value) if value else DataClassification.PUBLIC
    except ValueError:
        return DataClassification.PUBLIC


@dataclass(frozen=True)
class Document:
    id: str
    text: str
    tenant: str | None = None
    departments: tuple[str, ...] = ()
    classification: DataClassification = DataClassification.PUBLIC
    allow_users: tuple[str, ...] = ()
    allow_groups: tuple[str, ...] = ()


@dataclass(frozen=True)
class RetrievalSource:
    name: str
    type: str = "local"  # "local" (extension point: "http")
    documents: tuple[Document, ...] = field(default_factory=tuple)


def authorized(actor: Actor, doc: Document) -> bool:
    """Deterministic ABAC/ACL check - absence on a dimension = unrestricted there."""
    # Hard tenant isolation.
    if doc.tenant and doc.tenant != actor.tenant:
        return False
    # Department scoping.
    if doc.departments and actor.department not in doc.departments:
        return False
    # Clearance: the actor must be cleared at or above the document's class.
    if max_classification(actor.clearance, doc.classification) != actor.clearance:
        return False
    # Explicit allow-list (user or group).
    if doc.allow_users or doc.allow_groups:
        if actor.user_id in doc.allow_users:
            return True
        if set(actor.groups) & set(doc.allow_groups):
            return True
        return False
    return True


def _score(query: str, text: str) -> int:
    q = query.lower()
    body = text.lower()
    terms = [t for t in q.split() if t]
    return sum(body.count(t) for t in terms) if terms else (1 if q in body else 0)


class KnowledgeStore:
    def __init__(self, sources: list[RetrievalSource]):
        self._sources: dict[str, RetrievalSource] = {s.name: s for s in sources}

    def get(self, name: str) -> RetrievalSource | None:
        return self._sources.get(name)

    def list(self) -> list[str]:
        return list(self._sources)

    def retrieve(
        self, source: str, query: str, actor: Actor, top_k: int = 5
    ) -> tuple[list[dict[str, Any]], int, int]:
        """Return ``(authorized_docs, retrieved_n, filtered_n)``.

        Candidates are scored, then the authorization filter is applied *before*
        returning - a highly-relevant but unauthorized document is dropped.
        """
        spec = self._sources[source]  # caller guarantees existence (404 upstream)
        scored = [
            (doc, _score(query, doc.text))
            for doc in spec.documents
        ]
        scored = [(d, s) for d, s in scored if s > 0]
        scored.sort(key=lambda ds: ds[1], reverse=True)
        retrieved_n = len(scored)

        out: list[dict[str, Any]] = []
        filtered_n = 0
        for doc, score in scored:
            if not authorized(actor, doc):
                filtered_n += 1
                continue
            out.append(
                {
                    "id": doc.id,
                    "text": doc.text,
                    "score": score,
                    "classification": doc.classification.value,
                }
            )
            if len(out) >= top_k:
                break
        return out, retrieved_n, filtered_n


def _to_document(d: dict) -> Document:
    return Document(
        id=d["id"],
        text=d.get("text", ""),
        tenant=d.get("tenant"),
        departments=tuple(d.get("departments", ())),
        classification=_classification(d.get("classification")),
        allow_users=tuple(d.get("allow_users", ())),
        allow_groups=tuple(d.get("allow_groups", ())),
    )


def load_sources(path: str) -> list[RetrievalSource]:
    doc = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    raw = doc.get("sources", [])
    return [
        RetrievalSource(
            name=s["name"],
            type=s.get("type", "local"),
            documents=tuple(_to_document(d) for d in s.get("documents", [])),
        )
        for s in raw
    ]


def build_knowledge_store(settings: Settings) -> KnowledgeStore:
    path: str | None = settings.knowledge_file or (
        _DEFAULT_KNOWLEDGE_FILE if Path(_DEFAULT_KNOWLEDGE_FILE).exists() else None
    )
    if path is None:
        from agent_plane.defaults import default_config_file

        default = default_config_file("knowledge.yaml")
        path = str(default) if default.exists() else None
    return KnowledgeStore(load_sources(path) if path else [])
