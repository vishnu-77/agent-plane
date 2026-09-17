import { useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { Api, type DecisionDetail, type DecisionSummary } from "@/lib/api";
import { useStore } from "@/lib/store";
import { AuthorityConsequenceGraph, graphFromTrace, type GNode, type Graph } from "@/components/graph";
import { Badge, Empty } from "@/components/ui";
import { clock, outcomeTone, verdictLabel } from "@/lib/format";

type ViewMode = "execution" | "system";

function toneFor(decision: DecisionSummary): "allow" | "deny" | "approval" | "hold" | "neutral" {
  const tone = outcomeTone(decision.outcome, decision.would_be);
  return tone === "review" ? "approval" : tone;
}

function systemGraph(decisions: DecisionSummary[]): Graph | null {
  if (!decisions.length) return null;
  const nodes: Graph["nodes"] = [];
  const edges: Graph["edges"] = [];
  const seen = new Set<string>();
  const add = (node: Graph["nodes"][number]) => {
    if (!seen.has(node.id)) {
      seen.add(node.id);
      nodes.push(node);
    }
  };

  for (const d of decisions.slice(0, 40)) {
    const agentId = `agent:${d.agent}`;
    const actionId = `action:${d.agent}:${d.action}`;
    const resourceId = `res:${d.agent}:${d.action}:${d.resource}`;
    const decisionId = `decision:${d.decision_id}`;
    const tone = toneFor(d);

    add({ id: agentId, layer: "agent", label: d.agent, sub: d.task || "no task" });
    add({ id: actionId, layer: "action", label: d.action, sub: d.edge || undefined });
    add({ id: resourceId, layer: "resource", label: d.resource, sub: d.environment ?? undefined });
    add({ id: decisionId, layer: "decision", label: verdictLabel(d), sub: d.reason, tone });

    edges.push({ from: agentId, to: actionId, kind: "authority" });
    edges.push({ from: actionId, to: resourceId, kind: "authority" });
    edges.push({ from: resourceId, to: decisionId, kind: "consequence", tone });
  }
  return { nodes, edges };
}

export function GraphPage() {
  const { feed, source } = useStore();
  const [params, setParams] = useSearchParams();
  const [view, setView] = useState<ViewMode>("execution");
  const [detail, setDetail] = useState<DecisionDetail | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [selectedNode, setSelectedNode] = useState<GNode | null>(null);

  const requested = params.get("decision");
  const selectedId = requested && feed.decisions.some((d) => d.decision_id === requested)
    ? requested
    : feed.decisions[0]?.decision_id ?? null;

  useEffect(() => {
    if (!selectedId || view !== "execution") {
      setDetail(null);
      setError(null);
      return;
    }
    let alive = true;
    setLoading(true);
    setSelectedNode(null);
    Api.decision(selectedId, source)
      .then((next) => {
        if (!alive) return;
        setDetail(next);
        setError(null);
      })
      .catch((e: Error) => alive && setError(e.message))
      .finally(() => alive && setLoading(false));
    return () => { alive = false; };
  }, [selectedId, source, view]);

  const trace = detail?.trace ?? null;
  const siblings = useMemo(
    () => (trace ? feed.agents.filter((a) => a.tasks.includes(trace.task.id)) : []),
    [trace, feed.agents],
  );
  const contextAssets = detail?.context_lineage?.assets ?? [];
  const execution = useMemo(() => (trace ? graphFromTrace(trace, siblings, { contextAssets }) : null), [trace, siblings, contextAssets]);
  const system = useMemo(() => systemGraph(feed.decisions), [feed.decisions]);
  const graph = view === "execution" ? execution : system;

  const selectDecision = (id: string) => {
    setView("execution");
    setParams({ decision: id });
  };

  if (!feed.decisions.length) {
    return (
      <div className="mx-auto max-w-5xl px-4 py-6">
        <div className="mb-5">
          <h1 className="text-lg font-medium tracking-tight">Graph</h1>
          <p className="mt-1 text-sm text-ink-2">Authority–Consequence Graph</p>
        </div>
        <Empty title="No execution graph yet">Connect an agent and perform an action. Its context, authority, action, resource, consequence and decision will appear here.</Empty>
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-[1500px] px-4 py-5">
      <div className="mb-4 flex flex-wrap items-end justify-between gap-3">
        <div>
          <div className="eyebrow">Graph</div>
          <h1 className="mt-1 text-lg font-medium tracking-tight">Authority–Consequence Graph</h1>
          <p className="mt-1 max-w-2xl text-sm text-ink-2">See how agent intent, authority and runtime consequences combine to produce a decision. Context lineage will appear here as adapters register it.</p>
        </div>
        <div className="inline-flex rounded border border-hairline-strong bg-paper-raised p-[2px]">
          {(["execution", "system"] as const).map((mode) => (
            <button
              key={mode}
              type="button"
              aria-pressed={view === mode}
              onClick={() => { setView(mode); setSelectedNode(null); }}
              className={`dot rounded-sm px-3 py-1 text-2xs ${view === mode ? "bg-ink text-paper" : "text-ink-2 hover:text-ink"}`}
            >
              {mode}
            </button>
          ))}
        </div>
      </div>

      <div className="grid min-h-[680px] gap-3 lg:grid-cols-[260px_minmax(0,1fr)_260px]">
        <section className="panel min-h-0 overflow-hidden">
          <div className="border-b border-hairline px-3 py-2">
            <div className="eyebrow">Recent executions</div>
          </div>
          <div className="max-h-[640px] overflow-auto">
            {feed.decisions.slice(0, 50).map((d) => {
              const active = view === "execution" && d.decision_id === selectedId;
              return (
                <button key={d.decision_id} type="button" onClick={() => selectDecision(d.decision_id)}
                  className={`w-full border-b border-hairline px-3 py-2.5 text-left hover:bg-paper-sunk ${active ? "bg-paper-sunk" : ""}`}>
                  <div className="flex items-center justify-between gap-2">
                    <span className="truncate text-xs font-medium">{d.agent}</span>
                    <span className="font-mono text-2xs text-ink-3">{clock(d.created_at)}</span>
                  </div>
                  <div className="mt-1 truncate font-mono text-xs">{d.action}</div>
                  <div className="truncate font-mono text-2xs text-ink-2">{d.resource}</div>
                </button>
              );
            })}
          </div>
        </section>

        <section className="panel min-h-[680px] overflow-hidden">
          <div className="flex h-11 items-center justify-between border-b border-hairline px-3">
            <div className="text-sm font-medium">{view === "execution" ? "Execution graph" : "Observed system graph"}</div>
            {loading ? <span className="font-mono text-2xs text-ink-2">loading…</span> : null}
            {error ? <span className="font-mono text-2xs text-deny">{error}</span> : null}
          </div>
          <div className="h-[635px]">
            <AuthorityConsequenceGraph graph={graph} selectedId={selectedNode?.id ?? null} onSelect={setSelectedNode} live />
          </div>
        </section>

        <aside className="panel min-h-0 p-4">
          <div className="eyebrow">Inspect</div>
          {selectedNode ? (
            <div className="mt-3 space-y-3">
              <div>
                <div className="font-mono text-xs text-ink-2">{selectedNode.layer}</div>
                <div className="mt-1 break-words text-sm font-medium">{selectedNode.label}</div>
                {selectedNode.sub ? <div className="mt-1 break-words text-xs text-ink-2">{selectedNode.sub}</div> : null}
              </div>
              {selectedNode.tone && selectedNode.tone !== "neutral" ? <Badge tone={selectedNode.tone}>{selectedNode.tone}</Badge> : null}
            </div>
          ) : view === "execution" && trace ? (
            <div className="mt-3 space-y-4 text-sm">
              <div><div className="eyebrow">Agent</div><div className="mt-1">{trace.identity.agent}</div></div>
              <div><div className="eyebrow">Task</div><div className="mt-1 font-mono text-xs">{trace.task.id}</div></div>
              <div><div className="eyebrow">Context lineage</div><div className="mt-1">{contextAssets.length} asset{contextAssets.length === 1 ? "" : "s"}</div></div>
              <div><div className="eyebrow">Decision</div><div className="mt-1">{verdictLabel(trace.decision)}</div></div>
              <p className="text-xs leading-5 text-ink-2">Select any graph node to inspect it. Context nodes show what could influence the task; authority remains a separate boundary downstream.</p>
            </div>
          ) : (
            <p className="mt-3 text-xs leading-5 text-ink-2">System mode aggregates the recent observed agent → action → resource → decision topology. Select an execution for the full authority and consequence chain.</p>
          )}
        </aside>
      </div>
    </div>
  );
}
