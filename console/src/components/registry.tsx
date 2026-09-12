import type { AgentSummary, Origin, TaskRecord } from "@/lib/api";
import { ago, cn, outcomeLabel, outcomeTone } from "@/lib/utils";
import { Badge } from "./ui";
import { StateLamp } from "./instruments";

// ---------------------------------------------------------------- AuthorityChip
export function AuthorityChip({ action, state, scope }: { action: string; state?: "granted" | "observed" | "denied" | "undeclared" | "unused"; scope?: string }) {
  const tone = state === "denied" ? "deny" : state === "undeclared" ? "deny" : state === "granted" ? "ink" : state === "unused" ? "neutral" : "neutral";
  return (
    <span className={cn("inline-flex items-center gap-1.5 rounded-sm border px-1.5 py-[2px] font-mono text-xs", {
      "border-ink text-ink": tone === "ink",
      "border-deny/50 text-deny bg-deny-bg": tone === "deny",
      "border-hairline-strong text-ink-2": tone === "neutral",
    })}>
      {action}
      {scope ? <span className="text-ink-3">{scope}</span> : null}
      {state === "undeclared" ? <span className="dot text-[9px]">undeclared</span> : null}
    </span>
  );
}

// ---------------------------------------------------------------- PromptOrigin / TaskOrigin
export function PromptOrigin({ origin, compact }: { origin: Origin | null | undefined; compact?: boolean }) {
  if (!origin || (!origin.text && !origin.ref && !origin.created_by)) return <span className="text-xs text-ink-3">origin not recorded</span>;
  return (
    <div className="min-w-0">
      <div className="font-mono text-2xs text-ink-2">
        {origin.kind}{origin.ref ? ` · ${origin.ref}` : ""}{origin.created_by ? ` · ${origin.created_by}` : ""}
      </div>
      {origin.text ? <blockquote className={cn("mt-0.5 border-l-2 border-hairline-strong pl-2 text-ink", compact ? "truncate text-xs" : "text-sm")}>“{origin.text}”</blockquote> : null}
    </div>
  );
}

export function TaskOrigin({ task }: { task: TaskRecord }) {
  return (
    <div>
      <div className="eyebrow">Task</div>
      <div className="font-medium">{task.id}</div>
      <div className="mt-1">
        <div className="eyebrow">Origin</div>
        <PromptOrigin origin={task.origin} />
      </div>
    </div>
  );
}

// ---------------------------------------------------------------- AgentRow
export function AgentRow({ agent, selected, onSelect, task }: { agent: AgentSummary; selected?: boolean; onSelect?: (id: string) => void; task?: TaskRecord }) {
  const lastTone = agent.last_action ? outcomeTone(agent.last_action.outcome) : "neutral";
  const observed = Object.values(agent.requested_authority).reduce((a, b) => a + b, 0);
  return (
    <button
      type="button"
      onClick={() => onSelect?.(agent.id)}
      aria-pressed={selected}
      className={cn("row-hover grid w-full grid-cols-1 gap-3 border-b border-hairline px-4 py-3 text-left md:grid-cols-[1.1fr_1.4fr_1.4fr_0.9fr]", selected && "bg-paper-sunk")}
    >
      <div className="min-w-0">
        <div className="flex items-center gap-2">
          <StateLamp tone={agent.status === "quarantined" ? "hold" : agent.status === "active" ? "on" : "off"} pulse={agent.status === "active"} />
          <span className="dot text-sm font-semibold">{agent.id}</span>
          {agent.status === "quarantined" ? <Badge tone="hold">quarantined</Badge> : null}
        </div>
        <div className="mt-1 font-mono text-2xs text-ink-2">
          {agent.framework ?? "unknown runtime"} · {agent.application}
          {agent.parent_agent ? ` · ← ${agent.parent_agent}` : ""}
        </div>
      </div>
      <div className="min-w-0">
        <div className="eyebrow">Task</div>
        <div className="truncate text-sm">{agent.current_task ?? "—"}</div>
        <div className="mt-1 eyebrow">Origin</div>
        <PromptOrigin origin={task?.origin ?? agent.origin} compact />
      </div>
      <div className="min-w-0">
        <div className="eyebrow">Authority</div>
        <div className="mt-0.5 flex flex-wrap gap-1">
          {agent.granted_authority.length ? agent.granted_authority.slice(0, 6).map((a) => <AuthorityChip key={a} action={a} state="granted" />) : <span className="text-xs text-ink-3">nothing granted</span>}
          {agent.granted_authority.length > 6 ? <span className="font-mono text-2xs text-ink-2">+{agent.granted_authority.length - 6}</span> : null}
        </div>
        <div className="mt-1 font-mono text-2xs text-ink-2">
          observed {observed} actions
          {Object.keys(agent.denied_authority).length ? ` · ${Object.values(agent.denied_authority).reduce((a, b) => a + b, 0)} denied` : ""}
        </div>
      </div>
      <div className="min-w-0">
        <div className="eyebrow">Last action</div>
        {agent.last_action ? (
          <>
            <div className="truncate font-mono text-xs">{agent.last_action.action}</div>
            <div className="mt-0.5 flex items-center gap-2">
              <Badge tone={lastTone}>{outcomeLabel(agent.last_action.outcome)}</Badge>
              <span className="font-mono text-2xs text-ink-2">{ago(agent.last_action.at)}</span>
            </div>
          </>
        ) : (
          <span className="text-xs text-ink-3">none</span>
        )}
      </div>
    </button>
  );
}
