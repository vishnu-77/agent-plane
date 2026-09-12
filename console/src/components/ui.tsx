// Structural primitives (shadcn-style over Radix), restyled for the e-ink system.
import * as DialogPrimitive from "@radix-ui/react-dialog";
import * as TabsPrimitive from "@radix-ui/react-tabs";
import * as TooltipPrimitive from "@radix-ui/react-tooltip";
import { cva, type VariantProps } from "class-variance-authority";
import { X } from "lucide-react";
import { forwardRef, type ButtonHTMLAttributes, type HTMLAttributes, type InputHTMLAttributes, type ReactNode } from "react";
import { cn } from "@/lib/utils";

// ---------------------------------------------------------------- Button
const button = cva(
  "inline-flex items-center gap-1.5 whitespace-nowrap rounded border text-sm font-medium transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ink disabled:pointer-events-none disabled:opacity-40",
  {
    variants: {
      variant: {
        default: "border-ink bg-ink text-paper hover:bg-ink/90",
        outline: "border-hairline-strong bg-paper-raised text-ink hover:bg-paper-sunk",
        ghost: "border-transparent bg-transparent text-ink hover:bg-paper-sunk",
        allow: "border-allow text-allow bg-allow-bg hover:bg-allow-bg/70",
        deny: "border-deny text-deny bg-deny-bg hover:bg-deny-bg/70",
      },
      size: { sm: "h-7 px-2.5 text-xs", md: "h-8 px-3", lg: "h-9 px-4" },
    },
    defaultVariants: { variant: "outline", size: "md" },
  },
);
export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement>, VariantProps<typeof button> {}
export const Button = forwardRef<HTMLButtonElement, ButtonProps>(({ className, variant, size, ...props }, ref) => (
  <button ref={ref} className={cn(button({ variant, size }), className)} {...props} />
));
Button.displayName = "Button";

// ---------------------------------------------------------------- Badge
const badge = cva("inline-flex items-center gap-1 rounded-sm border px-1.5 py-[1px] text-2xs font-medium uppercase tracking-[0.12em]", {
  variants: {
    tone: {
      neutral: "border-hairline-strong text-ink-2 bg-paper-raised",
      ink: "border-ink text-ink bg-paper-raised",
      allow: "border-allow/40 text-allow bg-allow-bg",
      deny: "border-deny/40 text-deny bg-deny-bg",
      approval: "border-approval/40 text-approval bg-approval-bg",
      hold: "border-hold/40 text-hold bg-hold-bg",
    },
  },
  defaultVariants: { tone: "neutral" },
});
export function Badge({ className, tone, children, ...props }: HTMLAttributes<HTMLSpanElement> & VariantProps<typeof badge>) {
  return (
    <span className={cn(badge({ tone }), className)} {...props}>
      {children}
    </span>
  );
}

// ---------------------------------------------------------------- Input
export const Input = forwardRef<HTMLInputElement, InputHTMLAttributes<HTMLInputElement>>(({ className, ...props }, ref) => (
  <input
    ref={ref}
    className={cn(
      "h-8 w-full rounded border border-hairline-strong bg-paper-raised px-2.5 text-sm text-ink placeholder:text-ink-3 focus:border-ink focus:outline-none",
      className,
    )}
    {...props}
  />
));
Input.displayName = "Input";

export function Kbd({ children }: { children: ReactNode }) {
  return <kbd className="rounded-sm border border-hairline-strong bg-paper px-1 font-mono text-2xs text-ink-2">{children}</kbd>;
}

// ---------------------------------------------------------------- Dialog / Sheet
export const Dialog = DialogPrimitive.Root;
export const DialogTrigger = DialogPrimitive.Trigger;

