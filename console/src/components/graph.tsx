// The Authority–Consequence Graph: one runtime story, drawn as a layered
// graph. ORIGIN → TASK → AGENT → AUTHORITY → ACTION → RESOURCE → DIRECT
// EFFECT → DOWNSTREAM → CONSEQUENCE → DECISION. Custom SVG, no dependency:
// hairlines, dots, and one colour only where a state needs it.
//
// The chain is folded into two bands so it fits one screen: the authority
// band (who is authorised, from whom, for what) above, the consequence band
// (what it causes, how far it reaches, the decision) below. Two views of the
// same runtime, fused at the resource.
import { useEffect, useMemo, useRef, useState } from "react";
import type { AgentSummary, Consequence, LineageLink, Trace } from "@/lib/api";
import { cn, outcomeLabel, outcomeTone, shortId } from "@/lib/utils";

export type Layer = "origin" | "task" | "agent" | "authority" | "action" | "resource" | "effect" | "downstream" | "consequence" | "decision";

export interface GNode {
  id: string;
  layer: Layer;
  label: string;
  sub?: string;
  tone?: "allow" | "deny" | "approval" | "hold" | "neutral";
  onPath?: boolean;
  dim?: boolean;
  data?: unknown;
}
export interface GEdge {
  from: string;
  to: string;
  kind: "authority" | "consequence" | "lineage" | "sibling";
  onPath?: boolean;
  tone?: "allow" | "deny" | "approval" | "hold" | "neutral";
  label?: string;
}
export interface Graph {
  nodes: GNode[];
  edges: GEdge[];
}

export const LAYERS: Layer[] = ["origin", "task", "agent", "authority", "action", "resource", "effect", "downstream", "consequence", "decision"];
const BAND_OF: Record<Layer, 0 | 1> = { origin: 0, task: 0, agent: 0, authority: 0, action: 0, resource: 0, effect: 1, downstream: 1, consequence: 1, decision: 1 };
const LAYER_TITLE: Record<Layer, string> = {
  origin: "ORIGIN",
  task: "TASK",
  agent: "AGENT",
  authority: "AUTHORITY",
  action: "ACTION",
  resource: "RESOURCE",
  effect: "DIRECT EFFECT",
  downstream: "DOWNSTREAM",
  consequence: "CONSEQUENCE",
  decision: "DECISION",
};

