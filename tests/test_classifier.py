"""Derived classification + tool extraction in the normalizer."""
from __future__ import annotations

from agent_plane.gateway.normalizer import normalize
from agent_plane.guardrails.classifier import derive_classification
from agent_plane.schemas.canonical import Actor
from agent_plane.schemas.canonical import DataClassification as DC
from agent_plane.schemas.openai import ChatCompletionRequest


def test_benign_content_imposes_no_escalation():
    assert derive_classification([{"role": "user", "content": "hello there"}]) == DC.PUBLIC


def test_secret_escalates_to_confidential():
    msgs = [{"role": "user", "content": "key sk-abcdef0123456789ABCDEF"}]
    assert derive_classification(msgs) == DC.CONFIDENTIAL


def test_finance_topic_escalates_to_confidential():
    msgs = [{"role": "user", "content": "summarize Q3 revenue and earnings"}]
    assert derive_classification(msgs) == DC.CONFIDENTIAL


def test_caller_cannot_downgrade_below_derived_floor():
    # Caller labels confidential data "public"; content forces escalation.
    req = ChatCompletionRequest(
        model="gpt-4.1",
        data_classification="public",
        messages=[{"role": "user", "content": "the Q3 earnings call details"}],
    )
    canon = normalize(req, Actor(user_id="u1"))
    assert canon.data_classification == DC.CONFIDENTIAL


def test_tool_names_extracted_from_openai_block():
    req = ChatCompletionRequest(
        model="gpt-4.1",
        messages=[{"role": "user", "content": "hi"}],
        tools=[
            {"type": "function", "function": {"name": "search"}},
            {"type": "function", "function": {"name": "wire_transfer"}},
        ],
    )
    canon = normalize(req, Actor(user_id="u1"))
    assert canon.tools_requested == ["search", "wire_transfer"]
