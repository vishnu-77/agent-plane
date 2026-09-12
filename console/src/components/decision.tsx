import { useEffect, useMemo, useState } from "react";
import { Api, type DecisionDetail, type DecisionSummary, type Trace } from "@/lib/api";
import { useStore } from "@/lib/store";
import { clock, cn, fullDate, reasonText, unruled, verdictLabel, verdictTone, type Tone } from "@/lib/format";
import { AuthorityConsequenceGraph, LineageTree, graphFromTrace } from "./graph";
import { Badge, Button, Dialog, DialogContent } from "./ui";

const TONE_TEXT: Record<Tone, string> = {
  allow: "text-allow", deny: "text-deny", review: "text-approval", hold: "text-hold", neutral: "text-ink",
};
const TONE_BADGE: Record<Tone, "allow" | "deny" | "approval" | "hold" | "neutral"> = {
  allow: "allow", deny: "deny", review: "approval", hold: "hold", neutral: "neutral",
};

// --------------------------------------------------------------------------- //
// ActivityRow - time, agent, action, resource, decision. Nothing else.
// --------------------------------------------------------------------------- //
export function ActivityRow({ decision, onOpen }: { decision: DecisionSummary; onOpen: (id: string) => void }) {
  const tone = verdictTone(decision);
  const quiet = unruled(decision);
  return (
    <button
      type="button"
      onClick={() => onOpen(decision.decision_id)}
      className="row-hover grid w-full grid-cols-[54px_1fr_auto] items-start gap-4 border-b border-hairline px-4 py-3 text-left"
    >
      <span className="pt-[2px] font-mono text-2xs text-ink-2">{clock(decision.created_at)}</span>
      <span className="min-w-0">
        <span className="block text-sm font-medium">{decision.agent}</span>
        <span className="mt-1 block truncate font-mono text-sm">{decision.action}</span>
        <span className="block truncate font-mono text-xs text-ink-2">{decision.resource}</span>
      </span>
      <span className="flex flex-col items-end gap-1">
        <Badge tone={TONE_BADGE[tone]}>{verdictLabel(decision)}</Badge>
        {quiet ? (
          <span className="font-mono text-2xs text-ink-3">no rule yet</span>
        ) : !decision.enforced && decision.would_be ? (
          <span className="font-mono text-2xs text-ink-3">not blocked</span>
        ) : null}
      </span>
    </button>
  );
}

// --------------------------------------------------------------------------- //
// AuthoritySummary - CAN / ASK / NEVER, in that order
// --------------------------------------------------------------------------- //
export function AuthoritySummary({ can, ask, never, empty }: { can: string[]; ask: string[]; never: string[]; empty?: string }) {
  const groups: Array<[string, string[], Tone]> = [["Can", can, "allow"], ["Ask", ask, "review"], ["Never", never, "deny"]];
  if (!can.length && !ask.length && !never.length) {
    return <p className="text-xs text-ink-2">{empty ?? "No authority defined yet."}</p>;
  }
  return (
    <div className="space-y-3">
      {groups.filter(([, items]) => items.length).map(([label, items, tone]) => (
        <div key={label}>
          <div className={cn("eyebrow", TONE_TEXT[tone])}>{label}</div>
          <ul className="mt-1 space-y-0.5">
            {items.map((item) => (
              <li key={item} className="font-mono text-sm">
                <span className={cn("mr-2", TONE_TEXT[tone])}>{tone === "allow" ? "✓" : tone === "review" ? "○" : "×"}</span>
                {item}
              </li>
            ))}
          </ul>
        </div>
      ))}
    </div>
  );
}

// --------------------------------------------------------------------------- //
// DecisionDrawer - progressive disclosure: plain answer first, graph on request
// --------------------------------------------------------------------------- //
function useDecision(id: string | null) {
  const { source } = useStore();
  const [state, setState] = useState<{ detail: DecisionDetail | null; error: string | null; loading: boolean }>(
    { detail: null, error: null, loading: false });
  useEffect(() => {
    if (!id) {
      setState({ detail: null, error: null, loading: false });
      return;
    }
    let alive = true;
    setState((s) => ({ ...s, loading: true }));
    Api.decision(id, source)
      .then((detail) => alive && setState({ detail, error: null, loading: false }))
      .catch((e: Error) => alive && setState({ detail: null, error: e.message, loading: false }));
    return () => { alive = false; };
  }, [id, source]);
  return state;
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <div className="eyebrow">{label}</div>
      <div className="mt-0.5 text-sm">{children}</div>
    </div>
  );
}

