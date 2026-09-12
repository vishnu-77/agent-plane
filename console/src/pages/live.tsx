import { useEffect, useMemo, useState } from "react";
import { AnimatePresence, motion } from "motion/react";
import { api, type ScenarioRun } from "@/lib/api";
import { useStore } from "@/lib/store";
import { cn, outcomeLabel, outcomeTone, reasonText } from "@/lib/utils";
import { AuthorityConsequenceGraph, graphFromTrace } from "@/components/graph";
import { DecisionStrip, EvidenceDrawer, useDecision } from "@/components/decision";
import { DotMetric, InstrumentPanel, StateLamp } from "@/components/instruments";
import { Badge, Button } from "@/components/ui";

export function LivePage() {
  const { snapshot, selected, select, mode, creds, scenarios, refresh, connected } = useStore();
  const [drawer, setDrawer] = useState(false);
  const [running, setRunning] = useState<string | null>(null);
  const [lastRun, setLastRun] = useState<ScenarioRun | null>(null);
  const [error, setError] = useState<string | null>(null);

  // Follow the newest decision unless the operator pinned one.
  const [pinned, setPinned] = useState(false);
  useEffect(() => {
    if (!pinned && snapshot.decisions.length && snapshot.decisions[0].decision_id !== selected) select(snapshot.decisions[0].decision_id);
  }, [snapshot.decisions, pinned, selected, select]);

  const { detail } = useDecision(selected);
  const trace = detail?.trace ?? null;
  const siblings = useMemo(() => (trace ? snapshot.agents.filter((a) => a.current_task === trace.task.id || a.tasks.includes(trace.task.id)) : []), [trace, snapshot.agents]);
  const graph = useMemo(() => (trace ? graphFromTrace(trace, siblings) : null), [trace, siblings]);
  const sys = snapshot.system;
  const counts = sys?.decisions ?? {};

  async function runScenario(name: string) {
    setRunning(name);
    setError(null);
    setPinned(false);
    try {
      // Step by step so the graph visibly evolves.
      const scenario = scenarios.find((s) => s.name === name);
      const steps = scenario?.steps.map((s) => s.index) ?? [];
      const runId = Math.random().toString(36).slice(2, 8);
      let last: ScenarioRun | null = null;
      for (const i of steps) {
        last = await api<ScenarioRun>(`/demo/scenarios/${name}/run`, { mode, creds, method: "POST", body: JSON.stringify({ steps: [i], run_id: runId }) });
        setLastRun(last);
        await refresh();
        await new Promise((r) => setTimeout(r, 900));
      }
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setRunning(null);
    }
  }

  return (
    <div className="flex h-full flex-col">
      {/* telemetry strip */}
      <div className="flex flex-wrap items-center gap-x-8 gap-y-2 border-b border-hairline bg-paper-raised px-4 py-2.5">
        <DotMetric value={sys?.active_agents ?? 0} label="active" />
        <DotMetric value={sys?.tasks ?? 0} label="tasks" />
        <DotMetric value={counts.allow ?? 0} label="allow" tone="allow" width={3} />
        <DotMetric value={counts.deny ?? 0} label="deny" tone="deny" />
        <DotMetric value={counts.approval_required ?? 0} label="approval" tone="approval" />
        {counts.simulate ? <DotMetric value={counts.simulate} label="simulated" tone="hold" /> : null}
        {sys?.quarantined ? <DotMetric value={sys.quarantined} label="quarantined" tone="hold" /> : null}
        <div className="ml-auto flex items-center gap-3">
          <StateLamp tone={sys ? (sys.mode === "enforce" ? "on" : "approval") : "off"} label={sys?.mode ?? "offline"} pulse />
          {mode === "demo" ? (
            <div className="flex items-center gap-1.5">
              {scenarios.map((s) => (
                <Button key={s.name} size="sm" disabled={!!running || !connected} onClick={() => void runScenario(s.name)} title={s.prompt}>
                  {running === s.name ? "running…" : s.title}
                </Button>
              ))}
              <Button size="sm" variant="ghost" disabled={!!running} onClick={async () => { await api("/demo/reset", { mode, creds, method: "POST" }); setLastRun(null); select(null); await refresh(); }}>
                Reset
              </Button>
            </div>
          ) : null}
        </div>
      </div>
      {error ? <div className="border-b border-hairline bg-deny-bg px-4 py-1.5 text-xs text-deny">{error}</div> : null}

      <div className="grid min-h-0 flex-1 grid-cols-1 lg:grid-cols-[minmax(0,1.65fr)_minmax(340px,1fr)]">
        {/* canvas */}
        <InstrumentPanel
          className="rounded-none border-0 border-b border-hairline lg:border-b-0 lg:border-r"
          eyebrow="Authority–Consequence Graph"
          title={trace ? `${trace.identity.agent} · ${trace.action.name} → ${trace.resource.name}` : "Waiting for a decision"}
          right={
            <>
              {pinned ? <Button size="sm" variant="ghost" onClick={() => setPinned(false)}>Follow live</Button> : <Badge tone="ink">following</Badge>}
              {lastRun ? <span className="font-mono text-2xs text-ink-2">{lastRun.title} · run {lastRun.run_id}</span> : null}
            </>
          }
          dense
        >
          <div className="h-[420px] lg:h-full">
            <AuthorityConsequenceGraph graph={graph} live={!!running} onSelect={() => { setPinned(true); setDrawer(true); }} />
          </div>
        </InstrumentPanel>

        {/* current decision */}
        <div className="flex min-h-0 flex-col">
          <InstrumentPanel className="rounded-none border-0 border-b border-hairline" eyebrow="Current decision" title={trace ? trace.decision_id : "—"} dense>
            <AnimatePresence mode="wait">
              {trace ? (
                <motion.div key={trace.decision_id} initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0 }} transition={{ duration: 0.18 }} className="p-3">
                  <div className={cn("dot text-[26px] font-semibold leading-none", { "text-allow": outcomeTone(trace.decision.outcome) === "allow", "text-deny": outcomeTone(trace.decision.outcome) === "deny", "text-approval": outcomeTone(trace.decision.outcome) === "approval", "text-hold": outcomeTone(trace.decision.outcome) === "hold" })}>
                    {outcomeLabel(trace.decision.outcome)}
                  </div>
                  {trace.decision.would_be ? <div className="mt-1 font-mono text-2xs text-hold">observe mode · would be {outcomeLabel(trace.decision.would_be)}</div> : null}
                  <div className="mt-3 space-y-0.5 font-mono text-sm">
                    <div>{trace.identity.agent}</div>
                    <div>{trace.action.name}</div>
                    <div className="text-ink-2">{trace.resource.name}</div>
                  </div>
                  <dl className="mt-4 grid grid-cols-[92px_1fr] gap-x-3 gap-y-2 text-sm">
                    <dt className="eyebrow pt-0.5">Authority</dt>
                    <dd className="font-mono text-xs">{trace.authority.resources.join(", ") || <span className="text-deny">none for this task</span>}</dd>
                    <dt className="eyebrow pt-0.5">Consequence</dt>
                    <dd className="text-xs">
                      {trace.consequence ? trace.consequence.summary.map((s, i) => <div key={i} className={i === 0 ? "" : "text-ink-2"}>{s}</div>) : "—"}
                    </dd>
                    <dt className="eyebrow pt-0.5">Result</dt>
                    <dd className="font-mono text-xs uppercase tracking-[0.1em]">{reasonText(trace.decision.reason)}</dd>
                  </dl>
                  <Button className="mt-4 w-full justify-center" onClick={() => setDrawer(true)}>View evidence</Button>
                </motion.div>
              ) : (
                <div className="p-3 text-xs text-ink-2">
                  {mode === "demo" ? "Run a scenario to see the chain: prompt → task → agent → authority → action → consequence → decision." : "Decisions appear here as agents act. Connect operator access or switch to DEMO."}
                </div>
              )}
            </AnimatePresence>
          </InstrumentPanel>
          <InstrumentPanel className="min-h-0 flex-1 rounded-none border-0" eyebrow="Stream" title={`${snapshot.decisions.length} recent decisions`} dense>
            <div className="max-h-[360px] overflow-auto lg:max-h-none lg:h-full">
              {snapshot.decisions.slice(0, 40).map((d) => (
                <DecisionStrip key={d.decision_id} d={d} selected={d.decision_id === selected} onSelect={(id) => { setPinned(true); select(id); }} />
              ))}
              {!snapshot.decisions.length ? <div className="p-3 text-xs text-ink-2">No decisions yet.</div> : null}
            </div>
          </InstrumentPanel>
        </div>
      </div>
      <EvidenceDrawer id={selected} open={drawer} onOpenChange={setDrawer} />
    </div>
  );
}
