import { useMemo, useState } from "react";
import { useStore } from "@/lib/store";
import { cn, outcomeLabel, outcomeTone } from "@/lib/utils";
import { AuthorityConsequenceGraph, graphFromTrace } from "@/components/graph";
import { DecisionTrace, useDecision } from "@/components/decision";
import { InstrumentPanel } from "@/components/instruments";
import { Badge, Button, Empty } from "@/components/ui";

// AUTHORITY × CONSEQUENCE: pick any decision, or any agent's last decision,
// and read both views of the same runtime story side by side.
export function GovernPage() {
  const { snapshot, selected, select } = useStore();
  const [view, setView] = useState<"fused" | "authority" | "consequence">("fused");
  const { detail } = useDecision(selected ?? snapshot.decisions[0]?.decision_id ?? null);
  const trace = detail?.trace ?? null;
  const siblings = useMemo(() => (trace ? snapshot.agents.filter((a) => a.current_task === trace.task.id || a.tasks.includes(trace.task.id)) : []), [trace, snapshot.agents]);
  const graph = useMemo(() => (trace ? graphFromTrace(trace, siblings, { lineageOnly: view === "authority" }) : null), [trace, siblings, view]);

  const byAgent = useMemo(() => {
    const m = new Map<string, typeof snapshot.decisions>();
    for (const d of snapshot.decisions) m.set(d.agent, [...(m.get(d.agent) ?? []), d]);
    return [...m.entries()];
  }, [snapshot.decisions]);

  return (
    <div className="grid h-full grid-cols-1 lg:grid-cols-[240px_minmax(0,1fr)_minmax(340px,0.9fr)]">
      <aside className="border-b border-hairline bg-paper-raised lg:border-b-0 lg:border-r">
        <div className="border-b border-hairline px-3 py-2"><div className="eyebrow">Govern</div><div className="text-sm font-medium tracking-tight">Authority × Consequence</div></div>
        <div className="max-h-[300px] overflow-auto lg:max-h-none">
          {byAgent.length ? byAgent.map(([agent, ds]) => (
            <div key={agent} className="border-b border-hairline">
              <div className="dot px-3 pt-2 text-2xs">{agent}</div>
              {ds.slice(0, 6).map((d) => (
                <button key={d.decision_id} type="button" onClick={() => select(d.decision_id)} className={cn("row-hover flex w-full items-center gap-2 px-3 py-1.5 text-left", (selected ?? snapshot.decisions[0]?.decision_id) === d.decision_id && "bg-paper-sunk")}>
                  <Badge tone={outcomeTone(d.outcome)}>{outcomeLabel(d.outcome).slice(0, 4)}</Badge>
                  <span className="truncate font-mono text-2xs">{d.action} → {d.resource}</span>
                </button>
              ))}
            </div>
          )) : <Empty title="No decisions">Run the demo or connect a runtime.</Empty>}
        </div>
      </aside>
      <InstrumentPanel
        className="rounded-none border-0 border-b border-hairline lg:border-b-0 lg:border-r"
        eyebrow={view === "authority" ? "Who is authorised · from whom · for what" : view === "consequence" ? "What it can cause · reachability · blast radius" : "Who is authorised × what they can cause"}
        title={trace ? `${trace.identity.agent} · ${trace.action.name}` : "—"}
        right={
          <div className="inline-flex rounded border border-hairline-strong p-[2px]">
            {(["authority", "fused", "consequence"] as const).map((v) => (
              <Button key={v} size="sm" variant="ghost" className={cn("dot h-6 px-2 text-2xs", view === v && "bg-ink text-paper hover:bg-ink hover:text-paper")} onClick={() => setView(v)}>
                {v === "fused" ? "×" : v}
              </Button>
            ))}
          </div>
        }
        dense
      >
        <div className="h-[460px] lg:h-full">
          <AuthorityConsequenceGraph graph={graph} compact={view !== "fused"} />
        </div>
      </InstrumentPanel>
      <div className="overflow-auto p-4">
        {trace ? <DecisionTrace trace={trace} /> : <div className="text-xs text-ink-2">Select a decision.</div>}
        {trace?.consequence ? (
          <div className="mt-5 border-t border-hairline pt-3">
            <div className="eyebrow mb-2">Consequence dimensions</div>
            <dl className="grid grid-cols-[130px_1fr] gap-x-3 gap-y-1 font-mono text-xs">
              {([
                ["direct effect", trace.consequence.direct_effect],
                ["environment", trace.consequence.environment],
                ["criticality", trace.consequence.criticality],
                ["customer-facing", trace.consequence.customer_facing ? "yes" : "no"],
                ["reversibility", trace.consequence.reversibility],
                ["persistence", trace.consequence.persistence],
                ["blast radius", String(trace.consequence.blast_radius)],
                ["downstream", trace.consequence.downstream.join(", ") || "—"],
                ["protected", trace.consequence.protected ? "yes" : "no"],
                ["business", trace.consequence.business || "—"],
                ["impact", trace.consequence.impact],
              ] as Array<[string, string]>).map(([k, v]) => (
                <div key={k} className="contents"><dt className="text-ink-2">{k}</dt><dd>{v}</dd></div>
              ))}
            </dl>
          </div>
        ) : null}
      </div>
    </div>
  );
}
