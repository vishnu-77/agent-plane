import { Link } from "react-router-dom";
import { useStore } from "@/lib/store";
import { Button } from "@/components/ui";

export function PublicBrand() {
  return <Link to="/" aria-label="agent-plane home" className="inline-flex items-center gap-2.5">
    <img src="/brand/mark.svg" alt="" width={28} height={28} />
    <span className="dot text-sm font-semibold tracking-[0.2em]">AGENT-PLANE</span>
  </Link>;
}

export function HomePage() {
  const { authState, setSource } = useStore();
  const signup = authState?.signup_open && authState?.password_login !== false;
  return <div className="min-h-screen">
    <header className="border-b border-hairline">
      <div className="mx-auto flex max-w-6xl items-center justify-between gap-4 px-5 py-5 sm:px-8">
        <PublicBrand />
        <Link to="/login" className="text-sm underline underline-offset-4">Sign in</Link>
      </div>
    </header>
    <main className="mx-auto max-w-6xl px-5 py-14 sm:px-8 sm:py-24">
      <div className="grid items-center gap-12 lg:grid-cols-[1.15fr_1fr] lg:gap-20">
        <section>
          <p className="eyebrow">Runtime authority for AI agents</p>
          <h1 className="mt-5 max-w-xl text-4xl font-medium leading-tight tracking-tight sm:text-5xl">Every action.<br />Explicit authority.</h1>
          <p className="mt-6 max-w-md text-base leading-relaxed text-ink-2">See what your agents are doing. Define what each task permits. Inspect the decision before an action reaches your systems.</p>
          <div className="mt-8 flex flex-wrap items-center gap-4">
            <Link to={signup ? "/signup" : "/login"} className="inline-flex min-h-10 items-center justify-center bg-ink px-5 py-3 text-sm text-paper hover:opacity-90">{signup ? "Create an account" : "Open your workspace"}</Link>
            {authState?.demo_available ? <Button onClick={() => setSource("demo")}>Explore the demo</Button> : null}
          </div>
          <p className="mt-4 text-xs text-ink-2">Capability is not authority. A credential is only the outer boundary.</p>
          {!authState ? <p role="status" className="mt-6 border border-hairline p-3 text-sm">Cannot reach the account service. <button className="underline" onClick={() => window.location.reload()}>Retry connection</button></p> : null}
        </section>
        <section aria-label="Illustrative runtime decision" className="border border-hairline bg-paper-raised">
          <div className="flex flex-wrap justify-between gap-2 border-b border-hairline px-5 py-4"><span className="eyebrow">One action. One decision.</span><span className="text-2xs text-ink-2">ILLUSTRATIVE EXAMPLE</span></div>
          <dl className="divide-y divide-hairline px-5">
            {[["Agent", "repo-agent"], ["Task", "Clean up stale branches"], ["AuthorityLease", "Branch cleanup · main protected"], ["Proposed action", "branch.delete → main"]].map(([label, value], i) => <div key={label} className="grid grid-cols-[100px_minmax(0,1fr)] gap-3 py-5"><dt className="text-xs text-ink-2">0{i + 1} / {label}</dt><dd className="break-words font-mono text-sm">{value}</dd></div>)}
          </dl>
          <div className="border-t border-hairline bg-deny-bg px-5 py-5"><p className="font-mono text-sm text-deny">DENY · RESOURCE_PROTECTED</p><p className="mt-2 text-sm">This task does not permit deleting the protected branch.</p></div>
        </section>
      </div>
      <section aria-label="How agent-plane works" className="mt-16 grid border-t border-hairline md:mt-24 md:grid-cols-3">
        {[["01 / Observe", "Understand agent activity", "Inspect actions, resources, and recorded authority in one workspace."], ["02 / Define", "Set the task boundary", "Scope permissions to the agent, task, resources, and runtime constraints."], ["03 / Inspect", "Explain each decision", "Trace permissions and audit evidence. Keep authorization separate from execution."]].map(([label, title, body]) => <div key={label} className="py-7 md:pr-8"><p className="eyebrow">{label}</p><h2 className="mt-3 text-base font-medium">{title}</h2><p className="mt-2 max-w-xs text-sm leading-relaxed text-ink-2">{body}</p></div>)}
      </section>
    </main>
    <footer className="border-t border-hairline px-5 py-5"><div className="mx-auto flex max-w-6xl flex-wrap justify-between gap-3 text-xs text-ink-2"><span>Authority answers “Can the agent do this?”</span><a href="/docs" className="underline underline-offset-4">API documentation</a></div></footer>
  </div>;
}