// ---------------------------------------------------------------- model
export function graphFromTrace(trace: Trace, siblings: AgentSummary[] = [], opts: { lineageOnly?: boolean } = {}): Graph {
  const nodes: GNode[] = [];
  const edges: GEdge[] = [];
  const add = (n: GNode) => {
    if (!nodes.find((x) => x.id === n.id)) nodes.push(n);
    return n.id;
  };
  const tone = outcomeTone(trace.decision.outcome);
  const taskOrigin = trace.task.origin as Partial<{ created_by: string; kind: string }>;
  const leaseOrigin = trace.authority.origin as Partial<{ created_by: string; kind: string }>;
  const rootOrigin = trace.authority.lineage[0]?.origin as Partial<{ created_by: string; kind: string }> | undefined;
  const originText = taskOrigin.created_by || leaseOrigin.created_by || rootOrigin?.created_by || trace.identity.user || "unknown";
  const originKind = taskOrigin.kind || rootOrigin?.kind || leaseOrigin.kind || "intent";
  const origin = add({ id: "origin", layer: "origin", label: String(originText), sub: String(originKind), onPath: true });
  const task = add({ id: `task:${trace.task.id}`, layer: "task", label: trace.task.id, sub: trace.identity.tenant, onPath: true });
  edges.push({ from: origin, to: task, kind: "lineage", onPath: true });

  // Lineage: ancestors of the acting agent, in order, then the agent itself.
  const chain: LineageLink[] = trace.authority.lineage;
  const subjects = chain.map((l) => l.subject).filter(Boolean) as string[];
  if (!subjects.includes(trace.identity.agent)) subjects.push(trace.identity.agent);
  let prev: string | null = null;
  subjects.forEach((s, i) => {
    const link = chain.find((l) => l.subject === s);
    const id = add({ id: `agent:${s}`, layer: "agent", label: s, sub: i === 0 ? "root agent" : "delegated", onPath: true });
    if (i === 0) edges.push({ from: task, to: id, kind: "lineage", onPath: true });
    if (prev) edges.push({ from: prev, to: id, kind: "lineage", onPath: true, label: link ? `${link.actions?.length ?? 0} actions` : undefined });
    prev = id;
  });
  const actingAgent = `agent:${trace.identity.agent}`;

  // Siblings on the same task (branching agents), not on the path.
  for (const s of siblings) {
    if (s.id === trace.identity.agent || subjects.includes(s.id)) continue;
    const id = add({ id: `agent:${s.id}`, layer: "agent", label: s.id, sub: s.last_action?.action ?? "idle", dim: true,
      tone: s.last_action ? outcomeTone(s.last_action.outcome) : "neutral" });
    const parent = s.parent_agent && subjects.includes(s.parent_agent) ? `agent:${s.parent_agent}` : task;
    edges.push({ from: parent, to: id, kind: "sibling" });
  }

  const leaseId = trace.authority.lease;
  const authority = add({
    id: `lease:${leaseId ?? "none"}`,
    layer: "authority",
    label: leaseId ? shortId(leaseId, 26) : "no lease",
    sub: leaseId ? `${trace.authority.actions.length} actions · ${trace.authority.resources.join(", ") || "no scope"}` : "nothing granted for this task",
    onPath: true,
    tone: leaseId ? "neutral" : "deny",
  });
  edges.push({ from: actingAgent, to: authority, kind: "authority", onPath: true });

  const permits = trace.authority.lineage_permits_action;
  const action = add({ id: `action:${trace.action.name}`, layer: "action", label: trace.action.name, sub: trace.action.effect ?? undefined, onPath: true, tone: permits ? "neutral" : "deny" });
  edges.push({ from: authority, to: action, kind: "authority", onPath: true, tone: permits ? "neutral" : "deny", label: permits ? "granted" : "not granted" });

  const scopeTone = trace.resource.scope === "within" ? "allow" : "deny";
  const resource = add({ id: `res:${trace.resource.name}`, layer: "resource", label: trace.resource.name, sub: trace.resource.scope, onPath: true, tone: scopeTone });
  edges.push({ from: action, to: resource, kind: "authority", onPath: true, tone: scopeTone, label: trace.resource.scope });

  if (opts.lineageOnly) {
    const decision = add({ id: "decision", layer: "decision", label: outcomeLabel(trace.decision.outcome), sub: trace.decision.reason, onPath: true, tone });
    edges.push({ from: resource, to: decision, kind: "consequence", onPath: true, tone });
    return { nodes, edges };
  }

  const c: Consequence | null = trace.consequence;
  const readOnly = !c || c.effect === "read" || c.effect === "list";
  const effect = add({ id: "effect", layer: "effect", label: c?.direct_effect ?? "no state change", sub: c ? `${c.effect} · ${c.reversibility}` : undefined, onPath: true });
  edges.push({ from: resource, to: effect, kind: "consequence", onPath: true });
  if (c && c.downstream.length) {
    c.downstream.slice(0, 5).forEach((d) => {
      const id = add({ id: `down:${d}`, layer: "downstream", label: d, sub: "dependent", onPath: true });
      edges.push({ from: effect, to: id, kind: "consequence", onPath: true });
    });
  } else {
    const none = add({ id: "down:none", layer: "downstream", label: "none reachable", sub: readOnly ? "read only" : "isolated", onPath: true, dim: true });
    edges.push({ from: effect, to: none, kind: "consequence", onPath: true });
  }
  const consequence = add({
    id: "consequence",
    layer: "consequence",
    label: c ? `${c.impact} impact` : "unknown",
    sub: c ? [c.environment, c.customer_facing ? "customer-facing" : null, `blast ${c.blast_radius}`].filter(Boolean).join(" · ") : undefined,
    onPath: true,
    tone: c && (c.impact === "critical" || c.impact === "high") ? "deny" : "neutral",
  });
  for (const n of nodes.filter((n) => n.layer === "downstream")) edges.push({ from: n.id, to: consequence, kind: "consequence", onPath: true });
  const decision = add({ id: "decision", layer: "decision", label: outcomeLabel(trace.decision.outcome), sub: trace.decision.reason, onPath: true, tone });
  edges.push({ from: consequence, to: decision, kind: "consequence", onPath: true, tone });
  return { nodes, edges };
}

// ---------------------------------------------------------------- layout
interface Placed extends GNode {
  x: number;
  y: number;
  w: number;
  h: number;
  band: 0 | 1;
}

const NODE_H = 46;
const TITLE_H = 30;

