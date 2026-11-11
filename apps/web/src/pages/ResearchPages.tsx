import React, { useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import {
  Activity,
  AlertTriangle,
  ArrowRight,
  BookOpen,
  CheckCircle2,
  Cpu,
  Download,
  FileText,
  FolderPlus,
  Network,
  Play,
  RefreshCw,
  Search,
  ShieldAlert,
  Sparkles,
  Trash2,
  Upload,
} from "lucide-react";
import { apiFetch } from "../api";
import { Badge, Card, PageHeader, StatCard } from "../components/Layout";
import { useAuth } from "../context/AuthContext";

const RESEARCH_STATES = [
  "CREATED",
  "PLANNING",
  "RESEARCHING",
  "ANALYZING",
  "HYPOTHESIS_FORMATION",
  "EXPERIMENT_DESIGN",
  "AWAITING_APPROVAL",
  "EXECUTING",
  "VERIFYING",
  "REPORTING",
  "COMPLETED",
  "FAILED",
  "CANCELLED",
];

export function CommandCenterPage() {
  const { scope, t } = useAuth();
  const navigate = useNavigate();
  const [summary, setSummary] = useState<any>(null);
  const [projects, setProjects] = useState<any[]>([]);
  const [envStatus, setEnvStatus] = useState<any>(null);
  const [quickObjective, setQuickObjective] = useState("");
  const [launching, setLaunching] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const load = async () => {
    try {
      const [s, p, e] = await Promise.all([
        apiFetch(`/api/v1/analytics/summary?scope=${scope}`),
        apiFetch(`/api/v1/research/projects?scope=${scope}`),
        apiFetch("/api/v1/environment/status"),
      ]);
      setSummary(s);
      setProjects(p.items || []);
      setEnvStatus(e);
    } catch (ex: any) {
      setErr(ex.message);
    }
  };

  useEffect(() => {
    void load();
  }, [scope]);

  const handleQuickRun = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!quickObjective.trim()) return;
    setLaunching(true);
    setErr(null);
    try {
      await apiFetch("/api/v1/runs", {
        method: "POST",
        body: JSON.stringify({
          objective: quickObjective,
          execution_mode: "multi_agent_team",
          research_project_id: projects[0]?.id || null,
        }),
      });
      setQuickObjective("");
      navigate("/runs");
    } catch (ex: any) {
      setErr(ex.message);
    } finally {
      setLaunching(false);
    }
  };

  const stageProgressPct = (stateName: string) => {
    const idx = RESEARCH_STATES.indexOf(stateName);
    if (stateName === "COMPLETED") return 100;
    if (idx < 0) return 15;
    return Math.min(100, Math.max(12, Math.round(((idx + 1) / 11) * 100)));
  };

  return (
    <div className="space-y-6">
      <PageHeader
        title={t.nav.commandCenter}
        subtitle="Operacyjny przegląd projektów badawczych, orkiestracji wieloagentowej, dowodów i nadzoru R0–R3."
        actions={
          <button
            onClick={() => void load()}
            className="inline-flex items-center gap-1.5 rounded-xl border border-white/10 bg-white/[0.04] px-3.5 py-2 text-xs font-medium text-zinc-200 transition hover:border-indigo-400/50 hover:bg-white/[0.08]"
          >
            <RefreshCw className="h-3.5 w-3.5 text-indigo-400" />
            {t.common.refresh}
          </button>
        }
      />

      {err && (
        <div className="rounded-xl border border-rose-500/40 bg-rose-500/10 p-3 text-xs text-rose-200">
          {err}
        </div>
      )}

      {/* Telemetry Banner */}
      {envStatus && (
        <div className="relative overflow-hidden rounded-2xl border border-indigo-400/25 bg-gradient-to-r from-indigo-950/40 via-[#111626]/95 to-violet-950/30 px-5 py-4 shadow-lg">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div className="space-y-1">
              <div className="flex items-center gap-2 text-[11px] font-semibold uppercase tracking-widest text-indigo-300">
                <Sparkles className="h-3.5 w-3.5 text-indigo-400" />
                Status Środowiska Wykonawczego: {envStatus.verdict}
              </div>
              <p className="text-xs text-zinc-300/90">
                Docelowa baza produkcyjna: <b className="text-white">PostgreSQL 16 + pgvector + RLS</b> ·
                Sandbox nietestowanego kodu:{" "}
                <span className="text-amber-300">
                  {envStatus.experiment_sandbox?.isolation_verified
                    ? "ZWERYFIKOWANY"
                    : "ZABLOKOWANY (Polityka Bezpieczeństwa)"}
                </span>{" "}
                · Węzeł Lokalny:{" "}
                <span className="text-zinc-300">
                  {envStatus.hybrid_local_node?.user_machine_connected
                    ? "POŁĄCZONY"
                    : "TRYB CHMUROWY ARENA"}
                </span>
              </p>
            </div>
            <div className="flex flex-wrap gap-2">
              <Badge tone={envStatus.postgresql?.connected ? "emerald" : "amber"}>
                PG: {envStatus.postgresql?.connected ? "ONLINE" : "TRYB ARENA IN-MEMORY"}
              </Badge>
              <Badge tone="indigo">Outbox + Checkpointy: AKTYWNE</Badge>
            </div>
          </div>
        </div>
      )}

      {/* Executive KPI Cards */}
      {summary && (
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
          <StatCard
            label="Wykonania Agentów (DAG)"
            value={summary.agent_execution_count}
            icon={Activity}
            tone="emerald"
            detail={
              <span className="text-emerald-300">
                Sukces: {summary.successful_executions} · Śr. {summary.avg_execution_time_ms} ms
              </span>
            }
          />
          <StatCard
            label="Zużyte Tokeny i Koszt Modeli"
            value={`${summary.total_tokens_consumed.toLocaleString()} tok`}
            icon={Cpu}
            tone="indigo"
            detail={
              <span>
                Koszt: <b className="text-indigo-300">${summary.total_model_cost_usd.toFixed(4)}</b> ({summary.budget_utilization_pct}% budżetu)
              </span>
            }
          />
          <StatCard
            label="Wywołania Narzędzi (Idempotentne)"
            value={summary.tool_invocation_count}
            icon={Sparkles}
            tone="violet"
            detail={
              <span>
                Przepływy: {summary.workflow_succeeded}/{summary.workflow_total} ukończone
              </span>
            }
          />
          <StatCard
            label="Oczekujące Zatwierdzenia (R2/R3)"
            value={summary.pending_approvals_count}
            icon={ShieldAlert}
            tone="amber"
            footer={
              <Link
                to="/approvals"
                className="inline-flex items-center gap-1 text-xs font-medium text-indigo-300 transition hover:text-indigo-200 hover:underline"
              >
                Otwórz Centrum Zatwierdzeń <ArrowRight className="h-3 w-3" />
              </Link>
            }
          />
        </div>
      )}

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-3">
        <Card className="lg:col-span-2">
          <div className="flex flex-wrap items-start justify-between gap-2">
            <div>
              <h2 className="text-base font-semibold text-white">
                Szybkie Uruchomienie Zespołu Badawczego (8 Wyspecjalizowanych Agentów)
              </h2>
              <p className="mt-1 text-xs text-zinc-400">
                Orkiestracja DAG z trwałymi punktami kontrolnymi oraz idempotentnym rejestrem narzędzi.
              </p>
            </div>
            <Badge tone="violet">8 Ról AgentDNA</Badge>
          </div>

          {/* Interactive 8-Agent Pipeline Ribbon */}
          <div className="mt-4 flex flex-wrap items-center gap-1.5 rounded-xl border border-white/[0.06] bg-black/30 px-3 py-2.5 text-[11px]">
            {[
              "1. Supervisor",
              "2. Planner",
              "3. Source Scout",
              "4. Evidence",
              "5. Data",
              "6. Experiment",
              "7. Critic",
              "8. Writer",
            ].map((roleName, idx, arr) => (
              <React.Fragment key={roleName}>
                <span className="rounded-lg border border-indigo-400/20 bg-indigo-500/10 px-2 py-0.5 font-medium text-indigo-200">
                  {roleName}
                </span>
                {idx < arr.length - 1 && (
                  <span className="text-zinc-600">→</span>
                )}
              </React.Fragment>
            ))}
          </div>

          <form onSubmit={handleQuickRun} className="mt-4 flex flex-col gap-3 sm:flex-row">
            <input
              required
              value={quickObjective}
              onChange={(e) => setQuickObjective(e.target.value)}
              placeholder="Wprowadź cel badawczy lub hipotezę do weryfikacji..."
              className="flex-1 rounded-xl border px-3.5 py-2.5 text-sm text-white"
            />
            <button
              type="submit"
              disabled={launching}
              className="inline-flex items-center justify-center gap-2 rounded-xl bg-gradient-to-r from-indigo-600 to-violet-600 px-5 py-2.5 text-xs font-semibold text-white shadow-lg shadow-indigo-600/25 transition hover:brightness-110 disabled:opacity-50"
            >
              <Play className="h-3.5 w-3.5" />
              {launching ? "Wykonywanie..." : "Uruchom Zespół 8 Agentów"}
            </button>
          </form>

          {/* Quick Prompt Suggestions */}
          <div className="mt-2.5 flex flex-wrap items-center gap-1.5 text-[11px]">
            <span className="text-zinc-500">Przykłady:</span>
            {[
              "Weryfikacja sprzeczności w korpusie dowodowym RAG",
              "Analiza kompromisu koszt-trafność zespołów wieloagentowych",
            ].map((sample) => (
              <button
                key={sample}
                type="button"
                onClick={() => setQuickObjective(sample)}
                className="rounded-full border border-white/[0.07] bg-white/[0.03] px-2.5 py-0.5 text-zinc-400 transition hover:border-indigo-400/35 hover:text-zinc-200"
              >
                {sample}
              </button>
            ))}
          </div>

          <div className="mt-6 border-t border-white/[0.06] pt-5">
            <div className="mb-3 flex items-center justify-between">
              <h3 className="text-xs font-semibold uppercase tracking-wider text-zinc-400">
                Aktywne Projekty Badawcze
              </h3>
              <Link to="/projects" className="text-xs font-medium text-indigo-300 hover:underline">
                Wszystkie ({projects.length}) →
              </Link>
            </div>
            <div className="space-y-2.5">
              {projects.slice(0, 4).map((p) => {
                const pct = stageProgressPct(p.state);
                return (
                  <Link
                    key={p.id}
                    to={`/projects/${p.id}`}
                    className="group block rounded-xl border border-white/[0.07] bg-white/[0.025] px-4 py-3 transition-all hover:border-indigo-400/40 hover:bg-white/[0.045]"
                  >
                    <div className="flex items-center justify-between gap-3">
                      <div className="min-w-0">
                        <div className="truncate text-sm font-semibold text-zinc-100 group-hover:text-white">
                          {p.title}
                        </div>
                        <div className="mt-0.5 truncate text-xs text-zinc-400">
                          Przestrzeń: <span className="text-zinc-300">{p.workspace_scope.toUpperCase()}</span> · Wersja v{p.version}
                        </div>
                      </div>
                      <Badge tone={p.state === "COMPLETED" ? "emerald" : "indigo"}>
                        {p.state}
                      </Badge>
                    </div>
                    <div className="mt-2.5 h-1.5 w-full overflow-hidden rounded-full bg-white/[0.06]">
                      <div
                        className={`h-full rounded-full transition-all ${
                          p.state === "COMPLETED"
                            ? "bg-gradient-to-r from-emerald-500 to-teal-400"
                            : "bg-gradient-to-r from-indigo-500 to-violet-500"
                        }`}
                        style={{ width: `${pct}%` }}
                      />
                    </div>
                  </Link>
                );
              })}
            </div>
          </div>
        </Card>

        <Card className="flex flex-col justify-between">
          <div>
            <div className="flex items-center justify-between gap-2">
              <h2 className="text-base font-semibold text-white">
                Benchmark Porównawczy
              </h2>
              <Badge tone="indigo">Part IX</Badge>
            </div>
            <p className="mt-1 text-xs text-zinc-400">
              Rzeczywiste wyniki Single-Agent vs Multi-Agent na korpusie syntetycznym SRC-SYN-01..04:
            </p>
            <div className="mt-4 space-y-3.5">
              {(summary?.benchmark_comparisons || []).slice(0, 2).map((b: any) => {
                const acc = Math.round((b.scores?.research_accuracy || 0) * 100);
                const ev = Math.round((b.scores?.evidence_quality || 0) * 100);
                const cost = Math.round((b.scores?.cost_efficiency || 0) * 100);
                return (
                  <div
                    key={b.id}
                    className="rounded-xl border border-white/[0.07] bg-white/[0.025] p-3.5 text-xs"
                  >
                    <div className="flex items-center justify-between gap-2">
                      <span className="font-semibold text-white">{b.label}</span>
                      <span className="rounded-md bg-indigo-500/15 px-2 py-0.5 font-mono text-[11px] text-indigo-300 tabular-nums">
                        {b.tokens_used} tok
                      </span>
                    </div>
                    <div className="mt-3 space-y-2">
                      <div>
                        <div className="mb-1 flex justify-between text-[11px] text-zinc-400">
                          <span>Trafność badawcza</span>
                          <b className="text-zinc-100 tabular-nums">{acc}%</b>
                        </div>
                        <div className="h-1.5 rounded-full bg-white/[0.06]">
                          <div
                            className="h-full rounded-full bg-indigo-400"
                            style={{ width: `${acc}%` }}
                          />
                        </div>
                      </div>
                      <div>
                        <div className="mb-1 flex justify-between text-[11px] text-zinc-400">
                          <span>Jakość dowodów</span>
                          <b className="text-zinc-100 tabular-nums">{ev}%</b>
                        </div>
                        <div className="h-1.5 rounded-full bg-white/[0.06]">
                          <div
                            className="h-full rounded-full bg-emerald-400"
                            style={{ width: `${ev}%` }}
                          />
                        </div>
                      </div>
                      <div>
                        <div className="mb-1 flex justify-between text-[11px] text-zinc-400">
                          <span>Efektywność kosztowa</span>
                          <b className="text-zinc-100 tabular-nums">{cost}%</b>
                        </div>
                        <div className="h-1.5 rounded-full bg-white/[0.06]">
                          <div
                            className="h-full rounded-full bg-amber-400"
                            style={{ width: `${cost}%` }}
                          />
                        </div>
                      </div>
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
          <Link
            to="/evaluations"
            className="mt-5 inline-flex items-center justify-center gap-1.5 rounded-xl border border-indigo-400/30 bg-indigo-500/10 px-3.5 py-2 text-xs font-medium text-indigo-200 transition hover:bg-indigo-500/20"
          >
            Pełna macierz 9 kategorii ewaluacji <ArrowRight className="h-3.5 w-3.5" />
          </Link>
        </Card>
      </div>
    </div>
  );
}

export function ResearchProjectsPage() {
  const { scope, t } = useAuth();
  const navigate = useNavigate();
  const [projects, setProjects] = useState<any[]>([]);
  const [title, setTitle] = useState("");
  const [hypothesis, setHypothesis] = useState("");
  const [scopeSummary, setScopeSummary] = useState("");
  const [err, setErr] = useState<string | null>(null);

  const load = async () => {
    const res = await apiFetch(`/api/v1/research/projects?scope=${scope}`);
    setProjects(res.items || []);
  };

  useEffect(() => {
    void load();
  }, [scope]);

  const handleCreate = async (e: React.FormEvent) => {
    e.preventDefault();
    setErr(null);
    try {
      const created = await apiFetch("/api/v1/research/projects", {
        method: "POST",
        body: JSON.stringify({
          title,
          hypothesis,
          scope_summary: scopeSummary,
        }),
      });
      setTitle("");
      setHypothesis("");
      setScopeSummary("");
      navigate(`/projects/${created.id}`);
    } catch (ex: any) {
      setErr(ex.message);
    }
  };

  return (
    <div className="space-y-6">
      <PageHeader
        title={t.nav.projects}
        subtitle="Zarządzaj pełnym cyklem życia badań w oparciu o walidowaną 13-stanową maszynę stanów."
      />

      {err && (
        <div className="rounded-lg border border-rose-500/40 bg-rose-500/10 p-3 text-xs text-rose-300">
          {err}
        </div>
      )}

      <Card>
        <h2 className="mb-3 flex items-center gap-2 text-sm font-semibold text-zinc-100">
          <FolderPlus className="h-4 w-4 text-indigo-400" />
          Nowy Projekt Badawczy (Przestrzeń Produkcyjna)
        </h2>
        <form onSubmit={handleCreate} className="grid grid-cols-1 gap-3 md:grid-cols-3">
          <input
            required
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            placeholder="Tytuł projektu badawczego..."
            className="rounded-lg border border-zinc-800 bg-zinc-950 px-3 py-2 text-xs text-zinc-100"
          />
          <input
            value={hypothesis}
            onChange={(e) => setHypothesis(e.target.value)}
            placeholder="Hipoteza robocza..."
            className="rounded-lg border border-zinc-800 bg-zinc-950 px-3 py-2 text-xs text-zinc-100"
          />
          <div className="flex gap-2">
            <input
              value={scopeSummary}
              onChange={(e) => setScopeSummary(e.target.value)}
              placeholder="Zakres metodologiczny..."
              className="flex-1 rounded-lg border border-zinc-800 bg-zinc-950 px-3 py-2 text-xs text-zinc-100"
            />
            <button
              type="submit"
              className="rounded-lg bg-indigo-600 px-4 py-2 text-xs font-semibold text-white hover:bg-indigo-500"
            >
              {t.common.create}
            </button>
          </div>
        </form>
      </Card>

      <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
        {projects.map((p) => (
          <Card key={p.id} className="flex flex-col justify-between">
            <div>
              <div className="flex items-start justify-between gap-2">
                <Link
                  to={`/projects/${p.id}`}
                  className="text-base font-semibold text-zinc-100 hover:text-indigo-300"
                >
                  {p.title}
                </Link>
                <Badge tone={p.workspace_scope === "demo" ? "violet" : "emerald"}>
                  {p.workspace_scope.toUpperCase()}
                </Badge>
              </div>
              <p className="mt-2 text-xs text-zinc-400">
                {p.scope_summary || p.hypothesis || "Brak opisu zakresu."}
              </p>
            </div>
            <div className="mt-4 flex items-center justify-between border-t border-zinc-800/80 pt-3 text-xs">
              <Badge tone="indigo">{p.state}</Badge>
              <Link
                to={`/projects/${p.id}`}
                className="font-medium text-indigo-400 hover:underline"
              >
                Otwórz szczegóły projektu →
              </Link>
            </div>
          </Card>
        ))}
      </div>
    </div>
  );
}

export function ResearchProjectDetailPage() {
  const { projectId } = useParams();
  const [detail, setDetail] = useState<any>(null);
  const [nextState, setNextState] = useState("PLANNING");
  const [briefObj, setBriefObj] = useState("");
  const [methodology, setMethodology] = useState("");
  const [questionsRaw, setQuestionsRaw] = useState("");
  const [msg, setMsg] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);

  const load = async () => {
    if (!projectId) return;
    try {
      const d = await apiFetch(`/api/v1/research/projects/${projectId}`);
      setDetail(d);
      const lastBrief = d.briefs?.[d.briefs.length - 1];
      if (lastBrief) {
        setBriefObj(lastBrief.objective);
        setMethodology(lastBrief.methodology);
      }
      setQuestionsRaw(
        (d.questions || []).map((q: any) => q.question_text).join("\n")
      );
    } catch (ex: any) {
      setErr(ex.message);
    }
  };

  useEffect(() => {
    void load();
  }, [projectId]);

  const handleTransition = async () => {
    setErr(null);
    setMsg(null);
    try {
      await apiFetch(`/api/v1/research/projects/${projectId}/transition`, {
        method: "POST",
        body: JSON.stringify({ target_state: nextState }),
      });
      setMsg(`Przejście stanu do ${nextState} zatwierdzone.`);
      await load();
    } catch (ex: any) {
      setErr(ex.message);
    }
  };

  const handleSaveBrief = async (e: React.FormEvent) => {
    e.preventDefault();
    setErr(null);
    try {
      await apiFetch(`/api/v1/research/projects/${projectId}/brief`, {
        method: "POST",
        body: JSON.stringify({
          objective: briefObj,
          scope_summary: detail.scope_summary || "",
          methodology,
          expected_deliverables: "Raport z powiązaniami dowodowymi",
          questions: questionsRaw.split("\n").filter((x) => x.trim()),
        }),
      });
      setMsg("Zapisano nową wersję Research Brief oraz pytań badawczych.");
      await load();
    } catch (ex: any) {
      setErr(ex.message);
    }
  };

  const handleExportReport = async () => {
    const md = await apiFetch<string>(
      `/api/v1/research/projects/${projectId}/export`,
      { rawText: true }
    );
    const blob = new Blob([md], { type: "text/markdown;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `apex-one-report-${projectId?.slice(0, 8)}.md`;
    a.click();
    URL.revokeObjectURL(url);
  };

  if (!detail) {
    return <div className="text-sm text-zinc-400">Ładowanie projektu...</div>;
  }

  return (
    <div className="space-y-6">
      <PageHeader
        title={detail.title}
        subtitle={`Aktualny stan maszyny badawczej: ${detail.state} · Wersja v${detail.version} · Przestrzeń: ${detail.workspace_scope}`}
        actions={
          <button
            onClick={() => void handleExportReport()}
            className="inline-flex items-center gap-1.5 rounded-lg bg-indigo-600 px-3.5 py-2 text-xs font-semibold text-white hover:bg-indigo-500"
          >
            <Download className="h-3.5 w-3.5" />
            Eksportuj Raport (.md)
          </button>
        }
      />

      {err && (
        <div className="rounded-lg border border-rose-500/40 bg-rose-500/10 p-3 text-xs text-rose-300">
          {err}
        </div>
      )}
      {msg && (
        <div className="rounded-lg border border-emerald-500/40 bg-emerald-500/10 p-3 text-xs text-emerald-300">
          {msg}
        </div>
      )}

      <Card>
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <div className="text-xs font-semibold text-zinc-200">
              Walidowana Maszyna Stanów Badawczych (13 stanów)
            </div>
            <div className="text-xs text-zinc-400">
              Niedozwolone skoki stanów są odrzucane przez backend kodem HTTP 422.
            </div>
          </div>
          <div className="flex items-center gap-2">
            <select
              value={nextState}
              onChange={(e) => setNextState(e.target.value)}
              className="rounded-lg border border-zinc-800 bg-zinc-950 px-3 py-1.5 text-xs text-zinc-100"
            >
              {RESEARCH_STATES.map((st) => (
                <option key={st} value={st}>
                  {st}
                </option>
              ))}
            </select>
            <button
              onClick={() => void handleTransition()}
              className="rounded-lg border border-indigo-500/50 bg-indigo-500/15 px-3 py-1.5 text-xs font-medium text-indigo-200 hover:bg-indigo-500/25"
            >
              Zmień stan
            </button>
          </div>
        </div>
      </Card>

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
        <Card>
          <h2 className="mb-3 text-sm font-semibold text-zinc-100">
            Research Brief i Pytania Badawcze (Wersjonowane)
          </h2>
          <form onSubmit={handleSaveBrief} className="space-y-3">
            <div>
              <label className="mb-1 block text-xs text-zinc-400">Cel główny</label>
              <input
                required
                value={briefObj}
                onChange={(e) => setBriefObj(e.target.value)}
                className="w-full rounded-lg border border-zinc-800 bg-zinc-950 px-3 py-2 text-xs text-zinc-100"
              />
            </div>
            <div>
              <label className="mb-1 block text-xs text-zinc-400">Metodologia</label>
              <textarea
                rows={2}
                value={methodology}
                onChange={(e) => setMethodology(e.target.value)}
                className="w-full rounded-lg border border-zinc-800 bg-zinc-950 px-3 py-2 text-xs text-zinc-100"
              />
            </div>
            <div>
              <label className="mb-1 block text-xs text-zinc-400">
                Pytania badawcze (jedno w wierszu)
              </label>
              <textarea
                rows={3}
                value={questionsRaw}
                onChange={(e) => setQuestionsRaw(e.target.value)}
                className="w-full rounded-lg border border-zinc-800 bg-zinc-950 px-3 py-2 text-xs text-zinc-100"
              />
            </div>
            <button
              type="submit"
              className="rounded-lg bg-indigo-600 px-3.5 py-1.5 text-xs font-semibold text-white hover:bg-indigo-500"
            >
              Zapisz nową wersję Briefu
            </button>
          </form>
        </Card>

        <Card>
          <h2 className="mb-3 text-sm font-semibold text-zinc-100">
            Powiązane Źródła ({detail.sources?.length || 0}) i Twierdzenia ({detail.claims?.length || 0})
          </h2>
          <div className="space-y-2 max-h-64 overflow-y-auto pr-1">
            {(detail.claims || []).map((c: any) => (
              <div
                key={c.id}
                className="rounded-lg border border-zinc-800 bg-zinc-900/60 p-2.5 text-xs"
              >
                <div className="flex items-center justify-between gap-2">
                  <Badge
                    tone={
                      c.status === "supported"
                        ? "emerald"
                        : c.status === "contradicted"
                        ? "rose"
                        : "amber"
                    }
                  >
                    {c.status}
                  </Badge>
                  <span className="text-[11px] text-zinc-500">
                    Pokrycie empiryczne: {c.confidence_assessment?.empirical_coverage_ratio ?? 0}
                  </span>
                </div>
                <p className="mt-1.5 text-zinc-200">{c.statement}</p>
              </div>
            ))}
          </div>
        </Card>
      </div>

      {(detail.reports || []).length > 0 && (
        <Card>
          <h2 className="mb-3 flex items-center gap-2 text-sm font-semibold text-zinc-100">
            <FileText className="h-4 w-4 text-indigo-400" />
            Wygenerowane Raporty Badawcze ({detail.reports.length})
          </h2>
          <pre className="max-h-96 overflow-y-auto whitespace-pre-wrap rounded-lg border border-zinc-800 bg-zinc-950 p-4 text-xs leading-relaxed text-zinc-200">
            {detail.reports[detail.reports.length - 1].markdown_body}
          </pre>
        </Card>
      )}
    </div>
  );
}

export function SourceRegistryPage() {
  const { scope, t } = useAuth();
  const [sources, setSources] = useState<any[]>([]);
  const [projects, setProjects] = useState<any[]>([]);
  const [sourceType, setSourceType] = useState<"MD" | "TXT" | "URL" | "PDF" | "DOCX">("MD");
  const [title, setTitle] = useState("");
  const [uri, setUri] = useState("");
  const [rawText, setRawText] = useState("");
  const [contentBase64, setContentBase64] = useState<string | null>(null);
  const [projectId, setProjectId] = useState("");
  const [feedback, setFeedback] = useState<string | null>(null);
  const [liveUrl, setLiveUrl] = useState("");
  const [scholarQuery, setScholarQuery] = useState("");
  const [discovered, setDiscovered] = useState<any[]>([]);

  const load = async () => {
    const [s, p] = await Promise.all([
      apiFetch(`/api/v1/sources?scope=${scope}`),
      apiFetch(`/api/v1/research/projects?scope=${scope}`),
    ]);
    setSources(s.items || []);
    setProjects(p.items || []);
  };

  useEffect(() => {
    void load();
  }, [scope]);

  const handleFileSelect = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    setTitle((prev) => prev || file.name);
    setUri(file.name);
    const ext = file.name.split(".").pop()?.toUpperCase();
    if (ext === "PDF" || ext === "DOCX") {
      setSourceType(ext);
      const reader = new FileReader();
      reader.onload = () => {
        const result = String(reader.result || "");
        const b64 = result.includes(",") ? result.split(",")[1] : result;
        setContentBase64(b64);
      };
      reader.readAsDataURL(file);
    } else {
      setSourceType(ext === "MD" ? "MD" : "TXT");
      const reader = new FileReader();
      reader.onload = () => setRawText(String(reader.result || ""));
      reader.readAsText(file);
    }
  };

  const handleRegister = async (e: React.FormEvent) => {
    e.preventDefault();
    setFeedback(null);
    const res = await apiFetch("/api/v1/sources", {
      method: "POST",
      body: JSON.stringify({
        source_type: sourceType,
        title,
        uri_or_filename: uri || "document.md",
        raw_text: rawText || undefined,
        content_base64: contentBase64 || undefined,
        research_project_id: projectId || null,
      }),
    });
    setFeedback(
      res.duplicate_detected
        ? `Wykryto duplikat po sumie SHA-256 (${res.checksum_sha256.slice(0, 12)}...) — połączono z istniejącym rekordem.`
        : `Zarejestrowano źródło (SHA-256: ${res.checksum_sha256.slice(0, 12)}...) i zindeksowano w Pamięci Współdzielonej.`
    );
    setTitle("");
    setRawText("");
    setContentBase64(null);
    await load();
  };

  const handleFetchUrl = async (e: React.FormEvent) => {
    e.preventDefault();
    setFeedback(null);
    try {
      const res = await apiFetch("/api/v1/sources/fetch-url", {
        method: "POST",
        body: JSON.stringify({
          url: liveUrl,
          research_project_id: projectId || null,
        }),
      });
      setFeedback(
        `Pobrano i zweryfikowano stronę URL '${res.title}' (SHA-256: ${res.checksum_sha256.slice(0, 12)}...).`
      );
      setLiveUrl("");
      await load();
    } catch (ex: any) {
      setFeedback(`Błąd pobierania URL: ${ex.message}`);
    }
  };

  const handleDiscoverLiterature = async (e: React.FormEvent) => {
    e.preventDefault();
    setFeedback(null);
    try {
      const res = await apiFetch("/api/v1/sources/discover", {
        method: "POST",
        body: JSON.stringify({
          query: scholarQuery,
          connectors: ["openalex", "arxiv", "crossref", "github"],
          max_per_connector: 4,
          auto_register_project_id: projectId || null,
        }),
      });
      setDiscovered(res.results || []);
      const statuses = Object.entries(res.connector_statuses || {})
        .map(([k, v]) => `${k}: ${v}`)
        .join(" · ");
      setFeedback(
        `Wyszukiwanie zakończone (${res.total_found} publikacji/repozytoriów; zaimportowano: ${(res.registered_sources || []).length}). Status konektorów: ${statuses}`
      );
      await load();
    } catch (ex: any) {
      setFeedback(`Błąd wyszukiwania: ${ex.message}`);
    }
  };

  return (
    <div className="space-y-6">
      <PageHeader
        title={t.nav.sources}
        subtitle="Rejestr źródeł pierwotnych (URL, PDF, DOCX, TXT, MD), konektory literatury naukowej (OpenAlex, arXiv, Crossref, GitHub) i ochrona SSRF/Prompt Injection."
      />

      {feedback && (
        <div className="rounded-lg border border-indigo-500/40 bg-indigo-500/10 p-3 text-xs text-indigo-200">
          {feedback}
        </div>
      )}

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <Card>
          <h2 className="mb-2 text-sm font-semibold text-zinc-100">
            Konektory Literatury Naukowej (OpenAlex · arXiv · Crossref · GitHub)
          </h2>
          <form onSubmit={handleDiscoverLiterature} className="flex gap-2 text-xs">
            <input
              required
              value={scholarQuery}
              onChange={(e) => setScholarQuery(e.target.value)}
              placeholder="Zapytanie naukowe (np. multi-agent verification rag)..."
              className="flex-1 rounded-lg border border-zinc-800 bg-zinc-950 px-3 py-2 text-zinc-100"
            />
            <button
              type="submit"
              className="rounded-lg bg-indigo-600 px-4 py-2 font-semibold text-white hover:bg-indigo-500"
            >
              Szukaj Źródeł
            </button>
          </form>
          {discovered.length > 0 && (
            <div className="mt-3 max-h-40 space-y-1.5 overflow-y-auto text-xs">
              {discovered.map((d, idx) => (
                <div
                  key={idx}
                  className="rounded border border-zinc-800 bg-zinc-950/70 px-2.5 py-1.5 text-zinc-300"
                >
                  <span className="font-semibold text-indigo-300">[{d.connector}]</span>{" "}
                  {d.title}
                </div>
              ))}
            </div>
          )}
        </Card>

        <Card>
          <h2 className="mb-2 text-sm font-semibold text-zinc-100">
            Pobieranie URL na Żywo (SSRF-Safe HTML/PDF Snapshot)
          </h2>
          <form onSubmit={handleFetchUrl} className="flex gap-2 text-xs">
            <input
              required
              value={liveUrl}
              onChange={(e) => setLiveUrl(e.target.value)}
              placeholder="https://github.com/... lub artykuł badawczy..."
              className="flex-1 rounded-lg border border-zinc-800 bg-zinc-950 px-3 py-2 text-zinc-100"
            />
            <button
              type="submit"
              className="rounded-lg bg-emerald-600 px-4 py-2 font-semibold text-white hover:bg-emerald-500"
            >
              Pobierz URL
            </button>
          </form>
        </Card>
      </div>

      <Card>
        <h2 className="mb-3 flex items-center gap-2 text-sm font-semibold text-zinc-100">
          <Upload className="h-4 w-4 text-indigo-400" />
          Zarejestruj lub Zaimportuj Dokument Źródłowy
        </h2>
        <form onSubmit={handleRegister} className="space-y-3">
          <div className="grid grid-cols-1 gap-3 md:grid-cols-4">
            <select
              value={sourceType}
              onChange={(e) => setSourceType(e.target.value as any)}
              className="rounded-lg border border-zinc-800 bg-zinc-950 px-3 py-2 text-xs text-zinc-100"
            >
              <option value="MD">Markdown (MD)</option>
              <option value="TXT">Tekst (TXT)</option>
              <option value="PDF">Dokument PDF</option>
              <option value="DOCX">Dokument DOCX</option>
              <option value="URL">Zasób URL</option>
            </select>
            <input
              required
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              placeholder="Tytuł źródła..."
              className="rounded-lg border border-zinc-800 bg-zinc-950 px-3 py-2 text-xs text-zinc-100"
            />
            <input
              required
              value={uri}
              onChange={(e) => setUri(e.target.value)}
              placeholder="URI lub nazwa pliku..."
              className="rounded-lg border border-zinc-800 bg-zinc-950 px-3 py-2 text-xs text-zinc-100"
            />
            <select
              value={projectId}
              onChange={(e) => setProjectId(e.target.value)}
              className="rounded-lg border border-zinc-800 bg-zinc-950 px-3 py-2 text-xs text-zinc-100"
            >
              <option value="">-- Przypisz do projektu (opcjonalnie) --</option>
              {projects.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.title}
                </option>
              ))}
            </select>
          </div>

          <div className="flex flex-wrap items-center gap-3">
            <label className="cursor-pointer rounded-lg border border-zinc-700 bg-zinc-900 px-3 py-1.5 text-xs text-zinc-200 hover:border-indigo-500">
              Wybierz plik lokalny (.pdf, .docx, .md, .txt)
              <input
                type="file"
                accept=".pdf,.docx,.md,.txt"
                onChange={handleFileSelect}
                className="hidden"
              />
            </label>
            <span className="text-xs text-zinc-500">
              lub wklej treść dokumentu poniżej (lokalny silnik dokumentów działa w 100% bez płatnych API):
            </span>
          </div>

          <textarea
            rows={3}
            value={rawText}
            onChange={(e) => setRawText(e.target.value)}
            placeholder="Treść dokumentu źródłowego / abstrakt..."
            className="w-full rounded-lg border border-zinc-800 bg-zinc-950 px-3 py-2 text-xs text-zinc-100"
          />

          <button
            type="submit"
            className="rounded-lg bg-indigo-600 px-4 py-2 text-xs font-semibold text-white hover:bg-indigo-500"
          >
            Zarejestruj Źródło i Oblicz SHA-256
          </button>
        </form>
      </Card>

      <div className="space-y-3">
        {sources.map((s) => (
          <Card key={s.id}>
            <div className="flex flex-wrap items-start justify-between gap-2">
              <div>
                <div className="flex items-center gap-2">
                  <Badge tone="indigo">{s.source_type}</Badge>
                  <span className="text-sm font-semibold text-zinc-100">
                    {s.title}
                  </span>
                </div>
                <div className="mt-1 text-xs text-zinc-400">
                  URI: {s.uri_or_filename} · SHA-256:{" "}
                  <code className="text-zinc-300">{s.checksum_sha256.slice(0, 20)}...</code>
                </div>
              </div>
              <div className="flex items-center gap-2">
                {s.metadata_json?.safety_flags?.length > 0 && (
                  <Badge tone="rose">
                    <ShieldAlert className="h-3 w-3" />
                    Flaga Prompt-Injection: {s.metadata_json.safety_flags.join(", ")}
                  </Badge>
                )}
                <Badge
                  tone={
                    s.verification_status === "verified_checksum" ? "emerald" : "amber"
                  }
                >
                  {s.verification_status}
                </Badge>
              </div>
            </div>
            <p className="mt-2 line-clamp-2 text-xs text-zinc-300">
              {s.extracted_text}
            </p>
          </Card>
        ))}
      </div>
    </div>
  );
}

