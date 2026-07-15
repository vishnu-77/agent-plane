"""Token estimation.

Uses tiktoken when available; falls back to a word-count heuristic so the
gateway never hard-fails on an unknown model encoding.
"""
from __future__ import annotations

from agent_plane.schemas.openai import ChatCompletionRequest

try:
    import tiktoken

    _ENCODING = tiktoken.get_encoding("cl100k_base")
except Exception:  # noqa: BLE001 - tiktoken optional / offline
    _ENCODING = None


def _count(text: str) -> int:
    if _ENCODING is not None:
        return len(_ENCODING.encode(text))
    # ~4 chars/token heuristic.
    return max(1, len(text) // 4)


def estimate_tokens(request: ChatCompletionRequest) -> int:
    total = 0
    for message in request.messages:
        total += _count(message.content or "")
        total += 4  # per-message role/format overhead
    return total
