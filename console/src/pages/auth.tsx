import { useEffect, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { Api, type CatalogEntry, type Integration } from "@/lib/api";
import { useStore } from "@/lib/store";
import { Button, Input } from "@/components/ui";
import { capabilityText } from "@/lib/format";
import { PublicBrand } from "./home";
import { ConnectionWizard } from "./integrations";

/** Sign in, or create the first account on a fresh install. */
/** The callback hands a failure back in the URL rather than a blank screen. */
export function AuthPage({ mode }: { mode: "signup" | "login" }) {
  const { authState, refreshAccount, setSource, sessionEnded, clearSessionNotice } = useStore();
  const navigate = useNavigate();
  const [params] = useSearchParams();
  const firstRun = !!authState?.first_run;
  const passwordDisabled = authState?.password_login === false;
  const signupClosed = mode === "signup" && !authState?.signup_open;
  const formAvailable = !!authState && !passwordDisabled && !signupClosed;
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [name, setName] = useState("");
  const [error, setError] = useState<string | null>(
    params.get("sso_error") ?? (sessionEnded ? "Your session ended. Sign in again." : null));
  const [busy, setBusy] = useState(false);
  // Keep the notice on this form, but allow Back to home after an expired session.
  useEffect(() => clearSessionNotice(), [clearSessionNotice]);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!formAvailable || busy) return;
    setBusy(true);
    setError(null);
    try {
      if (mode === "signup") await Api.signup({ email, password, name });
      else await Api.login({ email, password });
      if (!await refreshAccount()) throw new Error("Signed in, but your workspace could not be loaded. Please try again.");
      setSource("live");
      navigate("/", { replace: true });
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="flex min-h-screen items-center justify-center px-4 py-10">
      <div className="w-full max-w-sm">
        <div className="mb-8 flex items-center gap-2.5">
          <PublicBrand />
        </div>

        <h1 className="text-xl font-medium tracking-tight">
          {mode === "signup" ? "Create your account" : "Sign in"}
        </h1>
        <p className="mt-2 text-sm text-ink-2">
          {mode === "signup"
            ? "See what your agents are doing before deciding what they should be allowed to do."
            : "Open your workspace."}
        </p>

        {authState?.sso_available ? (
          <div className="mt-6">
            <Button variant="default" className="w-full justify-center"
              onClick={() => { setSource("live"); window.location.href = "/v1/auth/oidc/start"; }}>
              Continue with single sign-on
            </Button>
            {formAvailable ? (
              <div className="mt-4 flex items-center gap-3 text-2xs text-ink-3">
                <span className="h-px flex-1 bg-hairline" />OR<span className="h-px flex-1 bg-hairline" />
              </div>
            ) : null}
          </div>
        ) : null}

        {passwordDisabled ? (
          <p className="mt-4 text-xs text-ink-3">
            {authState?.sso_available ? "This workspace uses single sign-on." : "Password sign-in is disabled. Contact your workspace administrator for access."}
          </p>
        ) : null}

        {!authState ? <div role="alert" className="mt-6 border border-hairline p-4 text-sm">Cannot reach the account service. <button className="underline" onClick={() => window.location.reload()}>Retry connection</button></div> : null}
        {signupClosed && authState ? <p role="status" className="mt-6 text-sm">Account creation is closed for this workspace. Sign in with an existing account or contact your administrator.</p> : null}
        {error ? <p role="alert" className="mt-4 text-sm text-deny">{error}</p> : null}
        {formAvailable ? <form className="mt-6 space-y-3" onSubmit={submit}>
          {mode === "signup" ? (
            <label className="block">
              <span className="eyebrow">Name</span>
              <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="Your name" className="mt-1" autoComplete="name" autoFocus />
            </label>
          ) : null}
          <label className="block">
            <span className="eyebrow">Email</span>
            <Input type="email" required value={email} onChange={(e) => setEmail(e.target.value)}
              className="mt-1" autoComplete="email" autoFocus={mode === "login"} />
          </label>
          <label className="block">
            <span className="eyebrow">Password</span>
            <Input type="password" required value={password} onChange={(e) => setPassword(e.target.value)}
              minLength={mode === "signup" ? 10 : undefined}
              className="mt-1" autoComplete={mode === "signup" ? "new-password" : "current-password"} />
            {mode === "signup" ? <span className="mt-1 block text-2xs text-ink-3">At least 10 characters.</span> : null}
          </label>
          <Button type="submit" variant="default" className="w-full justify-center" disabled={busy}>
            {busy ? "…" : mode === "signup" ? "Create account" : "Sign in"}
          </Button>
        </form> : null}

        <div className="mt-4 flex items-center justify-between text-xs text-ink-2">
          {(mode === "signup" || (authState?.signup_open && !passwordDisabled)) ? (
            <Link className="underline" to={mode === "signup" ? "/login" : "/signup"}>
              {mode === "signup" ? "I already have an account" : "Create an account"}
            </Link>
          ) : <span />}
          {authState?.demo_available ? (
            <button className="underline" onClick={() => { setSource("demo"); navigate("/"); }}>See the demo instead</button>
          ) : null}
        </div>

        <Link to="/" className="mt-6 inline-block text-xs text-ink-2 underline">Back to home</Link>
        {firstRun && mode === "signup" && formAvailable ? (
          <p className="mt-6 border-t border-hairline pt-4 text-xs text-ink-3">
            The first account owns this workspace. Additional accounts follow the workspace’s registration settings.
          </p>
        ) : null}
      </div>
    </div>
  );
}

