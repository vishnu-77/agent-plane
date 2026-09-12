import { useEffect, useState } from "react";
import { motion } from "motion/react";
import { api, type DecisionDetail, type DecisionSummary, type Lease, type Trace } from "@/lib/api";
import { useStore } from "@/lib/store";
import { cn, dateOf, outcomeLabel, outcomeTone, reasonText, shortId, timeOf } from "@/lib/utils";
import { Badge, Button, Dialog, DialogContent, Tabs, TabsContent, TabsList, TabsTrigger } from "./ui";
import { LineageTree } from "./graph";

// ---------------------------------------------------------------- data hook
export function useDecision(id: string | null): { detail: DecisionDetail | null; error: string | null; loading: boolean } {
  const { mode, creds } = useStore();
  const [state, setState] = useState<{ detail: DecisionDetail | null; error: string | null; loading: boolean }>({ detail: null, error: null, loading: false });
  useEffect(() => {
    if (!id) {
      setState({ detail: null, error: null, loading: false });
      return;
    }
    let alive = true;
    setState((s) => ({ ...s, loading: true }));
    api<DecisionDetail>(`/v1/decisions/${encodeURIComponent(id)}`, { mode, creds })
      .then((d) => alive && setState({ detail: d, error: null, loading: false }))
      .catch((e: Error) => alive && setState({ detail: null, error: e.message, loading: false }));
    return () => {
      alive = false;
    };
  }, [id, mode, creds]);
  return state;
}

// ---------------------------------------------------------------- DecisionStrip
export function DecisionStrip({ d, selected, onSelect }: { d: DecisionSummary; selected?: boolean; onSelect?: (id: string) => void }) {
  const tone = outcomeTone(d.outcome);
  return (
    <button
      type="button"
      onClick={() => onSelect?.(d.decision_id)}
      className={cn("row-hover grid w-full grid-cols-[62px_86px_1fr_auto] items-center gap-3 border-b border-hairline px-3 py-2 text-left", selected && "bg-paper-sunk")}
      aria-pressed={selected}
    >
      <span className="font-mono text-2xs text-ink-2">{timeOf(d.created_at).slice(0, 8)}</span>
      <Badge tone={tone}>{outcomeLabel(d.outcome)}</Badge>
      <span className="min-w-0">
        <span className="block truncate text-sm">
          <span className="font-medium">{d.agent}</span> <span className="text-ink-2">·</span> {d.action} <span className="text-ink-2">→</span> <span className="font-mono text-xs">{d.resource}</span>
        </span>
        <span className="block truncate font-mono text-2xs text-ink-2">
          {reasonText(d.reason)}
          {d.would_be ? ` · would be ${outcomeLabel(d.would_be)}` : ""}
          {d.impact ? ` · ${d.impact}` : ""}
        </span>
      </span>
      <span className="font-mono text-2xs text-ink-3">{shortId(d.decision_id, 14)}</span>
    </button>
  );
}

// ---------------------------------------------------------------- DecisionTrace
function Step({ n, title, status, tone, children }: { n: number; title: string; status?: string; tone?: "allow" | "deny" | "approval" | "hold" | "neutral"; children: React.ReactNode }) {
  return (
    <motion.li initial={{ opacity: 0, y: 4 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: n * 0.05 }} className="relative pl-9">
      <span className="dot absolute left-0 top-[3px] text-2xs text-ink-2">{String(n).padStart(2, "0")}</span>
      <div className="flex items-baseline justify-between gap-3">
        <div className="eyebrow">{title}</div>
        {status ? <Badge tone={tone ?? "neutral"}>{status}</Badge> : null}
      </div>
      <div className="mt-1 text-sm">{children}</div>
      <div className="my-2 ml-[3px] h-4 border-l border-dashed border-hairline-strong" aria-hidden />
    </motion.li>
  );
}

