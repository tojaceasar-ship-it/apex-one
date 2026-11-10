import React, { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Legend,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import {
  AlertTriangle,
  CheckCircle2,
  Database,
  FlaskConical,
  Play,
  Repeat,
  ShieldAlert,
  UserCheck,
} from "lucide-react";
import { apiFetch } from "../api";
import { Badge, Card, PageHeader } from "../components/Layout";
import { useAuth } from "../context/AuthContext";

const EVAL_CATEGORIES = [
  "functional_accuracy",
  "research_accuracy",
  "evidence_quality",
  "tool_reliability",
  "safety_compliance",
  "reproducibility",
  "latency",
  "cost_efficiency",
  "regression_detection",
];

export function ExperimentLabPage() {
  const { scope, activeOrgId, t } = useAuth();
  const [experiments, setExperiments] = useState<any[]>([]);
  const [datasets, setDatasets] = useState<any[]>([]);
  const [name, setName] = useState("");
  const [datasetId, setDatasetId] = useState("");
  const [execMode, setExecMode] = useState<"multi_agent_team" | "single_agent">(
    "multi_agent_team"
  );
  const [sandboxMsg, setSandboxMsg] = useState<string | null>(null);
  const [reproMsg, setReproMsg] = useState<string | null>(null);

  const load = async () => {
    const [ex, ds] = await Promise.all([
      apiFetch(`/api/v1/experiments?scope=${scope}`),
      apiFetch("/api/v1/datasets"),
    ]);
    setExperiments(ex.items || []);
    setDatasets(ds.items || []);
    if (!datasetId && ds.items?.[0]?.id) {
      setDatasetId(ds.items[0].id);
    }
  };

  useEffect(() => {
    void load();
  }, [scope]);

  const handleCreateExperiment = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!datasetId) return;
    await apiFetch("/api/v1/experiments", {
      method: "POST",
      body: JSON.stringify({
        name,
        description: "Kontrolowany eksperyment analityczny na przypiętej wersji zbioru danych.",
        dataset_id: datasetId,
        dataset_version: 1,
        execution_mode: execMode,
      }),
    });
    setName("");
    await load();
  };

  const handleReproduce = async (runId: string) => {
    const res = await apiFetch(`/api/v1/experiments/runs/${runId}/reproduce`, {
      method: "POST",
    });
    setReproMsg(
      `Reprodukcja przebiegu zakończona (ID: ${res.id.slice(0, 8)}) · Zgodność deterministyczna metryk: ${
        res.reproduction_verified ? "100% POTWIERDZONA" : "ROZBIEŻNOŚĆ"
      }`
    );
    await load();
  };

  const handleTestArbitraryCodeGuard = async () => {
    setSandboxMsg(null);
    try {
      await apiFetch("/api/v1/sandbox/jobs/execute", {
        method: "POST",
        headers: {
          "X-Tenant-Id": activeOrgId || "00000000-0000-0000-0000-000000000001",
        },
        body: JSON.stringify({
          runtime: "python3.11",
          entrypoint: ["python3", "untrusted_script.py"],
        }),
      });
    } catch (err: any) {
      setSandboxMsg(
        `Polityka Izolacji Potwierdzona (${err.code}): ${err.message}`
      );
    }
  };

  return (
    <div className="space-y-6">
      <PageHeader
        title={t.nav.experiments}
        subtitle="Projektowanie eksperymentów, wersjonowane zbiory danych, walidacja artefaktów SHA-256 oraz Silnik Reprodukcji."
        actions={
          <button
            onClick={() => void handleTestArbitraryCodeGuard()}
            className="inline-flex items-center gap-1.5 rounded-lg border border-amber-500/40 bg-amber-500/10 px-3 py-1.5 text-xs font-medium text-amber-200 hover:bg-amber-500/20"
          >
            <ShieldAlert className="h-3.5 w-3.5" />
            Zweryfikuj Blokadę Nieizolowanego Kodu
          </button>
        }
      />

      {sandboxMsg && (
        <div className="rounded-lg border border-amber-500/40 bg-amber-500/10 p-3 text-xs text-amber-200">
          {sandboxMsg}
        </div>
      )}
      {reproMsg && (
        <div className="rounded-lg border border-emerald-500/40 bg-emerald-500/10 p-3 text-xs text-emerald-200">
          {reproMsg}
        </div>
      )}

      <Card>
        <h2 className="mb-3 flex items-center gap-2 text-sm font-semibold text-zinc-100">
          <FlaskConical className="h-4 w-4 text-indigo-400" />
          Uruchom Kontrolowany Eksperyment Badawczy
        </h2>
        <form onSubmit={handleCreateExperiment} className="grid grid-cols-1 gap-3 md:grid-cols-4 text-xs">
          <input
            required
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="Nazwa eksperymentu..."
            className="rounded-lg border border-zinc-800 bg-zinc-950 px-3 py-2 text-zinc-100"
          />
          <select
            value={datasetId}
            onChange={(e) => setDatasetId(e.target.value)}
            className="rounded-lg border border-zinc-800 bg-zinc-950 px-3 py-2 text-zinc-100"
          >
            {datasets.map((ds) => (
              <option key={ds.id} value={ds.id}>
                {ds.name} (v{ds.current_version})
              </option>
            ))}
          </select>
          <select
            value={execMode}
            onChange={(e) => setExecMode(e.target.value as any)}
            className="rounded-lg border border-zinc-800 bg-zinc-950 px-3 py-2 text-zinc-100"
          >
            <option value="multi_agent_team">Multi-Agent Team (8 ról)</option>
            <option value="single_agent">Single-Agent Baseline</option>
          </select>
          <button
            type="submit"
            className="inline-flex items-center justify-center gap-1.5 rounded-lg bg-indigo-600 px-4 py-2 font-semibold text-white hover:bg-indigo-500"
          >
            <Play className="h-3.5 w-3.5" />
            Wykonaj Eksperyment
          </button>
        </form>
      </Card>

      <div className="space-y-4">
        {experiments.map((exp) => {
          const latestRun = exp.runs?.[exp.runs.length - 1];
          return (
            <Card key={exp.id}>
              <div className="flex flex-wrap items-start justify-between gap-2">
                <div>
                  <Link
                    to={`/experiments/${exp.id}`}
                    className="text-sm font-semibold text-zinc-100 hover:text-indigo-300"
                  >
                    {exp.name}
                  </Link>
                  <div className="mt-1 text-xs text-zinc-400">
                    Tryb: {exp.execution_mode} · Model: {exp.model_identifier} ·
                    Zbiór v{exp.dataset_version} · Przebiegów: {exp.runs?.length || 0}
                  </div>
                </div>
                <div className="flex items-center gap-2">
                  {latestRun && (
                    <button
                      onClick={() => void handleReproduce(latestRun.id)}
                      className="inline-flex items-center gap-1 rounded-lg border border-indigo-500/40 bg-indigo-500/15 px-3 py-1 text-xs font-medium text-indigo-200 hover:bg-indigo-500/25"
                    >
                      <Repeat className="h-3.5 w-3.5" />
                      Reprodukuj Wynik
                    </button>
                  )}
                  <Link
                    to={`/experiments/${exp.id}`}
                    className="text-xs font-medium text-indigo-400 hover:underline"
                  >
                    Artefakty i Logi →
                  </Link>
                </div>
              </div>

              {latestRun && (
                <div className="mt-3 grid grid-cols-2 gap-2 sm:grid-cols-6">
                  {(latestRun.metrics || []).map((m: any) => (
                    <div
                      key={m.metric_name}
                      className="rounded-lg border border-zinc-800 bg-zinc-900/70 p-2 text-xs"
                    >
                      <div className="truncate text-[10px] text-zinc-400">
                        {m.metric_name}
                      </div>
                      <div className="mt-0.5 font-semibold text-zinc-100">
                        {m.metric_value} <span className="text-[10px] text-zinc-500">{m.unit}</span>
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </Card>
          );
        })}
      </div>
    </div>
  );
}

export function ExperimentDetailPage() {
  const { experimentId } = useParams();
  const [exp, setExp] = useState<any>(null);

  useEffect(() => {
    void apiFetch("/api/v1/experiments").then((res) => {
      const found = (res.items || []).find((x: any) => x.id === experimentId);
      setExp(found || null);
    });
  }, [experimentId]);

  if (!exp) {
    return <div className="text-sm text-zinc-400">Ładowanie szczegółów eksperymentu...</div>;
  }

  return (
    <div className="space-y-6">
      <PageHeader
        title={exp.name}
        subtitle={`Pełna specyfikacja, logi uruchomieniowe i zwalidowane artefakty SHA-256 (${exp.runs?.length || 0} przebiegów).`}
      />

      {(exp.runs || []).map((r: any) => (
        <Card key={r.id} className="space-y-3">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div className="text-sm font-semibold text-zinc-100">
              Przebieg {r.id.slice(0, 8)} {r.reproduced_from_run_id ? `(Reprodukcja z ${r.reproduced_from_run_id.slice(0, 8)})` : ""}
            </div>
            <Badge tone="emerald">{r.status}</Badge>
          </div>
          <div className="rounded-lg border border-zinc-800 bg-zinc-950 p-3 font-mono text-[11px] text-zinc-300">
            {(r.logs_json || []).map((ln: string, i: number) => (
              <div key={i}>{ln}</div>
            ))}
          </div>
          {(r.artifacts || []).map((art: any) => (
            <div
              key={art.id}
              className="rounded-lg border border-indigo-500/30 bg-zinc-900/70 p-3 text-xs"
            >
              <div className="flex items-center justify-between">
                <span className="font-semibold text-indigo-300">
                  Artefakt: {art.relative_path} ({art.size_bytes} B)
                </span>
                <span className="font-mono text-[11px] text-zinc-400">
                  SHA-256: {art.checksum_sha256.slice(0, 24)}...
                </span>
              </div>
              <pre className="mt-2 max-h-48 overflow-y-auto text-[11px] text-zinc-300">
                {art.content_text}
              </pre>
            </div>
          ))}
        </Card>
      ))}
    </div>
  );
}

export function DatasetsPage() {
  const { t } = useAuth();
  const [datasets, setDatasets] = useState<any[]>([]);
  const [name, setName] = useState("");
  const [desc, setDesc] = useState("");
  const [recordsRaw, setRecordsRaw] = useState(
    '[{"doc_id":"REC-01","title":"Próbka badawcza","text":"Dane empiryczne do testu","labeled_claims":[]}]'
  );

  const load = async () => {
    const res = await apiFetch("/api/v1/datasets");
    setDatasets(res.items || []);
  };

  useEffect(() => {
    void load();
  }, []);

  const handleCreate = async (e: React.FormEvent) => {
    e.preventDefault();
    const parsed = JSON.parse(recordsRaw);
    await apiFetch("/api/v1/datasets", {
      method: "POST",
      body: JSON.stringify({
        name,
        description: desc,
        category: "empirical",
        records: parsed,
      }),
    });
    setName("");
    setDesc("");
    await load();
  };

  return (
    <div className="space-y-6">
      <PageHeader
        title={t.nav.datasets}
        subtitle="Wersjonowany rejestr zbiorów danych z kryptograficzną sumą kontrolną SHA-256 gwarantującą odtwarzalność."
      />

      <Card>
        <h2 className="mb-3 flex items-center gap-2 text-sm font-semibold text-zinc-100">
          <Database className="h-4 w-4 text-indigo-400" />
          Zarejestruj Nowy Zbiór Danych (JSON Records)
        </h2>
        <form onSubmit={handleCreate} className="space-y-3 text-xs">
          <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
            <input
              required
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="Nazwa zbioru danych..."
              className="rounded-lg border border-zinc-800 bg-zinc-950 px-3 py-2 text-zinc-100"
            />
            <input
              value={desc}
              onChange={(e) => setDesc(e.target.value)}
              placeholder="Opis metodologiczny..."
              className="rounded-lg border border-zinc-800 bg-zinc-950 px-3 py-2 text-zinc-100"
            />
          </div>
          <textarea
            rows={3}
            required
            value={recordsRaw}
            onChange={(e) => setRecordsRaw(e.target.value)}
            className="w-full rounded-lg border border-zinc-800 bg-zinc-950 p-2.5 font-mono text-zinc-200"
          />
          <button
            type="submit"
            className="rounded-lg bg-indigo-600 px-4 py-2 font-semibold text-white hover:bg-indigo-500"
          >
            Utwórz Zbiór Danych v1
          </button>
        </form>
      </Card>

      <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
        {datasets.map((ds) => (
          <Card key={ds.id}>
            <div className="flex items-center justify-between">
              <span className="font-semibold text-sm text-zinc-100">{ds.name}</span>
              <Badge tone="indigo">
                {ds.category} · v{ds.current_version}
              </Badge>
            </div>
            <p className="mt-1.5 text-xs text-zinc-400">{ds.description}</p>
            <div className="mt-3 text-xs text-zinc-400">
              Rekordów: <b className="text-zinc-200">{ds.row_count}</b> · SHA-256:{" "}
              <code className="text-indigo-300">{ds.checksum_sha256.slice(0, 20)}...</code>
            </div>
          </Card>
        ))}
      </div>
    </div>
  );
}

export function EvaluationsPage() {
  const { scope, t } = useAuth();
  const [data, setData] = useState<any>({ suites: [], runs: [], regressions: [] });
  const [label, setLabel] = useState("");
  const [mode, setMode] = useState<"multi_agent_team" | "single_agent">("multi_agent_team");
  const [humanRunId, setHumanRunId] = useState("");
  const [humanCat, setHumanCat] = useState("evidence_quality");
  const [humanScore, setHumanScore] = useState(0.9);
  const [humanRationale, setHumanRationale] = useState("");
  const [msg, setMsg] = useState<string | null>(null);

  const load = async () => {
    const res = await apiFetch(`/api/v1/evaluations?scope=${scope}`);
    setData(res);
    if (!humanRunId && res.runs?.[0]?.id) {
      setHumanRunId(res.runs[0].id);
    }
  };

  useEffect(() => {
    void load();
  }, [scope]);

  const handleRunBenchmark = async (e: React.FormEvent) => {
    e.preventDefault();
    const suite = data.suites?.[0];
    if (!suite) return;
    await apiFetch("/api/v1/evaluations/run", {
      method: "POST",
      body: JSON.stringify({
        suite_id: suite.id,
        label: label || `Benchmark (${mode})`,
        execution_mode: mode,
        baseline_eval_run_id: data.runs?.[0]?.id || null,
      }),
    });
    setLabel("");
    await load();
  };

  const handleHumanReview = async (e: React.FormEvent) => {
    e.preventDefault();
    const res = await apiFetch(`/api/v1/evaluations/runs/${humanRunId}/human-review`, {
      method: "POST",
      body: JSON.stringify({
        category: humanCat,
        score: Number(humanScore),
        rationale: humanRationale,
      }),
    });
    setMsg(
      `Zapisano ocenę ekspercką (${res.score}) · Automatyczna aktualizacja agenta produkcyjnego: ${res.production_agent_auto_updated} (zablokowana zgodnie z polityką).`
    );
    setHumanRationale("");
    await load();
  };

  const runA = data.runs?.find((r: any) => r.subject_type === "single_agent");
  const runB = data.runs?.find((r: any) => r.subject_type === "multi_agent_team");

  const chartData = EVAL_CATEGORIES.map((cat) => ({
    category: cat.replace("_", " "),
    SingleAgent: Number(((runA?.summary_scores?.[cat] || 0) * 100).toFixed(1)),
    MultiAgent: Number(((runB?.summary_scores?.[cat] || 0) * 100).toFixed(1)),
  }));

  return (
    <div className="space-y-6">
      <PageHeader
        title={t.nav.evaluations}
        subtitle="9 kategorii ewaluacji · Kontrolowane porównanie Single-Agent vs Multi-Agent · Alerty regresji · Oceny eksperckie bez auto-promocji."
      />

      {msg && (
        <div className="rounded-lg border border-emerald-500/40 bg-emerald-500/10 p-3 text-xs text-emerald-300">
          {msg}
        </div>
      )}

      {chartData.length > 0 && (
        <Card>
          <h2 className="mb-3 text-sm font-semibold text-zinc-100">
            Porównanie 9 Kategorii: Configuration A (Single-Agent) vs Configuration B (8-Agent Team)
          </h2>
          <div className="h-72 w-full">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={chartData}>
                <CartesianGrid strokeDasharray="3 3" stroke="#27272a" />
                <XAxis dataKey="category" tick={{ fill: "#a1a1aa", fontSize: 10 }} />
                <YAxis domain={[0, 100]} tick={{ fill: "#a1a1aa", fontSize: 11 }} />
                <Tooltip
                  contentStyle={{
                    backgroundColor: "#121620",
                    borderColor: "#3f3f46",
                    fontSize: "12px",
                  }}
                />
                <Legend />
                <Bar dataKey="SingleAgent" name="Config A: Single-Agent (%)" fill="#3b82f6" radius={[4, 4, 0, 0]} />
                <Bar dataKey="MultiAgent" name="Config B: 8-Agent Team (%)" fill="#8b5cf6" radius={[4, 4, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
          </div>
        </Card>
      )}

      {(data.regressions || []).length > 0 && (
        <Card className="border-amber-500/35">
          <h3 className="mb-2 flex items-center gap-2 text-sm font-semibold text-amber-300">
            <AlertTriangle className="h-4 w-4" />
            Wykryte Regresje i Kompromisy Kosztowe ({data.regressions.length})
          </h3>
          <div className="grid grid-cols-1 gap-2 md:grid-cols-2 text-xs">
            {data.regressions.map((rg: any) => (
              <div
                key={rg.id}
                className="rounded border border-amber-500/30 bg-amber-500/10 p-2.5 text-amber-200"
              >
                Kategoria: <b>{rg.category}</b> · Zmiana wyniku:{" "}
                <b>{(rg.delta_score * 100).toFixed(1)} pp</b> ({rg.severity})
              </div>
            ))}
          </div>
        </Card>
      )}

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
        <Card>
          <h3 className="mb-3 text-sm font-semibold text-zinc-100">
            Uruchom Nowy Przebieg Benchmarkowy
          </h3>
          <form onSubmit={handleRunBenchmark} className="space-y-3 text-xs">
            <input
              required
              value={label}
              onChange={(e) => setLabel(e.target.value)}
              placeholder="Etykieta przebiegu ewaluacyjnego..."
              className="w-full rounded-lg border border-zinc-800 bg-zinc-950 px-3 py-2 text-zinc-100"
            />
            <select
              value={mode}
              onChange={(e) => setMode(e.target.value as any)}
              className="w-full rounded-lg border border-zinc-800 bg-zinc-950 px-3 py-2 text-zinc-100"
            >
              <option value="multi_agent_team">Multi-Agent Team (8 ról)</option>
              <option value="single_agent">Single-Agent Baseline</option>
            </select>
            <button
              type="submit"
              className="rounded-lg bg-indigo-600 px-4 py-2 font-semibold text-white hover:bg-indigo-500"
            >
              Uruchom Benchmark
            </button>
          </form>
        </Card>

        <Card>
          <h3 className="mb-3 flex items-center gap-2 text-sm font-semibold text-zinc-100">
            <UserCheck className="h-4 w-4 text-indigo-400" />
            Ręczna Ocena Ekspercka (Rola Evaluator / Owner)
          </h3>
          <form onSubmit={handleHumanReview} className="space-y-2.5 text-xs">
            <div className="grid grid-cols-2 gap-2">
              <select
                value={humanRunId}
                onChange={(e) => setHumanRunId(e.target.value)}
                className="rounded-lg border border-zinc-800 bg-zinc-950 px-3 py-2 text-zinc-100"
              >
                {(data.runs || []).map((r: any) => (
                  <option key={r.id} value={r.id}>
                    {r.label}
                  </option>
                ))}
              </select>
              <select
                value={humanCat}
                onChange={(e) => setHumanCat(e.target.value)}
                className="rounded-lg border border-zinc-800 bg-zinc-950 px-3 py-2 text-zinc-100"
              >
                {EVAL_CATEGORIES.map((c) => (
                  <option key={c} value={c}>
                    {c}
                  </option>
                ))}
              </select>
            </div>
            <div className="grid grid-cols-3 gap-2">
              <input
                type="number"
                step="0.05"
                min={0}
                max={1}
                value={humanScore}
                onChange={(e) => setHumanScore(Number(e.target.value))}
                className="rounded-lg border border-zinc-800 bg-zinc-950 px-3 py-2 text-zinc-100"
              />
              <input
                required
                value={humanRationale}
                onChange={(e) => setHumanRationale(e.target.value)}
                placeholder="Uzasadnienie eksperta..."
                className="col-span-2 rounded-lg border border-zinc-800 bg-zinc-950 px-3 py-2 text-zinc-100"
              />
            </div>
            <button
              type="submit"
              className="rounded-lg bg-indigo-600 px-4 py-2 font-semibold text-white hover:bg-indigo-500"
            >
              Zapisz Ocenę Ekspercką (Bez Auto-Promocji)
            </button>
          </form>
        </Card>
      </div>
    </div>
  );
}
