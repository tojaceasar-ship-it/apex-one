import React from "react";
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import { AppShell } from "./components/Layout";
import { AuthProvider, useAuth } from "./context/AuthContext";
import {
  AgentBuilderPage,
  AgentCatalogPage,
  AgentDetailPage,
  AgentTeamsPage,
  RunsExecutionsPage,
} from "./pages/AgentPages";
import { LoginPage, RegisterPage } from "./pages/AuthPages";
import {
  DatasetsPage,
  EvaluationsPage,
  ExperimentDetailPage,
  ExperimentLabPage,
} from "./pages/ExperimentEvalPages";
import {
  AnalyticsPage,
  ApprovalCenterPage,
  AuditLogPage,
  ExtensionsPage,
  LocalNodesPage,
  ModelsProvidersPage,
  SettingsAdminPage,
  WorkflowsPage,
} from "./pages/GovernanceInfraPages";
import {
  CommandCenterPage,
  EvidenceGraphPage,
  MemoryExplorerPage,
  ResearchProjectDetailPage,
  ResearchProjectsPage,
  SourceRegistryPage,
} from "./pages/ResearchPages";

export function formatSubsystemBadge(
  ready: boolean,
  fallback: "CONDITIONAL" | "BLOCKED" = "CONDITIONAL"
): "PASS" | "CONDITIONAL" | "BLOCKED" {
  return ready ? "PASS" : fallback;
}

function ProtectedRoutes() {
  const { loading, user, t } = useAuth();
  if (loading) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-[#0b0d12] text-sm text-zinc-400">
        {t.common.loading}
      </div>
    );
  }
  if (!user) {
    return <Navigate to="/login" replace />;
  }
  return <AppShell />;
}

export function App() {
  return (
    <AuthProvider>
      <BrowserRouter>
        <Routes>
          <Route path="/login" element={<LoginPage />} />
          <Route path="/register" element={<RegisterPage />} />

          <Route element={<ProtectedRoutes />}>
            <Route index element={<CommandCenterPage />} />
            <Route path="projects" element={<ResearchProjectsPage />} />
            <Route path="projects/:projectId" element={<ResearchProjectDetailPage />} />
            <Route path="agents" element={<AgentCatalogPage />} />
            <Route path="agents/:agentId" element={<AgentDetailPage />} />
            <Route path="agent-builder" element={<AgentBuilderPage />} />
            <Route path="teams" element={<AgentTeamsPage />} />
            <Route path="runs" element={<RunsExecutionsPage />} />
            <Route path="sources" element={<SourceRegistryPage />} />
            <Route path="evidence" element={<EvidenceGraphPage />} />
            <Route path="experiments" element={<ExperimentLabPage />} />
            <Route path="experiments/:experimentId" element={<ExperimentDetailPage />} />
            <Route path="datasets" element={<DatasetsPage />} />
            <Route path="evaluations" element={<EvaluationsPage />} />
            <Route path="memory" element={<MemoryExplorerPage />} />
            <Route path="workflows" element={<WorkflowsPage />} />
            <Route path="models" element={<ModelsProvidersPage />} />
            <Route path="local-nodes" element={<LocalNodesPage />} />
            <Route path="approvals" element={<ApprovalCenterPage />} />
            <Route path="audit" element={<AuditLogPage />} />
            <Route path="analytics" element={<AnalyticsPage />} />
            <Route path="extensions" element={<ExtensionsPage />} />
            <Route path="settings" element={<SettingsAdminPage />} />
          </Route>

          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </BrowserRouter>
    </AuthProvider>
  );
}

export default App;
