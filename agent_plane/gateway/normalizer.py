"""Request normalization: OpenAI payload -> CanonicalAIRequest.

Normalizing into a provider-agnostic shape is what makes policy portable across
model providers.
"""
from __future__ import annotations

from agent_plane.gateway.tokens import estimate_tokens
from agent_plane.guardrails.classifier import derive_classification
from agent_plane.schemas.canonical import (
    Actor,
    CanonicalAIRequest,
    DataClassification,
    max_classification,
)
from agent_plane.schemas.openai import ChatCompletionRequest


def _classification(value: str | None) -> DataClassification:
    try:
        return DataClassification(value) if value else DataClassification.INTERNAL
    except ValueError:
        return DataClassification.INTERNAL


def normalize(req: ChatCompletionRequest, actor: Actor) -> CanonicalAIRequest:
    messages = [m.model_dump() for m in req.messages]
    # The caller's declared classification can only be *escalated* by what the
    # content actually contains - never trusted to lower it.
    declared = _classification(req.data_classification)
    effective = max_classification(declared, derive_classification(messages))
    return CanonicalAIRequest(
        request_type="chat_completion",
        messages=messages,
        model_requested=req.model,
        estimated_tokens=estimate_tokens(req),
        data_classification=effective,
        tools_requested=req.tool_names(),
        stream=req.stream,
        actor=actor,
    )
