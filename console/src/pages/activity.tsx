import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Api } from "@/lib/api";
import { useStore } from "@/lib/store";
import { cn, unruled } from "@/lib/format";
import { ActivityRow, DecisionDrawer } from "@/components/decision";
import { Button, Empty } from "@/components/ui";

const FILTERS = [
  { key: "all", label: "All" },
  { key: "allow", label: "Allowed" },
  { key: "review", label: "Review" },
  { key: "blocked", label: "Blocked" },
] as const;

export function ActivityPage() {
  const { feed, project, source, refresh, me } = useStore();
  const [open, setOpen] = useState<string | null>(null);
  const [filter, setFilter] = useState<(typeof FILTERS)[number]["key"]>("all");
  const [running, setRunning] = useState<string | null>(null);
  // Read the scenarios from the server rather than hard-coding them, so
  // renaming or adding one on the engine side cannot leave dead buttons here.
  const [scenarios, setScenarios] = useState<Array<{ name: string; title: string }>>([]);
  const navigate = useNavigate();

  useEffect(() => {
    if (source !== "demo") return;
    let alive = true;
    Api.demoScenarios()
      .then((d) => alive && setScenarios(d.scenarios.map((s) => ({ name: s.name, title: s.title }))))
      .catch(() => alive && setScenarios([]));
    return () => { alive = false; };
  }, [source]);

  const decisions = useMemo(() => feed.decisions.filter((d) => {
    if (filter === "all") return true;
    if (filter === "allow") return d.outcome === "allow";
    // A result no rule produced is not a review or a block; it is just activity.
    if (unruled(d)) return false;
    if (filter === "review") return d.outcome === "approval_required" || d.would_be === "approval_required";
    return d.outcome === "deny" || d.outcome === "quarantine" || d.would_be === "deny";
  }), [feed.decisions, filter]);

  const agentCount = feed.agents.length;
  const pending = feed.approvals.length;
  // Default-deny is a fact about the project, not about any one action: state it once.
  const unruledCount = useMemo(() => feed.decisions.filter(unruled).length, [feed.decisions]);
  const noRules = !!project && project.rules === 0 && unruledCount > 0;

  const runDemo = async (name: string) => {
    setRunning(name);
    try {
      await Api.runScenario(name);
      await refresh();
    } finally {
      setRunning(null);
    }
  };

  if (!project) {
    return (
      <Empty title="No project yet">
        Create a project to start observing your agents.
      </Empty>
    );
  }

  return (
    <div className="mx-auto max-w-4xl px-4 py-6">
      <div className="mb-5 flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-lg font-medium tracking-tight">{project.name}</h1>
          <p className="mt-0.5 text-sm text-ink-2">
            {agentCount
              ? `${agentCount} agent${agentCount === 1 ? "" : "s"} connected`
              : "No agents connected yet"}
            {pending ? ` · ${pending} awaiting review` : ""}
          </p>
        </div>
        <div className="flex items-center gap-1">
          {FILTERS.map((f) => (
            <button
              key={f.key}
              type="button"
              onClick={() => setFilter(f.key)}
              aria-pressed={filter === f.key}
              className={cn("dot rounded-sm border px-2 py-1 text-2xs",
                filter === f.key ? "border-ink bg-ink text-paper" : "border-hairline-strong text-ink-2 hover:text-ink")}
            >
              {f.label}
            </button>
          ))}
        </div>
      </div>

      {source === "demo" && !feed.decisions.length && scenarios.length ? (
        <div className="mb-4 rounded border border-hairline bg-paper-raised p-4">
          <p className="text-sm">Run a scenario to see real decisions from the real authority engine.</p>
          <div className="mt-3 flex flex-wrap gap-2">
            {scenarios.map((s) => (
              <Button key={s.name} size="sm" disabled={running !== null} onClick={() => void runDemo(s.name)}>
                {running === s.name ? "running…" : s.title}
              </Button>
            ))}
          </div>
        </div>
      ) : null}

      {noRules ? (
        <div className="mb-4 rounded border border-hairline bg-paper-raised px-4 py-3">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div>
              <div className="text-sm font-medium">No rules yet</div>
              <p className="mt-0.5 text-sm text-ink-2">
                Nothing here has been judged. Until you write a rule, Enforce would allow none of it.
              </p>
            </div>
            <Button size="sm" onClick={() => navigate("/rules")}>Write a rule</Button>
          </div>
        </div>
      ) : null}

      {pending ? (
        <div className="mb-4 rounded border border-approval/40 bg-approval-bg px-4 py-3">
          <div className="flex items-center justify-between gap-3">
            <div>
              <div className="text-sm font-medium">{pending} action{pending === 1 ? "" : "s"} waiting for a human</div>
              <div className="mt-0.5 font-mono text-xs text-ink-2">
                {feed.approvals.slice(0, 2).map((a) => `${a.subject} · ${a.action}`).join("   ")}
              </div>
            </div>
            <Button size="sm" onClick={() => setOpen(feed.approvals[0].evidence_id)}>Review</Button>
          </div>
        </div>
      ) : null}

      <div className="panel">
        {decisions.length ? (
          decisions.map((d) => <ActivityRow key={d.decision_id} decision={d} onOpen={setOpen} />)
        ) : feed.decisions.length ? (
          <Empty title="Nothing matches that filter">
            <button className="underline" onClick={() => setFilter("all")}>Show everything</button>
          </Empty>
        ) : (
          <div className="px-4 py-12 text-center">
            <div className="text-sm font-medium">No agent activity yet.</div>
            {source === "demo" ? (
              // Nothing can be connected to the demo project, so do not offer it.
              <p className="mx-auto mt-2 max-w-md text-sm text-ink-2">
                Run a scenario above to watch the authority engine decide.
              </p>
            ) : (
              <>
                <p className="mx-auto mt-2 max-w-md text-sm text-ink-2">
                  Connect a coding agent, MCP server, or application to start observing actions.
                </p>
                <Button className="mt-4" variant="default" onClick={() => navigate("/integrations")}>
                  Connect an integration
                </Button>
                {me && !me.onboarded ? (
                  <p className="mt-3 text-xs text-ink-3">
                    Or switch the toggle to DEMO to watch it work first.
                  </p>
                ) : null}
              </>
            )}
          </div>
        )}
      </div>

      {feed.decisions.length ? (
        <p className="mt-3 text-xs text-ink-3">
          Showing the most recent {feed.decisions.length} action{feed.decisions.length === 1 ? "" : "s"}.
          {" "}
          {project.mode === "observe"
            ? "Nothing is being blocked in Observe."
            : project.mode === "govern"
              ? "Violations are flagged but not blocked in Govern."
              : "Violations are blocked where the integration can block them."}
          {unruledCount > 0 && !noRules
            ? ` ${unruledCount} of them match no rule.`
            : ""}
        </p>
      ) : null}

      <DecisionDrawer id={open} open={open !== null} onOpenChange={(v) => !v && setOpen(null)} />
    </div>
  );
}
