# Roadmap

Ship one narrow, technically credible enforcement primitive, then expand the
plane around it - not the whole architecture at once.

| Release  | Primary capability                      | Status |
| -------- | ---------------------------------------- | ------ |
| **v0.1** | Runtime task-authority enforcement (`AuthorityLease`, `POST /v1/authorize`, capability-manifest gate, tamper-evident evidence) | ✅ shipped |
| **v0.2** | TypeScript SDK, MCP adapter, gateway/proxy mode | planned |
| **v0.3** | Lease delegation + child authority (attenuated sub-leases, mirroring the A2A identity edge) | planned |
| **v0.4** | Dynamic authority shrinking/revocation for active leases | planned |
| **v0.5** | Consequence-aware decisions (`maximum_impact` actually gates, not just informational) | planned |
| **v0.6** | Observed-vs-declared capability drift detection | planned |
| **v0.7** | Reconciliation / rollback hooks | planned |
| **v1.0** | Full authority lifecycle + attestations | planned |

v0.1 lives in `agent_plane/authority/` + `POST /v1/authorize` /
`POST /v1/leases`; see [`spec/authority-lease.md`](spec/authority-lease.md) for
the object shape and evaluation order, and
[`examples/devops-agent/demo.py`](examples/devops-agent/demo.py) for the
capability-vs-authority demo end to end.
