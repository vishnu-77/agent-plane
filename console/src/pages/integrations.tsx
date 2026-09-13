import { useCallback, useEffect, useState } from "react";
import { Check, Copy } from "lucide-react";
import { Api, type ApiKey, type CatalogEntry, type Integration } from "@/lib/api";
import { useStore } from "@/lib/store";
import { ago, capabilityText, cn, shortDate } from "@/lib/format";
import { FeedbackDialog } from "@/components/feedback";
import { Badge, Button, Dialog, DialogContent, Empty, Input } from "@/components/ui";

export function IntegrationsPage() {
  const { project, source, refresh, refreshAccount } = useStore();
  const [integrations, setIntegrations] = useState<Integration[]>([]);
  const [catalog, setCatalog] = useState<CatalogEntry[]>([]);
  const [keys, setKeys] = useState<ApiKey[]>([]);
  const [connect, setConnect] = useState<CatalogEntry | null>(null);
  const [error, setError] = useState<string | null>(null);
  const readOnly = source === "demo";

  const load = useCallback(async () => {
    if (!project) return;
    try {
      const data = await Api.integrations(project.id, source);
      setIntegrations(data.integrations);
      setCatalog(data.catalog);
      if (!readOnly) setKeys((await Api.keys(project.id)).keys);
      setError(null);
    } catch (e) {
      setError((e as Error).message);
    }
  }, [project, source, readOnly]);

  useEffect(() => { void load(); }, [load]);

  if (!project) return <Empty title="No project yet" />;
  const connected = new Map(integrations.map((i) => [i.kind, i]));

  return (
    <div className="mx-auto max-w-4xl px-4 py-6">
      <div className="mb-5">
        <h1 className="text-lg font-medium tracking-tight">Integrations</h1>
        <p className="mt-0.5 text-sm text-ink-2">
          Connect an agent so agent-plane can see what it does. Each one says plainly what it can observe and whether it can block.
        </p>
      </div>
      {error ? <p className="mb-3 text-xs text-deny">{error}</p> : null}

      <div className="space-y-3">
        {catalog.map((entry) => {
          const live = connected.get(entry.kind);
          return (
            <div key={entry.kind} className="panel flex flex-wrap items-center gap-4 px-4 py-4">
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-2">
                  <span className="text-sm font-medium">{entry.label}</span>
                  {live?.status === "connected"
                    ? <Badge tone="allow">connected</Badge>
                    : <Badge>not connected</Badge>}
                </div>
                <p className="mt-1 text-xs text-ink-2">{entry.summary}</p>
                <p className="mt-0.5 font-mono text-2xs text-ink-3">{capabilityText(entry.observation, entry.enforcement)}</p>
                {live?.status === "connected" ? (
                  <p className="mt-1 font-mono text-2xs text-ink-2">
                    {live.host ?? "unknown host"} · {live.actions} action{live.actions === 1 ? "" : "s"} · last seen {ago(live.last_seen_at)}
                  </p>
                ) : null}
              </div>
              <Button size="sm" disabled={readOnly} onClick={() => setConnect(entry)}>
                {live?.status === "connected" ? "Connect another" : "Connect"}
              </Button>
            </div>
          );
        })}
      </div>

      {!readOnly ? (
        <section className="mt-8">
          <div className="mb-3 flex items-end justify-between">
            <div>
              <h2 className="text-sm font-medium">API keys</h2>
              <p className="mt-0.5 text-xs text-ink-2">One per machine or environment, so you can revoke just that one.</p>
            </div>
            <CreateKey projectId={project.id} onCreated={load} />
          </div>
          {keys.length ? (
            <div className="panel divide-y divide-hairline">
              {keys.map((key) => <ApiKeyRow key={key.id} apiKey={key} onChanged={load} />)}
            </div>
          ) : (
            <div className="panel px-4 py-8 text-center text-sm text-ink-2">
              No keys yet. Create one to connect your first agent.
            </div>
          )}
        </section>
      ) : null}

      <ConnectionWizard
        entry={connect}
        projectId={project.id}
        onClose={() => setConnect(null)}
        onConnected={async () => { await load(); await refresh(); await refreshAccount(); }}
      />
    </div>
  );
}

// --------------------------------------------------------------------------- //
function CopyField({ value, label }: { value: string; label?: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <div className="flex items-stretch gap-2">
      <code className="min-w-0 flex-1 overflow-x-auto whitespace-pre rounded border border-hairline bg-paper px-2 py-1.5 font-mono text-xs">
        {value}
      </code>
      <Button
        size="sm"
        onClick={async () => {
          try {
            await navigator.clipboard.writeText(value);
            setCopied(true);
            setTimeout(() => setCopied(false), 1600);
          } catch { /* clipboard blocked; the value is selectable */ }
        }}
      >
        {copied ? <Check size={12} /> : <Copy size={12} />}
        {label ?? (copied ? "Copied" : "Copy")}
      </Button>
    </div>
  );
}

