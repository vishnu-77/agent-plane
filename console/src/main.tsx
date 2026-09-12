import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { createHashRouter, Navigate, RouterProvider } from "react-router-dom";
import "./index.css";
import { StoreProvider, useStore } from "./lib/store";
import { TooltipProvider } from "./components/ui";
import { Shell } from "./components/shell";
import { ActivityPage } from "./pages/activity";
import { AgentsPage } from "./pages/agents";
import { RulesPage } from "./pages/rules";
import { IntegrationsPage } from "./pages/integrations";
import { SettingsPage } from "./pages/settings";
import { AuthPage, OnboardingPage } from "./pages/auth";

const router = createHashRouter([
  {
    path: "/",
    element: <Shell />,
    children: [
      { index: true, element: <ActivityPage /> },
      { path: "agents", element: <AgentsPage /> },
      { path: "rules", element: <RulesPage /> },
      { path: "integrations", element: <IntegrationsPage /> },
      { path: "settings", element: <SettingsPage /> },
      { path: "*", element: <Navigate to="/" replace /> },
    ],
  },
]);

/** Sign-in, then onboarding, then the product. DEMO skips straight through. */
function Root() {
  const { ready, signedIn, source, projects } = useStore();
  if (!ready) {
    return (
      <div className="flex min-h-screen items-center justify-center">
        <span className="dot text-2xs text-ink-2">loading</span>
      </div>
    );
  }
  if (source === "demo") return <RouterProvider router={router} />;
  if (!signedIn) return <AuthPage />;
  if (!projects.length) return <OnboardingPage />;
  return <RouterProvider router={router} />;
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <StoreProvider>
      <TooltipProvider>
        <Root />
      </TooltipProvider>
    </StoreProvider>
  </StrictMode>,
);