export function EvidenceGraphPage() {
  const { scope, t } = useAuth();
  const [graph, setGraph] = useState<any>(null);
  const [projects, setProjects] = useState<any[]>([]);
  const [sources, setSources] = useState<any[]>([]);
  const [selectedProj, setSelectedProj] = useState("");
  const [statement, setStatement] = useState("");
  const [sourceId, setSourceId] = useState("");
  const [excerpt, setExcerpt] = useState("");
  const [evType, setEvType] = useState<"supporting" | "contradicting">("supporting");

  const load = async () => {
    const q = new URLSearchParams();
    if (scope) q.set("scope", scope);
    if (selectedProj) q.set("project_id", selectedProj);
    const [g, p, s] = await Promise.all([
      apiFetch(`/api/v1/evidence/graph?${q.toString()}`),
      apiFetch(`/api/v1/research/projects?scope=${scope}`),
      apiFetch(`/api/v1/sources?scope=${scope}`),
    ]);
    setGraph(g);
    setProjects(p.items || []);
    setSources(s.items || []);
  };

  useEffect(() => {
    void load();
  }, [scope, selectedProj]);

  const handleAddClaim = async (e: React.FormEvent) => {
    e.preventDefault();
    const pid = selectedProj || projects[0]?.id;
    if (!pid) return;
    await apiFetch("/api/v1/evidence/claims", {
      method: "POST",
      body: JSON.stringify({
        research_project_id: pid,
        statement,
        evidence_links: sourceId
          ? [
              {
                source_id: sourceId,
                supporting_text: excerpt || statement,
                evidence_type: evType,
                relevance: 0.9,
              },
            ]
          : [],
      }),
    });
    setStatement("");
    setExcerpt("");
    await load();
  };

  const handleStatusChange = async (claimId: string, status: string) => {
    await apiFetch(`/api/v1/evidence/claims/${claimId}`, {
      method: "PATCH",
      body: JSON.stringify({ status }),
    });
    await load();
  };

  const sourceNodes = (graph?.nodes || []).filter((n: any) => n.kind === "source");
  const claimNodes = (graph?.nodes || []).filter((n: any) => n.kind === "claim");

  return (
    <div className="space-y-6">
      <PageHeader
        title={t.nav.evidence}
        subtitle="Strukturalny graf powiązań Źródła → Dowody → Twierdzenia. Samoocena pewności LLM nigdy nie jest traktowana jako skalibrowane prawdopodobieństwo."
        actions={
          <select
            value={selectedProj}
            onChange={(e) => setSelectedProj(e.target.value)}
            className="rounded-lg border border-zinc-800 bg-zinc-900 px-3 py-1.5 text-xs text-zinc-200"
          >
            <option value="">Wszystkie projekty w przestrzeni</option>
            {projects.map((p) => (
              <option key={p.id} value={p.id}>
                {p.title}
              </option>
            ))}
          </select>
        }
      />

      {graph?.summary && (
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
          <StatCard
            label="Twierdzenia ogółem"
            value={graph.summary.total_claims}
            icon={FileText}
            tone="indigo"
          />
          <StatCard
            label="Wspierane dowodowo"
            value={graph.summary.supported_claims}
            icon={CheckCircle2}
            tone="emerald"
          />
          <StatCard
            label="Wykryte sprzeczności"
            value={graph.summary.contradicted_claims}
            icon={AlertTriangle}
            tone="rose"
          />
          <StatCard
            label="Pokrycie źródłowe"
            value={`${graph.summary.evidence_coverage_pct}%`}
            icon={Network}
            tone="violet"
          />
        </div>
      )}

      {/* Interactive SVG Topology */}
      <Card>
        <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
          <h2 className="flex items-center gap-2 text-sm font-semibold text-white">
            <Network className="h-4 w-4 text-indigo-400" />
            Wizualizacja Topologii Grafu Dowodowego (Źródła Pierwotne → Twierdzenia)
          </h2>
          <div className="flex flex-wrap items-center gap-3 text-[11px] text-zinc-400">
            <span className="flex items-center gap-1.5">
              <span className="h-2 w-4 rounded-full bg-indigo-400" />
              Dowód wspierający
            </span>
            <span className="flex items-center gap-1.5">
              <span className="h-0.5 w-4 border-t-2 border-dashed border-rose-400" />
              Sprzeczność międzyźródłowa
            </span>
          </div>
        </div>

        <div className="apex-blueprint-grid overflow-x-auto rounded-2xl border border-white/[0.08] p-4 shadow-inner">
          <svg
            width="880"
            height={Math.max(250, Math.max(sourceNodes.length, claimNodes.length) * 62 + 44)}
            className="mx-auto block"
          >
            <defs>
              <linearGradient id="edgeSupport" x1="0%" y1="0%" x2="100%" y2="0%">
                <stop offset="0%" stopColor="#60A5FA" />
                <stop offset="100%" stopColor="#34D399" />
              </linearGradient>
              <linearGradient id="edgeContra" x1="0%" y1="0%" x2="100%" y2="0%">
                <stop offset="0%" stopColor="#FB7185" />
                <stop offset="100%" stopColor="#F43F5E" />
              </linearGradient>
            </defs>

            {(graph?.edges || []).map((ed: any) => {
              const sIdx = sourceNodes.findIndex((n: any) => n.id === ed.from);
              const cIdx = claimNodes.findIndex((n: any) => n.id === ed.to);
              if (sIdx < 0 || cIdx < 0) return null;
              const y1 = 46 + sIdx * 60;
              const y2 = 46 + cIdx * 60;
              const isContra = ed.evidence_type === "contradicting";
              return (
                <g key={ed.id}>
                  <path
                    d={`M 294 ${y1} C 435 ${y1}, 435 ${y2}, 570 ${y2}`}
                    fill="none"
                    stroke={isContra ? "url(#edgeContra)" : "url(#edgeSupport)"}
                    strokeWidth={2.2}
                    strokeOpacity={0.9}
                    strokeDasharray={isContra ? "6,4" : undefined}
                  />
                </g>
              );
            })}
            {sourceNodes.map((sn: any, idx: number) => (
              <g key={sn.id} transform={`translate(20, ${22 + idx * 60})`}>
                <rect
                  width="274"
                  height="46"
                  rx="11"
                  fill="#101729"
                  stroke="#60A5FA"
                  strokeOpacity="0.55"
                  strokeWidth="1.2"
                />
                <text x="14" y="19" fill="#93C5FD" fontSize="9.5" fontWeight="600">
                  ŹRÓDŁO · {String(sn.source_type || "DOC").toUpperCase()}
                </text>
                <text x="14" y="34" fill="#F4F4F5" fontSize="11">
                  {sn.label.slice(0, 35)}
                </text>
                <circle cx="274" cy="23" r="4" fill="#60A5FA" />
              </g>
            ))}
            {claimNodes.map((cn: any, idx: number) => {
              const stroke =
                cn.status === "supported"
                  ? "#34D399"
                  : cn.status === "contradicted"
                  ? "#FB7185"
                  : "#FBBF24";
              return (
                <g key={cn.id} transform={`translate(570, ${22 + idx * 60})`}>
                  <rect
                    width="290"
                    height="46"
                    rx="11"
                    fill="#131726"
                    stroke={stroke}
                    strokeOpacity="0.7"
                    strokeWidth="1.4"
                  />
                  <circle cx="0" cy="23" r="4" fill={stroke} />
                  <text x="14" y="19" fill={stroke} fontSize="9.5" fontWeight="700">
                    TWIERDZENIE · {cn.status.toUpperCase()}
                  </text>
                  <text x="14" y="34" fill="#F4F4F5" fontSize="10.5">
                    {cn.label.slice(0, 40)}...
                  </text>
                </g>
              );
            })}
          </svg>
        </div>
      </Card>

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
        <Card>
          <h3 className="mb-2 flex items-center gap-2 text-sm font-semibold text-rose-300">
            <AlertTriangle className="h-4 w-4" />
            Wykryte Sprzeczności i Alerty Brakujących Dowodów
          </h3>
          <div className="space-y-2 text-xs">
            {(graph?.contradictions || []).map((c: any) => (
              <div
                key={c.evidence_id}
                className="rounded-lg border border-rose-500/30 bg-rose-500/10 p-2.5 text-rose-200"
              >
                <b>Sprzeczność:</b> „{c.claim_statement}” vs źródło <i>{c.source_title}</i>
              </div>
            ))}
            {(graph?.missing_evidence_alerts || []).map((m: any) => (
              <div
                key={m.claim_id}
                className="rounded-lg border border-amber-500/30 bg-amber-500/10 p-2.5 text-amber-200"
              >
                <b>Brak dowodu:</b> „{m.statement}” — {m.reason}
              </div>
            ))}
            {!graph?.contradictions?.length && !graph?.missing_evidence_alerts?.length && (
              <div className="text-zinc-500">Brak aktywnych alertów sprzeczności.</div>
            )}
          </div>
        </Card>

        <Card>
          <h3 className="mb-3 text-sm font-semibold text-zinc-100">
            Dodaj Twierdzenie Badawcze (ResearchClaim)
          </h3>
          <form onSubmit={handleAddClaim} className="space-y-2.5 text-xs">
            <input
              required
              value={statement}
              onChange={(e) => setStatement(e.target.value)}
              placeholder="Treść twierdzenia wymagającego weryfikacji..."
              className="w-full rounded-lg border border-zinc-800 bg-zinc-950 px-3 py-2 text-zinc-100"
            />
            <div className="grid grid-cols-2 gap-2">
              <select
                value={sourceId}
                onChange={(e) => setSourceId(e.target.value)}
                className="rounded-lg border border-zinc-800 bg-zinc-950 px-3 py-2 text-zinc-100"
              >
                <option value="">-- Bez dowodu (unresolved) --</option>
                {sources.map((s) => (
                  <option key={s.id} value={s.id}>
                    {s.title}
                  </option>
                ))}
              </select>
              <select
                value={evType}
                onChange={(e) => setEvType(e.target.value as any)}
                className="rounded-lg border border-zinc-800 bg-zinc-950 px-3 py-2 text-zinc-100"
              >
                <option value="supporting">Dowód wspierający (supporting)</option>
                <option value="contradicting">Dowód sprzeczny (contradicting)</option>
              </select>
            </div>
            <input
              value={excerpt}
              onChange={(e) => setExcerpt(e.target.value)}
              placeholder="Cytat / fragment ze źródła pierwotnego..."
              className="w-full rounded-lg border border-zinc-800 bg-zinc-950 px-3 py-2 text-zinc-100"
            />
            <button
              type="submit"
              className="rounded-lg bg-indigo-600 px-4 py-2 font-semibold text-white hover:bg-indigo-500"
            >
              Dodaj Twierdzenie do Grafu
            </button>
          </form>
        </Card>
      </div>

      <Card>
        <h3 className="mb-3 text-sm font-semibold text-zinc-100">
          Rejestr Twierdzeń i Kontrola Statusów Dowodowych
        </h3>
        <div className="space-y-2">
          {claimNodes.map((cn: any) => (
            <div
              key={cn.id}
              className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-zinc-800 bg-zinc-900/60 px-3 py-2 text-xs"
            >
              <span className="text-zinc-200">{cn.full_text}</span>
              <select
                value={cn.status}
                onChange={(e) => void handleStatusChange(cn.id, e.target.value)}
                className="rounded border border-zinc-700 bg-zinc-950 px-2 py-1 text-xs text-zinc-200"
              >
                <option value="supported">supported</option>
                <option value="partially_supported">partially_supported</option>
                <option value="contradicted">contradicted</option>
                <option value="unresolved">unresolved</option>
                <option value="rejected">rejected</option>
              </select>
            </div>
          ))}
        </div>
      </Card>
    </div>
  );
}

