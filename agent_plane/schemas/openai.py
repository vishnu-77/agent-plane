"""OpenAI-compatible request/response models.

The gateway exposes an OpenAI-shaped surface so existing SDKs work by changing
only ``base_url``. These models are intentionally permissive (extra fields are
allowed) to tolerate provider-specific parameters we pass through.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ChatMessage(BaseModel):
    model_config = ConfigDict(extra="allow")

    role: str
    content: str | None = None


class ChatCompletionRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    model: str
    messages: list[ChatMessage]
    max_tokens: int | None = None
    temperature: float | None = None
    stream: bool = False
    # Optional control-plane hint; apps may declare the sensitivity of the payload.
    data_classification: str | None = None

    def passthrough_body(self) -> dict[str, Any]:
        """The body forwarded upstream (excludes control-plane-only fields)."""
        body = self.model_dump(exclude_none=True)
        body.pop("data_classification", None)
        return body

    def tool_names(self) -> list[str]:
        """Function names declared in the OpenAI ``tools`` block, if any.

        ``tools`` arrives as a permissive extra field; each entry is normally
        ``{"type": "function", "function": {"name": ...}}``.
        """
        tools = (self.model_extra or {}).get("tools") or []
        names: list[str] = []
        for t in tools:
            if not isinstance(t, dict):
                continue
            fn = t.get("function") if isinstance(t.get("function"), dict) else None
            name = (fn or {}).get("name") or t.get("name")
            if name:
                names.append(name)
        return names


class ModelInfo(BaseModel):
    id: str
    object: str = "model"
    owned_by: str = "agent-plane"


class ModelList(BaseModel):
    object: str = "list"
    data: list[ModelInfo] = Field(default_factory=list)
