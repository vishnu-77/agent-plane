import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { useStore } from "@/lib/store";
import { InstrumentPanel, StateLamp } from "@/components/instruments";
import { Badge, Button } from "@/components/ui";

// ---------------------------------------------------------------- Integrations
const EDGES: Array<{ name: string; how: string; enforces: boolean; note: string }> = [
  { name: "OpenAI-compatible gateway", how: "Point base_url at /v1. Zero code change.", enforces: true, note: "policy, quotas, routing, audit; not task leases" },
  { name: "MCP gateway", how: "Point the MCP client at /mcp; map tools once with `agentplane mcp discover`.", enforces: true, note: "lease-gated admission before dispatch, gateway holds the upstream credential" },
  { name: "HTTP / API broker", how: "Route tool execution through /v1/tools/invoke; the broker holds the credential.", enforces: true, note: "policy + capability; add /v1/authorize in the dispatcher for task authority" },
  { name: "A2A delegation", how: "POST /v1/agents/delegate and /v1/leases/{id}/delegate.", enforces: true, note: "child authority ⊆ parent authority; lineage recorded" },
  { name: "External authorization", how: "Envoy / Kubernetes / cloud gateways call POST /v1/authorize as the decision point.", enforces: false, note: "binding only if the gateway enforces the answer" },
  { name: "Framework SDK / middleware", how: "govern(), governed_dispatch(), langchain_tool(), crewai_tool(), openai_agents_guard(); TypeScript govern().", enforces: false, note: "one wrapper at your dispatch point; conformance kit proves fail-closed" },
];

export function IntegrationsPage() {
  return (
    <div className="space-y-4 p-4">
      <div>
        <div className="eyebrow">Platform</div>
        <h1 className="text-lg font-medium tracking-tight">Integrations</h1>
        <p className="text-xs text-ink-2">Change the route, not the agent. One authority brain behind several enforcement surfaces.</p>
      </div>
      <pre className="panel overflow-auto p-3 font-mono text-xs leading-5 text-ink-2">{`BEFORE                              AFTER

Agent ─────► Model                  Agent
      ─────► MCP                      │
      ─────► GitHub                   ▼
      ─────► Cloud/API             agent-plane ── who is authorised × what they can cause
                                      ├────► Model
                                      ├────► MCP
                                      ├────► GitHub
                                      └────► Cloud/API`}</pre>
      <div className="grid grid-cols-1 gap-3 xl:grid-cols-2">
        {EDGES.map((e) => (
          <InstrumentPanel key={e.name} title={e.name} eyebrow={e.enforces ? "enforcement surface" : "decision surface"} right={<Badge tone={e.enforces ? "allow" : "neutral"}>{e.enforces ? "binding" : "advisory"}</Badge>}>
            <p className="text-sm">{e.how}</p>
            <p className="mt-1 text-xs text-ink-2">{e.note}</p>
          </InstrumentPanel>
        ))}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------- Gateway
interface GatewayInfo { environment: string; identity_mode: string; storage_backend: string; lease_storage: string; models: Array<Record<string, unknown>>; tools: Array<Record<string, unknown>>; knowledge: Array<Record<string, unknown>> }

export function GatewayPage() {
  const { mode, creds, snapshot } = useStore();
  const [info, setInfo] = useState<GatewayInfo | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    if (mode !== "live") return;
    api<GatewayInfo>("/admin/gateway", { mode, creds }).then((d) => { setInfo(d); setError(null); }).catch((e: Error) => setError(e.message));
  }, [mode, creds, snapshot.updatedAt]);
  return (
    <div className="space-y-4 p-4">
      <div><div className="eyebrow">Platform</div><h1 className="text-lg font-medium tracking-tight">Gateway</h1><p className="text-xs text-ink-2">What this instance can route: models, brokered tools, knowledge sources, and the MCP mapping.</p></div>
      {mode !== "live" ? <div className="text-xs text-ink-2">Operator access required.</div> : null}
      {error ? <div className="text-xs text-deny">{error}</div> : null}
      {info ? (
        <div className="grid grid-cols-1 gap-3 xl:grid-cols-3">
          <InstrumentPanel title="Models" eyebrow={`${info.models.length} configured`}>
            <ul className="font-mono text-xs">{info.models.map((m) => <li key={String(m.id)} className="flex justify-between border-b border-hairline py-1"><span>{String(m.id)}</span><span className="text-ink-2">{String(m.provider)}{m.credentials_configured ? "" : " · no key"}</span></li>)}</ul>
          </InstrumentPanel>
          <InstrumentPanel title="Brokered tools" eyebrow={`${info.tools.length} registered`}>
            <ul className="font-mono text-xs">{info.tools.map((t) => <li key={String(t.name)} className="flex justify-between border-b border-hairline py-1"><span>{String(t.name)}</span><span className="text-ink-2">{String(t.type)}</span></li>)}</ul>
          </InstrumentPanel>
          <InstrumentPanel title="Knowledge" eyebrow={`${info.knowledge.length} sources`}>
            <ul className="font-mono text-xs">{info.knowledge.map((k) => <li key={String(k.name)} className="flex justify-between border-b border-hairline py-1"><span>{String(k.name)}</span><span className="text-ink-2">{String(k.type)}</span></li>)}</ul>
          </InstrumentPanel>
        </div>
      ) : null}
    </div>
  );
}

