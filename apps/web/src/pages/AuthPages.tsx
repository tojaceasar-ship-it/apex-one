import React, { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import {
  Bot,
  CheckCircle2,
  Eye,
  EyeOff,
  Globe,
  Lock,
  Network,
  ShieldCheck,
  Sparkles,
} from "lucide-react";
import { ApexPrismLogo } from "../components/Layout";
import { useAuth } from "../context/AuthContext";

export function LoginPage() {
  const { loginWithCredentials, locale, setLocale, t } = useAuth();
  const navigate = useNavigate();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const performLogin = async (targetEmail: string, targetPass: string) => {
    setError(null);
    setBusy(true);
    try {
      await loginWithCredentials(targetEmail, targetPass);
      navigate("/");
    } catch (err: any) {
      setError(err.message || "Logowanie nie powiodło się.");
    } finally {
      setBusy(false);
    }
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    await performLogin(email, password);
  };

  return (
    <div className="apex-canvas-bg relative flex min-h-screen flex-col justify-center overflow-hidden text-zinc-100 lg:grid lg:grid-cols-12">
      {/* Ambient Background Glows */}
      <div className="pointer-events-none absolute -left-32 -top-32 h-96 w-96 rounded-full bg-indigo-600/20 blur-[120px]" />
      <div className="pointer-events-none absolute bottom-0 right-0 h-96 w-96 rounded-full bg-violet-600/15 blur-[130px]" />

      {/* Left Showcase Column */}
      <div className="relative hidden flex-col justify-between border-r border-white/[0.07] bg-[#080b13]/75 p-12 backdrop-blur-xl lg:col-span-7 lg:flex">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-3">
            <ApexPrismLogo size="lg" />
            <div>
              <div className="flex items-center gap-2">
                <span className="text-base font-bold tracking-wider text-white">
                  APEX ONE
                </span>
                <span className="rounded-full border border-indigo-400/30 bg-indigo-500/15 px-2 py-0.5 text-[10px] font-semibold text-indigo-200">
                  Full Research Edition 1.0
                </span>
              </div>
              <p className="text-xs text-zinc-400">
                Multi-Agent AI Research · Evidence Graph · Reproducible Experiments
              </p>
            </div>
          </div>
        </div>

        <div className="my-auto max-w-xl space-y-6 py-8">
          <div className="inline-flex items-center gap-2 rounded-full border border-indigo-400/25 bg-indigo-500/10 px-3 py-1 text-xs font-medium text-indigo-200">
            <Sparkles className="h-3.5 w-3.5 text-indigo-400" />
            {locale === "pl"
              ? "Architektura Badawcza Klasy Produkcyjnej"
              : "Production-Grade Scientific Research Architecture"}
          </div>

          <h2 className="bg-gradient-to-br from-white via-zinc-100 to-zinc-400 bg-clip-text text-3xl font-bold leading-tight tracking-tight text-transparent xl:text-4xl">
            {locale === "pl"
              ? "Od hipotezy i źródeł pierwotnych do zweryfikowanego dowodowo raportu."
              : "From primary sources and hypotheses to evidence-verified research reports."}
          </h2>

          <p className="text-sm leading-relaxed text-zinc-400">
            {locale === "pl"
              ? "Zintegrowane środowisko 8 wyspecjalizowanych agentów badawczych z wersjonowanym AgentDNA, ustrukturyzowanym Grafem Dowodowym, Silnikiem Reprodukcji Eksperymentów oraz nadzorem R0–R3 opartym na niezmiennym łańcuchu SHA-256."
              : "An integrated 8-agent research studio featuring versioned AgentDNA, structured Evidence Graphs, deterministic Experiment Reproduction, and R0–R3 governance backed by an append-only SHA-256 audit chain."}
          </p>

          {/* Architectural Feature Cards */}
          <div className="grid grid-cols-1 gap-3 pt-2 sm:grid-cols-3">
            <div className="rounded-2xl border border-white/[0.07] bg-white/[0.025] p-3.5">
              <Bot className="h-4 w-4 text-indigo-400" />
              <div className="mt-2 text-xs font-semibold text-white">
                {locale === "pl" ? "8 Ról Agentowych" : "8 Specialist Agents"}
              </div>
              <div className="mt-0.5 text-[11px] leading-snug text-zinc-400">
                {locale === "pl"
                  ? "Trwałe punkty kontrolne DAG i idempotentne narzędzia"
                  : "Durable DAG checkpoints & idempotent tool ledger"}
              </div>
            </div>
            <div className="rounded-2xl border border-white/[0.07] bg-white/[0.025] p-3.5">
              <Network className="h-4 w-4 text-emerald-400" />
              <div className="mt-2 text-xs font-semibold text-white">
                {locale === "pl" ? "Graf Dowodowy" : "Evidence Graph"}
              </div>
              <div className="mt-0.5 text-[11px] leading-snug text-zinc-400">
                {locale === "pl"
                  ? "Detekcja sprzeczności i pokrycie źródeł pierwotnych"
                  : "Contradiction detection & primary source attribution"}
              </div>
            </div>
            <div className="rounded-2xl border border-white/[0.07] bg-white/[0.025] p-3.5">
              <ShieldCheck className="h-4 w-4 text-violet-400" />
              <div className="mt-2 text-xs font-semibold text-white">
                {locale === "pl" ? "PostgreSQL 16 + RLS" : "PostgreSQL 16 + RLS"}
              </div>
              <div className="mt-0.5 text-[11px] leading-snug text-zinc-400">
                {locale === "pl"
                  ? "57 tabel, pgvector HNSW i zgody wiązane SHA-256"
                  : "57 tables, pgvector HNSW & SHA-256 bound approvals"}
              </div>
            </div>
          </div>

          {/* Pipeline Strip Preview */}
          <div className="rounded-2xl border border-white/[0.07] bg-[#0c101b]/90 p-4">
            <div className="mb-2.5 flex items-center justify-between text-[11px]">
              <span className="font-semibold uppercase tracking-wider text-zinc-400">
                {locale === "pl"
                  ? "Potok Orkiestracji Zespołu Badawczego"
                  : "Multi-Agent Research Pipeline"}
              </span>
              <span className="flex items-center gap-1 text-emerald-400">
                <CheckCircle2 className="h-3.5 w-3.5" />
                AgentDNA v1 · Checkpoint Ready
              </span>
            </div>
            <div className="flex flex-wrap items-center gap-1.5 text-[11px]">
              {[
                "Supervisor",
                "Planner",
                "Source Scout",
                "Evidence",
                "Data",
                "Experiment",
                "Critic",
                "Writer",
              ].map((step, i, arr) => (
                <React.Fragment key={step}>
                  <span className="rounded-lg border border-indigo-400/25 bg-indigo-500/10 px-2 py-1 font-medium text-indigo-200">
                    {step}
                  </span>
                  {i < arr.length - 1 && (
                    <span className="text-zinc-600">→</span>
                  )}
                </React.Fragment>
              ))}
            </div>
          </div>
        </div>

        <div className="flex items-center justify-between text-xs text-zinc-500">
          <span>Argon2id · HttpOnly + CSRF · Multi-Tenant RBAC</span>
          <span>APEX ONE 1.0</span>
        </div>
      </div>

      {/* Right Login Column */}
      <div className="relative flex flex-col items-center justify-center px-4 py-10 sm:px-8 lg:col-span-5">
        <div className="mb-4 flex w-full max-w-md items-center justify-between">
          <div className="flex items-center gap-2 lg:hidden">
            <ApexPrismLogo size="sm" />
            <span className="text-sm font-bold tracking-wider text-white">
              APEX ONE
            </span>
          </div>
          <div className="ml-auto">
            <button
              type="button"
              onClick={() => setLocale(locale === "pl" ? "en" : "pl")}
              className="inline-flex items-center gap-1.5 rounded-xl border border-white/10 bg-white/[0.04] px-3 py-1.5 text-xs font-medium text-zinc-200 transition hover:border-indigo-400/40 hover:bg-white/[0.08]"
            >
              <Globe className="h-3.5 w-3.5 text-indigo-400" />
              {locale === "pl" ? "Polski (PL)" : "English (EN)"}
            </button>
          </div>
        </div>

        <div className="w-full max-w-md rounded-3xl border border-white/[0.09] bg-gradient-to-b from-[#141928]/95 to-[#0d111b]/95 p-7 shadow-[inset_0_1px_0_0_rgba(255,255,255,0.08),0_24px_60px_-15px_rgba(0,0,0,0.85)] backdrop-blur-xl sm:p-8">
          <div className="mb-6 flex items-start gap-3.5">
            <ApexPrismLogo size="md" />
            <div>
              <h1 className="text-lg font-semibold tracking-tight text-white">
                {t.auth.signInTitle}
              </h1>
              <p className="mt-0.5 text-xs text-zinc-400">
                {locale === "pl"
                  ? "Uwierzytelnianie Argon2id · Izolowane organizacje badawcze"
                  : "Argon2id Authentication · Isolated research organizations"}
              </p>
            </div>
          </div>

          {error && (
            <div className="mb-4 rounded-xl border border-rose-500/40 bg-rose-500/10 px-3.5 py-2.5 text-xs text-rose-200">
              {error}
            </div>
          )}

          <form onSubmit={handleSubmit} className="space-y-4">
            <div>
              <label className="mb-1.5 block text-xs font-medium text-zinc-300">
                {t.auth.email}
              </label>
              <input
                type="email"
                aria-label="Email"
                required
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                className="w-full rounded-xl border px-3.5 py-2.5 text-sm text-white"
              />
            </div>

            <div>
              <label className="mb-1.5 block text-xs font-medium text-zinc-300">
                {t.auth.password}
              </label>
              <div className="relative">
                <input
                  type={showPassword ? "text" : "password"}
                  aria-label="Password"
                  required
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  className="w-full rounded-xl border px-3.5 py-2.5 pr-10 text-sm text-white"
                />
                <button
                  type="button"
                  onClick={() => setShowPassword((v) => !v)}
                  tabIndex={-1}
                  aria-label="Toggle password visibility"
                  className="absolute right-2.5 top-1/2 -translate-y-1/2 rounded-lg p-1 text-zinc-400 hover:text-zinc-200"
                >
                  {showPassword ? (
                    <EyeOff className="h-4 w-4" />
                  ) : (
                    <Eye className="h-4 w-4" />
                  )}
                </button>
              </div>
            </div>

            <button
              type="submit"
              disabled={busy}
              className="flex w-full items-center justify-center gap-2 rounded-xl bg-gradient-to-r from-indigo-600 via-indigo-500 to-violet-600 px-4 py-2.5 text-sm font-semibold text-white shadow-lg shadow-indigo-600/25 transition hover:brightness-110 disabled:opacity-50"
            >
              <Lock className="h-4 w-4" />
              {busy ? "..." : t.auth.signInBtn}
            </button>
          </form>

          <div className="mt-5 text-center text-xs text-zinc-400">
            <Link
              to="/register"
              className="font-medium text-indigo-300 transition hover:text-indigo-200 hover:underline"
            >
              {t.auth.signUpTitle} →
            </Link>
          </div>
        </div>
      </div>
    </div>
  );
}

export function RegisterPage() {
  const { registerAccount, locale, t } = useAuth();
  const navigate = useNavigate();
  const [displayName, setDisplayName] = useState("");
  const [orgName, setOrgName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    setBusy(true);
    try {
      await registerAccount({
        email,
        password,
        display_name: displayName,
        organization_name: orgName,
        locale,
      });
      navigate("/");
    } catch (err: any) {
      setError(err.message || "Rejestracja nie powiodła się.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="apex-canvas-bg flex min-h-screen items-center justify-center px-4 py-10 text-zinc-100">
      <div className="w-full max-w-md rounded-3xl border border-white/[0.09] bg-gradient-to-b from-[#141928]/95 to-[#0d111b]/95 p-7 shadow-2xl backdrop-blur-xl sm:p-8">
        <div className="mb-6 flex items-center gap-3">
          <ApexPrismLogo size="md" />
          <div>
            <div className="flex items-center gap-1.5 text-xs font-medium text-indigo-400">
              <Sparkles className="h-3.5 w-3.5" />
              APEX ONE 1.0
            </div>
            <h1 className="text-lg font-semibold text-white">
              {t.auth.signUpTitle}
            </h1>
          </div>
        </div>

        {error && (
          <div className="mb-4 rounded-xl border border-rose-500/40 bg-rose-500/10 p-3 text-xs text-rose-200">
            {error}
          </div>
        )}

        <form onSubmit={handleSubmit} className="space-y-3.5">
          <div>
            <label className="mb-1 block text-xs font-medium text-zinc-300">
              {t.auth.displayName}
            </label>
            <input
              required
              value={displayName}
              onChange={(e) => setDisplayName(e.target.value)}
              placeholder="np. Dr Jan Wiśniewski"
              className="w-full rounded-xl border px-3.5 py-2.5 text-sm text-white"
            />
          </div>
          <div>
            <label className="mb-1 block text-xs font-medium text-zinc-300">
              {t.auth.orgName}
            </label>
            <input
              required
              value={orgName}
              onChange={(e) => setOrgName(e.target.value)}
              placeholder="np. Instytut Badań Systemowych"
              className="w-full rounded-xl border px-3.5 py-2.5 text-sm text-white"
            />
          </div>
          <div>
            <label className="mb-1 block text-xs font-medium text-zinc-300">
              {t.auth.email}
            </label>
            <input
              type="email"
              required
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              className="w-full rounded-xl border px-3.5 py-2.5 text-sm text-white"
            />
          </div>
          <div>
            <label className="mb-1 block text-xs font-medium text-zinc-300">
              {t.auth.password} (min. 8 znaków)
            </label>
            <input
              type="password"
              minLength={8}
              required
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              className="w-full rounded-xl border px-3.5 py-2.5 text-sm text-white"
            />
          </div>
          <button
            type="submit"
            disabled={busy}
            className="w-full rounded-xl bg-gradient-to-r from-indigo-600 via-indigo-500 to-violet-600 px-4 py-2.5 text-sm font-semibold text-white shadow-lg shadow-indigo-600/25 transition hover:brightness-110 disabled:opacity-50"
          >
            {busy ? "..." : t.auth.signUpBtn}
          </button>
        </form>

        <div className="mt-5 text-center text-xs text-zinc-400">
          <Link
            to="/login"
            className="font-medium text-indigo-300 hover:text-indigo-200 hover:underline"
          >
            ← {t.auth.signInTitle}
          </Link>
        </div>
      </div>
    </div>
  );
}
