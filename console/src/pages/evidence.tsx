import { useEffect, useMemo, useState } from "react";
import { api, type AuditEvent } from "@/lib/api";
import { useStore } from "@/lib/store";
import { cn, dateOf, outcomeLabel, outcomeTone, timeOf } from "@/lib/utils";
import { EvidenceDrawer } from "@/components/decision";
import { InstrumentPanel } from "@/components/instruments";
import { Badge, Button, Empty, Table, Td, Th } from "@/components/ui";

// ---------------------------------------------------------------- Timeline
export function TimelinePage() {
  const { snapshot } = useStore();
  const [open, setOpen] = useState<string | null>(null);
  const groups = useMemo(() => {
    const m = new Map<string, typeof snapshot.decisions>();
    for (const d of snapshot.decisions) {
      const key = d.task || "(no task)";
      m.set(key, [...(m.get(key) ?? []), d]);
    }
    return [...m.entries()];
  }, [snapshot.decisions]);

  return (
    <div className="p-4">
      <div className="mb-3">
        <div className="eyebrow">Evidence</div>
        <h1 className="text-lg font-medium tracking-tight">Timeline</h1>
        <p className="text-xs text-ink-2">Every decision in order, grouped by task, so a run reads as a story: what was asked, what was attempted, what was allowed.</p>
      </div>
      {groups.length ? groups.map(([task, ds]) => (
        <InstrumentPanel key={task} className="mb-3" eyebrow="Task" title={task} dense>
          <ol className="relative ml-4 border-l border-hairline-strong">
            {[...ds].reverse().map((d) => (
              <li key={d.decision_id} className="relative py-2 pl-5">
                <span className={cn("absolute -left-[5px] top-[15px] h-[9px] w-[9px] rounded-full border border-paper", { "bg-allow": d.outcome === "allow", "bg-deny": d.outcome === "deny", "bg-approval": d.outcome === "approval_required", "bg-hold": d.outcome === "quarantine" || d.outcome === "simulate" })} />
                <button type="button" className="row-hover flex w-full flex-wrap items-center gap-3 rounded px-2 py-1 text-left" onClick={() => setOpen(d.decision_id)}>
                  <span className="font-mono text-2xs text-ink-2">{timeOf(d.created_at)}</span>
                  <Badge tone={outcomeTone(d.outcome)}>{outcomeLabel(d.outcome)}</Badge>
                  <span className="dot text-xs">{d.agent}</span>
                  <span className="font-mono text-xs">{d.action} → {d.resource}</span>
                  <span className="ml-auto font-mono text-2xs text-ink-3">{d.reason}</span>
                </button>
              </li>
            ))}
          </ol>
        </InstrumentPanel>
      )) : <Empty title="Nothing recorded yet" />}
      <EvidenceDrawer id={open} open={!!open} onOpenChange={(o) => !o && setOpen(null)} />
    </div>
  );
}

// ---------------------------------------------------------------- Audit
export function AuditPage() {
  const { mode, creds, snapshot } = useStore();
  const [events, setEvents] = useState<AuditEvent[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [open, setOpen] = useState<string | null>(null);
  useEffect(() => {
    if (mode !== "live") {
      setEvents([]);
      setError(null);
      return;
    }
    api<{ events: AuditEvent[] }>("/v1/audit?limit=200", { mode, creds })
      .then((d) => { setEvents(d.events); setError(null); })
      .catch((e: Error) => setError(e.message));
  }, [mode, creds, snapshot.updatedAt]);

  const exportJson = () => {
    const blob = new Blob([JSON.stringify({ format: "agent-plane.audit-export.v1", exported_at: new Date().toISOString(), events }, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `agent-plane-audit-${Date.now()}.json`;
    a.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  };

  return (
    <div className="p-4">
      <div className="mb-3 flex items-end justify-between">
        <div>
          <div className="eyebrow">Evidence</div>
          <h1 className="text-lg font-medium tracking-tight">Audit chain</h1>
          <p className="text-xs text-ink-2">Hash-chained, HMAC-signed records across every edge. Presence of a signature is not verification; verify with the signing key.</p>
        </div>
        {mode === "live" ? <Button size="sm" onClick={exportJson} disabled={!events.length}>Export JSON</Button> : null}
      </div>
      {mode !== "live" ? <div className="text-xs text-ink-2">The raw chain is operator-only. In DEMO, read decisions and their traces from the Decisions and Timeline screens.</div> : null}
      {error ? <div className="text-xs text-deny">{error}</div> : null}
      {events.length ? (
        <InstrumentPanel dense>
          <Table>
            <thead><tr><Th>Recorded</Th><Th>Edge</Th><Th>Identity</Th><Th>Decision</Th><Th>Reason</Th><Th>Hash</Th><Th></Th></tr></thead>
            <tbody>
              {events.map((e) => (
                <tr key={e.event_hash} className="row-hover">
                  <Td className="font-mono text-2xs">{dateOf(e.created_at)}</Td>
                  <Td className="font-mono text-xs">{e.model_requested}</Td>
                  <Td className="font-mono text-xs">{e.agent_id ?? e.user_id}<div className="text-2xs text-ink-2">{e.tenant}</div></Td>
                  <Td><Badge tone={outcomeTone(e.decision)}>{outcomeLabel(e.decision)}</Badge></Td>
                  <Td className="max-w-[280px] truncate font-mono text-2xs text-ink-2">{e.reason}</Td>
                  <Td className="font-mono text-2xs text-ink-3">{e.event_hash.slice(0, 12)}…</Td>
                  <Td>{e.model_requested.startsWith("authorize:") ? <button className="text-xs underline" onClick={() => setOpen(e.decision_id)}>trace</button> : null}</Td>
                </tr>
              ))}
            </tbody>
          </Table>
        </InstrumentPanel>
      ) : null}
      <EvidenceDrawer id={open} open={!!open} onOpenChange={(o) => !o && setOpen(null)} />
    </div>
  );
}
