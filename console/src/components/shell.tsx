import { useEffect, useState } from "react";
import { NavLink, Outlet, useNavigate } from "react-router-dom";
import { Check, ChevronDown } from "lucide-react";
import * as DropdownMenu from "@radix-ui/react-dropdown-menu";
import { Api, type Mode } from "@/lib/api";
import { useStore } from "@/lib/store";
import { MODE_COPY, ago, cn } from "@/lib/format";
import { Badge, Button, Dialog, DialogContent, Input } from "./ui";

// Four things a developer does, in the order they do them. Everything else
// is either a drill-down or lives in Settings.
const NAV = [
  { to: "/", label: "Activity", end: true },
  { to: "/agents", label: "Agents" },
  { to: "/rules", label: "Rules" },
  { to: "/integrations", label: "Integrations" },
];

export function ProjectSwitcher() {
  const { project, projects, selectProject, source } = useStore();
  const [creating, setCreating] = useState(false);
  const [name, setName] = useState("");
  const { refreshAccount } = useStore();
  const navigate = useNavigate();

  if (!project) return null;
  if (source === "demo") {
    return <span className="text-sm font-medium">{project.name}</span>;
  }
  return (
    <>
      <DropdownMenu.Root>
        <DropdownMenu.Trigger className="inline-flex items-center gap-1.5 rounded px-1.5 py-1 text-sm font-medium hover:bg-paper-sunk focus:outline-none">
          {project.name}
          <ChevronDown size={13} className="text-ink-2" />
        </DropdownMenu.Trigger>
        <DropdownMenu.Portal>
          <DropdownMenu.Content align="start" sideOffset={6} className="z-50 min-w-[220px] rounded border border-hairline bg-paper-raised p-1">
            {projects.map((p) => (
              <DropdownMenu.Item
                key={p.id}
                onSelect={() => selectProject(p.id)}
                className="flex cursor-pointer items-center justify-between gap-3 rounded px-2 py-1.5 text-sm outline-none data-[highlighted]:bg-paper-sunk"
              >
                <span className="truncate">{p.name}</span>
                <span className="flex items-center gap-2">
                  <span className="dot text-2xs text-ink-2">{p.mode}</span>
                  {p.id === project.id ? <Check size={12} /> : null}
                </span>
              </DropdownMenu.Item>
            ))}
            <DropdownMenu.Separator className="my-1 h-px bg-hairline" />
            <DropdownMenu.Item onSelect={() => setCreating(true)}
              className="cursor-pointer rounded px-2 py-1.5 text-sm outline-none data-[highlighted]:bg-paper-sunk">
              New project…
            </DropdownMenu.Item>
          </DropdownMenu.Content>
        </DropdownMenu.Portal>
      </DropdownMenu.Root>

      <Dialog open={creating} onOpenChange={setCreating}>
        <DialogContent title="New project" description="A project is the boundary: its own agents, rules, keys, and activity.">
          <form
            className="space-y-3"
            onSubmit={async (e) => {
              e.preventDefault();
              const created = await Api.createProject({ name, mode: "observe" });
              setName("");
              setCreating(false);
              await refreshAccount();
              selectProject(created.project.id);
              navigate("/integrations");
            }}
          >
            <label className="block">
              <span className="eyebrow">Project name</span>
              <Input autoFocus value={name} onChange={(e) => setName(e.target.value)} placeholder="production-ops" className="mt-1" />
            </label>
            <p className="text-xs text-ink-2">It starts in Observe, so nothing is blocked while you watch what your agents do.</p>
            <div className="flex justify-end gap-2">
              <Button type="button" variant="ghost" onClick={() => setCreating(false)}>Cancel</Button>
              <Button type="submit" variant="default" disabled={!name.trim()}>Create project</Button>
            </div>
          </form>
        </DialogContent>
      </Dialog>
    </>
  );
}

export function ModeSwitch({ compact }: { compact?: boolean }) {
  const { project, setMode, source } = useStore();
  const [pending, setPending] = useState<Mode | null>(null);
  if (!project) return null;
  const readOnly = source === "demo";

  const change = async (mode: Mode) => {
    if (mode === project.mode || readOnly) return;
    setPending(mode);
    try {
      await setMode(mode);
    } finally {
      setPending(null);
    }
  };

  if (compact) {
    return (
      <span className="dot rounded-sm border border-hairline-strong px-2 py-1 text-2xs text-ink-2">
        {project.mode}
      </span>
    );
  }
  return (
    <div role="group" aria-label="Runtime mode" className="inline-flex items-center rounded border border-hairline-strong bg-paper-raised p-[2px]">
      {(["observe", "govern", "enforce"] as const).map((mode) => (
        <button
          key={mode}
          type="button"
          disabled={readOnly || pending !== null}
          title={MODE_COPY[mode].blurb}
          aria-pressed={project.mode === mode}
          onClick={() => void change(mode)}
          className={cn("dot rounded-sm px-2.5 py-1 text-2xs transition-colors disabled:opacity-50",
            project.mode === mode ? "bg-ink text-paper" : "text-ink-2 hover:text-ink")}
        >
          {mode}
        </button>
      ))}
    </div>
  );
}

