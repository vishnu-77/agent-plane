import { useSearchParams } from "react-router-dom";
import { useStore } from "@/lib/store";
import { ago, cn, outcomeLabel, outcomeTone } from "@/lib/utils";
import { PromptOrigin } from "@/components/registry";
import { InstrumentPanel, StateLamp } from "@/components/instruments";
import { Badge, Empty } from "@/components/ui";

export function TasksPage() {
  const { snapshot } = useStore();
  const [params, setParams] = useSearchParams();
  const selectedId = params.get("select");
  const task = snapshot.tasks.find((t) => t.id === selectedId) ?? null;

  return (
    <div className="grid h-full grid-cols-1 lg:grid-cols-[minmax(0,1fr)_minmax(360px,0.9fr)]">
      <div className="p-4">
        <div className="mb-3">
          <div className="eyebrow">System</div>
          <h1 className="text-lg font-medium tracking-tight">Tasks</h1>
          <p className="text-xs text-ink-2">What needs to happen, who asked for it, and which agents are acting on it.</p>
        </div>
        <InstrumentPanel dense>
          {snapshot.tasks.length ? (
            snapshot.tasks.map((t) => (
              <button
                key={`${t.tenant}:${t.id}`}
                type="button"
                onClick={() => setParams({ select: t.id })}
                aria-pressed={t.id === selectedId}
                className={cn("row-hover grid w-full grid-cols-1 gap-3 border-b border-hairline px-4 py-3 text-left md:grid-cols-[1fr_1.4fr_1fr]", t.id === selectedId && "bg-paper-sunk")}
              >
                <div>
                  <div className="flex items-center gap-2"><StateLamp tone={t.status === "active" ? "on" : "off"} /><span className="font-medium">{t.id}</span></div>
                  <div className="mt-0.5 font-mono text-2xs text-ink-2">{t.tenant} · {ago(t.last_activity)}</div>
                </div>
                <PromptOrigin origin={t.origin} compact />
                <div className="flex flex-wrap items-center gap-1">
                  {t.agents.map((a) => <Badge key={a} tone="ink" className="normal-case tracking-normal">{a}</Badge>)}
                  <span className="ml-auto font-mono text-2xs text-ink-2">{Object.entries(t.decisions).map(([k, v]) => `${outcomeLabel(k)} ${v}`).join(" · ")}</span>
                </div>
              </button>
            ))
          ) : (
            <Empty title="No tasks yet">Tasks are created when an agent acts under one, or explicitly via POST /v1/tasks with the prompt or event that raised it.</Empty>
          )}
        </InstrumentPanel>
      </div>
      <aside className="border-t border-hairline bg-paper-raised p-4 lg:border-l lg:border-t-0">
        {task ? (
          <div className="space-y-4">
            <div>
              <div className="eyebrow">Task</div>
              <div className="text-base font-medium">{task.id}</div>
            </div>
            <div>
              <div className="eyebrow">Origin</div>
              <PromptOrigin origin={task.origin} />
              {task.origin.parent_agent ? <div className="mt-1 font-mono text-2xs text-ink-2">parent agent: {task.origin.parent_agent}</div> : null}
            </div>
            <div>
              <div className="eyebrow">Agents</div>
              <div className="mt-1 flex flex-wrap gap-1">{task.agents.map((a) => <Badge key={a} tone="ink" className="normal-case tracking-normal">{a}</Badge>)}</div>
            </div>
            <div>
              <div className="eyebrow">Leases</div>
              <div className="mt-1 font-mono text-xs">{task.leases.join(", ") || "none"}</div>
            </div>
            <div>
              <div className="eyebrow">Observed actions</div>
              <ul className="mt-1 font-mono text-xs">
                {Object.entries(task.observed_actions).map(([a, n]) => (
                  <li key={a} className="flex justify-between border-b border-hairline py-1"><span>{a}</span><span className="text-ink-2">×{n}</span></li>
                ))}
              </ul>
            </div>
            <div>
              <div className="eyebrow">Resources touched</div>
              <ul className="mt-1 font-mono text-xs">{task.resources.map((r) => <li key={r} className="border-b border-hairline py-1">{r}</li>)}</ul>
            </div>
            <div>
              <div className="eyebrow">Decisions</div>
              <div className="mt-1 flex flex-wrap gap-1">{Object.entries(task.decisions).map(([k, v]) => <Badge key={k} tone={outcomeTone(k)}>{outcomeLabel(k)} {v}</Badge>)}</div>
            </div>
          </div>
        ) : (
          <div className="text-xs text-ink-2">Select a task to see its origin, agents, leases, and what was observed under it.</div>
        )}
      </aside>
    </div>
  );
}
