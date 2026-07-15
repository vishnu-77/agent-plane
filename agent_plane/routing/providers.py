"""Provider clients (real calls via httpx).

Each provider exposes ``async chat(body, upstream_model) -> dict`` returning an
OpenAI chat-completion-shaped response. Anthropic is translated to/from the
OpenAI shape so the gateway surface stays uniform.
"""
from __future__ import annotations

from typing import Any, Protocol

import httpx

from agent_plane.config import Settings


class ProviderError(Exception):
    """Upstream call failed (network, timeout, or non-2xx)."""


class Provider(Protocol):
    name: str

    async def chat(self, body: dict[str, Any], upstream_model: str) -> dict[str, Any]: ...


def _forwarded_body(body: dict[str, Any], upstream_model: str) -> dict[str, Any]:
    out = dict(body)
    out["model"] = upstream_model
    out.pop("stream", None)  # MVP returns full responses
    out.pop("data_classification", None)
    return out


class OpenAIProvider:
    name = "openai"

    def __init__(self, settings: Settings):
        self._key = settings.openai_api_key
        self._base = settings.openai_base_url.rstrip("/")
        self._timeout = settings.upstream_timeout_seconds

    async def chat(self, body: dict[str, Any], upstream_model: str) -> dict[str, Any]:
        if not self._key:
            raise ProviderError("OPENAI_API_KEY not configured")
        payload = _forwarded_body(body, upstream_model)
        headers = {"Authorization": f"Bearer {self._key}"}
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.post(
                    f"{self._base}/chat/completions", json=payload, headers=headers
                )
                resp.raise_for_status()
                return resp.json()
        except httpx.HTTPError as exc:
            raise ProviderError(f"openai call failed: {exc}") from exc


class AzureOpenAIProvider:
    name = "azure"

    def __init__(self, settings: Settings):
        self._key = settings.azure_openai_api_key
        self._endpoint = (settings.azure_openai_endpoint or "").rstrip("/")
        self._api_version = settings.azure_openai_api_version
        self._timeout = settings.upstream_timeout_seconds

    async def chat(self, body: dict[str, Any], upstream_model: str) -> dict[str, Any]:
        if not self._key or not self._endpoint:
            raise ProviderError("AZURE_OPENAI_API_KEY / endpoint not configured")
        # upstream_model is the Azure deployment name.
        url = (
            f"{self._endpoint}/openai/deployments/{upstream_model}"
            f"/chat/completions?api-version={self._api_version}"
        )
        payload = _forwarded_body(body, upstream_model)
        payload.pop("model", None)  # implied by deployment in the URL
        headers = {"api-key": self._key}
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.post(url, json=payload, headers=headers)
                resp.raise_for_status()
                return resp.json()
        except httpx.HTTPError as exc:
            raise ProviderError(f"azure call failed: {exc}") from exc


class AnthropicProvider:
    name = "anthropic"

    def __init__(self, settings: Settings):
        self._key = settings.anthropic_api_key
        self._base = settings.anthropic_base_url.rstrip("/")
        self._timeout = settings.upstream_timeout_seconds

    @staticmethod
    def _to_anthropic(body: dict[str, Any], upstream_model: str) -> dict[str, Any]:
        system_parts: list[str] = []
        messages: list[dict[str, Any]] = []
        for msg in body.get("messages", []):
            role = msg.get("role")
            content = msg.get("content") or ""
            if role == "system":
                system_parts.append(content)
            else:
                messages.append({"role": role, "content": content})
        payload: dict[str, Any] = {
            "model": upstream_model,
            "messages": messages,
            "max_tokens": body.get("max_tokens") or 1024,
        }
        if system_parts:
            payload["system"] = "\n".join(system_parts)
        if body.get("temperature") is not None:
            payload["temperature"] = body["temperature"]
        return payload

    @staticmethod
    def _to_openai(resp: dict[str, Any], upstream_model: str) -> dict[str, Any]:
        text = "".join(
            block.get("text", "")
            for block in resp.get("content", [])
            if block.get("type") == "text"
        )
        usage = resp.get("usage", {})
        return {
            "id": resp.get("id", ""),
            "object": "chat.completion",
            "model": upstream_model,
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": text},
                    "finish_reason": resp.get("stop_reason", "stop"),
                }
            ],
            "usage": {
                "prompt_tokens": usage.get("input_tokens", 0),
                "completion_tokens": usage.get("output_tokens", 0),
                "total_tokens": usage.get("input_tokens", 0)
                + usage.get("output_tokens", 0),
            },
        }

    async def chat(self, body: dict[str, Any], upstream_model: str) -> dict[str, Any]:
        if not self._key:
            raise ProviderError("ANTHROPIC_API_KEY not configured")
        payload = self._to_anthropic(body, upstream_model)
        headers = {
            "x-api-key": self._key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.post(
                    f"{self._base}/messages", json=payload, headers=headers
                )
                resp.raise_for_status()
                return self._to_openai(resp.json(), upstream_model)
        except httpx.HTTPError as exc:
            raise ProviderError(f"anthropic call failed: {exc}") from exc


def build_providers(settings: Settings) -> dict[str, Provider]:
    return {
        "openai": OpenAIProvider(settings),
        "azure": AzureOpenAIProvider(settings),
        "anthropic": AnthropicProvider(settings),
    }
