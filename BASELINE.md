# Stable rollback baseline

This commit marks the stable rollback point for agent-plane before the next Context Registry / Context-to-Action runtime expansion.

- Project version: `0.7.0`
- Baseline date: `2026-09-17`
- Baseline tag: `baseline-v0.7.0-2026-09-17`
- Branch: `main`

## Included

- Developer-first console flow: Activity / Agents / Access / Connect
- Existing authority, consequence, approval, project-key, MCP and audit behaviour on `main`
- The non-breaking console changes already merged through PR #20

## Intentionally not included

- PR #21 (`feat/context-action-plane`): first-class Graph and Context workspaces
- `feat/context-registry-runtime`: Context Registry, context lineage, memory governance and runtime performance work

This point should remain a known-good reference. If subsequent Context-to-Action changes introduce a regression, compare against or restore from the tag above rather than reconstructing the pre-change state manually.

Typical recovery reference:

```text
git fetch --tags
git show baseline-v0.7.0-2026-09-17
```

Prefer reverting the specific breaking change where possible; use this baseline when a full known-good comparison or rollback point is required.