/** Three steps on first signup: name a project, pick a mode, connect an agent. */
export function OnboardingPage() {
  const { refreshAccount, selectProject, project, setSource, authState } = useStore();
  const [step, setStep] = useState<1 | 2 | 3>(1);
  const [name, setName] = useState("personal-coding");
  const [mode, setMode] = useState<"observe" | "govern" | "enforce">("observe");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const create = async () => {
    setBusy(true);
    setError(null);
    try {
      const created = await Api.createProject({ name, mode });
      await refreshAccount();
      selectProject(created.project.id);
      setStep(3);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="flex min-h-screen items-center justify-center px-4 py-10">
      <div className={step === 3 ? "w-full max-w-lg" : "w-full max-w-md"}>
        <div className="mb-8 flex items-center gap-2.5">
          <img src="/brand/mark.svg" alt="" width={28} height={28} />
          <span className="dot text-sm font-semibold tracking-[0.2em]">AGENT-PLANE</span>
        </div>
        <p className="eyebrow mb-1">Step {step} of 3</p>

        {step === 1 ? (
          <>
            <h1 className="text-xl font-medium tracking-tight">Create a project</h1>
            <p className="mt-2 text-sm text-ink-2">
              A project is one place where agents act: its own activity, rules, and keys.
            </p>
            <label className="mt-6 block">
              <span className="eyebrow">Project name</span>
              <Input autoFocus value={name} onChange={(e) => setName(e.target.value)} className="mt-1" />
            </label>
            <Button className="mt-4 w-full justify-center" variant="default" disabled={!name.trim()}
              onClick={() => setStep(2)}>
              Continue
            </Button>
          </>
        ) : step === 2 ? (
          <>
            <h1 className="text-xl font-medium tracking-tight">How should agent-plane start?</h1>
            <div className="mt-5 space-y-2">
              {([
                ["observe", "Observe", "See activity and authority without blocking anything."],
                ["govern", "Govern", "Flag authority violations without blocking."],
                ["enforce", "Enforce", "Block actions that violate authority."],
              ] as const).map(([key, label, blurb]) => (
                <button key={key} type="button" onClick={() => setMode(key)}
                  className={`panel block w-full px-4 py-3 text-left ${mode === key ? "border-ink" : ""}`}>
                  <div className="flex items-center gap-2">
                    <span className={`lamp ${mode === key ? "lamp-on" : ""}`} />
                    <span className="dot text-xs font-semibold">{label}</span>
                  </div>
                  <p className="mt-1 pl-4 text-sm text-ink-2">{blurb}</p>
                </button>
              ))}
            </div>
            <p className="mt-3 text-xs text-ink-3">You can change this later.</p>
            {error ? <p className="mt-2 text-xs text-deny">{error}</p> : null}
            <div className="mt-5 flex gap-2">
              <Button variant="ghost" onClick={() => setStep(1)}>Back</Button>
              <Button variant="default" className="flex-1 justify-center" disabled={busy} onClick={() => void create()}>
                {busy ? "…" : "Create project"}
              </Button>
            </div>
          </>
        ) : (
          <IntegrationStep projectId={project?.id ?? null} onDone={() => { location.hash = "#/"; }} />
        )}

        {step !== 3 && authState?.demo_available ? (
          <button className="mt-6 text-xs text-ink-2 underline" onClick={() => setSource("demo")}>
            Or look at the demo project first
          </button>
        ) : null}
      </div>
    </div>
  );
}

/** Onboarding's step 3: pick an integration kind, get the connect command,
 * see it arrive. The same catalog and wizard the Integrations page uses -
 * this just frames it as "finish setting up" instead of a page you find later. */
function IntegrationStep({ projectId, onDone }: { projectId: string | null; onDone: () => void }) {
  const [catalog, setCatalog] = useState<CatalogEntry[]>([]);
  const [integrations, setIntegrations] = useState<Integration[]>([]);
  const [connect, setConnect] = useState<CatalogEntry | null>(null);

  const load = async () => {
    if (!projectId) return;
    const data = await Api.integrations(projectId);
    setCatalog(data.catalog);
    setIntegrations(data.integrations);
  };
  useEffect(() => { void load(); }, [projectId]); // eslint-disable-line react-hooks/exhaustive-deps

  const connectedKinds = new Set(integrations.filter((i) => i.status === "connected").map((i) => i.kind));
  const anyConnected = connectedKinds.size > 0;

  return (
    <>
      <h1 className="text-xl font-medium tracking-tight">Connect your first agent</h1>
      <p className="mt-2 text-sm text-ink-2">
        Pick what you're running. Each one says plainly what it can observe and whether it can block.
      </p>
      <div className="mt-5 max-h-[360px] space-y-2 overflow-y-auto pr-0.5">
        {catalog.map((entry) => (
          <button key={entry.kind} type="button" onClick={() => setConnect(entry)}
            className={`panel block w-full px-4 py-3 text-left ${connectedKinds.has(entry.kind) ? "border-ink" : ""}`}>
            <div className="flex items-center justify-between gap-2">
              <span className="dot text-xs font-semibold">{entry.label}</span>
              {connectedKinds.has(entry.kind) ? <span className="text-2xs text-allow">connected</span> : null}
            </div>
            <p className="mt-1 text-sm text-ink-2">{entry.summary}</p>
            <p className="mt-0.5 font-mono text-2xs text-ink-3">{capabilityText(entry.observation, entry.enforcement)}</p>
          </button>
        ))}
      </div>
      <div className="mt-5 flex items-center justify-between gap-2">
        <button className="text-xs text-ink-2 underline" onClick={onDone}>
          {anyConnected ? "Skip the rest, take me in" : "Skip for now"}
        </button>
        {anyConnected ? <Button variant="default" onClick={onDone}>Go to activity</Button> : null}
      </div>
      <ConnectionWizard entry={connect} projectId={projectId ?? ""} onClose={() => setConnect(null)}
        onConnected={load} />
    </>
  );
}