export function DialogContent({ className, children, title, description, side }: { className?: string; children: ReactNode; title: string; description?: string; side?: "right" | "center" }) {
  const sheet = side === "right";
  return (
    <DialogPrimitive.Portal>
      <DialogPrimitive.Overlay className="fixed inset-0 z-40 bg-ink/20 backdrop-blur-[1px] data-[state=open]:animate-in" />
      <DialogPrimitive.Content
        className={cn(
          "fixed z-50 border border-hairline bg-paper-raised text-ink shadow-none focus:outline-none",
          sheet
            ? "right-0 top-0 h-full w-full max-w-[560px] overflow-y-auto"
            : "left-1/2 top-1/2 w-[min(560px,92vw)] -translate-x-1/2 -translate-y-1/2 rounded-md",
          className,
        )}
      >
        <div className="flex items-start justify-between gap-4 border-b border-hairline px-4 py-3">
          <div>
            <DialogPrimitive.Title className="text-sm font-medium tracking-tight">{title}</DialogPrimitive.Title>
            {description ? <DialogPrimitive.Description className="mt-0.5 text-xs text-ink-2">{description}</DialogPrimitive.Description> : null}
          </div>
          <DialogPrimitive.Close className="rounded p-1 text-ink-2 hover:bg-paper-sunk hover:text-ink" aria-label="Close">
            <X size={14} />
          </DialogPrimitive.Close>
        </div>
        <div className="p-4">{children}</div>
      </DialogPrimitive.Content>
    </DialogPrimitive.Portal>
  );
}

// ---------------------------------------------------------------- Tabs
export const Tabs = TabsPrimitive.Root;
export function TabsList({ className, ...props }: TabsPrimitive.TabsListProps) {
  return <TabsPrimitive.List className={cn("flex gap-0 border-b border-hairline", className)} {...props} />;
}
export function TabsTrigger({ className, ...props }: TabsPrimitive.TabsTriggerProps) {
  return (
    <TabsPrimitive.Trigger
      className={cn(
        "-mb-px border-b-2 border-transparent px-3 py-2 text-2xs font-medium uppercase tracking-[0.14em] text-ink-2 hover:text-ink data-[state=active]:border-ink data-[state=active]:text-ink",
        className,
      )}
      {...props}
    />
  );
}
export function TabsContent({ className, ...props }: TabsPrimitive.TabsContentProps) {
  return <TabsPrimitive.Content className={cn("pt-3 focus:outline-none", className)} {...props} />;
}

// ---------------------------------------------------------------- Tooltip
export const TooltipProvider = TooltipPrimitive.Provider;
export function Tip({ label, children }: { label: ReactNode; children: ReactNode }) {
  return (
    <TooltipPrimitive.Root delayDuration={200}>
      <TooltipPrimitive.Trigger asChild>{children}</TooltipPrimitive.Trigger>
      <TooltipPrimitive.Portal>
        <TooltipPrimitive.Content sideOffset={6} className="z-50 max-w-xs rounded border border-hairline bg-ink px-2 py-1 text-xs text-paper">
          {label}
        </TooltipPrimitive.Content>
      </TooltipPrimitive.Portal>
    </TooltipPrimitive.Root>
  );
}

// ---------------------------------------------------------------- Table
export function Table({ className, ...props }: HTMLAttributes<HTMLTableElement>) {
  return (
    <div className="w-full overflow-x-auto">
      <table className={cn("w-full border-collapse text-sm", className)} {...props} />
    </div>
  );
}
export function Th({ className, ...props }: HTMLAttributes<HTMLTableCellElement>) {
  return <th className={cn("eyebrow border-b border-hairline px-3 py-2 text-left font-medium", className)} {...props} />;
}
export function Td({ className, ...props }: HTMLAttributes<HTMLTableCellElement>) {
  return <td className={cn("border-b border-hairline px-3 py-2 align-top", className)} {...props} />;
}

export function Empty({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="px-4 py-10 text-center">
      <div className="text-sm font-medium">{title}</div>
      {children ? <div className="mt-1 text-xs text-ink-2">{children}</div> : null}
    </div>
  );
}
