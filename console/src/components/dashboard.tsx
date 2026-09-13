// The home dashboard: a glance at project health before the raw feed below -
// mode, who's connected and running right now, what's waiting on a human,
// and how busy things have been recently. No new API calls: every piece here
// reads the same feed (agents, approvals, decisions) the page already polls.
import { useState } from "react";
import { Link } from "react-router-dom";
import type { Agent, DecisionSummary } from "@/lib/api";
import { ago, asDate, cn, outcomeLabel, outcomeTone } from "@/lib/format";
import { Badge } from "./ui";

const TONE_BADGE = { allow: "allow", deny: "deny", review: "approval", hold: "hold", neutral: "neutral" } as const;

export function StatTile({ label, value, sub, tone }: { label: string; value: string; sub?: string; tone?: "approval" | "deny" }) {
  return (
    <div className={cn("panel px-3.5 py-3",
      tone === "approval" && "border-approval/40 bg-approval-bg",
      tone === "deny" && "border-deny/40 bg-deny-bg")}>
      <div className="eyebrow">{label}</div>
      <div className="dot mt-1 text-xl font-semibold leading-none">{value}</div>
      {sub ? <div className="mt-1 text-xs text-ink-2">{sub}</div> : null}
    </div>
  );
}

// --------------------------------------------------------------------------- //
// RunningAgents - the agents with a task in flight right now, not the full
// roster (that's the Agents tab). Nothing to show when none are active.
// --------------------------------------------------------------------------- //
export function RunningAgents({ agents }: { agents: Agent[] }) {
  const active = agents.filter((a) => a.status === "active");
  if (!active.length) return null;
  const shown = active.slice(0, 5);
  return (
    <div className="panel mb-4 divide-y divide-hairline">
      <div className="flex items-center justify-between px-4 py-2">
        <span className="eyebrow">Running now</span>
        <Link to="/agents" className="text-2xs text-ink-2 underline underline-offset-2">All agents</Link>
      </div>
      {shown.map((a) => {
        const tone = a.last_action ? outcomeTone(a.last_action.outcome) : "neutral";
        return (
          <Link key={a.id} to={`/agents?agent=${a.id}`} className="row-hover flex items-center justify-between gap-3 px-4 py-2.5">
            <div className="min-w-0">
              <div className="flex items-center gap-2">
                <span className="lamp lamp-on animate-pulse2" />
                <span className="text-sm font-medium">{a.id}</span>
              </div>
              <div className="mt-0.5 truncate font-mono text-xs text-ink-2">{a.current_task ?? "no active task"}</div>
            </div>
            {a.last_action ? (
              <div className="shrink-0 text-right">
                <div className="flex items-center gap-2">
                  <span className="truncate font-mono text-xs">{a.last_action.action}</span>
                  <Badge tone={TONE_BADGE[tone]}>{outcomeLabel(a.last_action.outcome)}</Badge>
                </div>
                <div className="mt-0.5 text-2xs text-ink-3">{ago(a.last_action.at)}</div>
              </div>
            ) : null}
          </Link>
        );
      })}
      {active.length > shown.length ? (
        <div className="px-4 py-1.5 text-center text-2xs text-ink-3">+{active.length - shown.length} more running</div>
      ) : null}
    </div>
  );
}

// --------------------------------------------------------------------------- //
// ActivitySparkline - one series (action volume), one minute per bar, over
// the last half hour. Magnitude over time -> bars, not a line: counts are
// discrete and there are few enough buckets that bars read cleanly at this
// size. A single series needs no legend; the tile title names it.
// --------------------------------------------------------------------------- //
const BUCKETS = 30;
const BUCKET_MS = 60_000;

export function ActivitySparkline({ decisions, now }: { decisions: DecisionSummary[]; now: number }) {
  const start = now - BUCKETS * BUCKET_MS;
  const counts = new Array(BUCKETS).fill(0);
  for (const d of decisions) {
    const t = asDate(d.created_at)?.getTime();
    if (t == null || t < start || t > now) continue;
    counts[Math.min(BUCKETS - 1, Math.floor((t - start) / BUCKET_MS))] += 1;
  }
  const total = counts.reduce((a, b) => a + b, 0);
  const max = Math.max(1, ...counts);
  const [hover, setHover] = useState<number | null>(null);

  const width = 300, height = 40, gap = 2;
  const barWidth = (width - gap * (BUCKETS - 1)) / BUCKETS;

  return (
    <div className="panel px-3.5 py-3">
      <div className="flex items-baseline justify-between">
        <div className="eyebrow">Actions · last 30 min</div>
        <div className="dot text-xl font-semibold leading-none">{total}</div>
      </div>
      <div className="relative mt-2">
        <svg viewBox={`0 0 ${width} ${height}`} className="block w-full" onMouseLeave={() => setHover(null)}>
          {counts.map((c, i) => {
            const live = i === BUCKETS - 1 && c > 0;
            const h = c ? Math.max(2, (c / max) * (height - 4)) : 1;
            return (
              <rect
                key={i}
                x={i * (barWidth + gap)} y={height - h} width={barWidth} height={h} rx={Math.min(1.5, barWidth / 2)}
                className={cn(live ? "fill-ink animate-pulse2" : c ? "fill-ink" : "fill-hairline", !c && !live && "opacity-70")}
                onMouseEnter={() => setHover(i)}
              />
            );
          })}
        </svg>
        {hover !== null ? (
          <div
            className="pointer-events-none absolute -top-6 -translate-x-1/2 whitespace-nowrap rounded border border-hairline-strong bg-paper-raised px-1.5 py-0.5 font-mono text-2xs shadow-hairline"
            style={{ left: `${((hover + 0.5) / BUCKETS) * 100}%` }}
          >
            {counts[hover]} · {new Date(start + hover * BUCKET_MS).toISOString().slice(11, 16)}
          </div>
        ) : null}
      </div>
    </div>
  );
}
