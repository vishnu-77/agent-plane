import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import {
  Api,
  ApiError,
  setDemoToken,
  type ApprovalRequest,
  type Agent,
  type AuthState,
  type DecisionSummary,
  type Me,
  type Mode,
  type Project,
  type Source,
  type SystemState,
} from "./api";

const DEMO_PROJECT = "prj_demo";
const LAST_PROJECT = "agentplane.project";

export interface Feed {
  system: SystemState | null;
  decisions: DecisionSummary[];
  agents: Agent[];
  approvals: ApprovalRequest[];
  error: string | null;
  loading: boolean;
  updatedAt: number | null;
}

interface Store {
  ready: boolean;
  authState: AuthState | null;
  me: Me | null;
  signedIn: boolean;
  source: Source;
  setSource: (s: Source) => void;
  project: Project | null;
  projects: Project[];
  selectProject: (id: string) => void;
  refreshAccount: () => Promise<Me | null>;
  setMode: (mode: Mode) => Promise<void>;
  feed: Feed;
  refresh: () => Promise<void>;
  paused: boolean;
  setPaused: (p: boolean) => void;
  signOut: () => Promise<void>;
}

const Ctx = createContext<Store | null>(null);
const EMPTY: Feed = { system: null, decisions: [], agents: [], approvals: [], error: null, loading: false, updatedAt: null };

const DEMO_PROJECT_RECORD: Project = {
  id: DEMO_PROJECT, workspace_id: "wsp_demo", name: "demo-project", slug: "demo", mode: "enforce",
  demo: true, collection: {}, created_at: "", keys: 0, integrations: 0, connected: 1, rules: 0,
};

export function StoreProvider({ children }: { children: ReactNode }) {
  const [ready, setReady] = useState(false);
  const [authState, setAuthState] = useState<AuthState | null>(null);
  const [me, setMe] = useState<Me | null>(null);
  const [source, setSourceState] = useState<Source>("live");
  const [projectId, setProjectId] = useState<string | null>(() => localStorage.getItem(LAST_PROJECT));
  const [feed, setFeed] = useState<Feed>(EMPTY);
  const [paused, setPaused] = useState(false);
  const generation = useRef(0);

  const refreshAccount = useCallback(async () => {
    try {
      const next = await Api.me();
      setMe(next);
      setProjectId((current) => {
        const known = next.projects.some((p) => p.id === current);
        return known ? current : (next.projects[0]?.id ?? null);
      });
      return next;
    } catch {
      setMe(null);
      return null;
    }
  }, []);

  useEffect(() => {
    (async () => {
      try {
        const state = await Api.authState();
        setAuthState(state);
        if (state.demo_token) setDemoToken(state.demo_token);
      } catch {
        setAuthState(null);
      }
      await refreshAccount();
      setReady(true);
    })();
  }, [refreshAccount]);

  const projects = me?.projects ?? [];
  const project = source === "demo"
    ? DEMO_PROJECT_RECORD
    : projects.find((p) => p.id === projectId) ?? projects[0] ?? null;

  const selectProject = useCallback((id: string) => {
    setProjectId(id);
    localStorage.setItem(LAST_PROJECT, id);
    setFeed(EMPTY);
  }, []);

  const setSource = useCallback((next: Source) => {
    setSourceState(next);
    setFeed(EMPTY);
  }, []);

  const refresh = useCallback(async () => {
    if (!project) {
      setFeed((f) => ({ ...f, loading: false }));
      return;
    }
    const gen = ++generation.current;
    setFeed((f) => ({ ...f, loading: true }));
    const results = await Promise.allSettled([
      Api.system(project.id, source),
      Api.decisions(project.id, 120, source),
      Api.agents(project.id, source),
      Api.approvals(project.id, source),
    ]);
    if (gen !== generation.current) return;
    const [system, decisions, agents, approvals] = results;
    const failure = results.find((r) => r.status === "rejected") as PromiseRejectedResult | undefined;
    setFeed({
      system: system.status === "fulfilled" ? system.value : null,
      decisions: decisions.status === "fulfilled" ? decisions.value.decisions : [],
      agents: agents.status === "fulfilled" ? agents.value.agents : [],
      approvals: approvals.status === "fulfilled" ? approvals.value.approvals : [],
      error: system.status === "rejected" ? (failure?.reason as ApiError)?.message ?? "Cannot reach agent-plane" : null,
      loading: false,
      updatedAt: Date.now(),
    });
  }, [project, source]);

  useEffect(() => {
    if (ready) void refresh();
  }, [ready, refresh]);

  useEffect(() => {
    const timer = setInterval(() => {
      if (!paused && !document.hidden) void refresh();
    }, 4000);
    return () => clearInterval(timer);
  }, [refresh, paused]);

  const setMode = useCallback(async (mode: Mode) => {
    if (!project || project.demo) return;
    await Api.updateProject(project.id, { mode });
    await refreshAccount();
    await refresh();
  }, [project, refreshAccount, refresh]);

  const signOut = useCallback(async () => {
    await Api.logout();
    setMe(null);
    setFeed(EMPTY);
  }, []);

  const value = useMemo<Store>(() => ({
    ready, authState, me, signedIn: !!me, source, setSource, project, projects, selectProject,
    refreshAccount, setMode, feed, refresh, paused, setPaused, signOut,
  }), [ready, authState, me, source, setSource, project, projects, selectProject, refreshAccount,
      setMode, feed, refresh, paused, signOut]);

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useStore(): Store {
  const store = useContext(Ctx);
  if (!store) throw new Error("StoreProvider missing");
  return store;
}
