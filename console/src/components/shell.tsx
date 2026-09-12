import { Command } from "cmdk";
import { useEffect, useState } from "react";
import { NavLink, Outlet, useNavigate } from "react-router-dom";
import { useStore } from "@/lib/store";
import { cn, pad } from "@/lib/utils";
import { DemoMarker, DemoSwitch, StateLamp, SystemTicker } from "./instruments";
import { Button, Dialog, DialogContent, Input, Kbd } from "./ui";

const NAV: Array<{ group: string; items: Array<{ to: string; label: string; end?: boolean }> }> = [
  { group: "", items: [{ to: "/", label: "Live", end: true }] },
  { group: "SYSTEM", items: [{ to: "/agents", label: "Agents" }, { to: "/tasks", label: "Tasks" }, { to: "/resources", label: "Resources" }] },
  { group: "GOVERN", items: [{ to: "/govern", label: "Authority × Consequence" }, { to: "/decisions", label: "Decisions" }, { to: "/policies", label: "Policies" }] },
  { group: "EVIDENCE", items: [{ to: "/timeline", label: "Timeline" }, { to: "/audit", label: "Audit" }] },
  { group: "PLATFORM", items: [{ to: "/integrations", label: "Integrations" }, { to: "/gateway", label: "Gateway" }, { to: "/runtime", label: "Runtime" }, { to: "/settings", label: "Settings" }] },
];

