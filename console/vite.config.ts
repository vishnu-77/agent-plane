import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import path from "node:path";

// Built assets are served by the FastAPI service under /console; the dev
// server proxies API calls to a locally running `agentplane serve`.
const backend = process.env.AGENT_PLANE_URL ?? "http://127.0.0.1:8000";

export default defineConfig({
  plugins: [react()],
  base: "/console/",
  resolve: { alias: { "@": path.resolve(__dirname, "src") } },
  build: {
    outDir: path.resolve(__dirname, "../agent_plane/console/dist"),
    emptyOutDir: true,
    sourcemap: false,
    chunkSizeWarningLimit: 900,
  },
  server: {
    port: 5173,
    proxy: Object.fromEntries(
      ["/v1", "/admin", "/demo", "/healthz", "/readyz", "/brand", "/metrics", "/docs", "/openapi.json"].map((p) => [
        p,
        { target: backend, changeOrigin: true },
      ]),
    ),
  },
});
