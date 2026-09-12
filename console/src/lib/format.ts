import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

// The product speaks in three words, whatever the engine calls them.
export const OUTCOME_LABEL: Record<string, string> = {
  allow: "ALLOWED",
  deny: "BLOCKED",
  approval_required: "REVIEW",
  quarantine: "HELD",
  simulate: "OBSERVED",
};

export function outcomeLabel(outcome: string | null | undefined, wouldBe?: string | null): string {
  if (!outcome) return "—";
  if (outcome === "simulate" && wouldBe) return wouldBe === "deny" ? "WOULD BLOCK" : "WOULD REVIEW";
  return OUTCOME_LABEL[outcome] ?? outcome.toUpperCase();
}

export type Tone = "allow" | "deny" | "review" | "hold" | "neutral";

/** Shape shared by the activity feed row and the trace's decision block. */
export interface Verdictish {
  outcome?: string | null;
  would_be?: string | null;
  reason?: string | null;
  enforced?: boolean;
}

/**
 * True when nothing the project configured produced this result.
 *
 * A project with no rules denies by default, so Observe and Govern compute
 * "would be denied" for every action. That is true of the project, not of the
 * action: no rule refused it, because no rule exists. Saying WOULD BLOCK on
 * each row would claim a judgement nobody made, so an unruled result reads as
 * OBSERVED and the warning is stated once, for the project.
 *
 * Enforce is different: there the denial is real and binding, so it stands.
 */
export function unruled(d: Verdictish): boolean {
  return d.enforced === false && d.reason === "NO_ACTIVE_LEASE";
}

export function verdictLabel(d: Verdictish): string {
  return unruled(d) ? "OBSERVED" : outcomeLabel(d.outcome, d.would_be);
}

export function verdictTone(d: Verdictish): Tone {
  return unruled(d) ? "neutral" : outcomeTone(d.outcome, d.would_be);
}

export function outcomeTone(outcome: string | null | undefined, wouldBe?: string | null): Tone {
  switch (outcome) {
    case "allow":
      return "allow";
    case "deny":
      return "deny";
    case "approval_required":
      return "review";
    case "quarantine":
      return "hold";
    case "simulate":
      return wouldBe === "deny" ? "deny" : wouldBe ? "review" : "neutral";
    default:
      return "neutral";
  }
}

const REASONS: Record<string, string> = {
  ACTION_WITHIN_TASK_AUTHORITY: "within the task's authority",
  ACTION_REQUIRES_APPROVAL: "a human needs to approve this",
  ACTION_APPROVED: "a human approved it",
  ACTION_REFUSED_BY_RULE: "a rule never allows this",
  RESOURCE_OUTSIDE_DELEGATED_SCOPE: "outside what this task may touch",
  RESOURCE_PROTECTED: "this resource is protected",
  ACTION_NOT_AUTHORIZED: "this action was never granted",
  ACTION_LIMIT_EXCEEDED: "use limit reached",
  ACTION_IMPACT_EXCEEDS_LEASE: "more impact than the task allows",
  CONSEQUENCE_OUTSIDE_TASK_BOUNDARY: "the consequence exceeds the task",
  NO_ACTIVE_LEASE: "no authority for this task yet",
  LEASE_EXPIRED: "the authority expired",
  LEASE_REVOKED: "the authority was revoked",
  AGENT_QUARANTINED: "the agent is held",
  ACTION_OUTSIDE_CAPABILITY_MANIFEST: "outside the agent's capabilities",
  APPROVAL_PENDING: "waiting for a human",
  APPROVAL_REJECTED: "a human rejected it",
  APPROVAL_EXPIRED: "the approval expired",
  APPROVAL_ALREADY_USED: "that approval was already used",
  APPROVAL_MISMATCH: "the approval was for something else",
  APPROVAL_NOT_FOUND: "no matching approval",
};

export function reasonText(reason: string | null | undefined): string {
  if (!reason) return "";
  return REASONS[reason] ?? reason.toLowerCase().replace(/_/g, " ");
}

export const MODE_COPY: Record<string, { label: string; blurb: string }> = {
  observe: { label: "Observe", blurb: "See activity and authority without blocking anything." },
  govern: { label: "Govern", blurb: "Flag authority violations without blocking." },
  enforce: { label: "Enforce", blurb: "Block actions that violate authority." },
};

export function pad(n: number, width = 2): string {
  return String(n).padStart(width, "0");
}

function asDate(iso: string | null | undefined): Date | null {
  if (!iso) return null;
  const d = new Date(/(?:Z|[+-]\d\d:\d\d)$/i.test(iso) ? iso : `${iso}Z`);
  return Number.isNaN(d.getTime()) ? null : d;
}

export function clock(iso: string | null | undefined): string {
  const d = asDate(iso);
  return d ? d.toISOString().slice(11, 16) : "--:--";
}

export function fullDate(iso: string | null | undefined): string {
  const d = asDate(iso);
  return d ? `${d.toISOString().replace("T", " ").slice(0, 19)} UTC` : "—";
}

export function ago(iso: string | null | undefined): string {
  const d = asDate(iso);
  if (!d) return "never";
  const s = Math.max(0, Math.round((Date.now() - d.getTime()) / 1000));
  if (s < 5) return "just now";
  if (s < 60) return `${s} seconds ago`;
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
}

export function shortDate(iso: string | null | undefined): string {
  const d = asDate(iso);
  if (!d) return "—";
  return d.toLocaleDateString(undefined, { day: "numeric", month: "short" });
}

/** "filesystem.write" -> "Modify the workspace" when we know a label, else the id. */
export function actionLabel(action: string, labels: Record<string, string> = {}): string {
  return labels[action] ?? action;
}

export function capabilityText(observation: string, enforcement: string): string {
  const obs = { full: "Sees every action", partial: "Sees most actions",
    application_defined: "Sees what your code reports" }[observation] ?? observation;
  const enf = { full: "can block", partial: "can block most actions",
    advisory: "cannot block" }[enforcement] ?? enforcement;
  return `${obs} · ${enf}`;
}
