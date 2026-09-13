import { useState } from "react";
import { Api, ApiError } from "@/lib/api";
import { Button, Dialog, DialogContent } from "./ui";

/** The one feedback channel: a short form that files a GitHub issue
 * server-side, so a reporter never sees or needs a GitHub account.
 * `context` is a one-line hint about where feedback was opened from (a page,
 * an integration) - never anything the reporter did not already see. */
export function FeedbackDialog({ open, onOpenChange, context }: { open: boolean; onOpenChange: (open: boolean) => void; context?: string }) {
  const [message, setMessage] = useState("");
  const [state, setState] = useState<"idle" | "sending" | "sent" | "error">("idle");

  const close = () => {
    onOpenChange(false);
    setMessage("");
    setState("idle");
  };

  return (
    <Dialog open={open} onOpenChange={(next) => (next ? onOpenChange(true) : close())}>
      <DialogContent title="Send feedback" description="Goes straight to the team as a GitHub issue.">
        {state === "sent" ? (
          <p className="text-sm">Thanks - filed. <button type="button" className="underline" onClick={close}>Close</button></p>
        ) : (
          <form
            className="space-y-3"
            onSubmit={async (e) => {
              e.preventDefault();
              setState("sending");
              try {
                await Api.sendFeedback({ message, context });
                setState("sent");
              } catch (err) {
                setState("error");
                console.error(err instanceof ApiError ? err.message : err);
              }
            }}
          >
            <label className="block">
              <span className="eyebrow">What happened, or what you'd change</span>
              <textarea
                autoFocus
                required
                rows={5}
                value={message}
                onChange={(e) => setMessage(e.target.value)}
                placeholder="Tell us what you saw and what you expected instead"
                className="mt-1 w-full rounded border border-hairline-strong bg-paper-raised px-2.5 py-2 text-sm text-ink placeholder:text-ink-3 focus:border-ink focus:outline-none"
              />
            </label>
            {state === "error" ? <p className="text-xs text-deny">Could not send that - try again in a moment.</p> : null}
            <div className="flex justify-end gap-2">
              <Button type="button" variant="ghost" onClick={close}>Cancel</Button>
              <Button type="submit" variant="default" disabled={!message.trim() || state === "sending"}>
                {state === "sending" ? "Sending…" : "Send feedback"}
              </Button>
            </div>
          </form>
        )}
      </DialogContent>
    </Dialog>
  );
}