const KEY_KIND = [
  { key: "live", label: "Agent key", blurb: "Connect an agent, MCP server, or gateway. This is almost always what you want." },
  { key: "test", label: "Test key", blurb: "Same as an agent key, marked test so you can tell it apart in the list." },
  { key: "mgmt", label: "Management key", blurb: "Manage this project by API instead of the console - pull or push rules, read decisions and agents. Cannot connect an agent." },
] as const;

function CreateKey({ projectId, onCreated }: { projectId: string; onCreated: () => Promise<void> }) {
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const [environment, setEnvironment] = useState<(typeof KEY_KIND)[number]["key"]>("live");
  const [secret, setSecret] = useState<string | null>(null);
  const kind = KEY_KIND.find((k) => k.key === environment)!;

  return (
    <>
      <Button size="sm" onClick={() => { setSecret(null); setName(""); setEnvironment("live"); setOpen(true); }}>New key</Button>
      <Dialog open={open} onOpenChange={(o) => { setOpen(o); if (!o) setSecret(null); }}>
        <DialogContent title={secret ? "Copy your key" : "New API key"}
          description={secret ? "This is the only time it is shown." : "Name it after the machine, environment, or script that will use it."}>
          {secret ? (
            <div className="space-y-3">
              <CopyField value={secret} />
              <p className="text-xs text-ink-2">
                Store it somewhere safe. If you lose it, rotate the key rather than creating another.
                {environment === "mgmt" ? (
                  <> Use it as <code>X-Admin-Token</code>, e.g. <code>agentplane rules pull --project {projectId} --key ...</code>.</>
                ) : null}
              </p>
              <div className="flex justify-end">
                <Button variant="default" onClick={() => { setOpen(false); setSecret(null); }}>Done</Button>
              </div>
            </div>
          ) : (
            <form
              className="space-y-3"
              onSubmit={async (e) => {
                e.preventDefault();
                const created = await Api.createKey({ project: projectId, name: name || "api key", environment });
                setSecret(created.secret);
                await onCreated();
              }}
            >
              <label className="block">
                <span className="eyebrow">Name</span>
                <Input autoFocus value={name} onChange={(e) => setName(e.target.value)} placeholder="personal-laptop" className="mt-1" />
              </label>
              <div>
                <span className="eyebrow">Kind</span>
                <div className="mt-1 flex gap-1">
                  {KEY_KIND.map((k) => (
                    <button key={k.key} type="button" onClick={() => setEnvironment(k.key)}
                      className={cn("dot flex-1 rounded-sm border px-2 py-1.5 text-2xs",
                        environment === k.key ? "border-ink bg-ink text-paper" : "border-hairline-strong text-ink-2 hover:text-ink")}>
                      {k.label}
                    </button>
                  ))}
                </div>
                <p className="mt-1.5 text-xs text-ink-2">{kind.blurb}</p>
              </div>
              <div className="flex justify-end gap-2">
                <Button type="button" variant="ghost" onClick={() => setOpen(false)}>Cancel</Button>
                <Button type="submit" variant="default">Create key</Button>
              </div>
            </form>
          )}
        </DialogContent>
      </Dialog>
    </>
  );
}

function ApiKeyRow({ apiKey, onChanged }: { apiKey: ApiKey; onChanged: () => Promise<void> }) {
  const [rotated, setRotated] = useState<string | null>(null);
  return (
    <div className="flex flex-wrap items-center gap-4 px-4 py-3">
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <span className="text-sm font-medium">{apiKey.name}</span>
          {apiKey.environment !== "live" ? <Badge>{apiKey.environment}</Badge> : null}
          {apiKey.status !== "active" ? <Badge tone="hold">{apiKey.status}</Badge> : null}
        </div>
        <div className="mt-0.5 font-mono text-xs text-ink-2">{apiKey.masked}</div>
        <div className="mt-1 font-mono text-2xs text-ink-3">
          Created {shortDate(apiKey.created_at)} · Last used {apiKey.last_used_at ? ago(apiKey.last_used_at) : "never"}
        </div>
      </div>
      {apiKey.status === "active" ? (
        <div className="flex gap-2">
          <Button size="sm" onClick={async () => { setRotated((await Api.rotateKey(apiKey.id)).secret); await onChanged(); }}>Rotate</Button>
          <Button size="sm" variant="deny" onClick={async () => { await Api.revokeKey(apiKey.id); await onChanged(); }}>Revoke</Button>
        </div>
      ) : null}
      <Dialog open={rotated !== null} onOpenChange={(o) => !o && setRotated(null)}>
        <DialogContent title="Copy your new key" description="The previous key stopped working the moment this one was created.">
          {rotated ? <CopyField value={rotated} /> : null}
          <div className="mt-3 flex justify-end"><Button variant="default" onClick={() => setRotated(null)}>Done</Button></div>
        </DialogContent>
      </Dialog>
    </div>
  );
}

