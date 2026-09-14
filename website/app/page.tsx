import { Navbar } from "@/components/Navbar";
import { GITHUB_URL } from "@/lib/site";

function Proof() {
  return (
    <div className="overflow-hidden rounded-md border border-line bg-panel shadow-[0_16px_40px_-24px_rgba(17,17,15,0.35)]">
      <div className="flex flex-wrap items-center gap-2 border-b border-line px-5 py-3.5 text-sm text-muted">
        <b className="text-ink">Capability ≠ Authority.</b> Same verb, three consequences — the credential
        can&apos;t tell them apart.
      </div>
      <pre className="overflow-x-auto bg-term px-5 py-4 font-mono text-[13px] leading-7 text-term-text">
        <span className="text-term-dim"># same agent, same token, one task authority</span>
        {"\n"}deployment.restart development/search{"   "}
        <span className="text-term-dim">low impact, reversible</span>
        {"\n"}deployment.restart staging/checkout{"    "}
        <span className="text-term-dim">medium impact, no customers</span>
        {"\n"}deployment.restart production/payments{" "}
        <span className="text-term-dim">critical, customer-facing, irreversible</span>
      </pre>
    </div>
  );
}

function Section({
  id,
  className = "",
  children,
}: {
  id?: string;
  className?: string;
  children: React.ReactNode;
}) {
  return (
    <section id={id} className={`border-t border-line py-11 ${className}`}>
      <div className="mx-auto max-w-5xl px-6">{children}</div>
    </section>
  );
}

// Hairline-grid-seam card pattern: a shared 1px gap (bg-line) between panel
// cells stands in for individual card borders/shadows - no drop shadows,
// no per-card radius, matching the product's flat, borders-only language.
const cardGridCls = "grid gap-px overflow-hidden rounded-md border border-line bg-line";
const cardCls = "bg-panel p-6";
const tag = "mb-3 inline-block rounded bg-muted-bg px-2 py-0.5 font-mono text-[11.5px] text-accent";

