import React, { useState } from "react";
import { NavLink, Outlet } from "react-router-dom";
import {
  Activity,
  AlertOctagon,
  BarChart3,
  BookOpen,
  Bot,
  Boxes,
  Brain,
  CheckSquare,
  Cpu,
  Database,
  FileCheck2,
  FlaskConical,
  FolderKanban,
  GitBranch,
  Globe,
  HardDrive,
  LayoutDashboard,
  LogOut,
  Menu,
  Network,
  Puzzle,
  Settings,
  ShieldAlert,
  Users,
  Wrench,
  X,
} from "lucide-react";
import { useAuth, WorkspaceScope } from "../context/AuthContext";

export function Card({
  children,
  className = "",
}: {
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <div
      className={`rounded-xl border border-zinc-800/90 bg-[#121620] p-5 shadow-lg shadow-black/20 ${className}`}
    >
      {children}
    </div>
  );
}

export function Badge({
  children,
  tone = "neutral",
}: {
  children: React.ReactNode;
  tone?: "neutral" | "indigo" | "emerald" | "amber" | "rose" | "violet";
}) {
  const styles: Record<string, string> = {
    neutral: "bg-zinc-800/80 text-zinc-300 border-zinc-700",
    indigo: "bg-indigo-500/15 text-indigo-300 border-indigo-500/30",
    emerald: "bg-emerald-500/15 text-emerald-300 border-emerald-500/30",
    amber: "bg-amber-500/15 text-amber-300 border-amber-500/30",
    rose: "bg-rose-500/15 text-rose-300 border-rose-500/30",
    violet: "bg-violet-500/15 text-violet-300 border-violet-500/30",
  };
  return (
    <span
      className={`inline-flex items-center gap-1 rounded-md border px-2 py-0.5 text-xs font-medium ${styles[tone]}`}
    >
      {children}
    </span>
  );
}

