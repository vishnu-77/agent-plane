import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { createHashRouter, Navigate, RouterProvider, useLocation } from "react-router-dom";
import "./index.css";
import { StoreProvider, useStore } from "./lib/store";
import { TooltipProvider } from "./components/ui";
import { Shell } from "./components/shell";
import { ActivityPage } from "./pages/activity";
import { AgentsPage } from "./pages/agents";
import { RulesPage } from "./pages/rules";
import { IntegrationsPage } from "./pages/integrations";
import { GraphPage } from "./pages/graph";
import { ContextPage } from "./pages/context";
import { SettingsPage } from "./pages/settings";
import { AuthPage, OnboardingPage } from "./pages/auth";
import { HomePage } from "./pages/home";

const router = createHashRouter([
  { path: "/login", element: <AuthRoute mode="login" /> },
  { path: "/signup", element: <AuthRoute mode="signup" /> },
  {
    path: "/",
    element: <WorkspaceGate />,
    children: [
      { index: true, element: <ActivityPage /> },
      { path: "agents", element: <AgentsPage /> },
      { path: "access", element: <RulesPage /> },
      { path: "connect", element: <IntegrationsPage /> },
      { path: "graph", element: <GraphPage /> },
      { path: "context", element: <ContextPage /> },
      // Legacy URLs stay valid: this is a UX vocabulary change, not a routing break.
      { path: "rules", element: <RulesPage /> },
      { path: "integrations", element: <IntegrationsPage /> },
      { path: "settings", element: <SettingsPage /> },
      { path: "*", element: <Navigate to="/" replace /> },
    ],
  },
]);

/** Sign-in, then onboarding, then the product. DEMO skips straight through. */
function Loading() {
  return <div className="flex min-h-screen items-center justify-center" role="status">Loading agent-plane…</div>;
}

function AuthRoute({ mode }: { mode: "login" | "signup" }) {
  const { ready, signedIn, source } = useStore();
  if (!ready) return <Loading />;
  if (signedIn && source === "live") return <Navigate to="/" replace />;
  return <AuthPage key={mode} mode={mode} />;
}

function WorkspaceGate() {
  const { ready, signedIn, source, projects, sessionEnded } = useStore();
  const location = useLocation();
  if (!ready) {
    return <Loading />;
  }
  if (source === "demo") return <Shell />;
  if (!signedIn) {
    if (sessionEnded || location.search.includes("sso_error=")) {
      return <Navigate to={`/login${location.search}`} replace />;
    }
    return location.pathname === "/" ? <HomePage /> : <Navigate to="/login" replace />;
  }
  if (!projects.length) return <OnboardingPage />;
  return <Shell />;
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <StoreProvider>
      <TooltipProvider>
        <RouterProvider router={router} />
      </TooltipProvider>
    </StoreProvider>
  </StrictMode>,
);
