import { GITHUB_URL } from "@/lib/site";
import { ThemeToggle } from "./ThemeToggle";
import { ViewToggle } from "./ViewToggle";

export function Navbar() {
  return (
    <header className="sticky top-0 z-20 border-b border-line bg-bg/80 backdrop-blur">
      <div className="mx-auto flex h-15 max-w-5xl items-center gap-4 px-6 py-3">
        <a href="#top" className="flex items-center gap-2 font-semibold tracking-tight text-ink">
          <span className="inline-block h-3 w-3 rounded-sm bg-accent shadow-[0_0_0_4px_color-mix(in_srgb,var(--accent)_20%,transparent)]" />
          agent-plane
        </a>
        <div className="flex-1" />
        <ViewToggle />
        <a
          href={GITHUB_URL}
          className="hidden text-sm text-muted transition-colors hover:text-ink sm:inline"
        >
          GitHub
        </a>
        <ThemeToggle />
      </div>
    </header>
  );
}