export function Shell() {
  const { mode, setMode, demoAvailable, snapshot, connected, creds, setAdminToken, paused, setPaused, refresh } = useStore();
  const [connectOpen, setConnectOpen] = useState(false);
  const [cmdOpen, setCmdOpen] = useState(false);
  const [token, setToken] = useState("");
  const navigate = useNavigate();
  const sys = snapshot.system;

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setCmdOpen((o) => !o);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  return (
    <div className="flex h-full min-h-screen flex-col">
      <header className="flex h-12 items-center gap-4 border-b border-hairline bg-paper-raised px-4">
        <a href="#/" className="flex items-center gap-2.5" aria-label="agent-plane">
          <img src="/brand/mark.svg" alt="" width={26} height={26} />
          <span className="dot text-sm font-semibold tracking-[0.22em]">AGENT-PLANE</span>
        </a>
        <span className="hidden text-hairline-strong md:inline">/</span>
        <span className="hidden font-mono text-2xs uppercase tracking-[0.16em] text-ink-2 md:inline">authority × consequence</span>
        <div className="ml-auto flex items-center gap-3">
          <StateLamp tone={sys ? (sys.mode === "enforce" ? "on" : "approval") : "off"} label={sys ? sys.mode : "offline"} pulse={!!sys} />
          <Button size="sm" variant="ghost" onClick={() => setCmdOpen(true)} className="hidden md:inline-flex">
            Go to… <Kbd>⌘K</Kbd>
          </Button>
          <Button size="sm" variant="ghost" onClick={() => setPaused(!paused)} aria-pressed={paused}>{paused ? "Resume" : "Pause"}</Button>
          <Button size="sm" variant="ghost" onClick={() => void refresh()}>Refresh</Button>
          <Button size="sm" variant={mode === "live" && !creds.admin ? "default" : "outline"} onClick={() => setConnectOpen(true)}>
            {creds.admin ? "Operator" : "Connect"}
          </Button>
          <DemoSwitch mode={mode} onChange={setMode} demoAvailable={demoAvailable} />
        </div>
      </header>
      {mode === "demo" ? <DemoMarker /> : null}
      <div className="flex min-h-0 flex-1">
        <nav className="hidden w-[196px] shrink-0 border-r border-hairline bg-paper px-2 py-3 md:block" aria-label="Main">
          {NAV.map((g) => (
            <div key={g.group || "live"} className="mb-4">
              {g.group ? <div className="eyebrow px-2 pb-1">{g.group}</div> : null}
              {g.items.map((it) => (
                <NavLink
                  key={it.to}
                  to={it.to}
                  end={it.end}
                  className={({ isActive }) =>
                    cn("block rounded px-2 py-1.5 text-sm text-ink-2 hover:bg-paper-sunk hover:text-ink", isActive && "bg-ink text-paper hover:bg-ink hover:text-paper", it.end && "dot text-xs")
                  }
                >
                  {it.label}
                </NavLink>
              ))}
            </div>
          ))}
        </nav>
        <main id="content" className="min-w-0 flex-1 overflow-auto">
          {!connected ? (
            <div className="border-b border-hairline bg-paper-sunk px-4 py-2 text-xs text-ink-2">
              {mode === "live" ? (
                <>Connect operator access to read this runtime. <button className="underline" onClick={() => setConnectOpen(true)}>Connect</button>{demoAvailable ? <> · or switch to <button className="underline" onClick={() => setMode("demo")}>DEMO</button></> : null}</>
              ) : (
                <>Demo mode is not enabled on this server.</>
              )}
            </div>
          ) : snapshot.error ? (
            <div className="border-b border-hairline bg-deny-bg px-4 py-2 text-xs text-deny">{snapshot.error}</div>
          ) : null}
          <Outlet />
        </main>
      </div>
      <SystemTicker
        items={[
          <span key="id">identity {sys ? (sys.identity_mode === "delegation" ? "verified" : "asserted") : "—"}</span>,
          <span key="ln">lineage {sys ? "recorded" : "—"}</span>,
          <span key="au">audit {sys?.audit_head ? "chained · " + sys.audit_head.slice(0, 10) : "—"}</span>,
          <span key="pv">policy {sys?.policy_version ?? "—"}</span>,
          <span key="st">store {sys?.authority_store ?? "—"}</span>,
          <span key="ag">{sys ? `${pad(sys.agents)} agents · ${pad(sys.tasks)} tasks · ${pad(sys.pending_approvals)} pending` : ""}</span>,
          <span key="up" className="ml-auto">{snapshot.updatedAt ? `updated ${new Date(snapshot.updatedAt).toISOString().slice(11, 19)}Z` : ""}</span>,
        ]}
      />

      <Dialog open={connectOpen} onOpenChange={setConnectOpen}>
        <DialogContent title="Connect operator access" description="The operator token (ADMIN_TOKEN) reads every tenant and can issue, narrow, or revoke authority. It stays in this tab's memory.">
          <form
            className="space-y-3"
            onSubmit={(e) => {
              e.preventDefault();
              setAdminToken(token.trim());
              setToken("");
              setMode("live");
              setConnectOpen(false);
            }}
          >
            <label className="block">
              <span className="eyebrow">X-Admin-Token</span>
              <Input type="password" autoComplete="off" value={token} onChange={(e) => setToken(e.target.value)} placeholder="operator token" className="mt-1" />
            </label>
            <div className="flex justify-between">
              <Button type="button" variant="ghost" onClick={() => { setAdminToken(""); setConnectOpen(false); }}>Disconnect</Button>
              <Button type="submit" variant="default">Connect</Button>
            </div>
          </form>
        </DialogContent>
      </Dialog>

      <Dialog open={cmdOpen} onOpenChange={setCmdOpen}>
        <DialogContent title="Go to" description="Jump to a screen, an agent, or a task.">
          <Command label="Command menu" className="text-sm">
            <Command.Input autoFocus placeholder="Type to search…" className="mb-2 h-8 w-full rounded border border-hairline-strong bg-paper px-2 text-sm focus:border-ink focus:outline-none" />
            <Command.List className="max-h-72 overflow-auto">
              <Command.Empty className="px-2 py-3 text-xs text-ink-2">Nothing matches.</Command.Empty>
              <Command.Group heading={<div className="eyebrow px-2 pt-2">Screens</div>}>
                {NAV.flatMap((g) => g.items).map((it) => (
                  <Command.Item key={it.to} value={it.label} onSelect={() => { navigate(it.to); setCmdOpen(false); }} className="cursor-pointer rounded px-2 py-1.5 aria-selected:bg-paper-sunk">
                    {it.label}
                  </Command.Item>
                ))}
              </Command.Group>
              <Command.Group heading={<div className="eyebrow px-2 pt-2">Agents</div>}>
                {snapshot.agents.map((a) => (
                  <Command.Item key={a.id} value={`agent ${a.id}`} onSelect={() => { navigate(`/agents?select=${encodeURIComponent(a.id)}`); setCmdOpen(false); }} className="cursor-pointer rounded px-2 py-1.5 font-mono text-xs aria-selected:bg-paper-sunk">
                    {a.id} <span className="text-ink-2">· {a.current_task ?? "idle"}</span>
                  </Command.Item>
                ))}
              </Command.Group>
              <Command.Group heading={<div className="eyebrow px-2 pt-2">Tasks</div>}>
                {snapshot.tasks.map((t) => (
                  <Command.Item key={t.id} value={`task ${t.id}`} onSelect={() => { navigate(`/tasks?select=${encodeURIComponent(t.id)}`); setCmdOpen(false); }} className="cursor-pointer rounded px-2 py-1.5 font-mono text-xs aria-selected:bg-paper-sunk">
                    {t.id}
                  </Command.Item>
                ))}
              </Command.Group>
            </Command.List>
          </Command>
        </DialogContent>
      </Dialog>
    </div>
  );
}
