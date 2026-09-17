import { useEffect, useMemo, useState } from "react";
import { Api, type Integration } from "@/lib/api";
import { useStore } from "@/lib/store";
import { Badge, Empty } from "@/components/ui";

type Asset = {
  id: string;
  type: string;
  name: string;
  source: string;
  trust: string;
  influence: "high" | "medium" | "low";
  signal: string;
};

const CATEGORY_ORDER = ["Instructions", "Skills", "Tools", "MCP", "Knowledge", "Memory", "Harness"] as const;

function integrationAsset(item: Integration): Asset {
  const isMcp = item.kind === "mcp";
  const isHarness = ["claude-code", "codex", "cursor", "langgraph"].includes(item.kind);
  const type = isMcp ? "MCP" : isHarness ? "Harness" : "Tools";
  const trust = item.status === "connected" ? "project-bound" : item.status;
  const influence: Asset["influence"] = isMcp || item.enforcement === "full" ? "high" : "medium";
  const signal = isMcp
    ? "External capability surface"
    : isHarness
      ? "Runtime instruction and tool boundary"
      : "Runtime ingress / capability surface";
  return {
    id: item.id,
    type,
    name: item.label || item.name,
    source: item.host || item.kind,
    trust,
    influence,
    signal,
  };
}

export function ContextPage() {
  const { project, source, feed } = useStore();
  const [integrations, setIntegrations] = useState<Integration[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!project) return;
    let alive = true;
    setLoading(true);
    Api.integrations(project.id, source)
      .then((data) => {
        if (!alive) return;
        setIntegrations(data.integrations);
        setError(null);
      })
      .catch((e: Error) => alive && setError(e.message))
      .finally(() => alive && setLoading(false));
    return () => { alive = false; };
  }, [project, source]);

  const assets = useMemo(() => integrations.map(integrationAsset), [integrations]);
  const uniqueActions = useMemo(() => new Set(feed.decisions.map((d) => d.action)).size, [feed.decisions]);
  const mcpCount = assets.filter((a) => a.type === "MCP").length;
  const harnessCount = assets.filter((a) => a.type === "Harness").length;

  const counts: Record<(typeof CATEGORY_ORDER)[number], number | null> = {
    Instructions: null,
    Skills: null,
    Tools: uniqueActions,
    MCP: mcpCount,
    Knowledge: null,
    Memory: null,
    Harness: harnessCount,
  };

  if (!project) {
    return <Empty title="No project yet">Create a project before building its context inventory.</Empty>;
  }

  return (
    <div className="mx-auto max-w-6xl px-4 py-6">
      <div className="mb-5 flex flex-wrap items-end justify-between gap-3">
        <div>
          <div className="eyebrow">Context</div>
          <h1 className="mt-1 text-lg font-medium tracking-tight">Influence surface</h1>
          <p className="mt-1 max-w-2xl text-sm text-ink-2">Everything that can shape what an agent decides to do. Capability and influence are visible here; authority is still granted separately in Access.</p>
        </div>
        <div className="font-mono text-2xs text-ink-2">{loading ? "discovering…" : `${assets.length} registered runtime source${assets.length === 1 ? "" : "s"}`}</div>
      </div>

      {error ? <div className="mb-4 rounded border border-deny/30 bg-deny-bg px-3 py-2 text-xs text-deny">{error}</div> : null}

      <div className="mb-4 grid grid-cols-2 gap-3 sm:grid-cols-4 lg:grid-cols-7">
        {CATEGORY_ORDER.map((category) => (
          <div key={category} className="rounded border border-hairline bg-paper-raised px-3 py-3">
            <div className="eyebrow">{category}</div>
            <div className="mt-2 font-mono text-xl">{counts[category] === null ? "—" : counts[category]}</div>
            <div className="mt-1 text-2xs text-ink-3">{counts[category] === null ? "adapter pending" : "observed"}</div>
          </div>
        ))}
      </div>

      <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_300px]">
        <section className="panel overflow-hidden">
          <div className="grid grid-cols-[110px_minmax(0,1fr)_120px_90px] gap-3 border-b border-hairline bg-paper-sunk px-4 py-2 font-mono text-2xs uppercase tracking-[0.12em] text-ink-2">
            <span>Type</span><span>Name / source</span><span>Trust</span><span>Influence</span>
          </div>
          {assets.length ? assets.map((asset) => (
            <div key={asset.id} className="grid grid-cols-[110px_minmax(0,1fr)_120px_90px] gap-3 border-b border-hairline px-4 py-3 text-sm last:border-b-0">
              <span className="font-mono text-xs">{asset.type}</span>
              <span className="min-w-0">
                <span className="block truncate font-medium">{asset.name}</span>
                <span className="block truncate font-mono text-2xs text-ink-2">{asset.source}</span>
                <span className="mt-1 block text-xs text-ink-2">{asset.signal}</span>
              </span>
              <span className="font-mono text-2xs text-ink-2">{asset.trust}</span>
              <span><Badge tone={asset.influence === "high" ? "hold" : "neutral"}>{asset.influence}</Badge></span>
            </div>
          )) : (
            <div className="px-4 py-10 text-center">
              <div className="text-sm font-medium">No context sources registered yet.</div>
              <p className="mx-auto mt-2 max-w-lg text-sm text-ink-2">Connect a runtime first. AGENTS.md, SKILL.md, RAG and memory adapters will extend this inventory without changing how existing integrations work.</p>
            </div>
          )}
        </section>

        <aside className="space-y-3">
          <div className="rounded border border-hairline bg-paper-raised p-4">
            <div className="eyebrow">Principle</div>
            <div className="mt-2 text-sm font-medium">Capability ≠ Authority</div>
            <p className="mt-1 text-xs leading-5 text-ink-2">A skill, MCP server, tool or memory can influence behaviour without granting permission. Access remains the explicit authority boundary.</p>
          </div>
          <div className="rounded border border-hairline bg-paper-raised p-4">
            <div className="eyebrow">Next adapters</div>
            <div className="mt-2 space-y-2 font-mono text-xs">
              <div className="flex justify-between gap-3"><span>AGENTS.md</span><span className="text-ink-3">provenance + drift</span></div>
              <div className="flex justify-between gap-3"><span>SKILL.md</span><span className="text-ink-3">capability reach</span></div>
              <div className="flex justify-between gap-3"><span>RAG</span><span className="text-ink-3">context admission</span></div>
              <div className="flex justify-between gap-3"><span>Memory</span><span className="text-ink-3">read / write authority</span></div>
            </div>
          </div>
          <div className="rounded border border-hairline bg-paper-raised p-4">
            <div className="eyebrow">Scoring</div>
            <p className="mt-2 text-xs leading-5 text-ink-2">Exposure scoring will rank attention only. Allow, deny, approval and quarantine continue to come from explicit authority and consequence rules rather than a single opaque number.</p>
          </div>
        </aside>
      </div>
    </div>
  );
}
