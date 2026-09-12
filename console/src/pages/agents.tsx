import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { api, type AgentDetail, type Lease } from "@/lib/api";
import { useStore } from "@/lib/store";
import { dateOf, outcomeLabel, outcomeTone } from "@/lib/utils";
import { AgentRow, AuthorityChip, PromptOrigin } from "@/components/registry";
import { LeaseInspector, EvidenceDrawer } from "@/components/decision";
import { LineageTree } from "@/components/graph";
import { InstrumentPanel } from "@/components/instruments";
import { Badge, Button, Dialog, DialogContent, Empty, Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui";

export function AgentsPage() {
  const { snapshot, mode, creds, refresh } = useStore();
  const [params, setParams] = useSearchParams();
  const selectedId = params.get("select");
  const [detail, setDetail] = useState<AgentDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [evidence, setEvidence] = useState<string | null>(null);

  useEffect(() => {
    if (!selectedId) {
      setDetail(null);
      return;
    }
    const tenant = snapshot.agents.find((a) => a.id === selectedId)?.tenant;
    const q = tenant ? `?tenant=${encodeURIComponent(tenant)}` : "";
    api<AgentDetail>(`/v1/agents/${encodeURIComponent(selectedId)}${q}`, { mode, creds })
      .then((d) => { setDetail(d); setError(null); })
      .catch((e: Error) => setError(e.message));
  }, [selectedId, mode, creds, snapshot.updatedAt, snapshot.agents]);

  const select = (id: string | null) => setParams(id ? { select: id } : {});
  const quarantine = async (on: boolean) => {
    if (!detail) return;
    await api(`/v1/agents/${encodeURIComponent(detail.id)}/quarantine?tenant=${encodeURIComponent(detail.tenant)}`, { mode, creds, method: on ? "POST" : "DELETE", body: on ? JSON.stringify({ note: "held from console" }) : undefined });
    await refresh();
  };
  const revoke = async (id: string) => {
    await api(`/v1/leases/${encodeURIComponent(id)}`, { mode, creds, method: "DELETE" });
    await refresh();
  };

  const leases: Lease[] = (detail?.leases as unknown as Lease[]) ?? [];

  return (
    <div className="p-4">
      <div className="mb-3 flex items-end justify-between">
        <div>
          <div className="eyebrow">System</div>
          <h1 className="text-lg font-medium tracking-tight">Agents</h1>
          <p className="text-xs text-ink-2">Discovered from governed traffic. Who exists, why it is running, what it may do, what it has done.</p>
        </div>
        <div className="dot text-2xs text-ink-2">{String(snapshot.agents.length).padStart(2, "0")} agents</div>
      </div>
      <InstrumentPanel dense>
        {snapshot.agents.length ? (
          snapshot.agents.map((a) => <AgentRow key={`${a.tenant}:${a.id}`} agent={a} selected={a.id === selectedId} onSelect={select} task={snapshot.tasks.find((t) => t.id === a.current_task)} />)
        ) : (
          <Empty title="No agents discovered yet">Agents appear the first time they ask for authority, call the MCP gateway, or register a task.</Empty>
        )}
      </InstrumentPanel>

      <Dialog open={!!selectedId} onOpenChange={(o) => !o && select(null)}>
        <DialogContent side="right" title={detail?.id ?? selectedId ?? ""} description={detail ? `${detail.framework ?? "unknown runtime"} · ${detail.application} · ${detail.tenant}` : undefined}>
          {error ? <div className="text-xs text-deny">{error}</div> : null}
          {detail ? (
            <Tabs defaultValue="authority">
              <TabsList className="flex-wrap">
                <TabsTrigger value="authority">Authority</TabsTrigger>
                <TabsTrigger value="origin">Origin</TabsTrigger>
                <TabsTrigger value="drift">Drift</TabsTrigger>
                <TabsTrigger value="resources">Resources</TabsTrigger>
                <TabsTrigger value="decisions">Decisions</TabsTrigger>
              </TabsList>
              <TabsContent value="authority" className="space-y-3">
                <div className="flex items-center gap-2">
                  <Badge tone={detail.status === "quarantined" ? "hold" : "allow"}>{detail.status}</Badge>
                  <span className="font-mono text-2xs text-ink-2">first seen {dateOf(detail.first_seen)}</span>
                  <div className="ml-auto">
                    {detail.status === "quarantined" ? <Button size="sm" onClick={() => void quarantine(false)}>Release</Button> : <Button size="sm" variant="deny" onClick={() => void quarantine(true)}>Quarantine</Button>}
                  </div>
                </div>
                <div>
                  <div className="eyebrow">Capabilities (identity)</div>
                  <div className="mt-1 flex flex-wrap gap-1">{detail.declared_capabilities.length ? detail.declared_capabilities.map((c) => <AuthorityChip key={c} action={c} />) : <span className="text-xs text-ink-3">unscoped identity</span>}</div>
                </div>
                <div>
                  <div className="eyebrow">Granted authority (leases)</div>
                  <div className="mt-1 flex flex-wrap gap-1">{detail.granted_authority.length ? detail.granted_authority.map((c) => <AuthorityChip key={c} action={c} state="granted" />) : <span className="text-xs text-ink-3">nothing granted</span>}</div>
                </div>
                <div>
                  <div className="eyebrow">Exercised</div>
                  <div className="mt-1 flex flex-wrap gap-1">{Object.entries(detail.exercised_authority).map(([a, n]) => <AuthorityChip key={a} action={a} state="observed" scope={`×${n}`} />)}</div>
                </div>
                {leases.map((l) => (
                  <div key={l.id}>
                    <LeaseInspector lease={l} onRevoke={mode === "live" ? revoke : undefined} />
                    {detail.lineage[l.id]?.length > 1 ? (
                      <div className="mt-2 rounded border border-hairline bg-paper p-2"><div className="eyebrow mb-1">Lineage</div><LineageTree chain={detail.lineage[l.id]} agent={detail.id} /></div>
                    ) : null}
                  </div>
                ))}
                {detail.children.length ? <div className="text-xs text-ink-2">Delegated to: {detail.children.join(", ")}</div> : null}
              </TabsContent>
              <TabsContent value="origin" className="space-y-3">
                {detail.parent_agent ? <div className="text-sm">Parent: <span className="font-mono">{detail.parent_agent}</span></div> : <div className="text-xs text-ink-2">Root agent (no parent recorded).</div>}
                {detail.tasks_detail.map((t) => (
                  <div key={t.id} className="rounded border border-hairline p-2">
                    <div className="eyebrow">Task</div>
                    <div className="font-medium">{t.id}</div>
                    <PromptOrigin origin={t.origin} />
                    <div className="mt-1 font-mono text-2xs text-ink-2">{Object.entries(t.decisions).map(([k, v]) => `${outcomeLabel(k)} ${v}`).join(" · ")}</div>
                  </div>
                ))}
                {!detail.tasks_detail.length ? <div className="text-xs text-ink-2">No task recorded. Register intents with POST /v1/tasks so prompts become provenance.</div> : null}
              </TabsContent>
              <TabsContent value="drift" className="space-y-3">
                <p className="text-xs text-ink-2">Declared (identity) vs granted (lease) vs observed (what it actually asked for).</p>
                <div><div className="eyebrow">Undeclared</div><div className="mt-1 flex flex-wrap gap-1">{detail.drift.undeclared.length ? detail.drift.undeclared.map((a) => <AuthorityChip key={a} action={a} state="undeclared" />) : <span className="text-xs text-ink-3">none</span>}</div></div>
                <div><div className="eyebrow">Observed but not granted</div><div className="mt-1 flex flex-wrap gap-1">{detail.drift.ungranted.length ? detail.drift.ungranted.map((a) => <AuthorityChip key={a} action={a} state="denied" />) : <span className="text-xs text-ink-3">none</span>}</div></div>
                <div><div className="eyebrow">Granted but never used</div><div className="mt-1 flex flex-wrap gap-1">{(detail.drift.unused_grants ?? []).length ? (detail.drift.unused_grants ?? []).map((a) => <AuthorityChip key={a} action={a} state="unused" />) : <span className="text-xs text-ink-3">none</span>}</div></div>
                <SuggestedLease agentId={detail.id} tenant={detail.tenant} />
              </TabsContent>
              <TabsContent value="resources">
                <ul className="font-mono text-xs">
                  {Object.entries(detail.resources).map(([r, n]) => (
                    <li key={r} className="flex justify-between border-b border-hairline py-1"><span>{r}</span><span className="text-ink-2">×{n}</span></li>
                  ))}
                </ul>
              </TabsContent>
              <TabsContent value="decisions">
                <ul className="font-mono text-xs">
                  {detail.recent_decisions.map((e) => (
                    <li key={e.decision_id} className="flex items-center gap-2 border-b border-hairline py-1">
                      <Badge tone={outcomeTone(e.decision)}>{outcomeLabel(e.decision)}</Badge>
                      <span className="truncate">{e.model_requested.replace("authorize:", "")} → {e.model_used}</span>
                      <button className="ml-auto underline" onClick={() => setEvidence(e.decision_id)}>trace</button>
                    </li>
                  ))}
                </ul>
              </TabsContent>
            </Tabs>
          ) : null}
        </DialogContent>
      </Dialog>
      <EvidenceDrawer id={evidence} open={!!evidence} onOpenChange={(o) => !o && setEvidence(null)} />
    </div>
  );
}

function SuggestedLease({ agentId, tenant }: { agentId: string; tenant: string }) {
  const { mode, creds } = useStore();
  const [lease, setLease] = useState<Record<string, unknown> | null>(null);
  useEffect(() => {
    api<{ lease: Record<string, unknown> }>(`/v1/agents/${encodeURIComponent(agentId)}/suggested-lease?tenant=${encodeURIComponent(tenant)}`, { mode, creds })
      .then((d) => setLease(d.lease))
      .catch(() => setLease(null));
  }, [agentId, tenant, mode, creds]);
  if (!lease || !Object.keys(lease).length) return null;
  return (
    <div>
      <div className="eyebrow">Suggested authority profile (Observe → Enforce)</div>
      <pre className="mt-1 max-h-60 overflow-auto rounded border border-hairline bg-paper p-2 font-mono text-2xs leading-4">{JSON.stringify({ ...lease, basis: undefined }, null, 2)}</pre>
      <p className="mt-1 text-xs text-ink-2">Review, then issue it with POST /v1/leases to move this agent to ENFORCE.</p>
    </div>
  );
}