export function PageHeader({
  title,
  subtitle,
  actions,
}: {
  title: string;
  subtitle?: string;
  actions?: React.ReactNode;
}) {
  return (
    <div className="mb-6 flex flex-col justify-between gap-4 border-b border-zinc-800/80 pb-4 sm:flex-row sm:items-center">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight text-zinc-100">
          {title}
        </h1>
        {subtitle && (
          <p className="mt-1 text-sm text-zinc-400">{subtitle}</p>
        )}
      </div>
      {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
    </div>
  );
}

export function AppShell() {
  const {
    user,
    role,
    organizations,
    activeOrgId,
    scope,
    locale,
    t,
    setScope,
    setLocale,
    switchOrganization,
    logout,
  } = useAuth();
  const [mobileOpen, setMobileOpen] = useState(false);

  const activeOrg = organizations.find((o) => o.organization_id === activeOrgId);

  const navGroups: Array<{
    label: string;
    items: Array<{ to: string; label: string; icon: any; end?: boolean }>;
  }> = [
    {
      label: t.groups.research,
      items: [
        { to: "/", label: t.nav.commandCenter, icon: LayoutDashboard, end: true },
        { to: "/projects", label: t.nav.projects, icon: FolderKanban },
        { to: "/sources", label: t.nav.sources, icon: BookOpen },
        { to: "/evidence", label: t.nav.evidence, icon: Network },
        { to: "/memory", label: t.nav.memory, icon: Brain },
      ],
    },
    {
      label: t.groups.agents,
      items: [
        { to: "/agents", label: t.nav.agents, icon: Bot },
        { to: "/agent-builder", label: t.nav.agentBuilder, icon: Wrench },
        { to: "/teams", label: t.nav.teams, icon: Users },
        { to: "/runs", label: t.nav.runs, icon: Activity },
      ],
    },
    {
      label: t.groups.experiments,
      items: [
        { to: "/experiments", label: t.nav.experiments, icon: FlaskConical },
        { to: "/datasets", label: t.nav.datasets, icon: Database },
        { to: "/evaluations", label: t.nav.evaluations, icon: FileCheck2 },
      ],
    },
    {
      label: t.groups.governance,
      items: [
        { to: "/workflows", label: t.nav.workflows, icon: GitBranch },
        { to: "/models", label: t.nav.models, icon: Cpu },
        { to: "/local-nodes", label: t.nav.localNodes, icon: HardDrive },
        { to: "/approvals", label: t.nav.approvals, icon: CheckSquare },
        { to: "/audit", label: t.nav.audit, icon: ShieldAlert },
        { to: "/analytics", label: t.nav.analytics, icon: BarChart3 },
        { to: "/extensions", label: t.nav.extensions, icon: Puzzle },
        { to: "/settings", label: t.nav.settings, icon: Settings },
      ],
    },
  ];

  const renderSidebar = () => (
    <div className="flex h-full flex-col bg-[#0e1118] border-r border-zinc-800/80">
      <div className="flex items-center justify-between px-4 py-4 border-b border-zinc-800/80">
        <div className="flex items-center gap-2.5">
          <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-gradient-to-br from-indigo-500 to-violet-600 font-bold text-white shadow-md shadow-indigo-600/30">
            A1
          </div>
          <div>
            <div className="text-sm font-bold tracking-wider text-zinc-100">
              {t.appTitle}
            </div>
            <div className="text-[11px] text-indigo-400">{t.editionBadge}</div>
          </div>
        </div>
        <button
          onClick={() => setMobileOpen(false)}
          className="rounded p-1 text-zinc-400 hover:bg-zinc-800 lg:hidden"
          aria-label="Close navigation"
        >
          <X className="h-5 w-5" />
        </button>
      </div>

      <nav className="flex-1 overflow-y-auto px-3 py-3 space-y-5">
        {navGroups.map((grp) => (
          <div key={grp.label}>
            <div className="px-2.5 pb-1.5 text-[10px] font-semibold uppercase tracking-widest text-zinc-500">
              {grp.label}
            </div>
            <div className="space-y-0.5">
              {grp.items.map((item) => {
                const Icon = item.icon;
                return (
                  <NavLink
                    key={item.to}
                    to={item.to}
                    end={item.end}
                    onClick={() => setMobileOpen(false)}
                    className={({ isActive }) =>
                      `flex items-center gap-2.5 rounded-lg px-2.5 py-1.5 text-xs font-medium transition-colors ${
                        isActive
                          ? "bg-indigo-500/15 text-indigo-300 border border-indigo-500/30"
                          : "text-zinc-400 hover:bg-zinc-800/60 hover:text-zinc-200 border border-transparent"
                      }`
                    }
                  >
                    <Icon className="h-4 w-4 shrink-0" />
                    <span className="truncate">{item.label}</span>
                  </NavLink>
                );
              })}
            </div>
          </div>
        ))}
      </nav>

      <div className="border-t border-zinc-800/80 p-3">
        <div className="flex items-center justify-between gap-2 rounded-lg bg-zinc-900/90 px-3 py-2">
          <div className="min-w-0">
            <div className="truncate text-xs font-medium text-zinc-200">
              {user?.display_name}
            </div>
            <div className="truncate text-[11px] text-zinc-500">
              {role} · {user?.email}
            </div>
          </div>
          <button
            onClick={() => void logout()}
            title={t.auth.logout}
            className="rounded-md p-1.5 text-zinc-400 hover:bg-rose-500/15 hover:text-rose-300"
          >
            <LogOut className="h-4 w-4" />
          </button>
        </div>
      </div>
    </div>
  );

  return (
    <div className="flex h-screen overflow-hidden bg-[#0b0d12] text-zinc-100">
      <aside className="hidden w-64 shrink-0 lg:block">{renderSidebar()}</aside>

      {mobileOpen && (
        <div className="fixed inset-0 z-50 flex lg:hidden">
          <div
            className="fixed inset-0 bg-black/70"
            onClick={() => setMobileOpen(false)}
          />
          <div className="relative z-10 w-72 max-w-full">{renderSidebar()}</div>
        </div>
      )}

      <div className="flex min-w-0 flex-1 flex-col overflow-hidden">
        {activeOrg?.emergency_stop_active && (
          <div className="flex items-center justify-center gap-2 bg-rose-600/90 px-4 py-1.5 text-xs font-semibold text-white">
            <AlertOctagon className="h-4 w-4" />
            {t.common.emergencyActive}
          </div>
        )}

        <header className="flex flex-wrap items-center justify-between gap-3 border-b border-zinc-800/80 bg-[#0e1118]/90 px-4 py-2.5 backdrop-blur">
          <div className="flex items-center gap-2">
            <button
              onClick={() => setMobileOpen(true)}
              className="rounded-lg border border-zinc-800 p-1.5 text-zinc-300 hover:bg-zinc-800 lg:hidden"
              aria-label="Open navigation"
            >
              <Menu className="h-4 w-4" />
            </button>
            <Boxes className="h-4 w-4 text-indigo-400 hidden sm:block" />
            <select
              aria-label="Organizacja"
              value={activeOrgId || ""}
              onChange={(e) => void switchOrganization(e.target.value)}
              className="rounded-lg border border-zinc-800 bg-zinc-900 px-2.5 py-1 text-xs font-medium text-zinc-200 focus:border-indigo-500 focus:outline-none"
            >
              {organizations.map((o) => (
                <option key={o.organization_id} value={o.organization_id}>
                  {o.name} ({o.role})
                </option>
              ))}
            </select>
          </div>

          <div className="flex flex-wrap items-center gap-2">
            <select
              aria-label="Workspace scope"
              value={scope}
              onChange={(e) => setScope(e.target.value as WorkspaceScope)}
              className="rounded-lg border border-zinc-800 bg-zinc-900 px-2.5 py-1 text-xs text-zinc-200 focus:border-indigo-500 focus:outline-none"
            >
              <option value="all">{t.scope.all}</option>
              <option value="research">{t.scope.research}</option>
              <option value="demo">{t.scope.demo}</option>
            </select>

            <button
              onClick={() => setLocale(locale === "pl" ? "en" : "pl")}
              className="inline-flex items-center gap-1.5 rounded-lg border border-zinc-800 bg-zinc-900 px-2.5 py-1 text-xs font-medium text-zinc-300 hover:border-indigo-500/50 hover:text-white"
            >
              <Globe className="h-3.5 w-3.5 text-indigo-400" />
              {locale.toUpperCase()}
            </button>
          </div>
        </header>

        <main className="flex-1 overflow-y-auto p-4 sm:p-6">
          <div className="mx-auto max-w-7xl">
            <Outlet />
          </div>
        </main>
      </div>
    </div>
  );
}
