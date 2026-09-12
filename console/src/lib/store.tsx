import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import {
  api,
  type AgentSummary,
  type ApprovalRequest,
  type Credentials,
  type DecisionSummary,
  type Mode,
  type Scenario,
  type SystemState,
  type TaskRecord,
  tenantQuery,
} from "./api";

export interface Snapshot {
  system: SystemState | null;
  agents: AgentSummary[];
  tasks: TaskRecord[];
  decisions: DecisionSummary[];
  approvals: ApprovalRequest[];
  error: string | null;
  updatedAt: number | null;
  loading: boolean;
}

interface Store {
  mode: Mode;
  setMode: (m: Mode) => void;
  creds: Credentials;
  setAdminToken: (t: string) => void;
  demoAvailable: boolean;
  scenarios: Scenario[];
  tenant: string | null;
  setTenant: (t: string | null) => void;
  snapshot: Snapshot;
  refresh: () => Promise<void>;
  paused: boolean;
  setPaused: (p: boolean) => void;
  selected: string | null;
  select: (id: string | null) => void;
  connected: boolean;
}

const Ctx = createContext<Store | null>(null);

const EMPTY: Snapshot = { system: null, agents: [], tasks: [], decisions: [], approvals: [], error: null, updatedAt: null, loading: false };

export function StoreProvider({ children }: { children: ReactNode }) {
  const [mode, setModeState] = useState<Mode>(() => (location.hash.includes("demo") ? "demo" : "live"));
  const [creds, setCreds] = useState<Credentials>({ admin: "", demo: "" });
  const [demoAvailable, setDemoAvailable] = useState(false);
  const [scenarios, setScenarios] = useState<Scenario[]>([]);
  const [tenant, setTenant] = useState<string | null>(null);
  const [snapshot, setSnapshot] = useState<Snapshot>(EMPTY);
  const [paused, setPaused] = useState(false);
  const [selected, setSelected] = useState<string | null>(null);
  const generation = useRef(0);

  // Discover the demo token once; the server only hands it out when DEMO_ENABLED.
  useEffect(() => {
    fetch("/demo/scenarios", { cache: "no-store" })
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => {
        if (d && d.token) {
          setDemoAvailable(true);
          setScenarios(d.scenarios ?? []);
          setCreds((c) => ({ ...c, demo: d.token }));
          // No operator token yet: start on the demo so the first screen is alive.
          setModeState((m) => (m === "live" && !creds.admin ? "demo" : m));
        }
      })
      .catch(() => {});
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const connected = mode === "demo" ? !!creds.demo : !!creds.admin;

  const refresh = useCallback(async () => {
    const gen = ++generation.current;
    if (!connected) {
      setSnapshot((s) => ({ ...s, loading: false, error: null }));
      return;
    }
    setSnapshot((s) => ({ ...s, loading: true }));
    const q = tenantQuery(mode, tenant);
    const sep = q ? "&" : "?";
    const results = await Promise.allSettled([
      api<SystemState>(`/v1/system${q}`, { mode, creds }),
      api<{ agents: AgentSummary[] }>(`/v1/agents${q}`, { mode, creds }),
      api<{ tasks: TaskRecord[] }>(`/v1/tasks${q}`, { mode, creds }),
      api<{ decisions: DecisionSummary[] }>(`/v1/decisions${q}${sep}limit=120`, { mode, creds }),
      api<{ approvals: ApprovalRequest[] }>(`/v1/approvals?status=pending&limit=100${mode === "demo" ? "&tenant=demo" : tenant ? `&tenant=${encodeURIComponent(tenant)}` : ""}`, { mode, creds }),
    ]);
    if (gen !== generation.current) return;
    const [sys, ag, tk, dc, ap] = results;
    const firstError = results.find((r) => r.status === "rejected") as PromiseRejectedResult | undefined;
    setSnapshot({
      system: sys.status === "fulfilled" ? sys.value : null,
      agents: ag.status === "fulfilled" ? ag.value.agents : [],
      tasks: tk.status === "fulfilled" ? tk.value.tasks : [],
      decisions: dc.status === "fulfilled" ? dc.value.decisions : [],
      approvals: ap.status === "fulfilled" ? ap.value.approvals : [],
      error: sys.status === "rejected" ? String((firstError?.reason as Error)?.message ?? "unreachable") : null,
      updatedAt: Date.now(),
      loading: false,
    });
  }, [mode, creds, tenant, connected]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  useEffect(() => {
    const t = setInterval(() => {
      if (!paused && !document.hidden) void refresh();
    }, 4000);
    return () => clearInterval(t);
  }, [refresh, paused]);

  const setMode = useCallback((m: Mode) => {
    setModeState(m);
    setSelected(null);
    setSnapshot(EMPTY);
  }, []);

  const value = useMemo<Store>(
    () => ({
      mode,
      setMode,
      creds,
      setAdminToken: (t) => setCreds((c) => ({ ...c, admin: t })),
      demoAvailable,
      scenarios,
      tenant,
      setTenant,
      snapshot,
      refresh,
      paused,
      setPaused,
      selected,
      select: setSelected,
      connected,
    }),
    [mode, setMode, creds, demoAvailable, scenarios, tenant, snapshot, refresh, paused, selected, connected],
  );
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useStore(): Store {
  const s = useContext(Ctx);
  if (!s) throw new Error("StoreProvider missing");
  return s;
}
