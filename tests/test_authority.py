from agent_plane.authority.engine import AuthorityEngine
from agent_plane.authority.schema import AuthorityContext, AuthorityManifest, AuthorityRule


def _engine() -> AuthorityEngine:
    return AuthorityEngine(
        AuthorityManifest(
            version="7",
            rules=[
                AuthorityRule(
                    agent_id="ops-*",
                    task="cleanup_staging",
                    tool="delete_records",
                    environments=["staging"],
                    resources=["staging/*"],
                ),
                AuthorityRule(
                    agent_id="finance-*",
                    task="refund_customer",
                    tool="wire_transfer",
                    environments=["production"],
                    resources=["refund/*"],
                    max_amount=100,
                    require_approval=True,
                ),
            ],
        )
    )


def test_task_authority_allows_matching_action():
    result = _engine().evaluate(
        agent_id="ops-1",
        tool="delete_records",
        context=AuthorityContext(
            task="cleanup_staging",
            environment="staging",
            resource="staging/temp-users",
        ),
    )
    assert result.decision == "allow"
    assert result.manifest_version == "7"


def test_capability_does_not_imply_task_authority():
    result = _engine().evaluate(
        agent_id="ops-1",
        tool="delete_records",
        context=AuthorityContext(
            task="cleanup_staging",
            environment="production",
            resource="production/customers",
        ),
    )
    assert result.decision == "deny"
    assert "exceeds task-scoped" in result.reason


def test_consequence_limit_requires_correct_amount():
    result = _engine().evaluate(
        agent_id="finance-1",
        tool="wire_transfer",
        context=AuthorityContext(
            task="refund_customer",
            environment="production",
            resource="refund/order-1",
            amount=250,
        ),
    )
    assert result.decision == "deny"


def test_approval_is_separate_from_possession_of_tool():
    result = _engine().evaluate(
        agent_id="finance-1",
        tool="wire_transfer",
        context=AuthorityContext(
            task="refund_customer",
            environment="production",
            resource="refund/order-1",
            amount=75,
        ),
    )
    assert result.decision == "approval_required"

    approved = _engine().evaluate(
        agent_id="finance-1",
        tool="wire_transfer",
        context=AuthorityContext(
            task="refund_customer",
            environment="production",
            resource="refund/order-1",
            amount=75,
            approved=True,
        ),
    )
    assert approved.decision == "allow"
