import { useMemo, useState } from "react";
import { api } from "@/lib/api";
import { useStore } from "@/lib/store";
import { cn, dateOf, outcomeLabel, reasonText } from "@/lib/utils";
import { DecisionStrip, DecisionTrace, useDecision } from "@/components/decision";
import { InstrumentPanel } from "@/components/instruments";
import { Badge, Button, Empty, Input } from "@/components/ui";

const FILTERS = ["all", "allow", "deny", "approval_required", "quarantine", "simulate"] as const;

export function DecisionsPage() {
  const { snapshot, selected, select, mode, creds, refresh } = useStore();
  const [filter, setFilter] = useState<(typeof FILTERS)[number]>("all");
  const [q, setQ] = useState("");
  const list = useMemo(
    () => snapshot.decisions.filter((d) => (filter === "all" || d.outcome === filter) && (!q || JSON.stringify(d).toLowerCase().includes(q.toLowerCase()))),
    [snapshot.decisions, filter, q],
  );
  const current = selected && list.find((d) => d.decision_id === selected) ? selected : list[0]?.decision_id ?? null;
  const { detail, error } = useDecision(current);
  const trace = detail?.trace ?? null;
  const approval = detail?.related.find((r) => r.kind === "approval")?.approval ?? null;

  const decide = async (verb: "approve" | "reject") => {
    if (!approval) return;
    await api(`/v1/approvals/${encodeURIComponent(approval.id)}/${verb}`, { mode, creds, method: "POST", body: JSON.stringify({ decided_by: "console" }) });
    await refresh();
  };

  return (
    <div className="grid h-full grid-cols-1 lg:grid-cols-[minmax(0,1fr)_minmax(400px,1fr)]">
      <div className="flex min-h-0 flex-col border-b border-hairline lg:border-b-0 lg:border-r">
        <div className="flex flex-wrap items-center gap-2 border-b border-hairline bg-paper-raised px-3 py-2">
          <div className="mr-2"><div className="eyebrow">Govern</div><div className="text-sm font-medium tracking-tight">Decisions</div></div>
          {FILTERS.map((f) => (
            <button key={f} type="button" onClick={() => setFilter(f)} className={cn("dot rounded-sm border px-2 py-0.5 text-2xs", filter === f ? "border-ink bg-ink text-paper" : "border-hairline-strong text-ink-2")}>
              {f === "all" ? "all" : outcomeLabel(f)}
            </button>
          ))}
          <Input value={q} onChange={(e) => setQ(e.target.value)} placeholder="search agent, action, resource, reason" className="ml-auto h-7 max-w-xs text-xs" />
        </div>
        <div className="min-h-0 flex-1 overflow-auto">
          {list.length ? list.map((d) => <DecisionStrip key={d.decision_id} d={d} selected={d.decision_id === current} onSelect={select} />) : <Empty title="No decisions match" />}
        </div>
        {snapshot.approvals.length ? (
          <InstrumentPanel className="rounded-none border-0 border-t border-hairline" eyebrow="Pending approvals" title={`${snapshot.approvals.length} waiting for a human`} dense>
            <ul>
              {snapshot.approvals.map((a) => (
                <li key={a.id} className="flex items-center gap-3 border-b border-hairline px-3 py-2 text-xs">
                  <Badge tone="approval">APPROVAL</Badge>
                  <span className="font-mono">{a.subject} · {a.action} → {a.resource}</span>
                  <span className="ml-auto font-mono text-2xs text-ink-2">{a.id}</span>
                  <Button size="sm" variant="allow" onClick={() => { void api(`/v1/approvals/${a.id}/approve`, { mode, creds, method: "POST", body: JSON.stringify({ decided_by: "console" }) }).then(refresh); }}>Approve</Button>
                  <Button size="sm" variant="deny" onClick={() => { void api(`/v1/approvals/${a.id}/reject`, { mode, creds, method: "POST", body: JSON.stringify({ decided_by: "console" }) }).then(refresh); }}>Reject</Button>
                </li>
              ))}
            </ul>
          </InstrumentPanel>
        ) : null}
      </div>
      <div className="overflow-auto p-4">
        {error ? <div className="text-xs text-deny">{error}</div> : null}
        {trace ? (
          <>
            <DecisionTrace trace={trace} />
            {approval ? (
              <div className="mt-4 rounded border border-hairline bg-paper p-3 text-xs">
                <div className="eyebrow mb-1">Approval request</div>
                <div className="font-mono">{approval.id} · {approval.status}{approval.decided_by ? ` · by ${approval.decided_by}` : ""}</div>
                <div className="text-ink-2">raised {dateOf(approval.created_at)}{approval.expires_at ? ` · expires ${dateOf(approval.expires_at)}` : ""}</div>
                {approval.status === "pending" ? (
                  <div className="mt-2 flex gap-2"><Button size="sm" variant="allow" onClick={() => void decide("approve")}>Approve</Button><Button size="sm" variant="deny" onClick={() => void decide("reject")}>Reject</Button></div>
                ) : null}
              </div>
            ) : null}
            <div className="mt-4 font-mono text-2xs text-ink-2">{reasonText(trace.decision.reason)} · recorded {dateOf(detail?.event.created_at)}</div>
          </>
        ) : (
          <div className="text-xs text-ink-2">Select a decision to read its trace.</div>
        )}
      </div>
    </div>
  );
}
