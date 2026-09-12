import { useCallback, useEffect, useState } from "react";
import { Api, type Project } from "@/lib/api";
import { useStore } from "@/lib/store";
import { MODE_COPY, cn } from "@/lib/format";
import { Badge, Button, Empty, Input, Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui";

const COLLECTION_LABELS: Record<string, string> = {
  agent_identity: "Agent identity",
  integration_identity: "Integration identity",
  session_metadata: "Session metadata",
  task_metadata: "Task metadata",
  action_name: "Tool / action name",
  resource_identifier: "Resource identifier",
  decision_result: "Decision result",
  authority_metadata: "Authority metadata",
  consequence_metadata: "Consequence metadata",
  prompt_content: "Prompt content",
  tool_arguments: "Tool arguments",
  tool_output: "Tool output",
  file_content: "File content",
  model_messages: "Model messages",
};

export function SettingsPage() {
  const { project, me, source } = useStore();
  if (!project || source === "demo") {
    return <Empty title="Settings are only available for your own projects" />;
  }
  return (
    <div className="mx-auto max-w-3xl px-4 py-6">
      <div className="mb-5">
        <h1 className="text-lg font-medium tracking-tight">Settings</h1>
        <p className="mt-0.5 text-sm text-ink-2">{project.name}</p>
      </div>
      <Tabs defaultValue="project">
        <TabsList className="flex-wrap">
          <TabsTrigger value="project">Project</TabsTrigger>
          <TabsTrigger value="mode">Runtime mode</TabsTrigger>
          <TabsTrigger value="collection">Data collection</TabsTrigger>
          <TabsTrigger value="members">Members</TabsTrigger>
          <TabsTrigger value="advanced">Advanced</TabsTrigger>
        </TabsList>
        <TabsContent value="project"><ProjectSettings project={project} /></TabsContent>
        <TabsContent value="mode"><ModeSettings project={project} /></TabsContent>
        <TabsContent value="collection"><CollectionSettings project={project} /></TabsContent>
        <TabsContent value="members"><Members workspaceId={project.workspace_id} email={me?.user.email} /></TabsContent>
        <TabsContent value="advanced"><Advanced project={project} /></TabsContent>
      </Tabs>
    </div>
  );
}

function ProjectSettings({ project }: { project: Project }) {
  const { refreshAccount } = useStore();
  const [name, setName] = useState(project.name);
  const [saved, setSaved] = useState(false);
  useEffect(() => setName(project.name), [project.name]);
  return (
    <form
      className="max-w-md space-y-3"
      onSubmit={async (e) => {
        e.preventDefault();
        await Api.updateProject(project.id, { name });
        await refreshAccount();
        setSaved(true);
        setTimeout(() => setSaved(false), 1600);
      }}
    >
      <label className="block">
        <span className="eyebrow">Project name</span>
        <Input value={name} onChange={(e) => setName(e.target.value)} className="mt-1" />
      </label>
      <dl className="grid grid-cols-[130px_1fr] gap-x-3 gap-y-1 font-mono text-xs text-ink-2">
        <dt>project id</dt><dd className="text-ink">{project.id}</dd>
        <dt>agents</dt><dd className="text-ink">{project.connected} connected integration(s)</dd>
        <dt>rules</dt><dd className="text-ink">{project.rules}</dd>
      </dl>
      <Button type="submit" variant="default">{saved ? "Saved" : "Save"}</Button>
    </form>
  );
}

function ModeSettings({ project }: { project: Project }) {
  const { setMode } = useStore();
  const [busy, setBusy] = useState(false);
  return (
    <div className="space-y-3">
      {(["observe", "govern", "enforce"] as const).map((mode) => (
        <button
          key={mode}
          type="button"
          disabled={busy}
          onClick={async () => { setBusy(true); try { await setMode(mode); } finally { setBusy(false); } }}
          className={cn("panel block w-full px-4 py-3 text-left", project.mode === mode && "border-ink")}
        >
          <div className="flex items-center gap-2">
            <span className={cn("lamp", project.mode === mode && "lamp-on")} />
            <span className="dot text-xs font-semibold">{MODE_COPY[mode].label}</span>
            {project.mode === mode ? <Badge tone="ink">current</Badge> : null}
          </div>
          <p className="mt-1 pl-4 text-sm text-ink-2">{MODE_COPY[mode].blurb}</p>
        </button>
      ))}
      <p className="text-xs text-ink-3">
        Enforce only blocks where the integration can block. An SDK or editor that reports after the fact is
        flagged, not stopped; the Integrations screen says which is which.
      </p>
    </div>
  );
}

function CollectionSettings({ project }: { project: Project }) {
  const { refreshAccount } = useStore();
  const [fields, setFields] = useState<{ defaults: Record<string, boolean>; optional: string[] } | null>(null);
  const [collection, setCollection] = useState<Record<string, boolean>>(project.collection);

  const load = useCallback(async () => {
    const data = await Api.project(project.id);
    setFields(data.collection_fields);
    setCollection(data.project.collection);
  }, [project.id]);
  useEffect(() => { void load(); }, [load]);

  const toggle = async (key: string, value: boolean) => {
    setCollection((c) => ({ ...c, [key]: value }));
    await Api.updateProject(project.id, { collection: { [key]: value } });
    await refreshAccount();
  };

  if (!fields) return <p className="text-xs text-ink-2">Loading…</p>;
  const optional = new Set(fields.optional);
  const metadata = Object.keys(fields.defaults).filter((k) => !optional.has(k));

  return (
    <div className="space-y-5">
      <p className="text-sm text-ink-2">
        agent-plane does not need prompt or file content to tell you what an agent did and whether it
        had the authority to do it.
      </p>
      <section>
        <h3 className="eyebrow">Always collected</h3>
        <ul className="mt-2 space-y-1">
          {metadata.map((key) => (
            <li key={key} className="flex items-center gap-2 text-sm">
              <span className="text-allow">✓</span>{COLLECTION_LABELS[key] ?? key}
            </li>
          ))}
        </ul>
      </section>
      <section>
        <h3 className="eyebrow">Optional · off by default</h3>
        <ul className="mt-2 space-y-1">
          {fields.optional.map((key) => (
            <li key={key}>
              <label className="flex cursor-pointer items-center gap-2 text-sm">
                <input
                  type="checkbox"
                  className="accent-ink"
                  checked={!!collection[key]}
                  onChange={(e) => void toggle(key, e.target.checked)}
                />
                {COLLECTION_LABELS[key] ?? key}
              </label>
            </li>
          ))}
        </ul>
        <p className="mt-2 text-xs text-ink-3">
          Turning these on stores the content itself in this project's evidence. Everyone with access to the
          project can read it.
        </p>
      </section>
    </div>
  );
}

function Members({ workspaceId, email }: { workspaceId: string; email?: string }) {
  const [members, setMembers] = useState<Array<{ user_id: string; role: string; email: string; name: string }>>([]);
  useEffect(() => {
    Api.members(workspaceId).then((d) => setMembers(d.members)).catch(() => setMembers([]));
  }, [workspaceId]);
  return (
    <div className="space-y-3">
      <div className="panel divide-y divide-hairline">
        {members.map((m) => (
          <div key={m.user_id} className="flex items-center justify-between px-4 py-3">
            <div>
              <div className="text-sm">{m.name || m.email}</div>
              <div className="font-mono text-2xs text-ink-2">{m.email}{m.email === email ? " · you" : ""}</div>
            </div>
            <Badge tone={m.role === "owner" ? "ink" : "neutral"}>{m.role}</Badge>
          </div>
        ))}
      </div>
      <p className="text-xs text-ink-3">Everyone in the workspace can see every project in it.</p>
    </div>
  );
}

function Advanced({ project }: { project: Project }) {
  const { refreshAccount, selectProject, projects } = useStore();
  const [confirm, setConfirm] = useState("");
  return (
    <div className="space-y-5">
      <section>
        <h3 className="text-sm font-medium">Underneath</h3>
        <p className="mt-1 text-xs text-ink-2">
          Rules compile into task-scoped authority leases, which is what the engine evaluates. You never have to
          write one, but the API is there if you want to issue authority for a single task.
        </p>
        <dl className="mt-2 grid grid-cols-[150px_1fr] gap-x-3 gap-y-1 font-mono text-xs text-ink-2">
          <dt>decision api</dt><dd className="text-ink">POST /v1/authorize</dd>
          <dt>ingestion</dt><dd className="text-ink">POST /v1/events/action</dd>
          <dt>api reference</dt><dd><a className="text-ink underline" href="/docs" target="_blank" rel="noreferrer">/docs</a></dd>
          <dt>metrics</dt><dd><a className="text-ink underline" href="/metrics" target="_blank" rel="noreferrer">/metrics</a></dd>
        </dl>
      </section>
      <section className="border-t border-hairline pt-4">
        <h3 className="text-sm font-medium text-deny">Delete this project</h3>
        <p className="mt-1 text-xs text-ink-2">
          Its keys stop working, its agents and activity are removed. This cannot be undone.
        </p>
        <div className="mt-2 flex max-w-md gap-2">
          <Input value={confirm} onChange={(e) => setConfirm(e.target.value)} placeholder={project.name} />
          <Button
            variant="deny"
            disabled={confirm !== project.name}
            onClick={async () => {
              await Api.deleteProject(project.id);
              const next = await refreshAccount();
              const remaining = next?.projects ?? projects;
              if (remaining[0]) selectProject(remaining[0].id);
            }}
          >
            Delete
          </Button>
        </div>
      </section>
    </div>
  );
}
