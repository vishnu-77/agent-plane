import { useEffect, useMemo, useState } from "react";
import { Api, type ContextAsset, type Integration } from "@/lib/api";
import { useStore } from "@/lib/store";
import { ago } from "@/lib/format";
import { Badge, Empty } from "@/components/ui";

const CATEGORY_ORDER = ["Instructions", "Skills", "Tools", "MCP", "Knowledge", "Memory", "Harness"] as const;
type Category = (typeof CATEGORY_ORDER)[number];

function category(kind: string): Category {
  if (kind === "instruction") return "Instructions";
  if (kind === "skill") return "Skills";
  if (kind === "mcp") return "MCP";
  if (kind === "knowledge") return "Knowledge";
  if (kind === "memory") return "Memory";
  if (kind === "harness") return "Harness";
  return "Tools";
}

function fallbackIntegration(item: Integration, tenant: string): ContextAsset {
  const kind = item.kind === "mcp" ? "mcp" : ["claude-code", "codex", "cursor", "langgraph"].includes(item.kind) ? "harness" : "tool";
  return {
    id: `integration:${item.id}`, tenant, kind, name: item.label || item.name, source: item.host || item.kind, digest: "observed", previous_digest: null,
    version: 1, change_count: 0, trust: item.status === "connected" ? "project-bound" : item.status, influence: kind === "mcp" || kind === "harness" ? "high" : "medium",
    capabilities: [], agents: item.agents, tasks: [], provenance: {}, metadata: { integration_id: item.id }, risk: {},
    exposure_score: kind === "mcp" ? 55 : kind === "harness" ? 45 : 30, first_seen: item.created_at, last_seen: item.last_seen_at || item.created_at, changed_at: item.created_at,
  };
}

export function ContextPage() {
  const { project, source } = useStore();
  const [registered, setRegistered] = useState<ContextAsset[]>([]);
  const [integrations, setIntegrations] = useState<Integration[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!project) return;
    let alive = true;
    setLoading(true);
    Promise.all([Api.contextAssets(project.id, source), Api.integrations(project.id, source)])
      .then(([ctx, ints]) => { if (alive) { setRegistered(ctx.assets); setIntegrations(ints.integrations); setError(null); } })
      .catch((e: Error) => alive && setError(e.message))
      .finally(() => alive && setLoading(false));
    return () => { alive = false; };
  }, [project, source]);

  const assets = useMemo(() => {
    if (!project) return [];
    const bySource = new Map(registered.map((a) => [a.source, a]));
    const ids = new Set(registered.map((a) => String(a.metadata?.integration_id || "")));
    const fallback = integrations.filter((i) => !ids.has(i.id)).map((i) => fallbackIntegration(i, project.id));
    for (const a of fallback) if (!bySource.has(a.source)) bySource.set(a.source, a);
    return [...bySource.values()].sort((a, b) => b.exposure_score - a.exposure_score);
  }, [project, registered, integrations]);

  const counts = Object.fromEntries(CATEGORY_ORDER.map((c) => [c, assets.filter((a) => category(a.kind) === c).length])) as Record<Category, number>;
  const changed = assets.filter((a) => a.change_count > 0).length;

  if (!project) return <Empty title="No project yet">Create a project before building its context inventory.</Empty>;

  return (
    <div className="mx-auto max-w-6xl px-4 py-6">
      <div className="mb-5 flex flex-wrap items-end justify-between gap-3">
        <div><div className="eyebrow">Context</div><h1 className="mt-1 text-lg font-medium tracking-tight">Influence surface</h1>
          <p className="mt-1 max-w-2xl text-sm text-ink-2">What can shape an agent before it acts: repository instructions, skills, tools, MCP, RAG, memory and harnesses. Capability and influence never grant authority.</p></div>
        <div className="font-mono text-2xs text-ink-2">{loading ? "discovering…" : `${assets.length} assets · ${changed} changed`}</div>
      </div>
      {error ? <div className="mb-4 rounded border border-deny/30 bg-deny-bg px-3 py-2 text-xs text-deny">{error}</div> : null}
      <div className="mb-4 grid grid-cols-2 gap-3 sm:grid-cols-4 lg:grid-cols-7">
        {CATEGORY_ORDER.map((c) => <div key={c} className="rounded border border-hairline bg-paper-raised px-3 py-3"><div className="eyebrow">{c}</div><div className="mt-2 font-mono text-xl">{counts[c]}</div><div className="mt-1 text-2xs text-ink-3">discovered</div></div>)}
      </div>
      <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_300px]">
        <section className="panel overflow-hidden">
          <div className="grid grid-cols-[92px_minmax(0,1fr)_105px_84px_62px] gap-3 border-b border-hairline bg-paper-sunk px-4 py-2 font-mono text-2xs uppercase tracking-[0.12em] text-ink-2"><span>Type</span><span>Name / source</span><span>Trust</span><span>Influence</span><span>Exposure</span></div>
          {assets.length ? assets.map((asset) => <div key={asset.id} className="grid grid-cols-[92px_minmax(0,1fr)_105px_84px_62px] gap-3 border-b border-hairline px-4 py-3 text-sm last:border-b-0">
            <span className="font-mono text-xs">{category(asset.kind)}</span>
            <span className="min-w-0"><span className="block truncate font-medium">{asset.name}</span><span className="block truncate font-mono text-2xs text-ink-2">{asset.source}</span><span className="mt-1 block text-2xs text-ink-3">v{asset.version}{asset.change_count ? ` · changed ${asset.change_count}×` : ""} · seen {ago(asset.last_seen)}</span></span>
            <span className="font-mono text-2xs text-ink-2">{asset.trust}</span><span><Badge tone={asset.influence === "high" ? "hold" : "neutral"}>{asset.influence}</Badge></span>
            <span className="font-mono text-xs">{asset.exposure_score}</span>
          </div>) : <div className="px-4 py-10 text-center"><div className="text-sm font-medium">No context assets discovered yet.</div><p className="mx-auto mt-2 max-w-lg text-sm text-ink-2">Connect a runtime and use it. Coding-agent hooks register AGENTS.md / CLAUDE.md / SKILL.md hashes; runtime actions, RAG and memory add their own lineage.</p></div>}
        </section>
        <aside className="space-y-3">
          <div className="rounded border border-hairline bg-paper-raised p-4"><div className="eyebrow">Principle</div><div className="mt-2 text-sm font-medium">Capability ≠ Authority</div><p className="mt-1 text-xs leading-5 text-ink-2">Context may influence behaviour or expose capability. Access remains the explicit permission boundary.</p></div>
          <div className="rounded border border-hairline bg-paper-raised p-4"><div className="eyebrow">Exposure score</div><p className="mt-2 text-xs leading-5 text-ink-2">0–100 ranks attention using provenance, integrity, influence, persistence, privilege amplification, external reach, sensitivity, propagation and reversibility. It never directly allows or blocks an action.</p></div>
          <div className="rounded border border-hairline bg-paper-raised p-4"><div className="eyebrow">Privacy</div><p className="mt-2 text-xs leading-5 text-ink-2">Repository instruction and skill discovery sends path, hash and metadata — not file contents. RAG lineage records authorised document references, not a second copy of the knowledge base.</p></div>
        </aside>
      </div>
    </div>
  );
}
