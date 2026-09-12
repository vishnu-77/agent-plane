import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { Api, type AgentDetail } from "@/lib/api";
import { useStore } from "@/lib/store";
import { ago, cn, outcomeLabel, outcomeTone } from "@/lib/format";
import { ActivityRow, AuthoritySummary, DecisionDrawer } from "@/components/decision";
import { Badge, Button, Empty, Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui";

const TONE_BADGE = { allow: "allow", deny: "deny", review: "approval", hold: "hold", neutral: "neutral" } as const;

export function AgentsPage() {
  const { feed, project, source, refresh } = useStore();
  const [params, setParams] = useSearchParams();
  const selected = params.get("agent");

  if (!project) return <Empty title="No project yet" />;

  return (
    <div className="mx-auto max-w-4xl px-4 py-6">
      <div className="mb-5">
        <h1 className="text-lg font-medium tracking-tight">Agents</h1>
        <p className="mt-0.5 text-sm text-ink-2">
          Agents appear on their own as soon as a connected integration reports activity.
        </p>
      </div>

      {feed.agents.length ? (
        <div className="space-y-3">
          {feed.agents.map((agent) => {
            const tone = agent.last_action ? outcomeTone(agent.last_action.outcome) : "neutral";
            const today = Object.values(agent.requested_authority).reduce((a, b) => a + b, 0);
            return (
              <button
                key={agent.id}
                type="button"
                onClick={() => setParams({ agent: agent.id })}
                className="panel row-hover block w-full px-4 py-4 text-left"
              >
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div>
                    <span className="dot text-sm font-semibold">{agent.id}</span>
                    <div className="mt-0.5 text-xs text-ink-2">{agent.framework ?? agent.application}</div>
                  </div>
                  <Badge tone={agent.status === "quarantined" ? "hold" : "allow"}>{agent.status}</Badge>
                </div>
                <dl className="mt-3 grid grid-cols-1 gap-x-8 gap-y-2 sm:grid-cols-2">
                  <div>
                    <dt className="eyebrow">Current task</dt>
                    <dd className="text-sm">{agent.current_task ?? "—"}</dd>
                  </div>
                  <div>
                    <dt className="eyebrow">Actions</dt>
                    <dd className="text-sm">{today}</dd>
                  </div>
                  <div>
                    <dt className="eyebrow">Last action</dt>
                    <dd className="flex items-center gap-2 text-sm">
                      {agent.last_action ? (
                        <>
                          <span className="font-mono text-xs">{agent.last_action.action}</span>
                          <Badge tone={TONE_BADGE[tone]}>{outcomeLabel(agent.last_action.outcome)}</Badge>
                        </>
                      ) : "—"}
                    </dd>
                  </div>
                  <div>
                    <dt className="eyebrow">Last seen</dt>
                    <dd className="text-sm">{ago(agent.last_seen)}</dd>
                  </div>
                </dl>
              </button>
            );
          })}
        </div>
      ) : (
        <div className="panel px-4 py-12 text-center">
          <div className="text-sm font-medium">No agents discovered yet.</div>
          <p className="mx-auto mt-2 max-w-md text-sm text-ink-2">
            Agents appear automatically when connected integrations begin sending activity.
          </p>
        </div>
      )}

      <AgentDrawer
        agentId={selected}
        projectId={project.id}
        source={source}
        onClose={() => setParams({})}
        onChanged={refresh}
      />
    </div>
  );
}

function AgentDrawer({ agentId, projectId, source, onClose, onChanged }: {
  agentId: string | null; projectId: string; source: "live" | "demo";
  onClose: () => void; onChanged: () => Promise<void>;
}) {
  const { feed } = useStore();
  const [detail, setDetail] = useState<AgentDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [decision, setDecision] = useState<string | null>(null);

  useEffect(() => {
    if (!agentId) { setDetail(null); return; }
    let alive = true;
    Api.agent(agentId, projectId, source)
      .then((d) => alive && (setDetail(d), setError(null)))
      .catch((e: Error) => alive && setError(e.message));
    return () => { alive = false; };
  }, [agentId, projectId, source, feed.updatedAt]);

  const activity = feed.decisions.filter((d) => d.agent === agentId);
  const can = detail?.granted_authority.filter((a) => !detail.leases.some((l) => (l.require_approval as string[] | undefined)?.includes(a))) ?? [];
  const ask = detail ? [...new Set(detail.leases.flatMap((l) => (l.require_approval as string[] | undefined) ?? []))] : [];
  const never = detail ? [...new Set(detail.leases.flatMap((l) => (l.denied_actions as string[] | undefined) ?? []))] : [];

  return (
    <>
      <Dialogish open={agentId !== null} onClose={onClose} title={detail?.id ?? agentId ?? ""}
        subtitle={detail ? `${detail.framework ?? detail.application} · ${ago(detail.last_seen)}` : undefined}>
        {error ? <p className="text-xs text-deny">{error}</p> : null}
        {detail ? (
          <Tabs defaultValue="overview">
            <TabsList>
              <TabsTrigger value="overview">Overview</TabsTrigger>
              <TabsTrigger value="activity">Activity</TabsTrigger>
              <TabsTrigger value="authority">Authority</TabsTrigger>
            </TabsList>

            <TabsContent value="overview" className="space-y-3">
              <dl className="grid grid-cols-[130px_1fr] gap-x-3 gap-y-1.5 text-sm">
                <dt className="eyebrow pt-[3px]">Identity</dt><dd className="font-mono text-xs">{detail.id}</dd>
                <dt className="eyebrow pt-[3px]">Integration</dt><dd>{detail.framework ?? "unknown"}</dd>
                <dt className="eyebrow pt-[3px]">Application</dt><dd>{detail.application}</dd>
                <dt className="eyebrow pt-[3px]">Current task</dt><dd>{detail.current_task ?? "—"}</dd>
                {detail.parent_agent ? (<><dt className="eyebrow pt-[3px]">Parent</dt><dd className="font-mono text-xs">{detail.parent_agent}</dd></>) : null}
                <dt className="eyebrow pt-[3px]">First seen</dt><dd>{ago(detail.first_seen)}</dd>
                <dt className="eyebrow pt-[3px]">Last seen</dt><dd>{ago(detail.last_seen)}</dd>
              </dl>
              {detail.tasks_detail[0]?.origin?.text ? (
                <div>
                  <div className="eyebrow">Origin</div>
                  <blockquote className="mt-1 border-l-2 border-hairline-strong pl-2 text-sm text-ink-2">
                    “{detail.tasks_detail[0].origin.text}”
                  </blockquote>
                </div>
              ) : null}
              <div className="flex gap-2 border-t border-hairline pt-3">
                {detail.status === "quarantined" ? (
                  <Button size="sm" disabled={source === "demo"}
                    onClick={async () => { await Api.quarantine(detail.id, projectId, false); await onChanged(); }}>
                    Release
                  </Button>
                ) : (
                  <Button size="sm" variant="deny" disabled={source === "demo"}
                    onClick={async () => { await Api.quarantine(detail.id, projectId, true); await onChanged(); }}>
                    Hold this agent
                  </Button>
                )}
              </div>
              {detail.status === "quarantined" ? (
                <p className="text-xs text-hold">Every action from this agent is refused until it is released.</p>
              ) : null}
            </TabsContent>

            <TabsContent value="activity">
              {activity.length
                ? activity.map((d) => <ActivityRow key={d.decision_id} decision={d} onOpen={setDecision} />)
                : <Empty title="Nothing recorded for this agent yet" />}
            </TabsContent>

            <TabsContent value="authority" className="space-y-4">
              <AuthoritySummary can={can} ask={ask} never={never}
                empty="No rules apply to this agent yet, so nothing has been granted." />
              {detail.drift.ungranted.length ? (
                <div className="rounded border border-deny/40 bg-deny-bg p-3">
                  <div className="eyebrow text-deny">Asked for, never granted</div>
                  <div className="mt-1 font-mono text-sm">{detail.drift.ungranted.join("  ")}</div>
                </div>
              ) : null}
              <details className="border-t border-hairline pt-3">
                <summary className="cursor-pointer text-xs text-ink-2">Advanced authority details</summary>
                <dl className="mt-3 grid grid-cols-[150px_1fr] gap-x-3 gap-y-1 font-mono text-xs">
                  <dt className="text-ink-2">declared</dt><dd>{detail.declared_capabilities.join(", ") || "unscoped"}</dd>
                  <dt className="text-ink-2">granted</dt><dd>{detail.granted_authority.join(", ") || "none"}</dd>
                  <dt className="text-ink-2">exercised</dt><dd>{Object.entries(detail.exercised_authority).map(([a, n]) => `${a}×${n}`).join(", ") || "none"}</dd>
                  <dt className="text-ink-2">refused</dt><dd>{Object.entries(detail.denied_authority).map(([a, n]) => `${a}×${n}`).join(", ") || "none"}</dd>
                  <dt className="text-ink-2">unused grants</dt><dd>{(detail.drift.unused_grants ?? []).join(", ") || "none"}</dd>
                  <dt className="text-ink-2">leases</dt><dd className="break-all">{detail.leases.map((l) => String(l.id)).join(", ") || "none"}</dd>
                  <dt className="text-ink-2">delegated to</dt><dd>{detail.children.join(", ") || "none"}</dd>
                </dl>
              </details>
            </TabsContent>
          </Tabs>
        ) : null}
      </Dialogish>
      <DecisionDrawer id={decision} open={decision !== null} onOpenChange={(v) => !v && setDecision(null)} />
    </>
  );
}

/** A right-hand sheet that does not fight the decision drawer for the dialog root. */
function Dialogish({ open, onClose, title, subtitle, children }: {
  open: boolean; onClose: () => void; title: string; subtitle?: string; children: React.ReactNode;
}) {
  if (!open) return null;
  return (
    <div className="fixed inset-0 z-40" role="dialog" aria-modal="true" aria-label={title}>
      <div className="absolute inset-0 bg-ink/20" onClick={onClose} />
      <div className={cn("absolute right-0 top-0 h-full w-full max-w-[560px] overflow-y-auto",
        "border-l border-hairline bg-paper-raised")}>
        <div className="flex items-start justify-between gap-4 border-b border-hairline px-4 py-3">
          <div>
            <div className="text-sm font-medium tracking-tight">{title}</div>
            {subtitle ? <div className="mt-0.5 text-xs text-ink-2">{subtitle}</div> : null}
          </div>
          <button onClick={onClose} className="rounded p-1 text-ink-2 hover:bg-paper-sunk hover:text-ink" aria-label="Close">×</button>
        </div>
        <div className="p-4">{children}</div>
      </div>
    </div>
  );
}
