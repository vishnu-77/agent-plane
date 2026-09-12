import { useCallback, useEffect, useMemo, useState } from "react";
import { Api, type ActionOption, type Rule, type RuleTemplate } from "@/lib/api";
import { useStore } from "@/lib/store";
import { cn } from "@/lib/format";
import { Badge, Button, Dialog, DialogContent, Empty, Input } from "@/components/ui";

type Bucket = "allow" | "ask" | "never" | "off";
const BUCKETS: Array<{ key: Exclude<Bucket, "off">; title: string; blurb: string; mark: string; tone: string }> = [
  { key: "allow", title: "Allow", blurb: "Runs without asking anyone.", mark: "✓", tone: "text-allow" },
  { key: "ask", title: "Ask first", blurb: "Pauses until a human approves it.", mark: "○", tone: "text-approval" },
  { key: "never", title: "Never", blurb: "Refused, whatever else grants it.", mark: "×", tone: "text-deny" },
];

export function RulesPage() {
  const { project, source, refresh } = useStore();
  const [rules, setRules] = useState<Rule[]>([]);
  const [actions, setActions] = useState<ActionOption[]>([]);
  const [templates, setTemplates] = useState<RuleTemplate[]>([]);
  const [suggestions, setSuggestions] = useState<Array<Record<string, unknown>>>([]);
  const [editing, setEditing] = useState<Rule | "new" | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    if (!project) return;
    try {
      const [data, suggested] = await Promise.all([
        Api.rules(project.id, source),
        Api.suggestedRules(project.id, source).catch(() => ({ suggestions: [] })),
      ]);
      setRules(data.rules);
      setActions(data.actions);
      setTemplates(data.templates);
      setSuggestions(suggested.suggestions);
      setError(null);
    } catch (e) {
      setError((e as Error).message);
    }
  }, [project, source]);

  useEffect(() => { void load(); }, [load]);

  const applyTemplate = async (template: RuleTemplate) => {
    if (!project) return;
    await Api.createRule({
      project: project.id, name: template.name, allow: template.allow, ask: template.ask,
      never: template.never, resources: template.resources ?? ["*"],
      protected_resources: template.protected_resources ?? [],
      permitted_consequence: template.permitted_consequence ?? {}, source: "template",
    });
    await load();
    await refresh();
  };

  const applySuggestion = async (draft: Record<string, unknown>) => {
    if (!project) return;
    await Api.createRule({ ...draft, project: project.id });
    await load();
    await refresh();
  };

  if (!project) return <Empty title="No project yet" />;
  const readOnly = source === "demo";

  return (
    <div className="mx-auto max-w-4xl px-4 py-6">
      <div className="mb-5 flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-lg font-medium tracking-tight">Rules</h1>
          <p className="mt-0.5 text-sm text-ink-2">
            What your agents may do, what needs a human, and what is never allowed.
          </p>
        </div>
        <Button variant="default" disabled={readOnly} onClick={() => setEditing("new")}>Create rule</Button>
      </div>
      {error ? <p className="mb-3 text-xs text-deny">{error}</p> : null}

      {rules.length ? (
        <div className="space-y-3">
          {rules.map((rule) => (
            <div key={rule.id} className={cn("panel px-4 py-4", !rule.enabled && "opacity-60")}>
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div>
                  <div className="text-sm font-medium">{rule.name}</div>
                  <div className="mt-0.5 text-xs text-ink-2">{rule.scope_label} · {rule.summary}</div>
                </div>
                <div className="flex items-center gap-2">
                  {rule.source !== "manual" ? <Badge>{rule.source}</Badge> : null}
                  {!rule.enabled ? <Badge tone="hold">off</Badge> : null}
                  <Button size="sm" variant="ghost" disabled={readOnly} onClick={() => setEditing(rule)}>Edit</Button>
                </div>
              </div>
              <div className="mt-3 grid grid-cols-1 gap-4 sm:grid-cols-3">
                {BUCKETS.map((bucket) => {
                  const items = rule[bucket.key];
                  return (
                    <div key={bucket.key}>
                      <div className={cn("eyebrow", bucket.tone)}>{bucket.title}</div>
                      <ul className="mt-1 space-y-0.5">
                        {items.length ? items.map((item) => (
                          <li key={item} className="font-mono text-xs">
                            <span className={cn("mr-1.5", bucket.tone)}>{bucket.mark}</span>{item}
                          </li>
                        )) : <li className="text-xs text-ink-3">—</li>}
                      </ul>
                    </div>
                  );
                })}
              </div>
              {rule.resources.join() !== "*" ? (
                <div className="mt-3 border-t border-hairline pt-2 font-mono text-2xs text-ink-2">
                  scope: {rule.resources.join(", ")}
                  {rule.protected_resources.length ? ` · except ${rule.protected_resources.join(", ")}` : ""}
                </div>
              ) : null}
            </div>
          ))}
        </div>
      ) : (
        <div className="panel px-4 py-10 text-center">
          <div className="text-sm font-medium">No custom rules.</div>
          <p className="mx-auto mt-2 max-w-md text-sm text-ink-2">
            agent-plane is currently observing activity without enforcing additional restrictions.
          </p>
          <Button className="mt-4" variant="default" disabled={readOnly} onClick={() => setEditing("new")}>Create rule</Button>
        </div>
      )}

      {suggestions.length ? (
        <section className="mt-6">
          <h2 className="text-sm font-medium">Suggested from what your agents actually did</h2>
          <p className="mt-0.5 text-xs text-ink-2">Reads are proposed as Allow, changes as Ask, destructive actions as Never. Review before applying.</p>
          <div className="mt-3 space-y-3">
            {suggestions.map((draft, i) => (
              <div key={i} className="panel px-4 py-3">
                <div className="flex items-start justify-between gap-3">
                  <div className="text-sm">{String(draft.name)}</div>
                  <Button size="sm" disabled={readOnly} onClick={() => void applySuggestion(draft)}>Apply</Button>
                </div>
                <div className="mt-2 grid grid-cols-1 gap-3 font-mono text-xs sm:grid-cols-3">
                  <div><span className="text-allow">allow</span> {(draft.allow as string[]).join(", ") || "—"}</div>
                  <div><span className="text-approval">ask</span> {(draft.ask as string[]).join(", ") || "—"}</div>
                  <div><span className="text-deny">never</span> {(draft.never as string[]).join(", ") || "—"}</div>
                </div>
              </div>
            ))}
          </div>
        </section>
      ) : null}

      {!rules.length && templates.length ? (
        <section className="mt-6">
          <h2 className="text-sm font-medium">Start from a template</h2>
          <div className="mt-3 grid grid-cols-1 gap-3 sm:grid-cols-2">
            {templates.map((t) => (
              <div key={t.key} className="panel px-4 py-3">
                <div className="text-sm font-medium">{t.name}</div>
                <p className="mt-1 text-xs text-ink-2">{t.description}</p>
                <Button size="sm" className="mt-3" disabled={readOnly} onClick={() => void applyTemplate(t)}>Use this</Button>
              </div>
            ))}
          </div>
        </section>
      ) : null}

      <PermissionsFile
        projectId={project.id}
        source={source}
        readOnly={readOnly}
        signature={rules.map((r) => `${r.id}:${r.updated_at}`).join("|")}
        onApplied={async () => { await load(); await refresh(); }}
      />

      <RuleEditor
        rule={editing}
        actions={actions}
        projectId={project.id}
        onClose={() => setEditing(null)}
        onSaved={async () => { setEditing(null); await load(); await refresh(); }}
      />
    </div>
  );
}

