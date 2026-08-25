from __future__ import annotations

from fnmatch import fnmatch

from agent_plane.authority.schema import (
    AuthorityContext,
    AuthorityDecision,
    AuthorityManifest,
)


class AuthorityEngine:
    """Evaluate a concrete tool invocation against task-scoped authority.

    This is deliberately separate from capability and identity checks. The
    caller may possess the tool and valid credentials, yet the specific action
    can still be denied when it exceeds the authority established for the task.
    """

    def __init__(self, manifest: AuthorityManifest):
        self.manifest = manifest

    @staticmethod
    def _match_optional(pattern: str | None, value: str | None) -> bool:
        if pattern is None:
            return True
        if value is None:
            return False
        return fnmatch(value, pattern)

    def evaluate(
        self,
        *,
        agent_id: str | None,
        tool: str,
        context: AuthorityContext,
    ) -> AuthorityDecision:
        matching = []
        for idx, rule in enumerate(self.manifest.rules):
            if rule.tool != tool:
                continue
            if not self._match_optional(rule.agent_id, agent_id):
                continue
            if not self._match_optional(rule.task, context.task):
                continue
            matching.append((idx, rule))

        if not matching:
            return AuthorityDecision(
                decision="deny",
                reason="No task-authority rule permits this tool invocation",
                manifest_version=self.manifest.version,
            )

        for idx, rule in matching:
            if rule.environments:
                if context.environment is None or not any(
                    fnmatch(context.environment, p) for p in rule.environments
                ):
                    continue

            if rule.resources:
                if context.resource is None or not any(
                    fnmatch(context.resource, p) for p in rule.resources
                ):
                    continue

            if rule.max_amount is not None:
                if context.amount is None or context.amount > rule.max_amount:
                    continue

            if rule.require_approval and not context.approved:
                return AuthorityDecision(
                    decision="approval_required",
                    reason="Task authority permits the action only after explicit approval",
                    manifest_version=self.manifest.version,
                    rule_index=idx,
                )

            return AuthorityDecision(
                decision="allow",
                reason="Action is within task-scoped authority",
                manifest_version=self.manifest.version,
                rule_index=idx,
            )

        return AuthorityDecision(
            decision="deny",
            reason="Action exceeds task-scoped environment, resource, or consequence limits",
            manifest_version=self.manifest.version,
        )
