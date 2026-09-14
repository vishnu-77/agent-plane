# agent-plane website

Marketing landing page for [agentplane.cc](https://agentplane.cc). Next.js (App Router,
static export) + Tailwind v4, mirroring the OpenReflex site's stack so the two stay
consistent.

## The Researcher / Builder toggle

The homepage serves two audiences from one document, switched by a toggle in the navbar:

- **Builder** — install, `connect`, three-list rules, the integration honesty table.
- **Researcher** — the authority model, attenuating delegation, the decision table, and
  the "what binds vs. advisory" honesty box.

The choice is remembered (`localStorage`) and shareable (`?view=research`); a pre-paint
inline script sets it before first paint so a shared link never flashes the wrong view.
See `lib/view.ts`.

## Develop

```bash
npm install
npm run dev      # http://localhost:3000
npm run build    # static export to ./out
```

Deploy `out/` to any static host (Vercel, Cloudflare Pages, GitHub Pages).
