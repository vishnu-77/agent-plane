"""Rules: the authority model developers actually write.

A rule says three things in plain language:

    ALLOW   read the repository, modify the workspace, run tests
    ASK     push git changes, install packages
    NEVER   delete repositories, touch production credentials

The backend compiles rules into the same internal :class:`AuthorityLease`
structures the engine has always evaluated, so nothing about the decision
pipeline changes. AuthorityLease stays; it just stops being the thing a
developer has to author.
"""
from agent_plane.rules.store import (
    AuthorityRule,
    RuleStore,
    build_rule_store,
    compile_rules,
    compiled_lease_id,
    suggest_rule,
)

__all__ = ["AuthorityRule", "RuleStore", "build_rule_store", "compile_rules", "compiled_lease_id", "suggest_rule"]
