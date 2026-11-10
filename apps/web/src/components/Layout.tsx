import React, { useEffect, useMemo, useState } from "react";
import { Link, NavLink, Outlet, useLocation, useNavigate } from "react-router-dom";
import {
  Activity,
  AlertOctagon,
  ArrowUpRight,
  BarChart3,
  BookOpen,
  Bot,
  Boxes,
  Brain,
  CheckSquare,
  ChevronRight,
  Command,
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
  Search,
  Settings,
  ShieldAlert,
  ShieldCheck,
  Users,
  Wrench,
  X,
} from "lucide-react";
import { useAuth, WorkspaceScope } from "../context/AuthContext";

export function ApexPrismLogo({ size = "md" }: { size?: "sm" | "md" | "lg" }) {
  const dims =
    size === "lg" ? "h-11 w-11" : size === "sm" ? "h-8 w-8" : "h-9 w-9";
  return (
    <div
      className={`relative flex ${dims} shrink-0 items-center justify-center rounded-xl bg-gradient-to-br from-indigo-500 via-violet-500 to-fuchsia-600 p-[1px] shadow-lg shadow-indigo-500/25`}
    >
      <div className="flex h-full w-full items-center justify-center rounded-[11px] bg-[#0b0f1a]/90">
        <svg viewBox="0 0 28 28" className="h-5 w-5" fill="none">
          <defs>
            <linearGradient id="apexPrismGrad" x1="2" y1="2" x2="26" y2="26" gradientUnits="userSpaceOnUse">
              <stop stopColor="#818CF8" />
              <stop offset="0.55" stopColor="#A78BFA" />
              <stop offset="1" stopColor="#38BDF8" />
            </linearGradient>
          </defs>
          <path
            d="M14 3L24.5 21.5H3.5L14 3Z"
            stroke="url(#apexPrismGrad)"
            strokeWidth="2"
            strokeLinejoin="round"
          />
          <path
            d="M14 9.5L19.5 19H8.5L14 9.5Z"
            fill="url(#apexPrismGrad)"
            fillOpacity="0.28"
          />
          <circle cx="14" cy="15.2" r="2" fill="#E0E7FF" />
        </svg>
      </div>
    </div>
  );
}

export function Card({
  children,
  className = "",
}: {
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <div
      className={`relative rounded-2xl border border-white/[0.07] bg-gradient-to-b from-[#131826]/95 to-[#0d111b]/95 p-5 shadow-[inset_0_1px_0_0_rgba(255,255,255,0.05),0_14px_34px_-12px_rgba(0,0,0,0.7)] backdrop-blur-sm transition-colors duration-150 hover:border-white/[0.11] ${className}`}
    >
      {children}
    </div>
  );
}