function layout(graph: Graph, availW: number, availH: number) {
  const byLayer = new Map<Layer, GNode[]>();
  for (const n of graph.nodes) byLayer.set(n.layer, [...(byLayer.get(n.layer) ?? []), n]);
  const active = LAYERS.filter((l) => byLayer.has(l));
  const bands: Array<Layer[]> = [active.filter((l) => BAND_OF[l] === 0), active.filter((l) => BAND_OF[l] === 1)].filter((b) => b.length);
  const cols = Math.max(...bands.map((b) => b.length));
  // Columns fill the available width; below a legible minimum the canvas scrolls.
  const colW = Math.max(132, Math.floor(availW / cols));
  const gutter = Math.max(18, Math.round(colW * 0.14));
  const bandH = Math.max(150, Math.floor((availH - 8) / bands.length));
  const placed: Placed[] = [];
  const titles: Array<{ layer: Layer; x: number; y: number; index: number }> = [];
  bands.forEach((layers, b) => {
    const y0 = b * bandH;
    const usable = bandH - TITLE_H - 12;
    layers.forEach((l, col) => {
      titles.push({ layer: l, x: col * colW + gutter / 2, y: y0 + TITLE_H - 10, index: active.indexOf(l) + 1 });
      const items = byLayer.get(l)!;
      items.sort((a, b2) => Number(!!b2.onPath) - Number(!!a.onPath));
      const gap = usable / (items.length + 1);
      items.forEach((n, i) => placed.push({ ...n, x: col * colW + gutter / 2, y: y0 + TITLE_H + gap * (i + 1), w: colW - gutter, h: NODE_H, band: b as 0 | 1 }));
    });
  });
  return { placed, titles, width: cols * colW, height: bands.length * bandH, colW, bands: bands.length };
}