/**
 * The same rules as a file, for people who keep permissions in version control.
 *
 * Read it, copy it, or paste one in. Applying is deliberately two decisions:
 * merging leaves rules the file does not mention alone, and replacing says the
 * file is the whole truth for this project.
 */
function PermissionsFile({ projectId, source, readOnly, signature, onApplied }: {
  projectId: string;
  source: "live" | "demo";
  readOnly: boolean;
  signature: string;
  onApplied: () => Promise<void>;
}) {
  const [open, setOpen] = useState(false);
  const [text, setText] = useState("");
  const [saved, setSaved] = useState("");
  const [status, setStatus] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const reload = useCallback(async () => {
    try {
      const data = await Api.exportRules(projectId, source);
      setText(data.yaml);
      setSaved(data.yaml);
      setError(null);
    } catch (e) {
      setError((e as Error).message);
    }
  }, [projectId, source]);

  // Re-read whenever a rule changes elsewhere on the page, so the file never
  // shows something the project no longer says.
  useEffect(() => { if (open) void reload(); }, [open, reload, signature]);

  const apply = async (mode: "merge" | "replace") => {
    setBusy(true);
    setError(null);
    try {
      const result = await Api.importRules(projectId, text, mode);
      setStatus(`${result.created.length} created, ${result.updated.length} updated, ${result.deleted.length} removed`);
      await onApplied();
      await reload();
      setTimeout(() => setStatus(null), 4000);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="mt-6 border-t border-hairline pt-4">
      <button type="button" className="text-xs text-ink-2 underline" onClick={() => setOpen((v) => !v)}>
        {open ? "Hide permissions file" : "Permissions as a file"}
      </button>
      {open ? (
        <div className="mt-3 space-y-2">
          <p className="text-xs text-ink-2">
            The same rules in YAML. Keep it next to the code it governs, or paste one in.
          </p>
          <textarea
            className="h-64 w-full rounded border border-hairline bg-paper p-3 font-mono text-2xs leading-4"
            spellCheck={false}
            value={text}
            readOnly={readOnly}
            onChange={(e) => setText(e.target.value)}
          />
          {error ? <p className="text-xs text-deny">{error}</p> : null}
          {status ? <p className="text-xs text-ink-2">{status}</p> : null}
          <div className="flex flex-wrap items-center gap-2">
            <Button size="sm" variant="ghost" onClick={() => void navigator.clipboard.writeText(text)}>Copy</Button>
            <Button size="sm" disabled={readOnly || busy || text === saved} onClick={() => void apply("merge")}>
              Apply
            </Button>
            <Button size="sm" variant="deny" disabled={readOnly || busy || text === saved}
              onClick={() => void apply("replace")}>
              Replace all
            </Button>
            {text !== saved ? (
              <button type="button" className="text-xs text-ink-2 underline" onClick={() => setText(saved)}>
                Discard changes
              </button>
            ) : null}
          </div>
          <p className="text-2xs text-ink-3">
            Apply creates and updates by name. Replace all also deletes every rule the file does not mention.
          </p>
        </div>
      ) : null}
    </section>
  );
}

function RuleEditor({ rule, actions, projectId, onClose, onSaved }: {
  rule: Rule | "new" | null; actions: ActionOption[]; projectId: string;
  onClose: () => void; onSaved: () => Promise<void>;
}) {
  const existing = rule !== "new" && rule !== null ? rule : null;
  const [name, setName] = useState("");
  const [buckets, setBuckets] = useState<Record<string, Bucket>>({});
  const [busy, setBusy] = useState(false);
  const [showSource, setShowSource] = useState(false);

  useEffect(() => {
    if (rule === null) return;
    setName(existing?.name ?? "");
    const next: Record<string, Bucket> = {};
    for (const action of actions) next[action.action] = "off";
    for (const a of existing?.allow ?? []) next[a] = "allow";
    for (const a of existing?.ask ?? []) next[a] = "ask";
    for (const a of existing?.never ?? []) next[a] = "never";
    setBuckets(next);
    setShowSource(false);
  }, [rule, existing, actions]);

  const payload = useMemo(() => ({
    name: name.trim() || "Rule",
    allow: Object.entries(buckets).filter(([, b]) => b === "allow").map(([a]) => a),
    ask: Object.entries(buckets).filter(([, b]) => b === "ask").map(([a]) => a),
    never: Object.entries(buckets).filter(([, b]) => b === "never").map(([a]) => a),
  }), [name, buckets]);

  const save = async () => {
    setBusy(true);
    try {
      if (existing) await Api.updateRule(existing.id, payload);
      else await Api.createRule({ ...payload, project: projectId });
      await onSaved();
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog open={rule !== null} onOpenChange={(open) => !open && onClose()}>
      <DialogContent title={existing ? "Edit rule" : "Create rule"}
        description="Pick what each action should do. Never always wins, whatever another rule allows.">
        <div className="space-y-4">
          <label className="block">
            <span className="eyebrow">Name</span>
            <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="Coding agents" className="mt-1" />
          </label>

          <div className="max-h-[45vh] overflow-y-auto rounded border border-hairline">
            {actions.map((action) => (
              <div key={action.action} className="flex items-center justify-between gap-3 border-b border-hairline px-3 py-2 last:border-b-0">
                <div className="min-w-0">
                  <div className="truncate text-sm">{action.label}</div>
                  <div className="truncate font-mono text-2xs text-ink-2">{action.action}</div>
                </div>
                <div className="inline-flex shrink-0 rounded border border-hairline-strong p-[2px]">
                  {(["off", "allow", "ask", "never"] as const).map((bucket) => (
                    <button
                      key={bucket}
                      type="button"
                      aria-pressed={buckets[action.action] === bucket}
                      onClick={() => setBuckets((b) => ({ ...b, [action.action]: bucket }))}
                      className={cn("dot rounded-sm px-1.5 py-0.5 text-[9px]",
                        buckets[action.action] === bucket ? "bg-ink text-paper" : "text-ink-2 hover:text-ink")}
                    >
                      {bucket}
                    </button>
                  ))}
                </div>
              </div>
            ))}
          </div>

          <button type="button" className="text-xs text-ink-2 underline" onClick={() => setShowSource((v) => !v)}>
            {showSource ? "Hide policy source" : "View policy source"}
          </button>
          {showSource ? (
            <pre className="max-h-48 overflow-auto rounded border border-hairline bg-paper p-2 font-mono text-2xs leading-4">
{JSON.stringify(payload, null, 2)}
            </pre>
          ) : null}

          <div className="flex justify-between">
            {existing ? (
              <div className="flex gap-2">
                <Button variant="ghost" disabled={busy}
                  onClick={async () => { await Api.updateRule(existing.id, { enabled: !existing.enabled }); await onSaved(); }}>
                  {existing.enabled ? "Turn off" : "Turn on"}
                </Button>
                <Button variant="deny" disabled={busy}
                  onClick={async () => { await Api.deleteRule(existing.id); await onSaved(); }}>Delete</Button>
              </div>
            ) : <span />}
            <div className="flex gap-2">
              <Button variant="ghost" onClick={onClose}>Cancel</Button>
              <Button variant="default" disabled={busy} onClick={() => void save()}>
                {existing ? "Save rule" : "Create rule"}
              </Button>
            </div>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}