// --------------------------------------------------------------------------- //
export function ConnectionWizard({ entry, projectId, onClose, onConnected }: {
  entry: CatalogEntry | null; projectId: string; onClose: () => void; onConnected: () => Promise<void>;
}) {
  const [secret, setSecret] = useState<string | null>(null);
  const [waiting, setWaiting] = useState(false);
  const [arrived, setArrived] = useState(false);
  const [feedbackOpen, setFeedbackOpen] = useState(false);

  useEffect(() => {
    if (!entry) { setSecret(null); setWaiting(false); setArrived(false); return; }
    let alive = true;
    (async () => {
      const created = await Api.createKey({ project: projectId, name: `${entry.kind}-${new Date().toISOString().slice(0, 10)}` });
      if (alive) { setSecret(created.secret); setWaiting(true); }
    })();
    return () => { alive = false; };
  }, [entry, projectId]);

  // Poll until the connector reports for the first time, then say so.
  useEffect(() => {
    if (!waiting || !entry) return;
    const timer = setInterval(async () => {
      const data = await Api.integrations(projectId).catch(() => null);
      const live = data?.integrations.find((i) => i.kind === entry.kind && i.status === "connected");
      if (live) {
        setArrived(true);
        setWaiting(false);
        await onConnected();
      }
    }, 2500);
    return () => clearInterval(timer);
  }, [waiting, entry, projectId, onConnected]);

  if (!entry) return null;
  // The CLI defaults to 127.0.0.1:8000. Anywhere else, the command has to say
  // so, or it silently talks to whatever is on that port instead of the
  // service the person is reading this in.
  const origin = window.location.origin;
  const needsUrl = origin !== "http://127.0.0.1:8000" && origin !== "http://localhost:8000";
  const usesCli = entry.connect.startsWith("agentplane");
  const command = entry.connect
    .replace("{key}", secret ?? "ap_live_...")
    .replace("{upstream}", "https://your-mcp-server/mcp")
    .replace("{base_url}", origin)
    + (needsUrl && usesCli ? ` --url ${origin}` : "");
  // Not on PyPI yet - installing from the repo is the only path today. Said
  // once here rather than assumed: "agentplane: command not found" is what a
  // first run looks like otherwise, in every terminal alike.
  const installCommand = `pip install "git+https://github.com/vishnu-77/agent-plane.git"`;
  const step = (n: number) => (usesCli ? n + 1 : n);

  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent title={`Connect ${entry.label}`} description={capabilityText(entry.observation, entry.enforcement)}>
        <div className="space-y-4">
          {usesCli ? (
            <div>
              <div className="eyebrow">1 · Install the CLI</div>
              <p className="mb-2 mt-1 text-xs text-ink-2">
                One-time, in a terminal of your choice - PowerShell, Command Prompt, Terminal, or any shell.
                Skip this if <code>agentplane --help</code> already works for you.
              </p>
              <CopyField value={installCommand} />
            </div>
          ) : null}
          <div>
            <div className="eyebrow">{step(1)} · Your API key</div>
            <p className="mb-2 mt-1 text-xs text-ink-2">Shown once. It identifies this project, nothing else.</p>
            {secret ? <CopyField value={secret} /> : <p className="text-xs text-ink-2">Creating…</p>}
          </div>
          <div>
            <div className="eyebrow">{step(2)} · Run this</div>
            <p className="mb-1 mt-1 text-xs text-ink-2">Same terminal as above - or any other, once the CLI is installed there too.</p>
            <div className="mt-2"><CopyField value={command} /></div>
            {entry.kind === "mcp" ? (
              <p className="mt-2 text-xs text-ink-2">Then point your MCP client at this server instead of the upstream one.</p>
            ) : null}
          </div>
          <div className="rounded border border-hairline bg-paper px-3 py-2">
            <div className="flex items-center gap-2 text-sm">
              <span className={cn("lamp", arrived ? "lamp-allow" : "lamp-on animate-pulse2")} />
              {arrived ? "First activity received." : "Waiting for first activity…"}
            </div>
            <p className="mt-1 text-xs text-ink-2">
              {arrived
                ? "Open Activity to see what it did."
                : "Use your agent as you normally would. The first action it takes will appear here."}
            </p>
          </div>
          <p className="text-xs text-ink-3">{entry.enforcement_note}</p>
          {!arrived ? (
            <div className="space-y-1">
              {usesCli ? (
                <p className="text-xs text-ink-3">
                  <code>agentplane</code> not found even after installing? Its Scripts folder isn't on your PATH -
                  run <code>python -m agent_plane.cli</code> instead.
                </p>
              ) : null}
              <p className="text-xs text-ink-3">
                Command not working?{" "}
                <button type="button" className="underline" onClick={() => setFeedbackOpen(true)}>Send feedback</button>
              </p>
            </div>
          ) : null}
          <div className="flex justify-end">
            <Button variant={arrived ? "default" : "outline"} onClick={onClose}>{arrived ? "Done" : "Close"}</Button>
          </div>
        </div>
      </DialogContent>
      <FeedbackDialog open={feedbackOpen} onOpenChange={setFeedbackOpen} context={`connecting ${entry.label}`} />
    </Dialog>
  );
}