export function StatCard({
  label,
  value,
  detail,
  icon: Icon,
  tone = "indigo",
  footer,
}: {
  label: string;
  value: React.ReactNode;
  detail?: React.ReactNode;
  icon: React.ComponentType<{ className?: string }>;
  tone?: "indigo" | "emerald" | "amber" | "violet" | "rose";
  footer?: React.ReactNode;
}) {
  const palette: Record<
    string,
    { iconBg: string; iconText: string; glow: string; bar: string }
  > = {
    indigo: {
      iconBg: "bg-indigo-500/15 border-indigo-400/25",
      iconText: "text-indigo-300",
      glow: "from-indigo-500/12 via-transparent to-transparent",
      bar: "bg-indigo-400",
    },
    emerald: {
      iconBg: "bg-emerald-500/15 border-emerald-400/25",
      iconText: "text-emerald-300",
      glow: "from-emerald-500/12 via-transparent to-transparent",
      bar: "bg-emerald-400",
    },
    amber: {
      iconBg: "bg-amber-500/15 border-amber-400/25",
      iconText: "text-amber-300",
      glow: "from-amber-500/12 via-transparent to-transparent",
      bar: "bg-amber-400",
    },
    violet: {
      iconBg: "bg-violet-500/15 border-violet-400/25",
      iconText: "text-violet-300",
      glow: "from-violet-500/12 via-transparent to-transparent",
      bar: "bg-violet-400",
    },
    rose: {
      iconBg: "bg-rose-500/15 border-rose-400/25",
      iconText: "text-rose-300",
      glow: "from-rose-500/12 via-transparent to-transparent",
      bar: "bg-rose-400",
    },
  };
  const p = palette[tone];

  return (
    <div className="group relative overflow-hidden rounded-2xl border border-white/[0.07] bg-gradient-to-b from-[#141928]/95 to-[#0d111b]/95 p-5 shadow-[inset_0_1px_0_0_rgba(255,255,255,0.06),0_14px_32px_-12px_rgba(0,0,0,0.7)] transition-all duration-200 hover:-translate-y-0.5 hover:border-indigo-400/25">
      <div
        className={`pointer-events-none absolute inset-0 bg-gradient-to-br ${p.glow} opacity-80`}
      />
      <div className="relative flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="text-[11px] font-medium uppercase tracking-wider text-zinc-400">
            {label}
          </div>
          <div className="mt-2 text-2xl font-bold tracking-tight text-white tabular-nums">
            {value}
          </div>
        </div>
        <div
          className={`flex h-10 w-10 shrink-0 items-center justify-center rounded-xl border ${p.iconBg} shadow-inner`}
        >
          <Icon className={`h-4 w-4 ${p.iconText}`} />
        </div>
      </div>
      {detail && <div className="relative mt-2.5 text-xs text-zinc-400">{detail}</div>}
      {footer && <div className="relative mt-2">{footer}</div>}
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
    neutral: "bg-zinc-800/75 text-zinc-300 border-white/10",
    indigo: "bg-indigo-500/15 text-indigo-200 border-indigo-400/30",
    emerald: "bg-emerald-500/15 text-emerald-200 border-emerald-400/30",
    amber: "bg-amber-500/15 text-amber-200 border-amber-400/30",
    rose: "bg-rose-500/15 text-rose-200 border-rose-400/30",
    violet: "bg-violet-500/15 text-violet-200 border-violet-400/30",
  };
  const dotStyles: Record<string, string> = {
    neutral: "bg-zinc-400",
    indigo: "bg-indigo-400",
    emerald: "bg-emerald-400",
    amber: "bg-amber-400",
    rose: "bg-rose-400",
    violet: "bg-violet-400",
  };
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-0.5 text-[11px] font-medium tracking-wide ${styles[tone]}`}
    >
      <span className={`h-1.5 w-1.5 rounded-full ${dotStyles[tone]}`} />
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
    <div className="mb-6 flex flex-col justify-between gap-4 border-b border-white/[0.07] pb-5 sm:flex-row sm:items-end">
      <div className="space-y-1">
        <div className="inline-flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-widest text-indigo-400/90">
          <span className="h-1.5 w-1.5 rounded-full bg-indigo-400 shadow-[0_0_8px_#818cf8]" />
          APEX ONE · Research OS
        </div>
        <h1 className="bg-gradient-to-r from-white via-zinc-100 to-zinc-300 bg-clip-text text-2xl font-semibold tracking-tight text-transparent sm:text-[26px]">
          {title}
        </h1>
        {subtitle && (
          <p className="max-w-3xl text-xs leading-relaxed text-zinc-400 sm:text-sm">
            {subtitle}
          </p>
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
  const location = useLocation();
  const navigate = useNavigate();
  const [mobileOpen, setMobileOpen] = useState(false);
  const [paletteOpen, setPaletteOpen] = useState(false);
  const [paletteQuery, setPaletteQuery] = useState("");

  const activeOrg = organizations.find((o) => o.organization_id === activeOrgId);

  const navGroups: Array<{
    label: string;
    items: Array<{ to: string; label: string; icon: any; end?: boolean }>;
  }> = useMemo(
    () => [
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
    ],
    [t]
  );

  const flatItems = useMemo(
    () =>
      navGroups.flatMap((g) =>
        g.items.map((item) => ({ ...item, group: g.label }))
      ),
    [navGroups]
  );

  const currentNavItem = useMemo(() => {
    if (location.pathname === "/") return flatItems[0];
    return (
      flatItems.find(
        (it) => it.to !== "/" && location.pathname.startsWith(it.to)
      ) || flatItems[0]
    );
  }, [flatItems, location.pathname]);

  useEffect(() => {
    const onKeyDown = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setPaletteOpen((prev) => !prev);
      } else if (e.key === "Escape") {
        setPaletteOpen(false);
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, []);

  const filteredPalette = useMemo(() => {
    const q = paletteQuery.trim().toLowerCase();
    if (!q) return flatItems;
    return flatItems.filter(
      (it) =>
        it.label.toLowerCase().includes(q) ||
        it.group.toLowerCase().includes(q) ||
        it.to.toLowerCase().includes(q)
    );
  }, [flatItems, paletteQuery]);

  const initials = (user?.display_name || user?.email || "A1")
    .split(" ")
    .filter(Boolean)
    .slice(0, 2)
    .map((s: string) => s[0]?.toUpperCase())
    .join("");

  const renderSidebar = () => (
    <div className="flex h-full flex-col border-r border-white/[0.07] bg-[#0a0d15]/95 backdrop-blur-xl">
      {/* Brand Header */}
      <div className="flex items-center justify-between border-b border-white/[0.06] px-4 py-4">
        <Link to="/" className="flex items-center gap-3">
          <ApexPrismLogo size="md" />
          <div>
            <div className="flex items-center gap-1.5">
              <span className="text-sm font-bold tracking-wider text-white">
                {t.appTitle}
              </span>
              <span className="rounded bg-indigo-500/20 px-1.5 py-0.5 text-[10px] font-semibold text-indigo-300">
                1.0
              </span>
            </div>
            <div className="mt-0.5 flex items-center gap-1.5 text-[11px] text-zinc-400">
              <span className="inline-block h-1.5 w-1.5 rounded-full bg-emerald-400 shadow-[0_0_6px_#34d399]" />
              {t.editionBadge}
            </div>
          </div>
        </Link>
        <button
          onClick={() => setMobileOpen(false)}
          className="rounded-lg p-1 text-zinc-400 hover:bg-white/5 hover:text-white lg:hidden"
          aria-label="Close navigation"
        >
          <X className="h-5 w-5" />
        </button>
      </div>

      {/* Quick Command Search Button */}
      <div className="px-3 pt-3">
        <button
          type="button"
          onClick={() => {
            setMobileOpen(false);
            setPaletteOpen(true);
          }}
          className="flex w-full items-center justify-between rounded-xl border border-white/[0.07] bg-white/[0.03] px-3 py-2 text-xs text-zinc-400 transition hover:border-indigo-400/35 hover:bg-white/[0.05] hover:text-zinc-200"
        >
          <span className="flex items-center gap-2">
            <Search className="h-3.5 w-3.5 text-indigo-400" />
            <span>{locale === "pl" ? "Szybka nawigacja..." : "Quick jump..."}</span>
          </span>
          <kbd className="rounded border border-white/10 bg-black/40 px-1.5 py-0.5 font-mono text-[10px] text-zinc-400">
            Ctrl K
          </kbd>
        </button>
      </div>

      {/* Navigation Groups */}
      <nav className="flex-1 space-y-5 overflow-y-auto px-3 py-4">
        {navGroups.map((grp) => (
          <div key={grp.label}>
            <div className="px-2.5 pb-1.5 text-[10px] font-semibold uppercase tracking-[0.14em] text-zinc-500">
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
                      `group relative flex items-center gap-2.5 rounded-xl px-3 py-2 text-xs font-medium transition-all duration-150 ${
                        isActive
                          ? "bg-gradient-to-r from-indigo-500/20 via-indigo-500/10 to-transparent text-white shadow-[inset_0_1px_0_0_rgba(255,255,255,0.08)] ring-1 ring-indigo-400/30"
                          : "text-zinc-400 hover:bg-white/[0.04] hover:text-zinc-100"
                      }`
                    }
                  >
                    {({ isActive }) => (
                      <>
                        {isActive && (
                          <span className="absolute left-0 top-1/2 h-4 w-1 -translate-y-1/2 rounded-r-full bg-indigo-400 shadow-[0_0_10px_#818cf8]" />
                        )}
                        <Icon
                          className={`h-4 w-4 shrink-0 transition-colors ${
                            isActive
                              ? "text-indigo-300"
                              : "text-zinc-500 group-hover:text-zinc-300"
                          }`}
                        />
                        <span className="truncate">{item.label}</span>
                      </>
                    )}
                  </NavLink>
                );
              })}
            </div>
          </div>
        ))}
      </nav>

      {/* User Footer Card */}
      <div className="border-t border-white/[0.06] p-3">
        <div className="flex items-center justify-between gap-2.5 rounded-xl border border-white/[0.07] bg-gradient-to-b from-white/[0.04] to-white/[0.015] p-2.5">
          <div className="flex min-w-0 items-center gap-2.5">
            <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-gradient-to-br from-indigo-500/30 to-violet-500/30 text-xs font-bold text-indigo-200 ring-1 ring-indigo-400/30">
              {initials}
            </div>
            <div className="min-w-0">
              <div className="truncate text-xs font-semibold text-zinc-100">
                {user?.display_name}
              </div>
              <div className="mt-0.5 flex items-center gap-1.5 text-[10px] text-zinc-400">
                <span className="rounded bg-indigo-500/15 px-1.5 py-0.2 font-medium text-indigo-300">
                  {role}
                </span>
                <span className="truncate text-zinc-500">{user?.email}</span>
              </div>
            </div>
          </div>
          <button
            onClick={() => void logout()}
            title={t.auth.logout}
            className="rounded-lg p-1.5 text-zinc-400 transition hover:bg-rose-500/15 hover:text-rose-300"
          >
            <LogOut className="h-4 w-4" />
          </button>
        </div>
      </div>
    </div>
  );

  const scopeOptions: Array<{ value: WorkspaceScope; shortPl: string; shortEn: string }> = [
    { value: "all", shortPl: "Wszystkie", shortEn: "All" },
    { value: "research", shortPl: "Badania", shortEn: "Research" },
    { value: "demo", shortPl: "Demo", shortEn: "Demo" },
  ];

  return (
    <div className="apex-canvas-bg flex h-screen overflow-hidden text-zinc-100">
      <aside className="hidden w-64 shrink-0 lg:block">{renderSidebar()}</aside>

      {mobileOpen && (
        <div className="fixed inset-0 z-50 flex lg:hidden">
          <div
            className="fixed inset-0 bg-black/75 backdrop-blur-xs"
            onClick={() => setMobileOpen(false)}
          />
          <div className="relative z-10 w-72 max-w-full">{renderSidebar()}</div>
        </div>
      )}

      {/* Command Palette Modal (Ctrl+K) */}
      {paletteOpen && (
        <div className="fixed inset-0 z-50 flex items-start justify-center px-4 pt-20">
          <div
            className="fixed inset-0 bg-black/70 backdrop-blur-xs"
            onClick={() => setPaletteOpen(false)}
          />
          <div className="relative z-10 w-full max-w-lg overflow-hidden rounded-2xl border border-white/15 bg-[#101420] shadow-2xl">
            <div className="flex items-center gap-2.5 border-b border-white/10 px-4 py-3">
              <Command className="h-4 w-4 text-indigo-400" />
              <input
                autoFocus
                value={paletteQuery}
                onChange={(e) => setPaletteQuery(e.target.value)}
                placeholder={
                  locale === "pl"
                    ? "Przejdź do modułu (np. Graf Dowodowy, Eksperymenty, Audyt)..."
                    : "Jump to module (e.g. Evidence Graph, Experiments, Audit)..."
                }
                className="w-full border-none bg-transparent text-sm text-white shadow-none focus:ring-0"
              />
              <button
                onClick={() => setPaletteOpen(false)}
                className="rounded p-1 text-zinc-400 hover:text-white"
              >
                <X className="h-4 w-4" />
              </button>
            </div>
            <div className="max-h-80 overflow-y-auto p-2">
              {filteredPalette.map((it) => {
                const Icon = it.icon;
                return (
                  <button
                    key={it.to}
                    type="button"
                    onClick={() => {
                      navigate(it.to);
                      setPaletteOpen(false);
                      setPaletteQuery("");
                    }}
                    className="flex w-full items-center justify-between rounded-xl px-3 py-2 text-left text-xs text-zinc-200 transition hover:bg-indigo-500/15 hover:text-white"
                  >
                    <span className="flex items-center gap-2.5">
                      <Icon className="h-4 w-4 text-indigo-400" />
                      <span className="font-medium">{it.label}</span>
                    </span>
                    <span className="flex items-center gap-1 text-[10px] text-zinc-500">
                      {it.group}
                      <ArrowUpRight className="h-3 w-3" />
                    </span>
                  </button>
                );
              })}
            </div>
          </div>
        </div>
      )}

      <div className="flex min-w-0 flex-1 flex-col overflow-hidden">
        {activeOrg?.emergency_stop_active && (
          <div className="flex items-center justify-center gap-2 bg-gradient-to-r from-rose-700 via-rose-600 to-rose-700 px-4 py-1.5 text-xs font-semibold text-white shadow-md">
            <AlertOctagon className="h-4 w-4 animate-pulse" />
            {t.common.emergencyActive}
          </div>
        )}

        {/* Top Sticky Glass Header */}
        <header className="sticky top-0 z-20 flex flex-wrap items-center justify-between gap-3 border-b border-white/[0.07] bg-[#0a0d16]/80 px-4 py-2.5 backdrop-blur-xl sm:px-6">
          <div className="flex min-w-0 items-center gap-2.5">
            <button
              onClick={() => setMobileOpen(true)}
              className="rounded-xl border border-white/10 bg-white/[0.03] p-1.5 text-zinc-300 hover:bg-white/[0.08] lg:hidden"
              aria-label="Open navigation"
            >
              <Menu className="h-4 w-4" />
            </button>

            {/* Organization Switcher Chip */}
            <div className="flex items-center gap-1.5 rounded-xl border border-white/[0.08] bg-white/[0.03] px-2.5 py-1">
              <ShieldCheck className="hidden h-3.5 w-3.5 text-emerald-400 sm:block" />
              <Boxes className="h-3.5 w-3.5 text-indigo-400 sm:hidden" />
              <select
                aria-label="Organizacja"
                value={activeOrgId || ""}
                onChange={(e) => void switchOrganization(e.target.value)}
                className="border-none bg-transparent py-0.5 pr-1 text-xs font-medium text-zinc-100 shadow-none focus:ring-0"
              >
                {organizations.map((o) => (
                  <option
                    key={o.organization_id}
                    value={o.organization_id}
                    className="bg-[#0e121d] text-zinc-100"
                  >
                    {o.name} ({o.role})
                  </option>
                ))}
              </select>
            </div>

            {/* Active Breadcrumb */}
            {currentNavItem && (
              <div className="hidden items-center gap-1.5 text-xs text-zinc-400 xl:flex">
                <ChevronRight className="h-3.5 w-3.5 text-zinc-600" />
                <span className="text-zinc-500">{currentNavItem.group}</span>
                <ChevronRight className="h-3.5 w-3.5 text-zinc-600" />
                <span className="font-medium text-zinc-200">
                  {currentNavItem.label}
                </span>
              </div>
            )}
          </div>

          <div className="flex flex-wrap items-center gap-2">
            {/* Segmented Workspace Scope Switcher */}
            <div
              role="group"
              aria-label="Workspace scope"
              className="inline-flex items-center rounded-xl border border-white/[0.08] bg-black/35 p-0.5"
            >
              {scopeOptions.map((opt) => {
                const active = scope === opt.value;
                return (
                  <button
                    key={opt.value}
                    type="button"
                    onClick={() => setScope(opt.value)}
                    title={t.scope[opt.value]}
                    className={`rounded-lg px-2.5 py-1 text-[11px] font-medium transition-all ${
                      active
                        ? "bg-indigo-500/25 text-indigo-200 shadow-xs ring-1 ring-indigo-400/35"
                        : "text-zinc-400 hover:text-zinc-200"
                    }`}
                  >
                    {locale === "pl" ? opt.shortPl : opt.shortEn}
                  </button>
                );
              })}
            </div>

            {/* Language Toggle */}
            <button
              onClick={() => setLocale(locale === "pl" ? "en" : "pl")}
              className="inline-flex items-center gap-1.5 rounded-xl border border-white/[0.08] bg-white/[0.03] px-2.5 py-1.5 text-xs font-semibold text-zinc-200 transition hover:border-indigo-400/40 hover:bg-white/[0.06]"
            >
              <Globe className="h-3.5 w-3.5 text-indigo-400" />
              <span>{locale === "pl" ? "PL" : "EN"}</span>
            </button>
          </div>
        </header>

        <main className="flex-1 overflow-y-auto px-4 py-5 sm:px-7 sm:py-6">
          <div key={location.pathname} className="apex-page-enter mx-auto max-w-7xl">
            <Outlet />
          </div>
        </main>
      </div>
    </div>
  );
}