// ---------------------------------------------------------------- Runtime
export function RuntimePage() {
  const { snapshot, mode, creds, refresh } = useStore();
  const sys = snapshot.system;
  const [health, setHealth] = useState<{ ok: boolean; ready: boolean } | null>(null);
  useEffect(() => {
    Promise.all([fetch("/healthz").then((r) => r.ok), fetch("/readyz").then((r) => r.ok)]).then(([ok, ready]) => setHealth({ ok, ready })).catch(() => setHealth({ ok: false, ready: false }));
  }, [snapshot.updatedAt]);
  const setMode = async (m: "observe" | "enforce") => {
    await api("/admin/mode", { mode, creds, method: "PUT", body: JSON.stringify({ mode: m }) });
    await refresh();
  };
  return (
    <div className="space-y-4 p-4">
      <div><div className="eyebrow">Platform</div><h1 className="text-lg font-medium tracking-tight">Runtime</h1></div>
      <div className="grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-4">
        <InstrumentPanel title="Liveness" eyebrow="/healthz"><StateLamp tone={health?.ok ? "allow" : "deny"} label={health?.ok ? "ok" : "down"} /></InstrumentPanel>
        <InstrumentPanel title="Readiness" eyebrow="/readyz"><StateLamp tone={health?.ready ? "allow" : "deny"} label={health?.ready ? "ready" : "not ready"} /></InstrumentPanel>
        <InstrumentPanel title="Identity" eyebrow="IDENTITY_MODE"><span className="font-mono text-sm">{sys?.identity_mode ?? "—"}</span><div className="text-2xs text-ink-2">{sys?.identity_mode === "delegation" ? "signed, verified scope" : "claims trusted as asserted"}</div></InstrumentPanel>
        <InstrumentPanel title="Authority store" eyebrow="AUTHORITY_STORE"><span className="font-mono text-sm">{sys?.authority_store ?? "—"}</span><div className="text-2xs text-ink-2">{sys?.authority_store === "sql" ? "durable, shared across replicas" : "process-local"}</div></InstrumentPanel>
      </div>
      <InstrumentPanel title="Observe → Enforce" eyebrow="enforcement mode" right={<StateLamp tone={sys?.mode === "enforce" ? "on" : "approval"} label={sys?.mode ?? "—"} />}>
        <p className="text-sm">
          <b>Observe</b> blocks nothing: would-be denials come back as SIMULATE and the registry learns what each agent asks for. <b>Enforce</b> returns DENY, APPROVAL, and QUARANTINE for real.
        </p>
        {mode === "live" ? (
          <div className="mt-3 flex gap-2">
            <Button size="sm" variant={sys?.mode === "observe" ? "default" : "outline"} onClick={() => void setMode("observe")}>Observe</Button>
            <Button size="sm" variant={sys?.mode === "enforce" ? "default" : "outline"} onClick={() => void setMode("enforce")}>Enforce</Button>
          </div>
        ) : <p className="mt-2 text-xs text-ink-2">Operator access required to switch.</p>}
      </InstrumentPanel>
      <InstrumentPanel title="Environment" eyebrow="settings">
        <dl className="grid grid-cols-[160px_1fr] gap-x-3 gap-y-1 font-mono text-xs">
          <dt className="text-ink-2">environment</dt><dd>{sys?.environment ?? "—"}</dd>
          <dt className="text-ink-2">policy bundle</dt><dd>{sys?.policy_version ?? "—"}</dd>
          <dt className="text-ink-2">tenant scope</dt><dd>{sys?.tenant ?? "all tenants"}</dd>
          <dt className="text-ink-2">audit head</dt><dd className="break-all">{sys?.audit_head ?? "—"}</dd>
          <dt className="text-ink-2">metrics</dt><dd><a className="underline" href="/metrics" target="_blank" rel="noreferrer">/metrics</a></dd>
          <dt className="text-ink-2">api reference</dt><dd><a className="underline" href="/docs" target="_blank" rel="noreferrer">/docs</a></dd>
        </dl>
      </InstrumentPanel>
    </div>
  );
}

