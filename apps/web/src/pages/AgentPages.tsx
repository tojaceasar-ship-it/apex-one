import React, { useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import {
  Activity,
  Bot,
  Copy,
  GitCompare,
  Play,
  RotateCcw,
  Sparkles,
  StopCircle,
  Users,
} from "lucide-react";
import { apiFetch, loadStoredSession } from "../api";
import { Badge, Card, PageHeader } from "../components/Layout";
import { useAuth } from "../context/AuthContext";

const AVAILABLE_TOOLS = [
  "source.lookup",
  "research.create_brief",
  "evidence.extract_claims",
  "evidence.detect_contradictions",
  "data.compute_stats",
  "experiment.design",
  "report.synthesize",
  "memory.retrieve",
  "workflow.inspect_budget",
];

export function AgentCatalogPage() {
  const { scope, t } = useAuth();
  const navigate = useNavigate();
  const [agents, setAgents] = useState<any[]>([]);

  const load = async () => {
    const res = await apiFetch(`/api/v1/agents?scope=${scope}`);
    setAgents(res.items || []);
  };

  useEffect(() => {
    void load();
  }, [scope]);

  const handleDuplicate = async (id: string) => {
    const dup = await apiFetch(`/api/v1/agents/${id}/duplicate`, {
      method: "POST",
    });
    navigate(`/agents/${dup.id}`);
  };

  const handleStatus = async (id: string, status: string) => {
    await apiFetch(`/api/v1/agents/${id}/status`, {
      method: "PATCH",
      body: JSON.stringify({ status }),
    });
    await load();
  };

  return (
    <div className="space-y-6">
      <PageHeader
        title={t.nav.agents}
        subtitle="Rejestr wyspecjalizowanych agentów badawczych z niezmiennym wersjonowaniem AgentDNA."
        actions={
          <Link
            to="/agent-builder"
            className="rounded-lg bg-indigo-600 px-3.5 py-2 text-xs font-semibold text-white hover:bg-indigo-500"
          >
            + Otwórz Kreator Agentów
          </Link>
        }
      />

      <div className="grid grid-cols-1 gap-4 md:grid-cols-2 lg:grid-cols-3">
        {agents.map((ag) => (
          <Card key={ag.id} className="flex flex-col justify-between">
            <div>
              <div className="flex items-start justify-between gap-2">
                <Link
                  to={`/agents/${ag.id}`}
                  className="text-sm font-semibold text-zinc-100 hover:text-indigo-300"
                >
                  {ag.name}
                </Link>
                <Badge
                  tone={
                    ag.status === "active"
                      ? "emerald"
                      : ag.status === "archived"
                      ? "rose"
                      : "amber"
                  }
                >
                  {ag.status} · v{ag.current_version}
                </Badge>
              </div>
              <div className="mt-1 text-[11px] font-medium text-indigo-400">
                Rola: {ag.role}
              </div>
              <p className="mt-2 text-xs text-zinc-400 line-clamp-3">
                {ag.description || ag.purpose}
              </p>
              <div className="mt-3 flex flex-wrap gap-1">
                {(ag.dna?.allowed_tools || []).slice(0, 4).map((tl: string) => (
                  <span
                    key={tl}
                    className="rounded bg-zinc-800 px-1.5 py-0.5 text-[10px] text-zinc-300"
                  >
                    {tl}
                  </span>
                ))}
              </div>
            </div>

            <div className="mt-4 flex items-center justify-between border-t border-zinc-800/80 pt-3 text-xs">
              <div className="flex items-center gap-1.5">
                <button
                  onClick={() => void handleDuplicate(ag.id)}
                  title="Duplikuj agenta"
                  className="rounded border border-zinc-700 px-2 py-1 text-zinc-300 hover:border-indigo-500"
                >
                  <Copy className="h-3.5 w-3.5" />
                </button>
                <select
                  value={ag.status}
                  onChange={(e) => void handleStatus(ag.id, e.target.value)}
                  className="rounded border border-zinc-700 bg-zinc-950 px-2 py-1 text-xs text-zinc-300"
                >
                  <option value="active">active</option>
                  <option value="inactive">inactive</option>
                  <option value="archived">archived</option>
                </select>
              </div>
              <Link
                to={`/agents/${ag.id}`}
                className="font-medium text-indigo-400 hover:underline"
              >
                AgentDNA →
              </Link>
            </div>
          </Card>
        ))}
      </div>
    </div>
  );
}

export function AgentDetailPage() {
  const { agentId } = useParams();
  const navigate = useNavigate();
  const [agent, setAgent] = useState<any>(null);
  const [instructions, setInstructions] = useState("");
  const [maxSteps, setMaxSteps] = useState(8);
  const [changeSummary, setChangeSummary] = useState("");
  const [verA, setVerA] = useState(1);
  const [verB, setVerB] = useState(1);
  const [diffResult, setDiffResult] = useState<any[] | null>(null);
  const [msg, setMsg] = useState<string | null>(null);

  const load = async () => {
    if (!agentId) return;
    const d = await apiFetch(`/api/v1/agents/${agentId}`);
    setAgent(d);
    setInstructions(d.dna?.instructions || "");
    setMaxSteps(d.dna?.max_steps || 8);
    setVerA(1);
    setVerB(d.current_version);
  };

  useEffect(() => {
    void load();
  }, [agentId]);

  const handleSaveNewVersion = async (e: React.FormEvent) => {
    e.preventDefault();
    const updated = await apiFetch(`/api/v1/agents/${agentId}`, {
      method: "PUT",
      body: JSON.stringify({
        updates: { instructions, max_steps: Number(maxSteps) },
        change_summary: changeSummary || "Aktualizacja instrukcji AgentDNA",
      }),
    });
    setMsg(
      `Utworzono niezmienną wersję AgentDNA v${updated.current_version} (poprzednie wersje zostały zachowane).`
    );
    setChangeSummary("");
    await load();
  };

  const handleCompare = async () => {
    const res = await apiFetch(
      `/api/v1/agents/${agentId}/compare?version_a=${verA}&version_b=${verB}`
    );
    setDiffResult(res.differences || []);
  };

  const handleTestRun = async () => {
    await apiFetch("/api/v1/runs", {
      method: "POST",
      body: JSON.stringify({
        objective: `Test uruchomieniowy agenta ${agent.name} (v${agent.current_version})`,
        execution_mode: "single_agent",
        agent_id: agent.id,
      }),
    });
    navigate("/runs");
  };

  if (!agent) return <div className="text-sm text-zinc-400">Ładowanie AgentDNA...</div>;

  return (
    <div className="space-y-6">
      <PageHeader
        title={`${agent.name} (v${agent.current_version})`}
        subtitle={`Rola: ${agent.role} · Profil ewaluacji: ${agent.dna?.evaluation_profile}`}
        actions={
          <button
            onClick={() => void handleTestRun()}
            className="inline-flex items-center gap-1.5 rounded-lg bg-indigo-600 px-3.5 py-2 text-xs font-semibold text-white hover:bg-indigo-500"
          >
            <Play className="h-3.5 w-3.5" />
            Uruchom Test Agenta
          </button>
        }
      />

      {msg && (
        <div className="rounded-lg border border-emerald-500/40 bg-emerald-500/10 p-3 text-xs text-emerald-300">
          {msg}
        </div>
      )}

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
        <Card>
          <h2 className="mb-3 text-sm font-semibold text-zinc-100">
            Edycja Konfiguracji (Tworzy Nową Wersję AgentDNA v{agent.current_version + 1})
          </h2>
          <form onSubmit={handleSaveNewVersion} className="space-y-3 text-xs">
            <div>
              <label className="mb-1 block text-zinc-400">Instrukcje systemowe i rygor</label>
              <textarea
                rows={4}
                required
                value={instructions}
                onChange={(e) => setInstructions(e.target.value)}
                className="w-full rounded-lg border border-zinc-800 bg-zinc-950 p-2.5 text-zinc-100"
              />
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="mb-1 block text-zinc-400">Maks. liczba kroków</label>
                <input
                  type="number"
                  min={1}
                  max={50}
                  value={maxSteps}
                  onChange={(e) => setMaxSteps(Number(e.target.value))}
                  className="w-full rounded-lg border border-zinc-800 bg-zinc-950 px-3 py-2 text-zinc-100"
                />
              </div>
              <div>
                <label className="mb-1 block text-zinc-400">Opis zmiany wersji</label>
                <input
                  value={changeSummary}
                  onChange={(e) => setChangeSummary(e.target.value)}
                  placeholder="np. Zaostrzenie weryfikacji cytowań"
                  className="w-full rounded-lg border border-zinc-800 bg-zinc-950 px-3 py-2 text-zinc-100"
                />
              </div>
            </div>
            <button
              type="submit"
              className="rounded-lg bg-indigo-600 px-4 py-2 font-semibold text-white hover:bg-indigo-500"
            >
              Zapisz jako AgentDNA v{agent.current_version + 1}
            </button>
          </form>
        </Card>

        <Card>
          <h2 className="mb-3 flex items-center gap-2 text-sm font-semibold text-zinc-100">
            <GitCompare className="h-4 w-4 text-indigo-400" />
            Historia i Porównywanie Wersji AgentDNA ({agent.versions?.length || 1})
          </h2>
          <div className="flex items-center gap-2 text-xs">
            <select
              value={verA}
              onChange={(e) => setVerA(Number(e.target.value))}
              className="rounded border border-zinc-700 bg-zinc-950 px-2.5 py-1.5 text-zinc-200"
            >
              {(agent.versions || []).map((v: any) => (
                <option key={v.id} value={v.version}>
                  Wersja v{v.version} — {v.change_summary}
                </option>
              ))}
            </select>
            <span>vs</span>
            <select
              value={verB}
              onChange={(e) => setVerB(Number(e.target.value))}
              className="rounded border border-zinc-700 bg-zinc-950 px-2.5 py-1.5 text-zinc-200"
            >
              {(agent.versions || []).map((v: any) => (
                <option key={v.id} value={v.version}>
                  Wersja v{v.version} — {v.change_summary}
                </option>
              ))}
            </select>
            <button
              onClick={() => void handleCompare()}
              className="rounded-lg border border-indigo-500/40 bg-indigo-500/15 px-3 py-1.5 font-medium text-indigo-200"
            >
              Porównaj
            </button>
          </div>

          {diffResult && (
            <div className="mt-3 space-y-2 text-xs">
              {diffResult.length === 0 ? (
                <div className="text-zinc-400">Brak różnic między wybranymi wersjami.</div>
              ) : (
                diffResult.map((d) => (
                  <div
                    key={d.field}
                    className="rounded border border-zinc-800 bg-zinc-950 p-2.5"
                  >
                    <div className="font-semibold text-indigo-300">Pole: {d.field}</div>
                    <div className="mt-1 text-rose-300">- Przed: {JSON.stringify(d.before)}</div>
                    <div className="text-emerald-300">+ Po: {JSON.stringify(d.after)}</div>
                  </div>
                ))
              )}
            </div>
          )}

          <pre className="mt-4 max-h-56 overflow-y-auto rounded-lg border border-zinc-800 bg-zinc-950 p-3 text-[11px] text-zinc-300">
            {JSON.stringify(agent.dna, null, 2)}
          </pre>
        </Card>
      </div>
    </div>
  );
}

export function AgentBuilderPage() {
  const { t } = useAuth();
  const navigate = useNavigate();
  const [name, setName] = useState("");
  const [role, setRole] = useState("domain_specialist");
  const [purpose, setPurpose] = useState("");
  const [instructions, setInstructions] = useState(
    "Weryfikuj każde twierdzenie względem zarejestrowanych źródeł pierwotnych i oznaczaj sprzeczności."
  );
  const [selectedTools, setSelectedTools] = useState<string[]>([
    "source.lookup",
    "evidence.extract_claims",
    "memory.retrieve",
  ]);
  const [riskCeiling, setRiskCeiling] = useState("R1");
  const [maxTokens, setMaxTokens] = useState(24000);
  const [maxSteps, setMaxSteps] = useState(8);

  const toggleTool = (tool: string) => {
    setSelectedTools((prev) =>
      prev.includes(tool) ? prev.filter((x) => x !== tool) : [...prev, tool]
    );
  };

  const handleCreate = async (e: React.FormEvent) => {
    e.preventDefault();
    const created = await apiFetch("/api/v1/agents", {
      method: "POST",
      body: JSON.stringify({
        name,
        role,
        purpose,
        description: purpose,
        instructions,
        allowed_tools: selectedTools,
        execution_policy: { risk_ceiling: riskCeiling, retry_limit: 2 },
        budget: { max_tokens: Number(maxTokens), max_cost_usd: 1.5 },
        max_steps: Number(maxSteps),
      }),
    });
    navigate(`/agents/${created.id}`);
  };

  return (
    <div className="space-y-6">
      <PageHeader
        title={t.nav.agentBuilder}
        subtitle="Graficzny konstruktor struktury AgentDNA z jawnymi uprawnieniami narzędziowymi, polityką pamięci i pułapem ryzyka R0–R3."
      />

      <Card>
        <form onSubmit={handleCreate} className="space-y-4 text-xs">
          <div className="grid grid-cols-1 gap-4 md:grid-cols-3">
            <div>
              <label className="mb-1 block text-zinc-300">Nazwa agenta</label>
              <input
                required
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="np. Audytor Cytowań Biomedycznych"
                className="w-full rounded-lg border border-zinc-800 bg-zinc-950 px-3 py-2 text-zinc-100"
              />
            </div>
            <div>
              <label className="mb-1 block text-zinc-300">Rola architektoniczna</label>
              <input
                required
                value={role}
                onChange={(e) => setRole(e.target.value)}
                className="w-full rounded-lg border border-zinc-800 bg-zinc-950 px-3 py-2 text-zinc-100"
              />
            </div>
            <div>
              <label className="mb-1 block text-zinc-300">Pułap ryzyka (Governance)</label>
              <select
                value={riskCeiling}
                onChange={(e) => setRiskCeiling(e.target.value)}
                className="w-full rounded-lg border border-zinc-800 bg-zinc-950 px-3 py-2 text-zinc-100"
              >
                <option value="R0">R0 — Wyłącznie odczyt</option>
                <option value="R1">R1 — Analiza wewnętrzna i szkice</option>
                <option value="R2">R2 — Modyfikacja kontrolowana (wymaga zgody)</option>
                <option value="R3">R3 — Operacje wysokiego wpływu (ścisła zgoda)</option>
              </select>
            </div>
          </div>

          <div>
            <label className="mb-1 block text-zinc-300">Cel operacyjny (Purpose)</label>
            <input
              required
              value={purpose}
              onChange={(e) => setPurpose(e.target.value)}
              placeholder="Opisz zakres odpowiedzialności agenta..."
              className="w-full rounded-lg border border-zinc-800 bg-zinc-950 px-3 py-2 text-zinc-100"
            />
          </div>

          <div>
            <label className="mb-1 block text-zinc-300">Instrukcje AgentDNA</label>
            <textarea
              rows={4}
              required
              value={instructions}
              onChange={(e) => setInstructions(e.target.value)}
              className="w-full rounded-lg border border-zinc-800 bg-zinc-950 p-3 text-zinc-100"
            />
          </div>

          <div>
            <label className="mb-2 block text-zinc-300">
              Dozwolone narzędzia (zasada najmniejszych uprawnień):
            </label>
            <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
              {AVAILABLE_TOOLS.map((tl) => (
                <label
                  key={tl}
                  className="flex cursor-pointer items-center gap-2 rounded-lg border border-zinc-800 bg-zinc-900/70 px-3 py-2"
                >
                  <input
                    type="checkbox"
                    checked={selectedTools.includes(tl)}
                    onChange={() => toggleTool(tl)}
                  />
                  <span>{tl}</span>
                </label>
              ))}
            </div>
          </div>

          <div className="grid grid-cols-2 gap-4 sm:w-1/2">
            <div>
              <label className="mb-1 block text-zinc-300">Budżet tokenów</label>
              <input
                type="number"
                value={maxTokens}
                onChange={(e) => setMaxTokens(Number(e.target.value))}
                className="w-full rounded-lg border border-zinc-800 bg-zinc-950 px-3 py-2 text-zinc-100"
              />
            </div>
            <div>
              <label className="mb-1 block text-zinc-300">Maks. liczba kroków</label>
              <input
                type="number"
                value={maxSteps}
                onChange={(e) => setMaxSteps(Number(e.target.value))}
                className="w-full rounded-lg border border-zinc-800 bg-zinc-950 px-3 py-2 text-zinc-100"
              />
            </div>
          </div>

          <button
            type="submit"
            className="inline-flex items-center gap-2 rounded-lg bg-indigo-600 px-5 py-2.5 text-xs font-semibold text-white hover:bg-indigo-500"
          >
            <Sparkles className="h-4 w-4" />
            Utwórz Agenta (AgentDNA v1)
          </button>
        </form>
      </Card>
    </div>
  );
}

export function AgentTeamsPage() {
  const { t } = useAuth();
  const navigate = useNavigate();
  const [teams, setTeams] = useState<any[]>([]);
  const [agents, setAgents] = useState<any[]>([]);
  const [name, setName] = useState("");
  const [desc, setDesc] = useState("");

  const load = async () => {
    const [tm, ag] = await Promise.all([
      apiFetch("/api/v1/teams"),
      apiFetch("/api/v1/agents"),
    ]);
    setTeams(tm.items || []);
    setAgents(ag.items || []);
  };

  useEffect(() => {
    void load();
  }, []);

  const handleCreate = async (e: React.FormEvent) => {
    e.preventDefault();
    const sup = agents.find((a) => a.slug === "supervisor");
    const members = agents
      .filter((a) => a.slug !== "supervisor" && a.slug !== "universal-baseline")
      .map((a) => a.id);
    await apiFetch("/api/v1/teams", {
      method: "POST",
      body: JSON.stringify({
        name,
        description: desc,
        supervisor_agent_id: sup?.id || null,
        member_agent_ids: members,
      }),
    });
    setName("");
    setDesc("");
    await load();
  };

  const launchTeam = async (team: any) => {
    await apiFetch("/api/v1/runs", {
      method: "POST",
      body: JSON.stringify({
        objective: `Złożona synteza wieloagentowa zespołu: ${team.name}`,
        execution_mode: "multi_agent_team",
        team_id: team.id,
      }),
    });
    navigate("/runs");
  };

  return (
    <div className="space-y-6">
      <PageHeader
        title={t.nav.teams}
        subtitle="Zespoły wieloagentowe z koordynacją DAG, detekcją konfliktów i kontrolą replikacyjną."
      />

      {teams.map((tm) => (
        <Card key={tm.id}>
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div>
              <div className="flex items-center gap-2">
                <Users className="h-4 w-4 text-indigo-400" />
                <h2 className="text-base font-semibold text-zinc-100">{tm.name}</h2>
                <Badge tone="indigo">8 Ról Połączonych</Badge>
              </div>
              <p className="mt-1 text-xs text-zinc-400">{tm.description}</p>
            </div>
            <button
              onClick={() => void launchTeam(tm)}
              className="inline-flex items-center gap-1.5 rounded-lg bg-indigo-600 px-3.5 py-2 text-xs font-semibold text-white hover:bg-indigo-500"
            >
              <Play className="h-3.5 w-3.5" />
              Uruchom Potok Zespołu
            </button>
          </div>

          <div className="mt-4 grid grid-cols-2 gap-2 sm:grid-cols-4">
            {[
              "1. Supervisor Agent",
              "2. Research Planner",
              "3. Source Scout",
              "4. Evidence Analyst",
              "5. Data Analyst",
              "6. Experiment Engineer",
              "7. Critic / Replicator",
              "8. Research Writer",
            ].map((stage) => (
              <div
                key={stage}
                className="rounded-lg border border-zinc-800 bg-zinc-900/80 px-3 py-2 text-xs font-medium text-zinc-200"
              >
                {stage}
              </div>
            ))}
          </div>
        </Card>
      ))}

      <Card>
        <h3 className="mb-3 text-sm font-semibold text-zinc-100">
          Utwórz Nową Konfigurację Zespołu
        </h3>
        <form onSubmit={handleCreate} className="flex flex-col gap-3 sm:flex-row text-xs">
          <input
            required
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="Nazwa zespołu..."
            className="flex-1 rounded-lg border border-zinc-800 bg-zinc-950 px-3 py-2 text-zinc-100"
          />
          <input
            value={desc}
            onChange={(e) => setDesc(e.target.value)}
            placeholder="Opis polityki koordynacji..."
            className="flex-1 rounded-lg border border-zinc-800 bg-zinc-950 px-3 py-2 text-zinc-100"
          />
          <button
            type="submit"
            className="rounded-lg bg-indigo-600 px-4 py-2 font-semibold text-white hover:bg-indigo-500"
          >
            Utwórz Zespół
          </button>
        </form>
      </Card>
    </div>
  );
}

export function RunsExecutionsPage() {
  const { scope, t } = useAuth();
  const [runs, setRuns] = useState<any[]>([]);
  const [projects, setProjects] = useState<any[]>([]);
  const [objective, setObjective] = useState("");
  const [mode, setMode] = useState<"multi_agent_team" | "single_agent">("multi_agent_team");
  const [modelId, setModelId] = useState("apex-analytical-local-v1");
  const [projId, setProjId] = useState("");
  const [simulateInterrupt, setSimulateInterrupt] = useState(false);
  const [asyncExec, setAsyncExec] = useState(false);
  const [autoDiscover, setAutoDiscover] = useState(false);
  const [streamEvents, setStreamEvents] = useState<string[]>([]);

  const load = async () => {
    const [r, p] = await Promise.all([
      apiFetch(`/api/v1/runs?scope=${scope}`),
      apiFetch(`/api/v1/research/projects?scope=${scope}`),
    ]);
    setRuns(r.items || []);
    setProjects(p.items || []);
  };

  useEffect(() => {
    void load();
  }, [scope]);

  const handleReplaySse = async (runId: string) => {
    setStreamEvents([]);
    const sess = loadStoredSession();
    const res = await fetch(`/api/v1/runs/${runId}/stream`, {
      credentials: "include",
      headers: {
        "X-Apex-Session-Token": sess.token || "",
        Authorization: sess.token ? `Bearer ${sess.token}` : "",
        "X-Organization-Id": sess.organizationId || "",
      },
    });
    if (!res.body) {
      const text = await res.text();
      const lines = text
        .split("\n")
        .filter((l) => l.startsWith("data: "))
        .map((l) => l.slice(6));
      setStreamEvents(lines);
      return;
    }
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const parts = buffer.split("\n\n");
      buffer = parts.pop() || "";
      for (const frame of parts) {
        const dataLine = frame
          .split("\n")
          .find((l) => l.startsWith("data: "));
        if (dataLine) {
          const rawJson = dataLine.slice(6);
          setStreamEvents((prev) => [...prev, rawJson]);
        }
      }
      await load();
    }
    await load();
  };

  const handleLaunch = async (e: React.FormEvent) => {
    e.preventDefault();
    const created = await apiFetch("/api/v1/runs", {
      method: "POST",
      body: JSON.stringify({
        objective,
        execution_mode: mode,
        research_project_id: projId || null,
        interrupt_after_step: simulateInterrupt ? 3 : null,
        model_identifier: modelId,
        async_execution: asyncExec && !simulateInterrupt,
        auto_discover_sources: autoDiscover,
      }),
    });
    setObjective("");
    await load();
    if (asyncExec && !simulateInterrupt && created?.id) {
      void handleReplaySse(created.id);
    }
  };

  const handleResume = async (runId: string) => {
    await apiFetch(`/api/v1/runs/${runId}/resume`, { method: "POST" });
    await load();
  };

  const handleCancel = async (runId: string) => {
    await apiFetch(`/api/v1/runs/${runId}/cancel`, { method: "POST" });
    await load();
  };

  return (
    <div className="space-y-6">
      <PageHeader
        title={t.nav.runs}
        subtitle="Trwała orkiestracja DAG z punktami kontrolnymi niezależnymi od kontekstu rozmowy, wykonaniem w tle i strumieniem SSE na żywo."
      />

      <Card>
        <h2 className="mb-3 text-sm font-semibold text-zinc-100">
          Uruchom Nowy Przebieg Badawczy
        </h2>
        <form onSubmit={handleLaunch} className="space-y-3 text-xs">
          <div className="grid grid-cols-1 gap-3 md:grid-cols-4">
            <input
              required
              value={objective}
              onChange={(e) => setObjective(e.target.value)}
              placeholder="Cel badawczy do wykonania..."
              className="rounded-lg border border-zinc-800 bg-zinc-950 px-3 py-2 text-zinc-100"
            />
            <select
              value={mode}
              onChange={(e) => setMode(e.target.value as any)}
              className="rounded-lg border border-zinc-800 bg-zinc-950 px-3 py-2 text-zinc-100"
            >
              <option value="multi_agent_team">
                Zespół 8 Wyspecjalizowanych Agentów (DAG)
              </option>
              <option value="single_agent">
                Pojedynczy Uniwersalny Agent Bazowy (Single-Agent)
              </option>
            </select>
            <select
              value={modelId}
              onChange={(e) => setModelId(e.target.value)}
              className="rounded-lg border border-zinc-800 bg-zinc-950 px-3 py-2 text-zinc-100"
            >
              <option value="apex-analytical-local-v1">APEX Analytical v1 (Auto / Fallback)</option>
              <option value="gpt-4.1-mini">OpenAI · gpt-4.1-mini</option>
              <option value="claude-sonnet-4-0">Anthropic · claude-sonnet-4-0</option>
              <option value="openrouter/auto">OpenRouter · auto</option>
              <option value="llama3.2">Ollama Local · llama3.2</option>
            </select>
            <select
              value={projId}
              onChange={(e) => setProjId(e.target.value)}
              className="rounded-lg border border-zinc-800 bg-zinc-950 px-3 py-2 text-zinc-100"
            >
              <option value="">-- Powiąż z projektem badawczym (opcjonalnie) --</option>
              {projects.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.title}
                </option>
              ))}
            </select>
          </div>
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div className="flex flex-wrap items-center gap-4">
              <label className="flex items-center gap-2 text-zinc-300">
                <input
                  type="checkbox"
                  checked={asyncExec}
                  onChange={(e) => setAsyncExec(e.target.checked)}
                />
                Asynchroniczne wykonanie w tle + strumień SSE na żywo
              </label>
              <label className="flex items-center gap-2 text-zinc-300">
                <input
                  type="checkbox"
                  checked={autoDiscover}
                  onChange={(e) => setAutoDiscover(e.target.checked)}
                />
                Automatyczne odkrywanie literatury (Source Scout)
              </label>
              <label className="flex items-center gap-2 text-zinc-300">
                <input
                  type="checkbox"
                  checked={simulateInterrupt}
                  onChange={(e) => setSimulateInterrupt(e.target.checked)}
                />
                Przerwij celowo po kroku 3 (test wznowienia)
              </label>
            </div>
            <button
              type="submit"
              className="inline-flex items-center gap-1.5 rounded-lg bg-indigo-600 px-4 py-2 font-semibold text-white hover:bg-indigo-500"
            >
              <Play className="h-3.5 w-3.5" />
              Uruchom Wykonanie
            </button>
          </div>
        </form>
      </Card>

      {streamEvents.length > 0 && (
        <Card className="border-indigo-500/40">
          <div className="mb-2 text-xs font-semibold text-indigo-300">
            Odebrane Zdarzenia Strumienia SSE (`text/event-stream`):
          </div>
          <div className="space-y-1 font-mono text-[11px] text-zinc-300">
            {streamEvents.map((ev, i) => (
              <div key={i}>{ev}</div>
            ))}
          </div>
        </Card>
      )}

      <div className="space-y-4">
        {runs.map((r) => (
          <Card key={r.id}>
            <div className="flex flex-wrap items-start justify-between gap-2">
              <div>
                <div className="flex items-center gap-2">
                  <Activity className="h-4 w-4 text-indigo-400" />
                  <span className="font-semibold text-sm text-zinc-100">
                    {r.objective}
                  </span>
                  <Badge
                    tone={
                      r.status === "COMPLETED"
                        ? "emerald"
                        : r.status === "INTERRUPTED"
                        ? "amber"
                        : "rose"
                    }
                  >
                    {r.status} ({r.progress_pct}%)
                  </Badge>
                </div>
                <div className="mt-1 text-xs text-zinc-400">
                  Tryb: {r.execution_mode} · Runtime: {r.runtime_adapter} · Tokeny:{" "}
                  {r.tokens_used} · Koszt: ${r.cost_usd.toFixed(5)} · Narzędzia:{" "}
                  {r.tool_invocations?.length || 0}
                </div>
              </div>

              <div className="flex items-center gap-2 text-xs">
                <button
                  onClick={() => void handleReplaySse(r.id)}
                  className="rounded border border-zinc-700 px-2.5 py-1 text-zinc-200 hover:border-indigo-500"
                >
                  Strumień SSE
                </button>
                {r.status === "INTERRUPTED" && (
                  <button
                    onClick={() => void handleResume(r.id)}
                    className="inline-flex items-center gap-1 rounded bg-emerald-600 px-3 py-1 font-semibold text-white hover:bg-emerald-500"
                  >
                    <RotateCcw className="h-3.5 w-3.5" />
                    Wznów z Checkpointu
                  </button>
                )}
                {r.status === "RUNNING" && (
                  <button
                    onClick={() => void handleCancel(r.id)}
                    className="inline-flex items-center gap-1 rounded bg-rose-600 px-2.5 py-1 text-white"
                  >
                    <StopCircle className="h-3.5 w-3.5" />
                    Anuluj
                  </button>
                )}
              </div>
            </div>

            <div className="mt-3 grid grid-cols-1 gap-2 sm:grid-cols-2 lg:grid-cols-4">
              {(r.steps || []).map((st: any) => (
                <div
                  key={st.id}
                  className="rounded-lg border border-zinc-800 bg-zinc-900/70 p-2.5 text-xs"
                >
                  <div className="flex items-center justify-between">
                    <span className="font-semibold text-zinc-200">
                      #{st.step_index} {st.agent_slug}
                    </span>
                    <Badge tone={st.status === "SUCCEEDED" ? "emerald" : "neutral"}>
                      {st.status}
                    </Badge>
                  </div>
                  <div className="mt-1 truncate text-[11px] text-zinc-400">
                    {st.task_title}
                  </div>
                </div>
              ))}
            </div>
          </Card>
        ))}
      </div>
    </div>
  );
}