export function MemoryExplorerPage() {
  const { t } = useAuth();
  const [memories, setMemories] = useState<any[]>([]);
  const [filterType, setFilterType] = useState("");
  const [query, setQuery] = useState("");
  const [searchHits, setSearchHits] = useState<any[] | null>(null);
  const [newType, setNewType] = useState("research");
  const [newTitle, setNewTitle] = useState("");
  const [newContent, setNewContent] = useState("");

  const load = async () => {
    const q = filterType ? `?memory_type=${filterType}` : "";
    const res = await apiFetch(`/api/v1/memories${q}`);
    setMemories(res.items || []);
  };

  useEffect(() => {
    void load();
  }, [filterType]);

  const handleSearch = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!query.trim()) {
      setSearchHits(null);
      return;
    }
    const q = new URLSearchParams({ q: query });
    if (filterType) q.set("memory_type", filterType);
    const res = await apiFetch(`/api/v1/memories/search?${q.toString()}`);
    setSearchHits(res.results || []);
  };

  const handleCreate = async (e: React.FormEvent) => {
    e.preventDefault();
    await apiFetch("/api/v1/memories", {
      method: "POST",
      body: JSON.stringify({
        memory_type: newType,
        title: newTitle,
        content: newContent,
      }),
    });
    setNewTitle("");
    setNewContent("");
    await load();
  };

  const handleAction = async (id: string, action: "reindex" | "delete") => {
    await apiFetch(`/api/v1/memories/${id}/action`, {
      method: "POST",
      body: JSON.stringify({ action }),
    });
    await load();
  };

  return (
    <div className="space-y-6">
      <PageHeader
        title={t.nav.memory}
        subtitle="7 typów pamięci współdzielonej · Wektory 1536-d (pgvector HNSW) · Wyszukiwanie hybrydowe semantyczne + pełnotekstowe z cytowaniami."
      />

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-3">
        <Card className="lg:col-span-2">
          <form onSubmit={handleSearch} className="flex flex-wrap gap-2">
            <input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Wyszukaj semantycznie i leksykalnie w pamięci (np. sprzeczności, tokeny, outbox)..."
              className="flex-1 rounded-lg border border-zinc-800 bg-zinc-950 px-3 py-2 text-xs text-zinc-100"
            />
            <select
              value={filterType}
              onChange={(e) => setFilterType(e.target.value)}
              className="rounded-lg border border-zinc-800 bg-zinc-950 px-3 py-2 text-xs text-zinc-200"
            >
              <option value="">Wszystkie 7 typów</option>
              {[
                "conversation",
                "research",
                "agent",
                "project",
                "document",
                "procedural",
                "experiment",
              ].map((mt) => (
                <option key={mt} value={mt}>
                  {mt}
                </option>
              ))}
            </select>
            <button
              type="submit"
              className="inline-flex items-center gap-1.5 rounded-lg bg-indigo-600 px-4 py-2 text-xs font-semibold text-white hover:bg-indigo-500"
            >
              <Search className="h-3.5 w-3.5" />
              Szukaj
            </button>
          </form>

          {searchHits && (
            <div className="mt-4 space-y-2">
              <div className="text-xs font-semibold text-indigo-300">
                Wyniki wyszukiwania hybrydowego ({searchHits.length}):
              </div>
              {searchHits.map((h) => (
                <div
                  key={h.chunk_id}
                  className="rounded-lg border border-indigo-500/30 bg-zinc-900/80 p-3 text-xs"
                >
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <span className="font-semibold text-zinc-100">
                      {h.memory_title}
                    </span>
                    <Badge tone="indigo">
                      Wynik: {h.hybrid_score} (sem: {h.semantic_score}, lex: {h.fulltext_score})
                    </Badge>
                  </div>
                  <p className="mt-1.5 text-zinc-300">{h.content}</p>
                  <div className="mt-1.5 text-[11px] text-indigo-400">
                    Cytowanie źródłowe: <code>{h.source_citation}</code>
                  </div>
                </div>
              ))}
            </div>
          )}
        </Card>

        <Card>
          <h3 className="mb-2.5 text-sm font-semibold text-zinc-100">
            Dodaj Obiekt Pamięci
          </h3>
          <form onSubmit={handleCreate} className="space-y-2.5 text-xs">
            <select
              value={newType}
              onChange={(e) => setNewType(e.target.value)}
              className="w-full rounded-lg border border-zinc-800 bg-zinc-950 px-3 py-2 text-zinc-100"
            >
              {[
                "research",
                "document",
                "procedural",
                "experiment",
                "agent",
                "project",
                "conversation",
              ].map((mt) => (
                <option key={mt} value={mt}>
                  {mt}
                </option>
              ))}
            </select>
            <input
              required
              value={newTitle}
              onChange={(e) => setNewTitle(e.target.value)}
              placeholder="Tytuł obiektu pamięci..."
              className="w-full rounded-lg border border-zinc-800 bg-zinc-950 px-3 py-2 text-zinc-100"
            />
            <textarea
              required
              rows={3}
              value={newContent}
              onChange={(e) => setNewContent(e.target.value)}
              placeholder="Treść wiedzy do podziału na fragmenty (chunking) i wektoryzacji..."
              className="w-full rounded-lg border border-zinc-800 bg-zinc-950 px-3 py-2 text-zinc-100"
            />
            <button
              type="submit"
              className="w-full rounded-lg bg-indigo-600 py-2 font-semibold text-white hover:bg-indigo-500"
            >
              Zapisz i Zindeksuj Wektorowo
            </button>
          </form>
        </Card>
      </div>

      <div className="space-y-2.5">
        {memories.map((m) => (
          <Card key={m.id} className="flex flex-wrap items-center justify-between gap-3 py-3.5">
            <div className="min-w-0 flex-1">
              <div className="flex items-center gap-2">
                <Badge tone="violet">{m.memory_type}</Badge>
                <span className="font-semibold text-sm text-zinc-100">{m.title}</span>
                <span className="text-xs text-zinc-500">
                  v{m.version} · {m.chunk_count} chunk(s) · retencja {m.retention_days}d
                </span>
              </div>
              <p className="mt-1 truncate text-xs text-zinc-400">{m.content}</p>
            </div>
            <div className="flex items-center gap-2">
              <button
                onClick={() => void handleAction(m.id, "reindex")}
                className="rounded-lg border border-zinc-700 px-2.5 py-1 text-xs text-zinc-200 hover:border-indigo-500"
              >
                Reindeksuj
              </button>
              <button
                onClick={() => void handleAction(m.id, "delete")}
                className="rounded-lg border border-rose-500/40 px-2.5 py-1 text-xs text-rose-300 hover:bg-rose-500/15"
              >
                <Trash2 className="h-3.5 w-3.5" />
              </button>
            </div>
          </Card>
        ))}
      </div>
    </div>
  );
}
