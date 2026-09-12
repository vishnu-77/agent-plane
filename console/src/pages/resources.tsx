import { useEffect, useState } from "react";
import { api, type ResourceEntry, tenantQuery } from "@/lib/api";
import { useStore } from "@/lib/store";
import { InstrumentPanel } from "@/components/instruments";
import { Badge, Empty, Table, Td, Th } from "@/components/ui";

interface ResourcesResponse {
  resources: ResourceEntry[];
  catalog: Array<Record<string, unknown>>;
  actions: Array<Record<string, unknown>>;
}

export function ResourcesPage() {
  const { mode, creds, tenant, snapshot } = useStore();
  const [data, setData] = useState<ResourcesResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    api<ResourcesResponse>(`/v1/resources${tenantQuery(mode, tenant)}`, { mode, creds })
      .then((d) => { setData(d); setError(null); })
      .catch((e: Error) => setError(e.message));
  }, [mode, creds, tenant, snapshot.updatedAt]);

  return (
    <div className="space-y-4 p-4">
      <div>
        <div className="eyebrow">System</div>
        <h1 className="text-lg font-medium tracking-tight">Resources</h1>
        <p className="text-xs text-ink-2">What agents have actually touched, with the consequence profile each resource carries and what depends on it.</p>
      </div>
      {error ? <div className="text-xs text-deny">{error}</div> : null}
      <InstrumentPanel eyebrow="Touched" title={`${data?.resources.length ?? 0} resources`} dense>
        {data?.resources.length ? (
          <Table>
            <thead><tr><Th>Resource</Th><Th>Environment</Th><Th>Criticality</Th><Th>Customer-facing</Th><Th>Reversibility</Th><Th>Downstream</Th><Th>Agents</Th><Th>Touches</Th></tr></thead>
            <tbody>
              {data.resources.map((r) => {
                const p = r.profile ?? {};
                const crit = String(p.criticality ?? "unknown");
                return (
                  <tr key={r.resource} className="row-hover">
                    <Td className="font-mono text-xs">{r.resource}{p.protected ? <Badge tone="deny" className="ml-2">protected</Badge> : null}</Td>
                    <Td className="font-mono text-xs">{String(p.environment ?? "—")}</Td>
                    <Td><Badge tone={crit === "critical" || crit === "high" ? "deny" : crit === "medium" ? "approval" : "neutral"}>{crit}</Badge></Td>
                    <Td className="text-xs">{p.customer_facing ? "yes" : "no"}</Td>
                    <Td className="font-mono text-xs">{String(p.reversibility ?? "—")}</Td>
                    <Td className="font-mono text-2xs text-ink-2">{r.downstream.length ? r.downstream.join(", ") : "—"}</Td>
                    <Td className="font-mono text-2xs">{r.agents.join(", ")}</Td>
                    <Td className="font-mono text-xs">{r.touches}</Td>
                  </tr>
                );
              })}
            </tbody>
          </Table>
        ) : (
          <Empty title="Nothing touched yet">Resources appear as agents act on them.</Empty>
        )}
      </InstrumentPanel>
      <InstrumentPanel eyebrow="Catalog" title="Declared consequence profiles (config/resources.yaml)" dense>
        <Table>
          <thead><tr><Th>Pattern</Th><Th>Environment</Th><Th>Criticality</Th><Th>Reversibility</Th><Th>Persistence</Th><Th>Dependents</Th><Th>Description</Th></tr></thead>
          <tbody>
            {(data?.catalog ?? []).map((p) => (
              <tr key={String(p.pattern)} className="row-hover">
                <Td className="font-mono text-xs">{String(p.pattern)}{p.protected ? <Badge tone="deny" className="ml-2">protected</Badge> : null}</Td>
                <Td className="font-mono text-xs">{String(p.environment)}</Td>
                <Td className="font-mono text-xs">{String(p.criticality)}</Td>
                <Td className="font-mono text-xs">{String(p.reversibility)}</Td>
                <Td className="font-mono text-xs">{String(p.persistence)}</Td>
                <Td className="font-mono text-2xs text-ink-2">{((p.dependents as string[]) ?? []).join(", ") || "—"}</Td>
                <Td className="text-xs text-ink-2">{String(p.description ?? "")}</Td>
              </tr>
            ))}
          </tbody>
        </Table>
      </InstrumentPanel>
    </div>
  );
}