// ---------------------------------------------------------------- component
export function AuthorityConsequenceGraph({
  graph,
  className,
  onSelect,
  selectedId,
  live,
  compact,
}: {
  graph: Graph | null;
  className?: string;
  onSelect?: (n: GNode) => void;
  selectedId?: string | null;
  live?: boolean;
  compact?: boolean;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const [size, setSize] = useState({ w: 900, h: 420 });
  useEffect(() => {
    if (!ref.current) return;
    const ro = new ResizeObserver((entries) => {
      for (const e of entries) setSize({ w: Math.max(320, e.contentRect.width), h: Math.max(280, e.contentRect.height) });
    });
    ro.observe(ref.current);
    return () => ro.disconnect();
  }, []);

  const model = useMemo(() => (graph ? layout(graph, size.w, size.h) : null), [graph, size.w, size.h]);
  const svgW = model ? Math.max(size.w, model.width) : size.w;
  const svgH = model ? Math.max(size.h, model.height) : size.h;
  const labelChars = compact ? 20 : 24;

  return (
    <div ref={ref} className={cn("relative h-full w-full overflow-auto bg-paper-raised", className)}>
      {!model ? (
        <div className="absolute inset-0 flex items-center justify-center text-xs text-ink-2">Select a decision to draw its chain.</div>
      ) : (
        <svg width={svgW} height={svgH} viewBox={`0 0 ${svgW} ${svgH}`} className="block select-none">
          <defs>
            <pattern id="grid" width="16" height="16" patternUnits="userSpaceOnUse">
              <circle cx="1" cy="1" r="0.6" fill="#11110F" fillOpacity="0.12" />
            </pattern>
            <marker id="arrow" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
              <path d="M0,0.5 L7,4 L0,7.5" fill="none" stroke="#11110F" strokeWidth="0.9" />
            </marker>
            <marker id="arrow-deny" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
              <path d="M0,0.5 L7,4 L0,7.5" fill="none" stroke="#B4322A" strokeWidth="0.9" />
            </marker>
            <marker id="arrow-allow" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
              <path d="M0,0.5 L7,4 L0,7.5" fill="none" stroke="#3E6B50" strokeWidth="0.9" />
            </marker>
          </defs>
          <rect width="100%" height="100%" fill="url(#grid)" />
          {/* band divider + labels */}
          {model.bands > 1 ? (
            <g>
              <line x1={0} y1={model.height / 2} x2={svgW} y2={model.height / 2} stroke="#D7D7CF" strokeDasharray="2 4" />
              <text x={svgW - 10} y={model.height / 2 - 8} textAnchor="end" className="dot" fontSize="8.5" fill="#9B9B93" letterSpacing="1.8">AUTHORITY · who is authorised</text>
              <text x={svgW - 10} y={model.height - 8} textAnchor="end" className="dot" fontSize="8.5" fill="#9B9B93" letterSpacing="1.8">CONSEQUENCE · what it can cause</text>
            </g>
          ) : null}
          {/* layer titles */}
          {model.titles.map((t) => (
            <text key={t.layer} x={t.x} y={t.y} className="dot" fontSize="9" fill="#66665F" letterSpacing="1.6">
              {pad2(t.index)} {LAYER_TITLE[t.layer]}
            </text>
          ))}
          {/* edges */}
          {graph!.edges.map((e, i) => {
            const a = model.placed.find((n) => n.id === e.from);
            const b = model.placed.find((n) => n.id === e.to);
            if (!a || !b) return null;
            const stroke = e.tone === "deny" ? "#B4322A" : e.tone === "allow" ? "#3E6B50" : "#11110F";
            const marker = e.tone === "deny" ? "url(#arrow-deny)" : e.tone === "allow" ? "url(#arrow-allow)" : "url(#arrow)";
            const onPath = !!e.onPath;
            let d: string;
            let lx: number, ly: number;
            if (a.band === b.band) {
              const x1 = a.x + a.w, y1 = a.y, x2 = b.x, y2 = b.y;
              const mx = (x1 + x2) / 2;
              d = `M${x1},${y1} C${mx},${y1} ${mx},${y2} ${x2},${y2}`;
              lx = mx;
              ly = (y1 + y2) / 2 - 5;
            } else {
              // Fold from the end of the authority band to the start of the consequence band.
              const x1 = a.x + a.w / 2, y1 = a.y + a.h / 2, x2 = b.x, y2 = b.y;
              const my = (y1 + y2) / 2;
              d = `M${x1},${y1} C${x1},${my} ${x2 - 30},${y2} ${x2},${y2}`;
              lx = (x1 + x2) / 2;
              ly = my;
            }
            return (
              <g key={i} opacity={onPath ? 1 : 0.4}>
                <path d={d} fill="none" stroke={stroke} strokeWidth={onPath ? 1.1 : 0.7}
                  strokeDasharray={e.kind === "consequence" ? "3 3" : e.kind === "sibling" ? "1.5 3" : undefined}
                  className={cn(live && onPath && "edge-live")} markerEnd={marker} />
                {e.label ? (
                  <text x={lx} y={ly} textAnchor="middle" fontSize="8.5" fill={stroke} className="font-mono" letterSpacing="0.6">
                    {e.label}
                  </text>
                ) : null}
              </g>
            );
          })}
          {/* nodes */}
          {model.placed.map((n) => {
            const sel = selectedId === n.id;
            const border = n.tone === "deny" ? "#B4322A" : n.tone === "allow" ? "#3E6B50" : n.tone === "approval" ? "#9A6B12" : n.tone === "hold" ? "#4A4A8A" : "#11110F";
            const isDecision = n.layer === "decision";
            const fill = isDecision ? (n.tone === "deny" ? "#F5E6E3" : n.tone === "allow" ? "#E9EFE9" : n.tone === "approval" ? "#F5EEDC" : "#E8E8F2") : "#FAFAF6";
            return (
              <g key={n.id} transform={`translate(${n.x} ${n.y - n.h / 2})`} className="cursor-pointer" onClick={() => onSelect?.(n)} opacity={n.dim ? 0.55 : 1}>
                <rect width={n.w} height={n.h} rx={3} fill={fill} stroke={border} strokeWidth={sel || n.onPath ? 1.2 : 0.7} strokeDasharray={n.dim ? "2 2" : undefined} />
                {sel ? <rect x={-3} y={-3} width={n.w + 6} height={n.h + 6} rx={4} fill="none" stroke="#11110F" strokeWidth={0.6} strokeDasharray="2 2" /> : null}
                <circle cx={10} cy={n.h / 2} r={2.6} fill={border} />
                <text x={20} y={isDecision ? n.h / 2 + 4 : 19} fontSize={isDecision ? 12 : 11} fontWeight={600} fill={isDecision ? border : "#11110F"} className={isDecision ? "dot" : ""} letterSpacing={isDecision ? 1.8 : 0}>
                  {clip(n.label, isDecision ? 14 : labelChars)}
                </text>
                {!isDecision && n.sub ? (
                  <text x={20} y={33} fontSize="9" fill="#66665F" className="font-mono">
                    {clip(n.sub, labelChars + 6)}
                  </text>
                ) : null}
              </g>
            );
          })}
        </svg>
      )}
    </div>
  );
}

function clip(s: string, n: number) {
  return s.length > n ? s.slice(0, n - 1) + "…" : s;
}
function pad2(n: number) {
  return String(n).padStart(2, "0");
}

// ---------------------------------------------------------------- Lineage tree (text)
export function LineageTree({ chain, action, agent }: { chain: LineageLink[]; action?: string; agent?: string }) {
  const rows = chain.length ? chain : [];
  const root = rows[0]?.origin?.created_by ? String(rows[0].origin.created_by) : "origin";
  return (
    <pre className="font-mono text-xs leading-5 text-ink">
      {root}
      {rows.map((l, i) => {
        const permits = action ? (l.actions ?? []).includes(action) : undefined;
        const mark = permits === undefined ? "" : permits ? "  ✓" : "  ·";
        return `\n${"    ".repeat(i)}└── ${l.subject ?? l.lease}${mark}${l.subject === agent ? "  ◀" : ""}\n${"    ".repeat(i + 1)}${(l.actions ?? []).join("  ") || "(no actions)"}`;
      })}
    </pre>
  );
}
