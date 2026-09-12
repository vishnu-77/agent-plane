import { useEffect, useState } from "react";
import { api, type Lease } from "@/lib/api";
import { useStore } from "@/lib/store";
import { LeaseInspector } from "@/components/decision";
import { InstrumentPanel } from "@/components/instruments";
import { Badge, Button, Empty, Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui";

interface PoliciesResponse { policy_version: string; rules: string[]; policies: Array<Record<string, unknown>> }
interface LeasesResponse { items: Lease[]; storage: string }
interface Template { name: string; description: string; actions: string[]; resources: string[]; protected_resources: string[]; require_approval: string[]; variables: string[]; ttl_seconds: number }

// Policies are the organisation's rules; leases are task authority. Both are
// inputs to the same decision, shown as what they are.
export function PoliciesPage() {
  const { mode, creds, snapshot, refresh } = useStore();
  const [policies, setPolicies] = useState<PoliciesResponse | null>(null);
  const [leases, setLeases] = useState<LeasesResponse | null>(null);
  const [templates, setTemplates] = useState<Template[]>([]);
  const [error, setError] = useState<string | null>(null);
  const live = mode === "live";

  useEffect(() => {
    if (!live) {
      setPolicies(null);
      setLeases(null);
      return;
    }
    Promise.allSettled([
      api<PoliciesResponse>("/admin/policies", { mode, creds }),
      api<LeasesResponse>("/admin/leases", { mode, creds }),
      api<{ templates: Template[] }>("/v1/lease-templates", { mode, creds }),
    ]).then(([p, l, t]) => {
      if (p.status === "fulfilled") setPolicies(p.value);
      if (l.status === "fulfilled") setLeases(l.value);
      if (t.status === "fulfilled") setTemplates(t.value.templates);
      const rejected = [p, l, t].find((r) => r.status === "rejected") as PromiseRejectedResult | undefined;
      setError(rejected ? String((rejected.reason as Error).message) : null);
    });
  }, [mode, creds, live, snapshot.updatedAt]);

  const revoke = async (id: string) => {
    await api(`/v1/leases/${encodeURIComponent(id)}`, { mode, creds, method: "DELETE" });
    await refresh();
  };

  return (
    <div className="space-y-4 p-4">
      <div>
        <div className="eyebrow">Govern</div>
        <h1 className="text-lg font-medium tracking-tight">Policies &amp; authority</h1>
        <p className="text-xs text-ink-2">Organisation policy (YAML rules), task authority (leases), and the lease templates a trusted backend issues from.</p>
      </div>
      {!live ? <div className="text-xs text-ink-2">Policy and lease administration needs operator access (LIVE). Demo leases are visible per agent and per decision.</div> : null}
      {error ? <div className="text-xs text-deny">{error}</div> : null}
      <Tabs defaultValue="leases">
        <TabsList>
          <TabsTrigger value="leases">Leases ({leases?.items.length ?? 0})</TabsTrigger>
          <TabsTrigger value="templates">Templates ({templates.length})</TabsTrigger>
          <TabsTrigger value="policies">Policies ({policies?.rules.length ?? 0})</TabsTrigger>
        </TabsList>
        <TabsContent value="leases">
          {leases?.items.length ? (
            <div className="grid grid-cols-1 gap-3 xl:grid-cols-2">
              {leases.items.map((l) => <LeaseInspector key={l.id} lease={l} onRevoke={revoke} />)}
            </div>
          ) : (
            <Empty title="No leases">Issue one from a template with POST /v1/leases/from-template.</Empty>
          )}
        </TabsContent>
        <TabsContent value="templates">
          <div className="grid grid-cols-1 gap-3 xl:grid-cols-2">
            {templates.map((t) => (
              <InstrumentPanel key={t.name} title={t.name} eyebrow="Lease template">
                <p className="text-xs text-ink-2">{t.description}</p>
                <dl className="mt-2 grid grid-cols-[110px_1fr] gap-x-3 gap-y-1 font-mono text-xs">
                  <dt className="text-ink-2">actions</dt><dd className="flex flex-wrap gap-1">{t.actions.map((a) => <Badge key={a} className="normal-case tracking-normal">{a}</Badge>)}</dd>
                  <dt className="text-ink-2">resources</dt><dd>{t.resources.join(", ")}</dd>
                  <dt className="text-ink-2">protected</dt><dd>{t.protected_resources.join(", ") || "—"}</dd>
                  <dt className="text-ink-2">approval</dt><dd>{t.require_approval.join(", ") || "—"}</dd>
                  <dt className="text-ink-2">variables</dt><dd>{t.variables.join(", ") || "none"}</dd>
                  <dt className="text-ink-2">ttl</dt><dd>{t.ttl_seconds}s</dd>
                </dl>
              </InstrumentPanel>
            ))}
          </div>
        </TabsContent>
        <TabsContent value="policies">
          <div className="mb-2 flex items-center gap-3">
            <span className="font-mono text-xs text-ink-2">bundle {policies?.policy_version ?? "—"}</span>
            {live ? <Button size="sm" onClick={() => void api("/admin/policies/reload", { mode, creds, method: "POST" }).then(refresh)}>Reload from disk</Button> : null}
          </div>
          <div className="grid grid-cols-1 gap-3 xl:grid-cols-2">
            {(policies?.policies ?? []).map((p) => (
              <InstrumentPanel key={String(p.name)} title={String(p.name)} eyebrow={`policy · ${String(p.effect ?? p.decision ?? "")}`}>
                <pre className="max-h-64 overflow-auto font-mono text-2xs leading-4 text-ink-2">{JSON.stringify(p, null, 2)}</pre>
              </InstrumentPanel>
            ))}
          </div>
        </TabsContent>
      </Tabs>
    </div>
  );
}