export default function Home() {
  return (
    <>
      <Navbar />
      <main id="top">
        {/* HERO */}
        <div className="mx-auto max-w-5xl px-6 pb-10 pt-16">
          <div className="view-builder">
            <p className="mb-4 text-xs font-semibold uppercase tracking-[0.14em] text-accent">
              Runtime authorization for AI agents
            </p>
            <h1 className="mb-5 text-4xl font-semibold leading-[1.05] tracking-tight text-ink sm:text-5xl">
              Governed agents in <span className="bg-muted-bg px-1">one command.</span>
            </h1>
            <p className="mb-7 max-w-[60ch] text-lg text-muted">
              Your agent&apos;s credentials say what it <em>can</em> do. agent-plane controls what it&apos;s{" "}
              <em>allowed</em> to do for the task in front of it — allow, deny, or ask a human — and blocks
              nothing until you say so.
            </p>
            <div className="flex flex-wrap items-center gap-3">
              <a
                href="#connect"
                className="rounded-md bg-accent px-4.5 py-2.5 text-sm font-semibold text-bg"
              >
                Connect an agent →
              </a>
              <a
                href="#integrations"
                className="rounded-md border border-line bg-panel px-4.5 py-2.5 text-sm font-semibold text-ink"
              >
                See integrations
              </a>
              <span className="text-sm text-muted">Python 3.11+ · Docker · pip install agent-plane</span>
            </div>
          </div>

          <div className="view-research">
            <p className="mb-4 text-xs font-semibold uppercase tracking-[0.14em] text-accent">
              A per-task authorization plane
            </p>
            <h1 className="mb-5 text-4xl font-semibold leading-[1.05] tracking-tight text-ink sm:text-5xl">
              Authority you can <span className="bg-muted-bg px-1">reason about.</span>
            </h1>
            <p className="mb-7 max-w-[60ch] text-lg text-muted">
              Capability is the ceiling; authority is a narrower, expiring, per-task floor evaluated at action
              time. Attenuating delegation, live revocation, consequence-aware decisions, and a signed audit
              chain — with enforcement claimed only where it is real.
            </p>
            <div className="flex flex-wrap items-center gap-3">
              <a
                href="#model"
                className="rounded-md bg-accent px-4.5 py-2.5 text-sm font-semibold text-bg"
              >
                Read the model →
              </a>
              <a
                href="#honest"
                className="rounded-md border border-line bg-panel px-4.5 py-2.5 text-sm font-semibold text-ink"
              >
                What binds vs. advisory
              </a>
              <span className="text-sm text-muted">MIT · deterministic · signed audit</span>
            </div>
          </div>

          <div className="mt-8">
            <Proof />
          </div>
        </div>

        {/* ---------- BUILDER ---------- */}
        <div className="view-builder">
          <Section id="connect">
            <h2 className="mb-2.5 text-2xl font-semibold tracking-tight text-ink sm:text-3xl">
              Connect in one command
            </h2>
            <p className="mb-6 max-w-[64ch] text-muted">
              No JWT to mint, no authority YAML to write, no policy bundle to load. New projects start in{" "}
              <b>Observe</b> — it watches and explains, and blocks nothing until you decide.
            </p>
            <pre className="overflow-x-auto rounded-md border border-line bg-panel p-5 font-mono text-[13.5px] leading-relaxed">
              <span className="text-muted"># install and run</span>
              {"\n"}pip install agent-plane{"\n"}agentplane serve{"\n\n"}
              <span className="text-muted"># connect your coding agent — the console shows you this line</span>
              {"\n"}agentplane connect claude --key ap_live_… <span className="font-semibold text-ink">--scope project</span>
            </pre>
          </Section>

          <Section>
            <h2 className="mb-2.5 text-2xl font-semibold tracking-tight text-ink sm:text-3xl">
              A rule is three lists
            </h2>
            <p className="mb-6 max-w-[64ch] text-muted">
              Write them in the console, over the API, or in version control next to the code they govern.
            </p>
            <pre className="overflow-x-auto rounded-md border border-line bg-panel p-5 font-mono text-[13.5px] leading-relaxed">
              ALLOW{"       "}filesystem.read{"   "}tests.execute{"\n"}ASK FIRST{"   "}filesystem.write{"  "}
              git.push{"\n"}NEVER{"       "}repository.delete{"   "}
              <span className="text-muted"># absolute — no lease grants it back</span>
            </pre>
            <div className={`mt-5 ${cardGridCls} sm:grid-cols-3`}>
              <div className={cardCls}>
                <span className={tag}>observe</span>
                <h3 className="mb-1.5 text-base font-semibold text-ink">Watch first</h3>
                <p className="text-sm text-muted">Every action recorded and explained. Nothing blocked.</p>
              </div>
              <div className={cardCls}>
                <span className={tag}>govern</span>
                <h3 className="mb-1.5 text-base font-semibold text-ink">Decide + flag</h3>
                <p className="text-sm text-muted">Violations decided and surfaced. Execution stays the caller&apos;s.</p>
              </div>
              <div className={cardCls}>
                <span className={tag}>enforce</span>
                <h3 className="mb-1.5 text-base font-semibold text-ink">Bind</h3>
                <p className="text-sm text-muted">The decision binds, wherever the integration can enforce it.</p>
              </div>
            </div>
          </Section>

          <Section id="integrations">
            <h2 className="mb-2.5 text-2xl font-semibold tracking-tight text-ink sm:text-3xl">
              One brain, several enforcement edges
            </h2>
            <p className="mb-6 max-w-[64ch] text-muted">
              Enforcement is only ever claimed where it&apos;s real — every connector says what it can see and
              whether it can block.
            </p>
            <div className="overflow-x-auto">
              <table className="w-full border border-line text-sm">
                <thead>
                  <tr className="text-left text-xs uppercase tracking-wide text-muted">
                    <th className="border-b border-line px-3.5 py-2.5">Integration</th>
                    <th className="border-b border-line px-3.5 py-2.5">Sees</th>
                    <th className="border-b border-line px-3.5 py-2.5">Can block</th>
                  </tr>
                </thead>
                <tbody className="text-text">
                  {[
                    ["Claude Code / Codex", "every action", "most actions"],
                    ["MCP server", "every action", "yes"],
                    ["API / model gateway", "every action", "yes"],
                    ["Cursor / OpenCode", "most actions", "no"],
                    ["LangGraph · SDK · your app", "what your code reports", "your code decides"],
                  ].map((r) => (
                    <tr key={r[0]}>
                      <td className="border-b border-line px-3.5 py-2.5">{r[0]}</td>
                      <td className="border-b border-line px-3.5 py-2.5">{r[1]}</td>
                      <td className="border-b border-line px-3.5 py-2.5">{r[2]}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Section>
        </div>

        {/* ---------- RESEARCH ---------- */}
        <div className="view-research">
          <Section id="model">
            <h2 className="mb-2.5 text-2xl font-semibold tracking-tight text-ink sm:text-3xl">The model</h2>
            <p className="mb-6 max-w-[64ch] text-muted">
              A credential defines the ceiling of what an identity could ever do. agent-plane imposes a
              narrower floor beneath it, evaluated at the moment of the action.
            </p>
            <pre className="overflow-x-auto rounded-md border border-line bg-panel p-5 font-mono text-[13.5px] leading-relaxed">
              Executable Authority ={"\n"}
              {"  "}Identity ∩ Task Authority ∩ Delegated Authority ∩ Resource Scope{"\n"}
              {"           "}∩ Policy ∩ Runtime Constraints ∩{" "}
              <span className="font-semibold text-ink">Permitted Consequence</span>
              {"\n\n"}Outcomes: ALLOW · DENY · APPROVAL · QUARANTINE · SIMULATE
            </pre>
            <div className={`mt-5 ${cardGridCls} sm:grid-cols-2`}>
              <div className={cardCls}>
                <span className={tag}>AuthorityLease</span>
                <h3 className="mb-1.5 text-base font-semibold text-ink">Task-scoped grant</h3>
                <p className="text-sm text-muted">
                  Actions, resources, protected carve-outs, NEVER rules, use budgets, approvals, expiry,
                  permitted consequence.
                </p>
              </div>
              <div className={cardCls}>
                <span className={tag}>Child ⊆ Parent</span>
                <h3 className="mb-1.5 text-base font-semibold text-ink">Attenuating delegation</h3>
                <p className="text-sm text-muted">
                  A holder mints child leases that can only narrow. Any widened field is refused, not silently
                  capped.
                </p>
              </div>
              <div className={cardCls}>
                <span className={tag}>consequence</span>
                <h3 className="mb-1.5 text-base font-semibold text-ink">Structure, not a score</h3>
                <p className="text-sm text-muted">
                  Impact, reversibility, blast radius, environment, customer-facing — modelled explicitly,
                  never collapsed into one number.
                </p>
              </div>
              <div className={cardCls}>
                <span className={tag}>revocation</span>
                <h3 className="mb-1.5 text-base font-semibold text-ink">Live, on every use</h3>
                <p className="text-sm text-muted">
                  Authority revoked or narrowed independently of the long-lived credential, checked at each
                  evaluation.
                </p>
              </div>
            </div>
          </Section>

          <Section id="decision">
            <h2 className="mb-2.5 text-2xl font-semibold tracking-tight text-ink sm:text-3xl">
              The decision, one identity
            </h2>
            <p className="mb-6 max-w-[64ch] text-muted">
              A single lease — delete branches in <code className="font-mono">acme/app</code>,{" "}
              <code className="font-mono">main</code> protected, repo-delete NEVER, two uses — produces a
              different verdict for a different reason each time.
            </p>
            <div className="overflow-x-auto">
              <table className="w-full border border-line text-sm">
                <thead>
                  <tr className="text-left text-xs uppercase tracking-wide text-muted">
                    <th className="border-b border-line px-3.5 py-2.5">Request</th>
                    <th className="border-b border-line px-3.5 py-2.5">Decision</th>
                    <th className="border-b border-line px-3.5 py-2.5">Reason</th>
                  </tr>
                </thead>
                <tbody className="text-text">
                  {[
                    ["action before any lease", "DENY", "NO_ACTIVE_LEASE"],
                    ["in-scope branch delete", "ALLOW", "ACTION_WITHIN_TASK_AUTHORITY"],
                    ["delete protected main", "DENY", "RESOURCE_PROTECTED"],
                    ["delete the repository", "DENY", "ACTION_REFUSED_BY_RULE"],
                    ["act outside acme/app", "DENY", "RESOURCE_OUTSIDE_DELEGATED_SCOPE"],
                    ["3rd delete (budget 2)", "DENY", "ACTION_LIMIT_EXCEEDED"],
                    ["after revocation", "DENY", "LEASE_REVOKED"],
                  ].map((r) => (
                    <tr key={r[2]}>
                      <td className="border-b border-line px-3.5 py-2.5">{r[0]}</td>
                      <td className="border-b border-line px-3.5 py-2.5">
                        <span className={r[1] === "ALLOW" ? "font-semibold text-ok" : "font-semibold text-deny"}>
                          {r[1]}
                        </span>
                      </td>
                      <td className="border-b border-line px-3.5 py-2.5 font-mono text-[12.5px]">{r[2]}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Section>

          <Section id="honest">
            <h2 className="mb-2.5 text-2xl font-semibold tracking-tight text-ink sm:text-3xl">
              What binds, and what is advisory
            </h2>
            <p className="mb-6 max-w-[64ch] text-muted">
              The product never implies it stopped something it could not stop.
            </p>
            <div className="rounded-md border border-line-strong bg-muted-bg p-5">
              <h3 className="mb-2 text-sm font-semibold text-accent">Read before relying on it</h3>
              <ul className="list-disc space-y-1.5 pl-5 text-sm text-muted">
                <li>
                  <b className="text-ink">/v1/authorize is a decision, not a sandbox.</b> Binding enforcement
                  happens where agent-plane holds the credential (MCP gateway, tool broker, model proxy) or
                  where a hook obeys its verdict.
                </li>
                <li>
                  <b className="text-ink">impact is caller-declared.</b> The consequence ceiling gates a value
                  the caller supplies — it stops an honest agent, not a lying one.
                </li>
                <li>
                  <b className="text-ink">Tenant isolation is real under signed delegation,</b> not the default
                  token-claims identity mode.
                </li>
              </ul>
            </div>
          </Section>
        </div>

        {/* ---------- SHARED ---------- */}
        <Section>
          <h2 className="mb-2.5 text-2xl font-semibold tracking-tight text-ink sm:text-3xl">
            What agent-plane is not
          </h2>
          <p className="mb-6 max-w-[64ch] text-muted">
            Not an AI gateway, model router, MCP proxy, prompt-injection scanner, content guardrail,
            agent-inventory product, IAM replacement, API gateway, or risk-scoring dashboard. It integrates
            with many of those.
          </p>
          <div className="overflow-x-auto">
            <table className="w-full border border-line text-sm">
              <tbody className="text-text">
                {[
                  ["IAM / RBAC", "who may access this resource?"],
                  ["MCP authorization", "which tool can be called?"],
                  ["Guardrails", "is this model behaviour unsafe?"],
                  ["agent-plane", "why does this agent have this authority, for this task, and may it cause this consequence?"],
                ].map((r) => (
                  <tr key={r[0]}>
                    <td className="border-b border-line px-3.5 py-2.5 font-semibold text-ink">{r[0]}</td>
                    <td className="border-b border-line px-3.5 py-2.5">{r[1]}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Section>
      </main>

      <footer className="border-t border-line py-10">
        <div className="mx-auto max-w-5xl px-6 text-sm text-muted">
          <p className="view-builder mb-1">
            You&apos;re reading the <b className="text-ink">Builder</b> view — install, connect, ship. Switch to{" "}
            <b className="text-ink">Researcher</b> for the model and guarantees.
          </p>
          <p className="view-research mb-1">
            You&apos;re reading the <b className="text-ink">Researcher</b> view — the model, delegation,
            consequence, honesty. Switch to <b className="text-ink">Builder</b> for install and integrations.
          </p>
          <p>
            MIT · Python 3.11+ · deterministic decisions · hash-chained, HMAC-signed audit ·{" "}
            <a href={GITHUB_URL} className="text-accent underline-offset-4 hover:underline">
              GitHub
            </a>
          </p>
        </div>
      </footer>
    </>
  );
}
