import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { createHashRouter, RouterProvider } from "react-router-dom";
import "./index.css";
import { StoreProvider } from "./lib/store";
import { TooltipProvider } from "./components/ui";
import { Shell } from "./components/shell";
import { LivePage } from "./pages/live";
import { AgentsPage } from "./pages/agents";
import { TasksPage } from "./pages/tasks";
import { ResourcesPage } from "./pages/resources";
import { GovernPage } from "./pages/govern";
import { DecisionsPage } from "./pages/decisions";
import { PoliciesPage } from "./pages/policies";
import { TimelinePage, AuditPage } from "./pages/evidence";
import { IntegrationsPage, GatewayPage, RuntimePage, SettingsPage } from "./pages/platform";

// Hash routing keeps the app self-contained under /console without server
// rewrites, and lets a URL like /console#/decisions?select=az_… be shared.
const router = createHashRouter([
  {
    path: "/",
    element: <Shell />,
    children: [
      { index: true, element: <LivePage /> },
      { path: "agents", element: <AgentsPage /> },
      { path: "tasks", element: <TasksPage /> },
      { path: "resources", element: <ResourcesPage /> },
      { path: "govern", element: <GovernPage /> },
      { path: "decisions", element: <DecisionsPage /> },
      { path: "policies", element: <PoliciesPage /> },
      { path: "timeline", element: <TimelinePage /> },
      { path: "audit", element: <AuditPage /> },
      { path: "integrations", element: <IntegrationsPage /> },
      { path: "gateway", element: <GatewayPage /> },
      { path: "runtime", element: <RuntimePage /> },
      { path: "settings", element: <SettingsPage /> },
      { path: "*", element: <LivePage /> },
    ],
  },
]);

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <StoreProvider>
      <TooltipProvider>
        <RouterProvider router={router} />
      </TooltipProvider>
    </StoreProvider>
  </StrictMode>,
);