// ---------------------------------------------------------------- Settings
export function SettingsPage() {
  const { tenant, setTenant, mode, paused, setPaused } = useStore();
  const [value, setValue] = useState(tenant ?? "");
  return (
    <div className="space-y-4 p-4">
      <div><div className="eyebrow">Platform</div><h1 className="text-lg font-medium tracking-tight">Settings</h1></div>
      <InstrumentPanel title="Tenant filter" eyebrow="operator view">
        <p className="text-xs text-ink-2">Operators see every tenant by default. Narrow the console to one tenant; the demo viewer is always scoped to the demo tenant.</p>
        <form className="mt-2 flex gap-2" onSubmit={(e) => { e.preventDefault(); setTenant(value.trim() || null); }}>
          <input className="h-8 w-64 rounded border border-hairline-strong bg-paper-raised px-2 font-mono text-xs" value={value} onChange={(e) => setValue(e.target.value)} placeholder="all tenants" disabled={mode === "demo"} />
          <Button size="sm" type="submit" disabled={mode === "demo"}>Apply</Button>
        </form>
      </InstrumentPanel>
      <InstrumentPanel title="Polling" eyebrow="console">
        <p className="text-xs text-ink-2">The console polls every 4 seconds while visible. Credentials stay in this tab's memory and are cleared on reload.</p>
        <Button size="sm" className="mt-2" onClick={() => setPaused(!paused)}>{paused ? "Resume polling" : "Pause polling"}</Button>
      </InstrumentPanel>
      <InstrumentPanel title="Visual language" eyebrow="e-ink">
        <div className="flex flex-wrap gap-2 font-mono text-2xs">
          {[["paper", "#F4F4EF"], ["surface", "#FAFAF6"], ["ink", "#11110F"], ["secondary", "#66665F"], ["hairline", "#D7D7CF"], ["allow", "#3E6B50"], ["deny", "#B4322A"], ["approval", "#9A6B12"]].map(([n, c]) => (
            <span key={n} className="inline-flex items-center gap-1.5 rounded border border-hairline px-2 py-1"><span className="h-3 w-3 rounded-sm border border-hairline" style={{ background: c }} />{n} {c}</span>
          ))}
        </div>
      </InstrumentPanel>
    </div>
  );
}
