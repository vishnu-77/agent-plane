// The home dashboard: a glance at project health before the raw feed below -
// mode, who's connected and running right now, what's waiting on a human,
// and how busy things have been recently. No new API calls: every piece here
// reads the same feed (agents, approvals, decisions) the page already polls.
import { useState } from "react";
import { Link } from "react-router-dom";
import type { Agent, DecisionSummary } from "@/lib/api";
import { ago, asDate, cn, outcomeLabel, outcomeTone, verdictTone, type Tone } from "@/lib/format";
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
// DecisionTimeline - not "actions over time" (that tells you activity, not
// authority): each bar is a minute of decisions stacked by verdict, the same
// tones as every Badge and lamp elsewhere in the console. The shape of the
// stack *is* the story - a bar that's all quiet gray is unruled traffic
// nobody has judged yet; a red cap growing is enforcement doing its job.
// Composition over time -> a stacked bar, one tile per bucket; a legend
// names the tones once since color is the only encoding.
// --------------------------------------------------------------------------- //
const BUCKETS = 30;
const BUCKET_MS = 60_000;
// Bottom -> top: quiet fact of the project first, most consequential last,
// so a bar that needed a human or a block visibly caps the stack.
const TONE_STACK: Tone[] = ["neutral", "allow", "review", "hold", "deny"];
const TONE_HEX: Record<Tone, string> = {
  neutral: "#9B9B93", allow: "#3E6B50", review: "#9A6B12", hold: "#4A4A8A", deny: "#B4322A",
};
const TONE_LABEL: Record<Tone, string> = {
  neutral: "No rule", allow: "Allowed", review: "Review", hold: "Held", deny: "Blocked",
};

export function DecisionTimeline({ decisions, now }: { decisions: DecisionSummary[]; now: number }) {
  const start = now - BUCKETS * BUCKET_MS;
  // counts[bucket][tone]
  const counts: Record<Tone, number>[] = Array.from({ length: BUCKETS },
    () => ({ neutral: 0, allow: 0, review: 0, hold: 0, deny: 0 }));
  for (const d of decisions) {
    const t = asDate(d.created_at)?.getTime();
    if (t == null || t < start || t > now) continue;
    const bucket = Math.min(BUCKETS - 1, Math.floor((t - start) / BUCKET_MS));
    counts[bucket][verdictTone(d)] += 1;
  }
  const totals = counts.map((c) => TONE_STACK.reduce((sum, tone) => sum + c[tone], 0));
  const total = totals.reduce((a, b) => a + b, 0);
  const max = Math.max(1, ...totals);
  const present = TONE_STACK.filter((tone) => counts.some((c) => c[tone] > 0));
  const [hover, setHover] = useState<number | null>(null);

  const width = 300, height = 40, gap = 2;
  const barWidth = (width - gap * (BUCKETS - 1)) / BUCKETS;

  return (
    <div className="panel px-3.5 py-3">
      <div className="flex items-baseline justify-between">
        <div className="eyebrow">Decisions · last 30 min</div>
        <div className="dot text-xl font-semibold leading-none">{total}</div>
      </div>
      <div className="relative mt-2">
        <svg viewBox={`0 0 ${width} ${height}`} className="block w-full" onMouseLeave={() => setHover(null)}>
          {counts.map((bucket, i) => {
            const x = i * (barWidth + gap);
            const live = i === BUCKETS - 1 && totals[i] > 0;
            if (!totals[i]) {
              return <rect key={i} x={x} y={height - 1} width={barWidth} height={1} className="fill-hairline" onMouseEnter={() => setHover(i)} />;
            }
            let y = height;
            return (
              <g key={i} onMouseEnter={() => setHover(i)} className={live ? "animate-pulse2" : undefined}>
                {TONE_STACK.filter((tone) => bucket[tone] > 0).map((tone) => {
                  const h = Math.max(1, (bucket[tone] / max) * (height - 4));
                  y -= h;
                  return <rect key={tone} x={x} y={y} width={barWidth} height={h} fill={TONE_HEX[tone]} rx={Math.min(1, barWidth / 2)} />;
                })}
              </g>
            );
          })}
        </svg>
        {hover !== null ? (
          <div
            className="pointer-events-none absolute -top-8 -translate-x-1/2 whitespace-nowrap rounded border border-hairline-strong bg-paper-raised px-1.5 py-1 font-mono text-2xs shadow-hairline"
            style={{ left: `${((hover + 0.5) / BUCKETS) * 100}%` }}
          >
            {new Date(start + hover * BUCKET_MS).toISOString().slice(11, 16)}
            {TONE_STACK.filter((tone) => counts[hover][tone] > 0)
              .map((tone) => ` · ${TONE_LABEL[tone].toLowerCase()} ${counts[hover][tone]}`).join("") || " · nothing"}
          </div>
        ) : null}
      </div>
      {present.length ? (
        <div className="mt-2 flex flex-wrap gap-x-3 gap-y-1">
          {present.map((tone) => (
            <span key={tone} className="flex items-center gap-1 text-2xs text-ink-2">
              <span className="lamp" style={{ backgroundColor: TONE_HEX[tone] }} />
              {TONE_LABEL[tone]}
            </span>
          ))}
        </div>
      ) : null}
    </div>
  );
}
