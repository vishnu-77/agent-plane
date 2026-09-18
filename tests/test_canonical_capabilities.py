"""PR-2: Actor.capabilities is a read-only alias for Actor.allowed_tools."""
from agent_plane.schemas.canonical import Actor


def test_capabilities_aliases_allowed_tools():
    actor = Actor(user_id="u1", allowed_tools=["repo.read", "tests.execute"])
    assert actor.capabilities == ["repo.read", "tests.execute"]
    assert actor.capabilities == actor.allowed_tools


def test_capabilities_empty_default():
    actor = Actor(user_id="u1")
    assert actor.capabilities == []


def test_capabilities_not_in_model_dump():
    actor = Actor(user_id="u1", allowed_tools=["repo.read"])
    assert "capabilities" not in actor.model_dump()


if __name__ == "__main__":
    test_capabilities_aliases_allowed_tools()
    test_capabilities_empty_default()
    test_capabilities_not_in_model_dump()
    print("ok")
