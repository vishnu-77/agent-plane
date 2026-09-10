# AuthorityLease

**Capability ≠ Authority.** `Actor.allowed_tools` (the capability manifest,
resolved from identity) says what an agent can *technically* do. An
`AuthorityLease` says what it's *authorised* to do, right now, for one task.
A proposed action is only allowed where both agree - see
`agent_plane/authority/evaluator.py`.

## Shape

```yaml
apiVersion: agent-plane/v1alpha1
kind: AuthorityLease

metadata:
  id: lease-fix-staging          # unique
  task: fix-staging-checkout     # the task this grant is scoped to

subject:
  agent: devops-agent            # Actor.agent_id (falls back to user_id)

authority:
  resources: ["staging/*"]       # fnmatch globs; a resource must match one
  actions: ["deployment.read", "deployment.restart"]

constraints:
  protected_resources: ["production/*"]  # always denied, even inside `resources`
  max_uses: { branch.delete: 5 }         # per-action cap; unset = unlimited
  require_approval: []                    # in-scope actions still needing a human
  expires_at: "2027-01-01T00:00:00Z"

consequence:
  maximum_impact: reversible     # reversible | irreversible - ceiling this lease permits

delegation:
  child_authority: subset_only   # subset_only | none - "none" refuses re-delegation
```

A flat dict (same field names, no nesting) is also accepted - that's what
`POST /v1/leases` and `POST /v1/authorize` responses use.

## `POST /v1/authorize`

```json
{ "task": "fix-staging-checkout", "action": "deployment.delete", "resource": "production/checkout",
  "impact": "reversible" }
```

`impact` is optional (defaults to `reversible`) and caller-declared, the same
way `action`/`resource` are - the evaluator enforces it against the matched
lease's `maximum_impact` ceiling, it doesn't independently classify it.

→ `200` (`decision: allow`), `202` (`approval_required`), or `403` (`deny`), always with:

```json
{ "decision": "deny", "reason": "RESOURCE_OUTSIDE_DELEGATED_SCOPE", "lease": null, "evidence_id": "az_..." }
```

Every call is recorded in the same hash-chained, HMAC-signed audit log as every
other edge (`GET /v1/audit`) - `evidence_id` is that record's `decision_id`.

## Evaluation order (first match wins)

1. **Capability gate** - `action`'s namespace (text before the first `.`) must
   be in `Actor.allowed_tools`, if that grant is non-empty →
   `ACTION_OUTSIDE_CAPABILITY_MANIFEST`
2. No lease for `(agent, task)` → `NO_ACTIVE_LEASE`
3. All leases for `(agent, task)` expired → `LEASE_EXPIRED`
4. For each active lease, in order:
   - `resource` doesn't match `resources` → skip (contributes `RESOURCE_OUTSIDE_DELEGATED_SCOPE`)
   - `resource` matches `protected_resources` → **deny immediately**, `RESOURCE_PROTECTED`
     (a protected resource can't be reached via a more permissive lease for the same task)
   - `action` not in `actions` → skip, `ACTION_NOT_AUTHORIZED`
   - declared `impact` outranks the lease's `maximum_impact` → skip (does not
     consume a use), `ACTION_IMPACT_EXCEEDS_LEASE`
   - `max_uses[action]` reached → skip, `ACTION_LIMIT_EXCEEDED`
   - else → `ALLOW` (`ACTION_WITHIN_TASK_AUTHORITY`), or `APPROVAL_REQUIRED`
     (`ACTION_REQUIRES_APPROVAL`) if `action` is in `require_approval`
5. No lease matched → deny with the most specific reason seen above.

## Issuing a lease

```bash
curl -X POST localhost:8000/v1/leases -H "X-Admin-Token: $ADMIN_TOKEN" -d '{
  "id": "lease-1", "task": "fix-staging-checkout", "agent": "devops-agent",
  "resources": ["staging/*"], "actions": ["deployment.restart"]
}'
```

`config/leases.yaml` seeds the default set the same way `config/tools.yaml`
seeds the tool catalog - edit it for your own agents/tasks.

## Known limits

Two matter most when reading this spec — an evaluation that returns `allow`
executes nothing, and `impact` is a self-report by the party being governed.

The maintained list lives in
[SECURITY.md § Known limitations](../SECURITY.md#known-limitations-read-before-relying-on-it),
not here.