export function DecisionTrace({ trace, className }: { trace: Trace; className?: string }) {
  const tone = outcomeTone(trace.decision.outcome);
  const c = trace.consequence;
  const lease = trace.authority.lease;
  const scope = trace.resource.scope;
  return (
    <div className={cn("text-ink", className)}>
      <div className="flex items-start justify-between gap-4 border-b border-hairline pb-3">
        <div>
          <div className={cn("dot text-[22px] font-semibold leading-none", tone === "deny" ? "text-deny" : tone === "allow" ? "text-allow" : tone === "approval" ? "text-approval" : "text-hold")}>
            {outcomeLabel(trace.decision.outcome)}
          </div>
          <div className="mt-2 font-mono text-sm">{trace.action.name}</div>
          <div className="font-mono text-sm text-ink-2">{trace.resource.name}</div>
        </div>
        <div className="text-right">
          <div className="font-mono text-2xs text-ink-2">{trace.decision_id}</div>
          <div className="mt-1 font-mono text-2xs text-ink-3">{trace.edge} · {trace.mode}</div>
          {trace.decision.would_be ? <Badge tone="hold" className="mt-2">would be {outcomeLabel(trace.decision.would_be)}</Badge> : null}
        </div>
      </div>

      <ol className="mt-4 list-none">
        <Step n={1} title="Identity" status={trace.identity.verified ? "VERIFIED" : "ASSERTED"} tone={trace.identity.verified ? "allow" : "neutral"}>
          <span className="font-medium">{trace.identity.agent}</span>
          <span className="text-ink-2"> · {trace.identity.tenant}{trace.identity.application ? ` · ${trace.identity.application}` : ""}</span>
          <div className="mt-0.5 font-mono text-2xs text-ink-2">
            capabilities: {trace.identity.declared_capabilities.length ? trace.identity.declared_capabilities.join("  ") : "unscoped"}
          </div>
        </Step>
        <Step n={2} title="Task">
          <span className="font-medium">{trace.task.id}</span>
          {"text" in trace.task.origin && trace.task.origin.text ? (
            <blockquote className="mt-1 border-l-2 border-hairline-strong pl-2 text-sm text-ink-2">“{trace.task.origin.text}”</blockquote>
          ) : null}
          {"created_by" in trace.task.origin && trace.task.origin.created_by ? (
            <div className="mt-0.5 font-mono text-2xs text-ink-2">origin: {String(trace.task.origin.kind)} · {String(trace.task.origin.created_by)}</div>
          ) : null}
        </Step>
        <Step n={3} title="Authority" status={lease ? (trace.authority.lineage_permits_action ? "GRANTS ACTION" : "DOES NOT GRANT") : "NONE"} tone={lease ? (trace.authority.lineage_permits_action ? "allow" : "deny") : "deny"}>
          {lease ? (
            <>
              <div className="font-mono text-xs">{lease}</div>
              <div className="mt-1 flex flex-wrap gap-1">
                {trace.authority.actions.map((a) => (
                  <Badge key={a} tone={a === trace.action.name ? "ink" : "neutral"} className="normal-case tracking-normal">{a}</Badge>
                ))}
              </div>
              <div className="mt-1 font-mono text-2xs text-ink-2">scope: {trace.authority.resources.join(", ") || "—"}</div>
              {trace.authority.protected_resources.length ? <div className="font-mono text-2xs text-ink-2">protected: {trace.authority.protected_resources.join(", ")}</div> : null}
              {Object.keys(trace.authority.permitted_consequence).length ? (
                <div className="font-mono text-2xs text-ink-2">permitted consequence: {JSON.stringify(trace.authority.permitted_consequence)}</div>
              ) : null}
              {trace.authority.lineage.length > 1 ? (
                <div className="mt-2 rounded border border-hairline bg-paper p-2">
                  <div className="eyebrow mb-1">Lineage</div>
                  <LineageTree chain={trace.authority.lineage} action={trace.action.name} agent={trace.identity.agent} />
                </div>
              ) : null}
            </>
          ) : (
            <span className="text-ink-2">No lease binds this agent to this task.</span>
          )}
        </Step>
        <Step n={4} title="Action">
          <span className="font-mono">{trace.action.name}</span>
          <span className="text-ink-2"> · {trace.action.effect ?? "unknown effect"} · declared {trace.action.declared_impact}</span>
        </Step>
        <Step n={5} title="Resource" status={scope === "within" ? "IN SCOPE" : scope === "protected" ? "PROTECTED" : "MISMATCH"} tone={scope === "within" ? "allow" : "deny"}>
          <span className="font-mono">{trace.resource.name}</span>
        </Step>
        <Step n={6} title="Consequence" status={c ? c.impact.toUpperCase() : undefined} tone={c && (c.impact === "critical" || c.impact === "high") ? "deny" : c && c.impact === "medium" ? "approval" : "neutral"}>
          {c ? (
            <ul className="text-sm">
              {c.summary.map((s, i) => (
                <li key={i} className="text-ink-2 first:text-ink">{s}</li>
              ))}
              {c.downstream.length ? <li className="mt-1 font-mono text-2xs text-ink-2">downstream: {c.downstream.join(", ")}</li> : null}
              {trace.decision.reasons.filter((r) => r.startsWith("CONSEQUENCE:")).map((r) => (
                <li key={r} className="mt-1 text-deny">{r.slice("CONSEQUENCE:".length)}</li>
              ))}
            </ul>
          ) : (
            <span className="text-ink-2">not modelled</span>
          )}
        </Step>
        <li className="pl-9">
          <div className={cn("dot text-[18px] font-semibold", tone === "deny" ? "text-deny" : tone === "allow" ? "text-allow" : tone === "approval" ? "text-approval" : "text-hold")}>
            {outcomeLabel(trace.decision.outcome)}
          </div>
          <div className="mt-1 font-mono text-xs text-ink-2">{trace.decision.reasons.filter((r) => !r.startsWith("CONSEQUENCE:")).join("  ")}</div>
        </li>
      </ol>

      <div className="mt-5 border-t border-hairline pt-3">
        <div className="eyebrow mb-1">Why?</div>
        {trace.explanation.map((line, i) => (
          <p key={i} className="text-sm leading-6">{line}</p>
        ))}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------- EvidenceDrawer
export function EvidenceDrawer({ id, open, onOpenChange }: { id: string | null; open: boolean; onOpenChange: (o: boolean) => void }) {
  const { detail, error, loading } = useDecision(open ? id : null);
  const trace = detail?.trace;
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent side="right" title={trace ? `${outcomeLabel(trace.decision.outcome)} · ${trace.action.name}` : "Decision evidence"} description={id ?? undefined}>
        {loading ? <div className="text-xs text-ink-2">Loading evidence…</div> : null}
        {error ? <div className="text-xs text-deny">{error}</div> : null}
        {detail && trace ? (
          <Tabs defaultValue="trace">
            <TabsList>
              <TabsTrigger value="trace">Trace</TabsTrigger>
              <TabsTrigger value="evidence">Evidence</TabsTrigger>
              <TabsTrigger value="receipts">Receipts ({detail.receipts.length})</TabsTrigger>
              <TabsTrigger value="raw">Raw</TabsTrigger>
            </TabsList>
            <TabsContent value="trace">
              <DecisionTrace trace={trace} />
            </TabsContent>
            <TabsContent value="evidence">
              <dl className="grid grid-cols-[140px_1fr] gap-x-3 gap-y-1.5 font-mono text-xs">
                <dt className="text-ink-2">recorded</dt><dd>{dateOf(detail.event.created_at)}</dd>
                <dt className="text-ink-2">decision id</dt><dd className="break-all">{detail.event.decision_id}</dd>
                <dt className="text-ink-2">policy version</dt><dd>{detail.event.policy_version ?? "—"}</dd>
                <dt className="text-ink-2">event hash</dt><dd className="break-all">{detail.event.event_hash}</dd>
                <dt className="text-ink-2">prev hash</dt><dd className="break-all">{detail.event.prev_hash ?? "—"}</dd>
                <dt className="text-ink-2">signature</dt><dd className="break-all">{detail.event.signature}</dd>
                <dt className="text-ink-2">approval</dt><dd>{trace.decision.approval_id ?? "—"}</dd>
                <dt className="text-ink-2">context</dt><dd className="break-all">{Object.keys(trace.context).length ? JSON.stringify(trace.context) : "—"}</dd>
              </dl>
              <p className="mt-3 text-xs text-ink-2">Hash-chained and HMAC-signed. Signature presence is not verification; verify against the chain with the signing key.</p>
            </TabsContent>
            <TabsContent value="receipts">
              {detail.receipts.length ? (
                <ul className="space-y-2">
                  {detail.receipts.map((r) => (
                    <li key={r.decision_id} className="rounded border border-hairline p-2 font-mono text-xs">
                      <div className="flex items-center justify-between"><span>{r.model_requested}</span><span className="text-ink-2">{timeOf(r.created_at)}</span></div>
                      <div className="text-ink-2">{r.reason}</div>
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="text-xs text-ink-2">No execution receipt. Authorization is not execution: only the gateway and the demo harness record dispatch.</p>
              )}
            </TabsContent>
            <TabsContent value="raw">
              <pre className="max-h-[60vh] overflow-auto rounded border border-hairline bg-paper p-2 font-mono text-2xs leading-4">{JSON.stringify(detail.trace, null, 2)}</pre>
            </TabsContent>
          </Tabs>
        ) : null}
      </DialogContent>
    </Dialog>
  );
}

// ---------------------------------------------------------------- LeaseInspector
export function LeaseInspector({ lease, onRevoke, onShrink }: { lease: Lease; onRevoke?: (id: string) => void; onShrink?: (id: string) => void }) {
  const status = lease.revoked ? "REVOKED" : lease.status?.toUpperCase() ?? "ACTIVE";
  return (
    <div className="rounded border border-hairline bg-paper-raised p-3 text-sm">
      <div className="flex items-start justify-between gap-3">
        <div>
          <div className="font-mono text-xs">{lease.id}</div>
          <div className="text-ink-2">{lease.subject} · {lease.task} · {lease.tenant}</div>
        </div>
        <Badge tone={lease.revoked ? "deny" : status === "EXPIRED" ? "hold" : "allow"}>{status}</Badge>
      </div>
      <dl className="mt-3 grid grid-cols-[130px_1fr] gap-x-3 gap-y-1 font-mono text-xs">
        <dt className="text-ink-2">actions</dt><dd className="flex flex-wrap gap-1">{lease.actions.map((a) => <Badge key={a} className="normal-case tracking-normal">{a}</Badge>)}</dd>
        <dt className="text-ink-2">resources</dt><dd>{lease.resources.join(", ") || "—"}</dd>
        <dt className="text-ink-2">protected</dt><dd>{lease.protected_resources.join(", ") || "—"}</dd>
        <dt className="text-ink-2">approval</dt><dd>{lease.require_approval.join(", ") || "—"}</dd>
        <dt className="text-ink-2">use limits</dt><dd>{Object.keys(lease.max_uses).length ? Object.entries(lease.max_uses).map(([k, v]) => `${k}: ${lease.uses?.[k] ?? 0}/${v}`).join("  ") : "unlimited"}</dd>
        <dt className="text-ink-2">consequence</dt><dd>{Object.keys(lease.permitted_consequence ?? {}).length ? JSON.stringify(lease.permitted_consequence) : `≤ ${lease.maximum_impact}`}</dd>
        <dt className="text-ink-2">expires</dt><dd>{lease.expires_at ? dateOf(lease.expires_at) : "no expiry"}</dd>
        <dt className="text-ink-2">delegation</dt><dd>{lease.child_authority}{lease.parent_lease ? ` · from ${lease.parent_lease}` : ""}</dd>
        <dt className="text-ink-2">origin</dt><dd>{Object.keys(lease.origin ?? {}).length ? `${String(lease.origin.kind ?? "")} ${String(lease.origin.created_by ?? "")}`.trim() : "—"}</dd>
      </dl>
      {(onRevoke || onShrink) && !lease.revoked ? (
        <div className="mt-3 flex gap-2">
          {onShrink ? <Button size="sm" onClick={() => onShrink(lease.id)}>Narrow</Button> : null}
          {onRevoke ? <Button size="sm" variant="deny" onClick={() => onRevoke(lease.id)}>Revoke</Button> : null}
        </div>
      ) : null}
    </div>
  );
}
