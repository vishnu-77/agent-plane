"""Policy engine: rule matching + decision-object correctness."""
from __future__ import annotations

from agent_plane.policy.engine import YamlPolicyEngine
from agent_plane.policy.loader import load_bundle
from agent_plane.schemas.canonical import (
    Actor,
    CanonicalAIRequest,
    DataClassification,
    DecisionAction,
)

# Model -> provider tags used by the engine to evaluate model_provider matches.
TAGS = {
    "gpt-4.1": {"openai", "external"},
    "azure-private-gpt4": {"azure_openai_private", "external"},
}


def _engine():
    bundle = load_bundle("policies")
    return YamlPolicyEngine(bundle, provider_resolver=lambda m: TAGS.get(m, set()))


def _req(
    model="gpt-4.1",
    dept=None,
    classification=DataClassification.INTERNAL,
    agent=False,
    tools=None,
    allowed_tools=None,
):
    return CanonicalAIRequest(
        model_requested=model,
        data_classification=classification,
        tools_requested=tools or [],
        actor=Actor(
            user_id="u1",
            tenant="default",
            department=dept,
            agent_id="a1" if agent else None,
            allowed_tools=allowed_tools or [],
        ),
    )


def test_normal_request_allowed_with_obligations():
    decision = _engine().evaluate(_req())
    assert decision.decision == DecisionAction.ALLOW
    assert "pii-redaction-required" in decision.rules_matched
    assert "token-quota" in decision.rules_matched
    assert decision.max_tokens == 2000
    assert decision.policy_version.startswith("bundle-")
    assert decision.decision_id.startswith("dec_")


def test_confidential_finance_external_model_denied():
    decision = _engine().evaluate(
        _req(model="gpt-4.1", dept="finance", classification=DataClassification.CONFIDENTIAL)
    )
    assert decision.decision == DecisionAction.DENY
    assert "finance-data-external-model-restriction" in decision.rules_matched
    assert "external models" in (decision.reason or "")


def test_exception_allows_private_azure_for_confidential_finance():
    decision = _engine().evaluate(
        _req(
            model="azure-private-gpt4",
            dept="finance",
            classification=DataClassification.CONFIDENTIAL,
        )
    )
    assert decision.decision == DecisionAction.ALLOW
    # Exception obligations are merged in.
    assert "retain_audit_90_days" in decision.obligations
    assert "redact_pii" in decision.obligations


def test_redact_pii_obligation_populates_default_fields():
    decision = _engine().evaluate(_req())
    for field in ("email", "credit_card", "api_key"):
        assert field in decision.redact


def test_tool_outside_allowlist_denied():
    decision = _engine().evaluate(
        _req(agent=True, tools=["wire_transfer"], allowed_tools=["search"])
    )
    assert decision.decision == DecisionAction.DENY
    assert decision.denied_tools == ["wire_transfer"]


def test_tool_within_allowlist_allowed():
    decision = _engine().evaluate(
        _req(agent=True, tools=["search"], allowed_tools=["search", "calendar"])
    )
    assert decision.decision == DecisionAction.ALLOW
    assert decision.denied_tools == []


def test_no_allowlist_means_no_identity_tool_restriction():
    # An empty grant is not enforced at the identity layer (govern via policy).
    decision = _engine().evaluate(_req(tools=["search"]))
    assert decision.decision == DecisionAction.ALLOW


def test_sensitive_tool_requires_approval_via_policy():
    decision = _engine().evaluate(_req(tools=["delete_records"]))
    assert decision.decision == DecisionAction.APPROVAL_REQUIRED
    assert decision.requires_human_approval is True
    assert "sensitive-tool-approval" in decision.rules_matched
