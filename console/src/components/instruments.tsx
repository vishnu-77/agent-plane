import { motion } from "motion/react";
import type { ReactNode } from "react";
import { cn, pad } from "@/lib/utils";

// ---------------------------------------------------------------- InstrumentPanel
export function InstrumentPanel({
  title,
  eyebrow,
  right,
  children,
  className,
  dense,
}: {
  title?: ReactNode;
  eyebrow?: ReactNode;
  right?: ReactNode;
  children: ReactNode;
  className?: string;
  dense?: boolean;
}) {
  return (
    <section className={cn("panel flex min-w-0 flex-col", className)}>
      {title || eyebrow || right ? (
        <header className="flex items-center justify-between gap-3 border-b border-hairline px-3 py-2">
          <div className="min-w-0">
            {eyebrow ? <div className="eyebrow">{eyebrow}</div> : null}
            {title ? <div className="truncate text-sm font-medium tracking-tight">{title}</div> : null}
          </div>
          {right ? <div className="flex shrink-0 items-center gap-2">{right}</div> : null}
        </header>
      ) : null}
      <div className={cn("min-h-0 flex-1", dense ? "" : "p-3")}>{children}</div>
    </section>
  );
}

// ---------------------------------------------------------------- StateLamp
export function StateLamp({ tone, label, pulse }: { tone: "on" | "off" | "allow" | "deny" | "approval" | "hold"; label?: ReactNode; pulse?: boolean }) {
  const cls = { on: "lamp-on", off: "", allow: "lamp-allow", deny: "lamp-deny", approval: "lamp-approval", hold: "lamp-hold" }[tone];
  return (
    <span className="inline-flex items-center gap-1.5">
      <span className={cn("lamp", cls, pulse && "animate-pulse2")} />
      {label ? <span className="dot text-2xs">{label}</span> : null}
    </span>
  );
}

// ---------------------------------------------------------------- DotMetric
export function DotMetric({ value, label, tone, width = 2 }: { value: number | string; label: string; tone?: "allow" | "deny" | "approval" | "hold"; width?: number }) {
  const shown = typeof value === "number" ? pad(value, width) : value;
  const color = tone === "allow" ? "text-allow" : tone === "deny" ? "text-deny" : tone === "approval" ? "text-approval" : tone === "hold" ? "text-hold" : "text-ink";
  return (
    <div className="flex items-baseline gap-2">
      <motion.span key={String(shown)} initial={{ opacity: 0.4 }} animate={{ opacity: 1 }} className={cn("dot text-[15px] font-medium leading-none", color)}>
        {shown}
      </motion.span>
      <span className="dot text-2xs text-ink-2">{label}</span>
    </div>
  );
}

// ---------------------------------------------------------------- SystemTicker
export function SystemTicker({ items }: { items: Array<ReactNode> }) {
  return (
    <div className="flex flex-wrap items-center gap-x-4 gap-y-1 border-t border-hairline bg-paper px-4 py-1.5 font-mono text-2xs text-ink-2">
      {items.map((it, i) => (
        <span key={i} className="inline-flex items-center gap-1.5">
          {i > 0 ? <span className="text-hairline-strong">│</span> : null}
          {it}
        </span>
      ))}
    </div>
  );
}

// ---------------------------------------------------------------- DemoSwitch
export function DemoSwitch({ mode, onChange, demoAvailable }: { mode: "live" | "demo"; onChange: (m: "live" | "demo") => void; demoAvailable: boolean }) {
  return (
    <div role="group" aria-label="Data source" className="inline-flex items-center rounded border border-hairline-strong bg-paper-raised p-[2px]">
      {(["live", "demo"] as const).map((m) => (
        <button
          key={m}
          type="button"
          disabled={m === "demo" && !demoAvailable}
          onClick={() => onChange(m)}
          aria-pressed={mode === m}
          className={cn(
            "dot rounded-sm px-2.5 py-1 text-2xs transition-colors disabled:opacity-40",
            mode === m ? "bg-ink text-paper" : "text-ink-2 hover:text-ink",
          )}
        >
          {m}
        </button>
      ))}
    </div>
  );
}

export function DemoMarker() {
  return (
    <div className="flex items-center gap-2 border-b border-hairline bg-paper-sunk px-4 py-1 font-mono text-2xs uppercase tracking-[0.16em] text-ink-2">
      <span className="lamp lamp-on animate-pulse2" />
      demo environment · no external side effects · real authority engine, simulated targets
    </div>
  );
}
