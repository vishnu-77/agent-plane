import { useState } from "react";
import { Api } from "@/lib/api";
import { useStore } from "@/lib/store";
import { Button, Input } from "@/components/ui";

/** Sign in, or create the first account on a fresh install. */
export function AuthPage() {
  const { authState, refreshAccount, setSource } = useStore();
  const firstRun = !!authState?.first_run;
  const [mode, setMode] = useState<"signup" | "login">(firstRun ? "signup" : "login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [name, setName] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      if (mode === "signup") await Api.signup({ email, password, name });
      else await Api.login({ email, password });
      await refreshAccount();
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
          <img src="/brand/mark.svg" alt="" width={28} height={28} />
          <span className="dot text-sm font-semibold tracking-[0.2em]">AGENT-PLANE</span>
        </div>

        <h1 className="text-xl font-medium tracking-tight">
          {mode === "signup" ? "Welcome to agent-plane" : "Sign in"}
        </h1>
        <p className="mt-2 text-sm text-ink-2">
          {mode === "signup"
            ? "See what your agents are doing before deciding what they should be allowed to do."
            : "Open your workspace."}
        </p>

        <form className="mt-6 space-y-3" onSubmit={submit}>
          {mode === "signup" ? (
            <label className="block">
              <span className="eyebrow">Name</span>
              <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="Your name" className="mt-1" autoComplete="name" />
            </label>
          ) : null}
          <label className="block">
            <span className="eyebrow">Email</span>
            <Input type="email" required value={email} onChange={(e) => setEmail(e.target.value)}
              className="mt-1" autoComplete="email" autoFocus />
          </label>
          <label className="block">
            <span className="eyebrow">Password</span>
            <Input type="password" required value={password} onChange={(e) => setPassword(e.target.value)}
              className="mt-1" autoComplete={mode === "signup" ? "new-password" : "current-password"} />
            {mode === "signup" ? <span className="mt-1 block text-2xs text-ink-3">At least 10 characters.</span> : null}
          </label>
          {error ? <p className="text-xs text-deny">{error}</p> : null}
          <Button type="submit" variant="default" className="w-full justify-center" disabled={busy}>
            {busy ? "…" : mode === "signup" ? "Create account" : "Sign in"}
          </Button>
        </form>

        <div className="mt-4 flex items-center justify-between text-xs text-ink-2">
          {authState?.signup_open ? (
            <button className="underline" onClick={() => { setMode(mode === "signup" ? "login" : "signup"); setError(null); }}>
              {mode === "signup" ? "I already have an account" : "Create an account"}
            </button>
          ) : <span />}
          {authState?.demo_available ? (
            <button className="underline" onClick={() => setSource("demo")}>See the demo instead</button>
          ) : null}
        </div>

        {firstRun ? (
          <p className="mt-6 border-t border-hairline pt-4 text-xs text-ink-3">
            This is a fresh install, so this first account owns the workspace. Sign-up closes afterwards
            unless you set SIGNUP_MODE=open.
          </p>
        ) : null}
      </div>
    </div>
  );
}

/** Steps 2-4 of onboarding: name a project, pick a mode, connect something. */
export function OnboardingPage() {
  const { refreshAccount, selectProject, setSource, authState } = useStore();
  const [step, setStep] = useState<1 | 2>(1);
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
      location.hash = "#/integrations";
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="flex min-h-screen items-center justify-center px-4 py-10">
      <div className="w-full max-w-md">
        <div className="mb-8 flex items-center gap-2.5">
          <img src="/brand/mark.svg" alt="" width={28} height={28} />
          <span className="dot text-sm font-semibold tracking-[0.2em]">AGENT-PLANE</span>
        </div>

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
        ) : (
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
        )}

        {authState?.demo_available ? (
          <button className="mt-6 text-xs text-ink-2 underline" onClick={() => setSource("demo")}>
            Or look at the demo project first
          </button>
        ) : null}
      </div>
    </div>
  );
}
