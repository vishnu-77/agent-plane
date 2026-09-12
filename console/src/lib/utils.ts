import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

export type Outcome = "allow" | "deny" | "approval_required" | "quarantine" | "simulate" | string;

export const OUTCOME_LABEL: Record<string, string> = {
  allow: "ALLOW",
  deny: "DENY",
  approval_required: "APPROVAL",
  quarantine: "QUARANTINE",
  simulate: "SIMULATE",
};

export function outcomeLabel(o: Outcome | undefined | null): string {
  if (!o) return "—";
  return OUTCOME_LABEL[o] ?? o.toUpperCase();
}

export function outcomeTone(o: Outcome | undefined | null): "allow" | "deny" | "approval" | "hold" | "neutral" {
  switch (o) {
    case "allow":
      return "allow";
    case "deny":
      return "deny";
    case "approval_required":
      return "approval";
    case "quarantine":
    case "simulate":
      return "hold";
    default:
      return "neutral";
  }
}

export function pad(n: number, width = 2): string {
  return String(n).padStart(width, "0");
}

export function shortId(id: string | null | undefined, n = 12): string {
  if (!id) return "—";
  return id.length > n ? id.slice(0, n) + "…" : id;
}

export function timeOf(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(/(?:Z|[+-]\d\d:\d\d)$/i.test(iso) ? iso : iso + "Z");
  if (Number.isNaN(d.getTime())) return "—";
  return d.toISOString().slice(11, 23);
}

export function dateOf(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(/(?:Z|[+-]\d\d:\d\d)$/i.test(iso) ? iso : iso + "Z");
  if (Number.isNaN(d.getTime())) return "—";
  return d.toISOString().replace("T", " ").slice(0, 19) + " UTC";
}

export function ago(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(/(?:Z|[+-]\d\d:\d\d)$/i.test(iso) ? iso : iso + "Z").getTime();
  if (Number.isNaN(d)) return "—";
  const s = Math.max(0, Math.round((Date.now() - d) / 1000));
  if (s < 60) return `${s}s ago`;
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
}

export function reasonText(reason: string | undefined | null): string {
  const map: Record<string, string> = {
    ACTION_WITHIN_TASK_AUTHORITY: "within task authority",
    ACTION_REQUIRES_APPROVAL: "requires human approval",
    ACTION_APPROVED: "approved by a human",
    RESOURCE_OUTSIDE_DELEGATED_SCOPE: "outside delegated scope",
    RESOURCE_PROTECTED: "protected resource",
    ACTION_NOT_AUTHORIZED: "no lineage grants this action",
    ACTION_LIMIT_EXCEEDED: "use limit reached",
    ACTION_IMPACT_EXCEEDS_LEASE: "declared impact exceeds lease",
    CONSEQUENCE_OUTSIDE_TASK_BOUNDARY: "consequence outside task boundary",
    NO_ACTIVE_LEASE: "no authority for this task",
    LEASE_EXPIRED: "authority expired",
    LEASE_REVOKED: "authority revoked",
    AGENT_QUARANTINED: "agent quarantined",
    ACTION_OUTSIDE_CAPABILITY_MANIFEST: "outside capability manifest",
    APPROVAL_PENDING: "approval pending",
    APPROVAL_REJECTED: "approval rejected",
    APPROVAL_EXPIRED: "approval expired",
    APPROVAL_ALREADY_USED: "approval already consumed",
    APPROVAL_MISMATCH: "approval for a different action",
    APPROVAL_NOT_FOUND: "approval not found",
  };
  return reason ? map[reason] ?? reason.toLowerCase().replace(/_/g, " ") : "—";
}