export function Shell() {
  const { project, feed, source, setSource, authState, me, signOut, paused, setPaused, refresh } = useStore();
  const navigate = useNavigate();
  const connectedAgents = feed.agents.filter((a) => a.status !== "idle").length;

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "r" && (e.metaKey || e.ctrlKey)) return;
      if (e.key === "/" && document.activeElement?.tagName !== "INPUT") {
        e.preventDefault();
        navigate("/");
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [navigate]);

  return (
    <div className="flex min-h-screen flex-col">
      <header className="flex h-12 shrink-0 items-center gap-3 border-b border-hairline bg-paper-raised px-4">
        <a href="#/" className="flex items-center gap-2" aria-label="agent-plane">
          <img src="/brand/mark.svg" alt="" width={22} height={22} />
          <span className="dot text-[13px] font-semibold tracking-[0.2em]">AGENT-PLANE</span>
        </a>
        <span className="text-hairline-strong">/</span>
        <ProjectSwitcher />

        <nav className="ml-6 hidden items-center gap-1 md:flex" aria-label="Main">
          {NAV.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.end}
              className={({ isActive }) =>
                cn("rounded px-2.5 py-1 text-sm text-ink-2 hover:bg-paper-sunk hover:text-ink",
                   isActive && "bg-paper-sunk font-medium text-ink")
              }
            >
              {item.label}
            </NavLink>
          ))}
        </nav>

        <div className="ml-auto flex items-center gap-3">
          <ModeSwitch />
          {authState?.demo_available ? (
            <div role="group" aria-label="Data source" className="inline-flex items-center rounded border border-hairline-strong bg-paper-raised p-[2px]">
              {(["live", "demo"] as const).map((s) => (
                <button key={s} type="button" aria-pressed={source === s} onClick={() => setSource(s)}
                  className={cn("dot rounded-sm px-2 py-1 text-2xs", source === s ? "bg-ink text-paper" : "text-ink-2 hover:text-ink")}>
                  {s}
                </button>
              ))}
            </div>
          ) : null}
          <DropdownMenu.Root>
            <DropdownMenu.Trigger className="rounded px-2 py-1 text-xs text-ink-2 hover:bg-paper-sunk hover:text-ink focus:outline-none">
              {me?.user.name ?? "Account"}
            </DropdownMenu.Trigger>
            <DropdownMenu.Portal>
              <DropdownMenu.Content align="end" sideOffset={6} className="z-50 min-w-[180px] rounded border border-hairline bg-paper-raised p-1">
                <DropdownMenu.Item onSelect={() => navigate("/settings")} className="cursor-pointer rounded px-2 py-1.5 text-sm outline-none data-[highlighted]:bg-paper-sunk">Settings</DropdownMenu.Item>
                <DropdownMenu.Item onSelect={() => setPaused(!paused)} className="cursor-pointer rounded px-2 py-1.5 text-sm outline-none data-[highlighted]:bg-paper-sunk">
                  {paused ? "Resume live updates" : "Pause live updates"}
                </DropdownMenu.Item>
                <DropdownMenu.Item onSelect={() => void refresh()} className="cursor-pointer rounded px-2 py-1.5 text-sm outline-none data-[highlighted]:bg-paper-sunk">Refresh now</DropdownMenu.Item>
                <DropdownMenu.Separator className="my-1 h-px bg-hairline" />
                <DropdownMenu.Item onSelect={() => void signOut()} className="cursor-pointer rounded px-2 py-1.5 text-sm outline-none data-[highlighted]:bg-paper-sunk">Sign out</DropdownMenu.Item>
              </DropdownMenu.Content>
            </DropdownMenu.Portal>
          </DropdownMenu.Root>
        </div>
      </header>

      <nav className="flex items-center gap-1 border-b border-hairline bg-paper-raised px-4 py-1.5 md:hidden" aria-label="Main">
        {NAV.map((item) => (
          <NavLink key={item.to} to={item.to} end={item.end}
            className={({ isActive }) => cn("rounded px-2 py-1 text-sm text-ink-2", isActive && "bg-paper-sunk text-ink")}>
            {item.label}
          </NavLink>
        ))}
      </nav>

      {source === "demo" ? (
        <div className="flex items-center gap-2 border-b border-hairline bg-paper-sunk px-4 py-1 font-mono text-2xs uppercase tracking-[0.16em] text-ink-2">
          <span className="lamp lamp-on animate-pulse2" />
          demo environment · no external side effects · real authority engine, simulated targets
        </div>
      ) : null}

      {feed.error ? (
        <div className="border-b border-hairline bg-deny-bg px-4 py-1.5 text-xs text-deny">{feed.error}</div>
      ) : null}

      <main className="min-h-0 flex-1">
        <Outlet />
      </main>

      <footer className="flex flex-wrap items-center gap-x-4 gap-y-1 border-t border-hairline bg-paper px-4 py-1.5 font-mono text-2xs text-ink-2">
        <span>{project ? `${connectedAgents} agent${connectedAgents === 1 ? "" : "s"} connected` : "no project"}</span>
        {feed.system ? <><span className="text-hairline-strong">│</span><span>{feed.system.pending_approvals} awaiting review</span></> : null}
        {paused ? <><span className="text-hairline-strong">│</span><Badge tone="hold">paused</Badge></> : null}
        <span className="ml-auto">{feed.updatedAt ? `updated ${ago(new Date(feed.updatedAt).toISOString())}` : ""}</span>
      </footer>
    </div>
  );
}