export function DecisionDrawer({ id, open, onOpenChange }: { id: string | null; open: boolean; onOpenChange: (open: boolean) => void }) {
  const { detail, error, loading } = useDecision(open ? id : null);
  const { feed, refresh, source } = useStore();
  const [showPath, setShowPath] = useState(false);
  const [showEvidence, setShowEvidence] = useState(false);
  useEffect(() => { setShowPath(false); setShowEvidence(false); }, [id]);

  const trace: Trace | null = detail?.trace ?? null;
  const approval = detail?.related.find((r) => r.kind === "approval")?.approval ?? null;
  const siblings = useMemo(
    () => (trace ? feed.agents.filter((a) => a.tasks.includes(trace.task.id)) : []),
    [trace, feed.agents]);
  const graph = useMemo(() => (trace ? graphFromTrace(trace, siblings) : null), [trace, siblings]);
  const tone = trace ? verdictTone(trace.decision) : "neutral";

  const decide = async (verb: "approve" | "reject") => {
    if (!approval) return;
    await Api.decideApproval(approval.id, verb);
    await refresh();
    onOpenChange(false);
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent side="right" title={trace ? trace.action.name : "Decision"} description={trace?.resource.name}>
        {loading ? <p className="text-xs text-ink-2">Loading…</p> : null}
        {error ? <p className="text-xs text-deny">{error}</p> : null}
        {trace ? (
          <div className="space-y-5">
            <div>
              <div className="text-sm text-ink-2">{trace.identity.agent}</div>
              <div className={cn("dot mt-1 text-[22px] font-semibold leading-none", TONE_TEXT[tone])}>
                {verdictLabel(trace.decision)}
              </div>
              {unruled(trace.decision) ? (
                <p className="mt-1.5 text-xs text-ink-2">
                  No rule covers this yet, so agent-plane has no opinion about it. It is recorded so you can
                  decide what this agent should be allowed to do.
                </p>
              ) : !trace.decision.enforced ? (
                <p className="mt-1.5 text-xs text-ink-2">
                  {trace.mode === "observe"
                    ? "Observe mode: recorded, nothing was blocked."
                    : "Govern mode: flagged, but not blocked."}
                </p>
              ) : null}
            </div>

            <div className="space-y-3 border-t border-hairline pt-4">
              <Field label="Task">
                {trace.task.id}
                {"text" in trace.task.origin && trace.task.origin.text ? (
                  <blockquote className="mt-1 border-l-2 border-hairline-strong pl-2 text-sm text-ink-2">“{trace.task.origin.text}”</blockquote>
                ) : null}
              </Field>
              <Field label="Authority">
                {trace.authority.lease ? (
                  <>
                    <div>{trace.authority.lineage_permits_action ? "This action is granted" : "This action was never granted"}</div>
                    <div className="mt-0.5 font-mono text-xs text-ink-2">
                      {/* A bare "*" is the scope, but it reads as noise: say what it means. */}
                      {trace.authority.resources.length === 1 && trace.authority.resources[0] === "*"
                        ? "any resource in this project"
                        : trace.authority.resources.join(", ") || "no resources"}
                      {trace.authority.protected_resources.length ? ` · except ${trace.authority.protected_resources.join(", ")}` : ""}
                    </div>
                  </>
                ) : (
                  <span className="text-ink-2">Nothing has been granted for this task yet.</span>
                )}
              </Field>
              <Field label="Resource"><span className="font-mono text-sm">{trace.resource.name}</span></Field>
              {trace.consequence ? (
                <Field label="Consequence">
                  <div>{trace.consequence.consequence_class.replace(/_/g, " ")}</div>
                  <div className="mt-0.5 text-xs text-ink-2">{trace.consequence.direct_effect}</div>
                </Field>
              ) : null}
            </div>

            <div className="border-t border-hairline pt-4">
              <div className="eyebrow">Why</div>
              {trace.explanation.map((line, i) => (
                <p key={i} className="mt-1 text-sm leading-6">{line}</p>
              ))}
            </div>

            {approval && approval.status === "pending" ? (
              <div className="flex gap-2 border-t border-hairline pt-4">
                <Button variant="allow" onClick={() => void decide("approve")} disabled={source === "demo"}>Approve once</Button>
                <Button variant="deny" onClick={() => void decide("reject")} disabled={source === "demo"}>Reject</Button>
              </div>
            ) : null}

            <div className="flex flex-wrap gap-2 border-t border-hairline pt-4">
              <Button size="sm" onClick={() => setShowPath((v) => !v)}>
                {showPath ? "Hide authority path" : "View authority path"}
              </Button>
              <Button size="sm" variant="ghost" onClick={() => setShowEvidence((v) => !v)}>
                {showEvidence ? "Hide evidence" : "Evidence"}
              </Button>
            </div>

            {showPath ? (
              <div className="space-y-3">
                <div className="h-[320px] overflow-hidden rounded border border-hairline">
                  <AuthorityConsequenceGraph graph={graph} compact />
                </div>
                {trace.authority.lineage.length > 1 ? (
                  <div className="rounded border border-hairline bg-paper p-2">
                    <div className="eyebrow mb-1">Where the authority came from</div>
                    <LineageTree chain={trace.authority.lineage} action={trace.action.name} agent={trace.identity.agent} />
                  </div>
                ) : null}
              </div>
            ) : null}

            {showEvidence && detail ? (
              <dl className="grid grid-cols-[110px_1fr] gap-x-3 gap-y-1 border-t border-hairline pt-4 font-mono text-2xs">
                <dt className="text-ink-2">recorded</dt><dd>{fullDate(detail.event.created_at)}</dd>
                <dt className="text-ink-2">decision</dt><dd className="break-all">{detail.decision_id}</dd>
                <dt className="text-ink-2">reason</dt><dd>{trace.decision.reason} · {reasonText(trace.decision.reason)}</dd>
                <dt className="text-ink-2">event hash</dt><dd className="break-all">{detail.event.event_hash}</dd>
                <dt className="text-ink-2">signature</dt><dd className="break-all">{detail.event.signature}</dd>
                <dt className="text-ink-2">receipts</dt><dd>{detail.receipts.length || "none"}</dd>
              </dl>
            ) : null}
          </div>
        ) : null}
      </DialogContent>
    </Dialog>
  );
}
