import { useState } from "react";
import { Link } from "react-router-dom";
import { useStore } from "@/lib/store";
import { Button } from "@/components/ui";
import { FeedbackDialog } from "@/components/feedback";

export function PublicBrand() {
  return <Link to="/" aria-label="agent-plane home" className="inline-flex items-center gap-2.5">
    <img src="/brand/mark.svg" alt="" width={28} height={28} />
    <span className="dot text-sm font-semibold tracking-[0.2em]">AGENT-PLANE</span>
  </Link>;
}

export function HomePage() {
  const { authState, setSource } = useStore();
  const signup = authState?.signup_open && authState?.password_login !== false;
  const [feedbackOpen, setFeedbackOpen] = useState(false);
  return <div className="min-h-screen">
    <header className="border-b border-hairline">
      <div className="mx-auto flex max-w-6xl items-center justify-between gap-4 px-5 py-5 sm:px-8">
        <PublicBrand />
        <nav aria-label="Account" className="flex items-center gap-4">
          <Link to="/login" className="text-sm underline underline-offset-4">Sign in</Link>
          <Link to="/signup" className="inline-flex min-h-10 items-center justify-center bg-ink px-4 py-2 text-sm text-paper hover:opacity-90">Sign up</Link>
        </nav>
      </div>
    </header>
    <main className="mx-auto max-w-6xl px-5 py-14 sm:px-8 sm:py-24">
      <div className="grid items-center gap-12 lg:grid-cols-[1.15fr_1fr] lg:gap-20">
        <section>
          <p className="eyebrow">Runtime authority for AI agents</p>
          <h1 className="mt-5 max-w-xl text-4xl font-medium leading-tight tracking-tight sm:text-5xl">Every action.<br />Explicit authority.</h1>
          <p className="mt-6 max-w-md text-base leading-relaxed text-ink-2">A control console for your AI agents. Connect your coding agent, see which files and tools it touches, and set rules for what it can do. Review risky actions before they run through an enforced connection.</p>
          <div className="mt-8 flex flex-wrap items-center gap-4">
            <Link to={signup ? "/signup" : "/login"} className="inline-flex min-h-10 items-center justify-center bg-ink px-5 py-3 text-sm text-paper hover:opacity-90">{signup ? "Create an account" : "Open your workspace"}</Link>
            {authState?.demo_available ? <Button onClick={() => setSource("demo")}>Explore the demo</Button> : null}
          </div>
          <p className="mt-4 text-xs text-ink-2">Start by observing activity. Enable enforcement when your connection and rules are ready.</p>
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
        {[["01 / Connect", "Bring your agent", "Create a project, then follow the integration steps for your coding agent or application."], ["02 / Observe", "See what it actually does", "Inspect attempted actions and the files, tools, and services involved before choosing restrictions."], ["03 / Set rules", "Choose allow, ask, or never", "Review permissions, enable enforcement on a supported connection, and inspect why each action was allowed or stopped."]].map(([label, title, body]) => <div key={label} className="py-7 md:pr-8"><p className="eyebrow">{label}</p><h2 className="mt-3 text-base font-medium">{title}</h2><p className="mt-2 max-w-xs text-sm leading-relaxed text-ink-2">{body}</p></div>)}
      </section>
      <section aria-label="Recently shipped" className="mt-16 border-t border-hairline pt-10 md:mt-24">
        <p className="eyebrow">Recently shipped</p>
        <div className="mt-6 grid gap-x-8 gap-y-7 sm:grid-cols-2">
          {[
            ["Causal reachability", "An action's consequence now traces what it makes reachable, not just what it touches directly — a push to main that enables a production deploy is bounded before the deploy runs."],
            ["Task-state composition", "Edits and actions accumulate within a task, so two unremarkable steps that add up to something else are caught together, not evaluated one at a time in isolation."],
            ["Confirmed execution", "A binding deny keyed on what a task already did only fires once that precondition is confirmed to have actually happened — never on intent alone."],
            ["Per-session pause", "Hold one session's actions without quarantining the whole agent, or disconnecting it and losing visibility entirely."],
          ].map(([title, body]) => <div key={title}><h2 className="text-sm font-medium">{title}</h2><p className="mt-1.5 max-w-sm text-sm leading-relaxed text-ink-2">{body}</p></div>)}
        </div>
      </section>
    </main>
    <footer className="border-t border-hairline px-5 py-5"><div className="mx-auto flex max-w-6xl flex-wrap justify-between gap-3 text-xs text-ink-2"><span>Authority answers “Can the agent do this?”</span><span className="flex gap-4"><a href="/docs" className="underline underline-offset-4">API documentation</a><button type="button" onClick={() => setFeedbackOpen(true)} className="underline underline-offset-4">Feedback</button><a href="https://buymeacoffee.com/vishnuprashanth" target="_blank" rel="noopener" className="underline underline-offset-4">Buy me a coffee</a></span></div></footer>
    <FeedbackDialog open={feedbackOpen} onOpenChange={setFeedbackOpen} context="home page" />
  </div>;
}
