import React, { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { Globe, Lock, ShieldCheck, Sparkles } from "lucide-react";
import { useAuth } from "../context/AuthContext";

const SEEDED_LAB_ACCOUNTS = [
  { email: "admin@apex-one.local", role: "Owner", label: "Dr Anna Kowalska (Owner)" },
  { email: "researcher@apex-one.local", role: "Researcher", label: "Mgr Piotr Nowak (Researcher)" },
  { email: "evaluator@apex-one.local", role: "Evaluator", label: "Dr Ewa Wiśniewska (Evaluator)" },
  { email: "viewer@apex-one.local", role: "Viewer", label: "Tomasz Zieliński (Viewer)" },
];

export function LoginPage() {
  const { loginWithCredentials, locale, setLocale, t } = useAuth();
  const navigate = useNavigate();
  const [email, setEmail] = useState("admin@apex-one.local");
  const [password, setPassword] = useState("ApexResearch!2026");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    setBusy(true);
    try {
      await loginWithCredentials(email, password);
      navigate("/");
    } catch (err: any) {
      setError(err.message || "Logowanie nie powiodło się.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="flex min-h-screen flex-col items-center justify-center bg-[#0b0d12] px-4 py-10 text-zinc-100">
      <div className="mb-4 flex w-full max-w-md justify-end">
        <button
          type="button"
          onClick={() => setLocale(locale === "pl" ? "en" : "pl")}
          className="inline-flex items-center gap-1.5 rounded-lg border border-zinc-800 bg-zinc-900 px-3 py-1 text-xs text-zinc-300 hover:border-indigo-500"
        >
          <Globe className="h-3.5 w-3.5 text-indigo-400" />
          {locale === "pl" ? "Polski (PL)" : "English (EN)"}
        </button>
      </div>

      <div className="w-full max-w-md rounded-2xl border border-zinc-800/90 bg-[#121620] p-7 shadow-2xl">
        <div className="mb-6 flex items-center gap-3">
          <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-gradient-to-br from-indigo-500 to-violet-600 font-bold text-white">
            A1
          </div>
          <div>
            <h1 className="text-lg font-semibold text-zinc-100">
              {t.auth.signInTitle}
            </h1>
            <p className="text-xs text-zinc-400">
              Argon2id · Sesje HttpOnly + CSRF · RBAC · Izolacja Tenantów
            </p>
          </div>
        </div>

        {error && (
          <div className="mb-4 rounded-lg border border-rose-500/40 bg-rose-500/10 p-3 text-xs text-rose-300">
            {error}
          </div>
        )}

        <form onSubmit={handleSubmit} className="space-y-4">
          <div>
            <label className="mb-1 block text-xs font-medium text-zinc-300">
              {t.auth.email}
            </label>
            <input
              type="email"
              required
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              className="w-full rounded-lg border border-zinc-800 bg-zinc-950 px-3 py-2 text-sm text-zinc-100 focus:border-indigo-500 focus:outline-none"
            />
          </div>

          <div>
            <label className="mb-1 block text-xs font-medium text-zinc-300">
              {t.auth.password}
            </label>
            <input
              type="password"
              required
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              className="w-full rounded-lg border border-zinc-800 bg-zinc-950 px-3 py-2 text-sm text-zinc-100 focus:border-indigo-500 focus:outline-none"
            />
          </div>

          <button
            type="submit"
            disabled={busy}
            className="flex w-full items-center justify-center gap-2 rounded-lg bg-indigo-600 px-4 py-2.5 text-sm font-medium text-white hover:bg-indigo-500 disabled:opacity-50"
          >
            <Lock className="h-4 w-4" />
            {busy ? "..." : t.auth.signInBtn}
          </button>
        </form>

        <div className="mt-6 border-t border-zinc-800 pt-4">
          <div className="mb-2 flex items-center gap-1.5 text-xs font-medium text-zinc-400">
            <ShieldCheck className="h-3.5 w-3.5 text-indigo-400" />
            {t.auth.quickAccounts}
          </div>
          <div className="grid grid-cols-1 gap-1.5">
            {SEEDED_LAB_ACCOUNTS.map((acc) => (
              <button
                key={acc.email}
                type="button"
                onClick={() => {
                  setEmail(acc.email);
                  setPassword("ApexResearch!2026");
                }}
                className="flex items-center justify-between rounded-lg border border-zinc-800/90 bg-zinc-900/70 px-3 py-1.5 text-left text-xs text-zinc-300 hover:border-indigo-500/50 hover:bg-zinc-900"
              >
                <span>{acc.label}</span>
                <span className="rounded bg-indigo-500/15 px-1.5 py-0.5 text-[10px] text-indigo-300">
                  {acc.role}
                </span>
              </button>
            ))}
          </div>
        </div>

        <div className="mt-5 text-center text-xs text-zinc-400">
          <Link to="/register" className="text-indigo-400 hover:underline">
            {t.auth.signUpTitle} →
          </Link>
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
    <div className="flex min-h-screen items-center justify-center bg-[#0b0d12] px-4 py-10 text-zinc-100">
      <div className="w-full max-w-md rounded-2xl border border-zinc-800/90 bg-[#121620] p-7 shadow-2xl">
        <div className="mb-5 flex items-center gap-2.5">
          <Sparkles className="h-5 w-5 text-indigo-400" />
          <h1 className="text-lg font-semibold">{t.auth.signUpTitle}</h1>
        </div>

        {error && (
          <div className="mb-4 rounded-lg border border-rose-500/40 bg-rose-500/10 p-3 text-xs text-rose-300">
            {error}
          </div>
        )}

        <form onSubmit={handleSubmit} className="space-y-3.5">
          <div>
            <label className="mb-1 block text-xs text-zinc-300">
              {t.auth.displayName}
            </label>
            <input
              required
              value={displayName}
              onChange={(e) => setDisplayName(e.target.value)}
              placeholder="np. Dr Jan Wiśniewski"
              className="w-full rounded-lg border border-zinc-800 bg-zinc-950 px-3 py-2 text-sm text-zinc-100"
            />
          </div>
          <div>
            <label className="mb-1 block text-xs text-zinc-300">
              {t.auth.orgName}
            </label>
            <input
              required
              value={orgName}
              onChange={(e) => setOrgName(e.target.value)}
              placeholder="np. Instytut Badań Systemowych"
              className="w-full rounded-lg border border-zinc-800 bg-zinc-950 px-3 py-2 text-sm text-zinc-100"
            />
          </div>
          <div>
            <label className="mb-1 block text-xs text-zinc-300">
              {t.auth.email}
            </label>
            <input
              type="email"
              required
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              className="w-full rounded-lg border border-zinc-800 bg-zinc-950 px-3 py-2 text-sm text-zinc-100"
            />
          </div>
          <div>
            <label className="mb-1 block text-xs text-zinc-300">
              {t.auth.password} (min. 8 znaków)
            </label>
            <input
              type="password"
              minLength={8}
              required
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              className="w-full rounded-lg border border-zinc-800 bg-zinc-950 px-3 py-2 text-sm text-zinc-100"
            />
          </div>
          <button
            type="submit"
            disabled={busy}
            className="w-full rounded-lg bg-indigo-600 px-4 py-2.5 text-sm font-medium text-white hover:bg-indigo-500 disabled:opacity-50"
          >
            {busy ? "..." : t.auth.signUpBtn}
          </button>
        </form>

        <div className="mt-4 text-center text-xs text-zinc-400">
          <Link to="/login" className="text-indigo-400 hover:underline">
            ← {t.auth.signInTitle}
          </Link>
        </div>
      </div>
    </div>
  );
}
