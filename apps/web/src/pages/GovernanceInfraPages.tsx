import React, { useEffect, useState } from "react";
import {
  Area,
  AreaChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import {
  AlertOctagon,
  CheckCircle2,
  Cpu,
  Download,
  GitBranch,
  HardDrive,
  KeyRound,
  Lock,
  Puzzle,
  ShieldAlert,
  ShieldCheck,
  UserPlus,
  XCircle,
} from "lucide-react";
import { apiFetch } from "../api";
import { Badge, Card, PageHeader } from "../components/Layout";
import { useAuth } from "../context/AuthContext";
import { browserLanBridge, BrowserRelayState } from "../lanBridgeRelay";

export function WorkflowsPage() {
  const { t } = useAuth();
  const [data, setData] = useState<any>({ definitions: [], runs: [] });
  const [name, setName] = useState("");
  const [desc, setDesc] = useState("");
  const [risk, setRisk] = useState<"R0" | "R1" | "R2" | "R3">("R1");
  const [err, setErr] = useState<string | null>(null);

  const load = async () => {
    const res = await apiFetch("/api/v1/automation/workflows");
    setData(res);
  };

  useEffect(() => {
    void load();
  }, []);

  const handleCreate = async (e: React.FormEvent) => {
    e.preventDefault();
    setErr(null);
    try {
      await apiFetch("/api/v1/automation/workflows", {
        method: "POST",
        body: JSON.stringify({
          name,
          description: desc,
          risk_level: risk,
          require_approval: risk === "R2" || risk === "R3",
        }),
      });
      setName("");
      setDesc("");
      await load();
    } catch (ex: any) {
      setErr(ex.message);
    }
  };

  const handleResume = async (runId: string) => {
    setErr(null);
    try {
      await apiFetch(`/api/v1/automation/workflows/runs/${runId}/resume`, {
        method: "POST",
      });
      await load();
    } catch (ex: any) {
      setErr(ex.message);
    }
  };

  return (
    <div className="space-y-6">
      <PageHeader
        title={t.nav.workflows}
        subtitle="Trwałe przepływy automatyzacji badań z kluczami idempotencji i bramkami zatwierdzeń R2/R3."
      />

      {err && (
        <div className="rounded-lg border border-rose-500/40 bg-rose-500/10 p-3 text-xs text-rose-300">
          {err}
        </div>
      )}

      <Card>
        <h2 className="mb-3 flex items-center gap-2 text-sm font-semibold text-zinc-100">
          <GitBranch className="h-4 w-4 text-indigo-400" />
          Zdefiniuj i Uruchom Przepływ Pracy
        </h2>
        <form onSubmit={handleCreate} className="grid grid-cols-1 gap-3 md:grid-cols-4 text-xs">
          <input
            required
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="Nazwa przepływu..."
            className="rounded-lg border border-zinc-800 bg-zinc-950 px-3 py-2 text-zinc-100"
          />
          <input
            value={desc}
            onChange={(e) => setDesc(e.target.value)}
            placeholder="Opis kroków..."
            className="rounded-lg border border-zinc-800 bg-zinc-950 px-3 py-2 text-zinc-100"
          />
          <select
            value={risk}
            onChange={(e) => setRisk(e.target.value as any)}
            className="rounded-lg border border-zinc-800 bg-zinc-950 px-3 py-2 text-zinc-100"
          >
            <option value="R0">R0 — Odczyt (wykonaj od razu)</option>
            <option value="R1">R1 — Analiza wewnętrzna (wykonaj od razu)</option>
            <option value="R2">R2 — Kontrolowane (wstrzymaj do zatwierdzenia)</option>
            <option value="R3">R3 — Wysoki wpływ (wymaga ścisłego zatwierdzenia)</option>
          </select>
          <button
            type="submit"
            className="rounded-lg bg-indigo-600 px-4 py-2 font-semibold text-white hover:bg-indigo-500"
          >
            Uruchom Przepływ
          </button>
        </form>
      </Card>

      <div className="space-y-3">
        {(data.runs || []).map((wr: any) => (
          <Card key={wr.id} className="flex flex-wrap items-center justify-between gap-3">
            <div>
              <div className="flex items-center gap-2">
                <span className="font-semibold text-sm text-zinc-100">
                  {wr.workflow_name}
                </span>
                <Badge tone={wr.risk_level === "R3" ? "rose" : wr.risk_level === "R2" ? "amber" : "indigo"}>
                  {wr.risk_level}
                </Badge>
                <Badge tone={wr.status === "COMPLETED" ? "emerald" : "amber"}>
                  {wr.status}
                </Badge>
              </div>
              <div className="mt-1 text-xs text-zinc-400">
                Klucz idempotencji: <code>{wr.idempotency_key}</code>
              </div>
            </div>
            {wr.status === "AWAITING_APPROVAL" && (
              <button
                onClick={() => void handleResume(wr.id)}
                className="rounded-lg bg-emerald-600 px-3.5 py-1.5 text-xs font-semibold text-white hover:bg-emerald-500"
              >
                Wznów po Zatwierdzeniu
              </button>
            )}
          </Card>
        ))}
      </div>
    </div>
  );
}

export function ModelsProvidersPage() {
  const { t } = useAuth();
  const [providers, setProviders] = useState<any[]>([]);
  const [slug, setSlug] = useState("");
  const [name, setName] = useState("");
  const [baseUrl, setBaseUrl] = useState("");
  const [secretSlug, setSecretSlug] = useState("lmstudio-lan-qwen");
  const [apiKeyInput, setApiKeyInput] = useState("");
  const [overrideBaseUrl, setOverrideBaseUrl] = useState("");
  const [testModel, setTestModel] = useState(
    "qwen3.6-35b-a3b-uncensored-hauhaucs-aggressive"
  );
  const [testPrompt, setTestPrompt] = useState(
    "Podsumuj w dwóch zdaniach znaczenie niezależnej weryfikacji źródeł pierwotnych."
  );
  const [testResult, setTestResult] = useState<any>(null);
  const [statusMsg, setStatusMsg] = useState<string | null>(null);
  const [errMsg, setErrMsg] = useState<string | null>(null);
  const [testingNow, setTestingNow] = useState(false);
  const [bridgeStatus, setBridgeStatus] = useState<any>(null);
  const [bridgeScriptPreview, setBridgeScriptPreview] = useState<string | null>(null);
  const [browserRelay, setBrowserRelay] = useState<BrowserRelayState>(
    browserLanBridge.getState()
  );

  const load = async () => {
    try {
      const [res, br] = await Promise.all([
        apiFetch("/api/v1/models/providers"),
        apiFetch("/api/v1/lan-bridge/status").catch(() => null),
      ]);
      setProviders(res.items || []);
      if (br) setBridgeStatus(br);
    } catch (ex: any) {
      setErrMsg(ex.message || "Błąd ładowania dostawców modeli.");
    }
  };

  useEffect(() => {
    void load();
    const unsub = browserLanBridge.subscribe(setBrowserRelay);
    const timer = window.setInterval(() => {
      void apiFetch("/api/v1/lan-bridge/status")
        .then((br) => setBridgeStatus(br))
        .catch(() => {});
    }, 4000);
    return () => {
      unsub();
      window.clearInterval(timer);
    };
  }, []);

  const fetchBridgeScriptText = async () => {
    const origin = encodeURIComponent(window.location.origin);
    const code = await apiFetch<string>(
      `/api/v1/lan-bridge/script.py?origin=${origin}&lmstudio_url=${encodeURIComponent(
        "http://192.168.18.184:1234/v1"
      )}&model=${encodeURIComponent("qwen3.6-35b-a3b-uncensored-hauhaucs-aggressive")}`,
      { rawText: true }
    );
    if (!code.startsWith("#!/usr/bin/env python3")) {
      throw new Error("Nie udało się wygenerować skryptu mostu (zaloguj się ponownie).");
    }
    return code;
  };

  const handleDownloadBridgeScript = async () => {
    setErrMsg(null);
    try {
      const code = await fetchBridgeScriptText();
      setBridgeScriptPreview(code);
      const blob = new Blob([code], { type: "text/x-python;charset=utf-8" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = "apex_lan_bridge.py";
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
      setStatusMsg(
        "Krok 1 gotowy: Zapisz kod poniżej (lub pobrany plik) jako apex_lan_bridge.py, uruchom `python apex_lan_bridge.py`, a następnie kliknij zielony przycisk '2. Połącz przeglądarkę z mostem lokalnym'!"
      );
    } catch (ex: any) {
      setErrMsg(ex.message);
    }
  };

  const copyTextWithIframeFallback = async (text: string): Promise<boolean> => {
    try {
      await navigator.clipboard.writeText(text);
      return true;
    } catch {
      try {
        const ta = document.createElement("textarea");
        ta.value = text;
        ta.style.position = "fixed";
        ta.style.opacity = "0";
        document.body.appendChild(ta);
        ta.focus();
        ta.select();
        const ok = document.execCommand("copy");
        ta.remove();
        return ok;
      } catch {
        return false;
      }
    }
  };

  const handleCopyBridgeScript = async () => {
    setErrMsg(null);
    try {
      const code = await fetchBridgeScriptText();
      setBridgeScriptPreview(code);
      const copied = await copyTextWithIframeFallback(code);
      setStatusMsg(
        copied
          ? "Skopiowano zaktualizowany skrypt apex_lan_bridge.py do schowka! Zapisz go na dysku i uruchom: python apex_lan_bridge.py (skrypt sam przetestuje bezpośredni port chmury E2B)."
          : "Wyświetlono kod poniżej — kliknij w pole kodu, naciśnij Ctrl+A, potem Ctrl+C, zapisz jako apex_lan_bridge.py i uruchom: python apex_lan_bridge.py."
      );
    } catch (ex: any) {
      setErrMsg(ex.message);
    }
  };

  const handleConnectBrowserRelay = async () => {
    setErrMsg(null);
    setStatusMsg(null);
    const st = await browserLanBridge.connectAndStart();
    if (st.active) {
      setStatusMsg(
        `POŁĄCZONO! Przeglądarka przekazuje teraz zapytania Arena Preview prosto do ${st.localTarget} (${st.models.join(", ")}). Możesz uruchomić test lub przebieg 8 agentów!`
      );
      await load();
    } else if (st.lastError) {
      setErrMsg(st.lastError);
    }
  };

  const handleRegister = async (e: React.FormEvent) => {
    e.preventDefault();
    await apiFetch("/api/v1/models/providers", {
      method: "POST",
      body: JSON.stringify({
        slug,
        name,
        category: "compatible_http",
        base_url: baseUrl,
        models: [{ id: `${slug}/default`, capabilities: ["reasoning"] }],
      }),
    });
    setSlug("");
    setName("");
    setBaseUrl("");
    await load();
  };

  const handleSaveSecret = async (e: React.FormEvent) => {
    e.preventDefault();
    setStatusMsg(null);
    const res = await apiFetch(`/api/v1/models/providers/${secretSlug}/secret`, {
      method: "POST",
      body: JSON.stringify({
        api_key: apiKeyInput,
        base_url: overrideBaseUrl || null,
      }),
    });
    setApiKeyInput("");
    setStatusMsg(
      `Zaktualizowano poświadczenia dostawcy '${res.provider_slug}' (${res.health_status}). Klucz nie jest nigdy zwracany w API.`
    );
    await load();
  };

  const handleTestInference = async (e: React.FormEvent) => {
    e.preventDefault();
    setErrMsg(null);
    setTestingNow(true);
    try {
      const res = await apiFetch("/api/v1/models/test-inference", {
        method: "POST",
        body: JSON.stringify({
          model_identifier: testModel,
          prompt: testPrompt,
        }),
      });
      setTestResult(res);
    } catch (ex: any) {
      setErrMsg(ex.message || "Błąd wywołania testu modelu.");
    } finally {
      setTestingNow(false);
    }
  };

  const isAnyBridgeOnline = Boolean(bridgeStatus?.online || browserRelay.active);

  return (
    <div className="space-y-6">
      <PageHeader
        title={t.nav.models}
        subtitle="Dynamiczny rejestr modeli chmurowych (OpenAI, Anthropic, OpenRouter), lokalnych (LM Studio LAN, Ollama) i HTTP z łańcuchami fallback i żywą diagnostyką wnioskowania."
      />

      {errMsg && (
        <div className="rounded-lg border border-rose-500/40 bg-rose-500/10 p-3 text-xs text-rose-200">
          {errMsg}
        </div>
      )}

      {statusMsg && (
        <div className="rounded-lg border border-emerald-500/40 bg-emerald-500/10 p-3 text-xs text-emerald-200">
          {statusMsg}
        </div>
      )}

      <Card className="border-indigo-500/35 bg-gradient-to-r from-indigo-950/35 via-zinc-950/90 to-emerald-950/20">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="space-y-1.5 max-w-2xl">
            <div className="flex flex-wrap items-center gap-2">
              <HardDrive className="h-4 w-4 text-indigo-400" />
              <h2 className="text-sm font-semibold text-zinc-100">
                Most LAN dla Arena Preview (192.168.18.184:1234 ↔ Chmura Arena)
              </h2>
              <Badge tone={isAnyBridgeOnline ? "emerald" : "amber"}>
                {isAnyBridgeOnline
                  ? `POŁĄCZONO Z LM STUDIO (${browserRelay.localTarget || bridgeStatus?.endpoint})`
                  : "OCZEKIWANIE NA POŁĄCZENIE"}
              </Badge>
            </div>
            <p className="text-xs text-zinc-300 leading-relaxed">
              Domena <code>*.arena.site</code> chroni sesję ciasteczkiem przeglądarki, dlatego most
              działa dwustopniowo: <b>(1)</b> pobierz/skopiuj nowy skrypt{" "}
              <code>apex_lan_bridge.py</code> i uruchom <code>python apex_lan_bridge.py</code> na
              komputerze, a następnie <b>(2)</b> kliknij zielony przycisk{" "}
              <b>Połącz przeglądarkę z mostem lokalnym</b> (lub włącz <i>Enable CORS</i> w LM Studio na tym samym PC).
            </p>
            {isAnyBridgeOnline && (
              <div className="text-xs text-emerald-300">
                Tryb: <b>{browserRelay.active ? `Przekaźnik Przeglądarki (${browserRelay.mode})` : bridgeStatus?.bridge_host}</b>{" "}
                · Wykryte modele:{" "}
                <b>{(browserRelay.models.length ? browserRelay.models : bridgeStatus?.models || []).join(", ")}</b>{" "}
                · Zadania LLM: <b>{Math.max(browserRelay.completedJobs, bridgeStatus?.completed_jobs || 0)}</b>
                {browserRelay.btcSynced ? " · Snapshot BTC/USD 30D: WGRANY" : ""}
              </div>
            )}
          </div>
          <div className="flex flex-wrap gap-2 text-xs">
            <button
              type="button"
              onClick={() => void handleCopyBridgeScript()}
              className="inline-flex items-center gap-1.5 rounded-lg bg-indigo-600 px-3.5 py-2 font-semibold text-white hover:bg-indigo-500"
            >
              <Download className="h-3.5 w-3.5" />
              1. Skopiuj / Pobierz apex_lan_bridge.py
            </button>
            <button
              type="button"
              onClick={() => void handleConnectBrowserRelay()}
              className="rounded-lg bg-emerald-600 px-3.5 py-2 font-semibold text-white hover:bg-emerald-500"
            >
              {browserRelay.active
                ? "Połączono z LM Studio (Odśwież)"
                : "2. Połącz przeglądarkę z mostem lokalnym"}
            </button>
          </div>
        </div>
        {typeof window !== "undefined" && window.self !== window.top && !isAnyBridgeOnline && (
          <div className="mt-3 rounded-lg border border-amber-500/30 bg-amber-500/10 p-2.5 text-xs text-amber-100">
            <div className="mb-1 font-medium">
              Wskazówka (jeśli ramka Arena blokuje połączenie z <code>127.0.0.1</code>): skopiuj ręcznie poniższy adres (<code>Ctrl+C</code>), otwórz nową kartę w przeglądarce (<code>Ctrl+T</code>) i wklej go w pasek adresu (<code>Ctrl+V</code>):
            </div>
            <input
              readOnly
              value={window.location.href}
              onFocus={(e) => e.currentTarget.select()}
              onClick={(e) => e.currentTarget.select()}
              className="w-full rounded border border-amber-400/40 bg-zinc-950 px-2.5 py-1.5 font-mono text-[11px] text-amber-200"
            />
          </div>
        )}
        {bridgeScriptPreview && (
          <div className="mt-3 space-y-1.5">
            <div className="flex items-center justify-between text-[11px] text-zinc-400">
              <span>
                Zapisz ten kod jako <code>apex_lan_bridge.py</code> na komputerze i uruchom:{" "}
                <code>python apex_lan_bridge.py</code>
              </span>
              <button
                type="button"
                onClick={() => void handleDownloadBridgeScript()}
                className="text-indigo-300 underline"
              >
                Zapisz jako plik .py
              </button>
            </div>
            <textarea
              readOnly
              rows={8}
              value={bridgeScriptPreview}
              className="w-full rounded-lg border border-zinc-800 bg-zinc-950 p-2.5 font-mono text-[11px] text-zinc-300"
            />
          </div>
        )}
      </Card>

      <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
        {providers.map((p) => (
          <Card key={p.id}>
            <div className="flex items-start justify-between gap-2">
              <div>
                <div className="flex items-center gap-2">
                  <Cpu className="h-4 w-4 text-indigo-400" />
                  <span className="font-semibold text-sm text-zinc-100">{p.name}</span>
                </div>
                <div className="mt-1 text-xs text-zinc-400">
                  Kategoria: {p.category} · Endpoint: <code>{p.base_url}</code>
                </div>
              </div>
              <Badge
                tone={
                  p.health_status.startsWith("READY") ||
                  p.health_status === "CONFIGURED" ||
                  p.health_status === "BRIDGE_ONLINE_LAN"
                    ? "emerald"
                    : "amber"
                }
              >
                {p.health_status}
              </Badge>
            </div>
            <div className="mt-3 text-xs text-zinc-400">
              Fallback: <b>{p.fallback_provider_slug || "brak"}</b> · Limit:{" "}
              {p.rate_limit_rpm} RPM · Budżet: ${p.budget_limit_usd}
            </div>
          </Card>
        ))}
      </div>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <Card>
          <h3 className="mb-3 flex items-center gap-2 text-sm font-semibold text-zinc-100">
            <KeyRound className="h-4 w-4 text-indigo-400" />
            Powiąż Klucz API Dostawcy lub Endpoint Ollama (Sesja Runtime)
          </h3>
          <form onSubmit={handleSaveSecret} className="space-y-3 text-xs">
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
              <select
                value={secretSlug}
                onChange={(e) => setSecretSlug(e.target.value)}
                className="rounded-lg border border-zinc-800 bg-zinc-950 px-3 py-2 text-zinc-100"
              >
                {providers
                  .filter((p) => p.slug !== "apex-analytical")
                  .map((p) => (
                    <option key={p.slug} value={p.slug}>
                      {p.name} ({p.slug})
                    </option>
                  ))}
              </select>
              <input
                type="password"
                value={apiKeyInput}
                onChange={(e) => setApiKeyInput(e.target.value)}
                placeholder="Klucz API (np. sk-... lub sk-ant-...)"
                className="rounded-lg border border-zinc-800 bg-zinc-950 px-3 py-2 text-zinc-100"
              />
            </div>
            <div className="flex gap-2">
              <input
                value={overrideBaseUrl}
                onChange={(e) => setOverrideBaseUrl(e.target.value)}
                placeholder="Opcjonalnie nadpisz URL (np. http://192.168.1.10:11434)"
                className="flex-1 rounded-lg border border-zinc-800 bg-zinc-950 px-3 py-2 text-zinc-100"
              />
              <button
                type="submit"
                className="rounded-lg bg-indigo-600 px-4 py-2 font-semibold text-white hover:bg-indigo-500"
              >
                Zapisz Klucz
              </button>
            </div>
          </form>
        </Card>

        <Card>
          <h3 className="mb-3 flex items-center gap-2 text-sm font-semibold text-zinc-100">
            <CheckCircle2 className="h-4 w-4 text-emerald-400" />
            Test Wnioskowania Live LLM i Łańcucha Fallback
          </h3>
          <form onSubmit={handleTestInference} className="space-y-3 text-xs">
            <div className="flex gap-2">
              <select
                value={testModel}
                onChange={(e) => setTestModel(e.target.value)}
                className="flex-1 rounded-lg border border-zinc-800 bg-zinc-950 px-3 py-2 text-zinc-100"
              >
                <option value="qwen3.6-35b-a3b-uncensored-hauhaucs-aggressive">
                  LM Studio LAN (192.168.18.184:1234) · qwen3.6-35b-a3b-uncensored-hauhaucs-aggressive
                </option>
                <option value="gpt-4.1-mini">OpenAI · gpt-4.1-mini</option>
                <option value="claude-sonnet-4-0">Anthropic · claude-sonnet-4-0</option>
                <option value="llama3.2">Ollama · llama3.2</option>
                <option value="apex-analytical-local-v1">APEX Analytical v1</option>
              </select>
              <button
                type="submit"
                disabled={testingNow}
                className="rounded-lg bg-emerald-600 px-4 py-2 font-semibold text-white hover:bg-emerald-500 disabled:opacity-50"
              >
                {testingNow ? "Testowanie..." : "Testuj Model"}
              </button>
            </div>
            <input
              value={testPrompt}
              onChange={(e) => setTestPrompt(e.target.value)}
              className="w-full rounded-lg border border-zinc-800 bg-zinc-950 px-3 py-2 text-zinc-100"
            />
          </form>
          {testResult && (
            <div className="mt-3 rounded-lg border border-zinc-800 bg-zinc-950/80 p-3 text-xs text-zinc-300">
              <div className="mb-1 flex flex-wrap items-center gap-2 text-[11px] text-zinc-400">
                <Badge tone={testResult.live_llm_invoked ? "emerald" : "amber"}>
                  {testResult.live_llm_invoked ? "LIVE LLM" : "LOCAL FALLBACK"}
                </Badge>
                <span>
                  {testResult.provider_slug} / {testResult.model_identifier}
                </span>
                <span>· {testResult.latency_ms} ms</span>
                <span>· {testResult.total_tokens} tok</span>
                <span>· ${testResult.cost_usd}</span>
              </div>
              <p className="text-zinc-200">{testResult.content}</p>
            </div>
          )}
        </Card>
      </div>

      <Card>
        <h3 className="mb-3 text-sm font-semibold text-zinc-100">
          Zarejestruj Kompatybilnego Dostawcę HTTP (OpenAI-Compatible / vLLM / LiteLLM)
        </h3>
        <form onSubmit={handleRegister} className="grid grid-cols-1 gap-3 md:grid-cols-4 text-xs">
          <input
            required
            value={slug}
            onChange={(e) => setSlug(e.target.value)}
            placeholder="Identyfikator slug (np. vllm-cluster)"
            className="rounded-lg border border-zinc-800 bg-zinc-950 px-3 py-2 text-zinc-100"
          />
          <input
            required
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="Nazwa wyświetlana..."
            className="rounded-lg border border-zinc-800 bg-zinc-950 px-3 py-2 text-zinc-100"
          />
          <input
            required
            value={baseUrl}
            onChange={(e) => setBaseUrl(e.target.value)}
            placeholder="https://llm-gateway.example.org/v1"
            className="rounded-lg border border-zinc-800 bg-zinc-950 px-3 py-2 text-zinc-100"
          />
          <button
            type="submit"
            className="rounded-lg bg-indigo-600 px-4 py-2 font-semibold text-white hover:bg-indigo-500"
          >
            Dodaj Dostawcę
          </button>
        </form>
      </Card>
    </div>
  );
}

export function LocalNodesPage() {
  const { t } = useAuth();
  const [devices, setDevices] = useState<any[]>([]);
  const [gwStatus, setGwStatus] = useState<any>(null);
  const [name, setName] = useState("");
  const [dirs, setDirs] = useState("/Users/researcher/apex-workspace");
  const [canRead, setCanRead] = useState(true);
  const [canWrite, setCanWrite] = useState(false);
  const [canExec, setCanExec] = useState(false);
  const [pairingTokenOnce, setPairingTokenOnce] = useState<string | null>(null);

  const load = async () => {
    const [d, g] = await Promise.all([
      apiFetch("/api/v1/local-nodes/devices"),
      apiFetch("/api/v1/local-node/status"),
    ]);
    setDevices(d.items || []);
    setGwStatus(g);
  };

  useEffect(() => {
    void load();
  }, []);

  const handleRegister = async (e: React.FormEvent) => {
    e.preventDefault();
    const res = await apiFetch("/api/v1/local-nodes/devices", {
      method: "POST",
      body: JSON.stringify({
        name,
        allowed_directories: dirs
          .split(",")
          .map((x) => x.trim())
          .filter(Boolean),
        can_read: canRead,
        can_write: canWrite,
        can_execute: canExec,
      }),
    });
    setPairingTokenOnce(res.pairing_token_once);
    setName("");
    await load();
  };

  const handleRevoke = async (id: string) => {
    if (!window.confirm("Czy na pewno unieważnić poświadczenia tego Węzła Lokalnego (R3)?")) {
      return;
    }
    await apiFetch(`/api/v1/local-nodes/devices/${id}/revoke`, {
      method: "POST",
    });
    await load();
  };

  return (
    <div className="space-y-6">
      <PageHeader
        title={t.nav.localNodes}
        subtitle="Architektura Hybrydowa APEX ONE: parowanie urządzeń z wygasającymi tokenami, oddzielne uprawnienia Odczyt / Zapis / Wykonanie i brak zdalnego terminala."
      />

      <Card className="border-amber-500/30 bg-amber-500/5">
        <div className="text-xs text-amber-200">
          <b>Rzetelny Status Środowiska Arena:</b> Protokół bramki Węzła Lokalnego (`{gwStatus?.protocol_version}`) działa i jest zweryfikowany testami HMAC/WebSocket, natomiast fizyczny komputer użytkownika posiada status{" "}
          <code>user_machine_connected = {String(gwStatus?.user_machine_connected ?? false)}</code>{" "}
          (środowisko chmurowe Arena jest odizolowane od sieci lokalnej użytkownika). Aby połączyć własną maszynę po wdrożeniu, uruchom <code>apex-local-node</code> w katalogu <code>local_node/</code>.
        </div>
      </Card>

      {pairingTokenOnce && (
        <Card className="border-emerald-500/40 bg-emerald-500/10">
          <div className="text-xs text-emerald-200">
            <b>Jednorazowy wygasający token parowania (ważny 24h — skopiuj teraz):</b>
            <div className="mt-1 font-mono text-sm text-white">{pairingTokenOnce}</div>
          </div>
        </Card>
      )}

      <Card>
        <h2 className="mb-3 flex items-center gap-2 text-sm font-semibold text-zinc-100">
          <HardDrive className="h-4 w-4 text-indigo-400" />
          Sparuj Nowy Węzeł Lokalny (Jawny Podział Uprawnień Read / Write / Execute)
        </h2>
        <form onSubmit={handleRegister} className="space-y-3 text-xs">
          <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
            <input
              required
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="Nazwa stacji roboczej (np. Stacja GPU Lab-02)"
              className="rounded-lg border border-zinc-800 bg-zinc-950 px-3 py-2 text-zinc-100"
            />
            <input
              required
              value={dirs}
              onChange={(e) => setDirs(e.target.value)}
              placeholder="Dozwolone katalogi lokalne (oddzielone przecinkiem)"
              className="rounded-lg border border-zinc-800 bg-zinc-950 px-3 py-2 text-zinc-100"
            />
          </div>
          <div className="flex flex-wrap items-center gap-5">
            <label className="flex items-center gap-2">
              <input
                type="checkbox"
                checked={canRead}
                onChange={(e) => setCanRead(e.target.checked)}
              />
              Zezwól na lokalny ODCZYT (read)
            </label>
            <label className="flex items-center gap-2">
              <input
                type="checkbox"
                checked={canWrite}
                onChange={(e) => setCanWrite(e.target.checked)}
              />
              Zezwól na lokalny ZAPIS (write)
            </label>
            <label className="flex items-center gap-2">
              <input
                type="checkbox"
                checked={canExec}
                onChange={(e) => setCanExec(e.target.checked)}
              />
              Zezwól na typowane WYKONANIE zadań (execute — bez terminala shell!)
            </label>
          </div>
          <button
            type="submit"
            className="rounded-lg bg-indigo-600 px-4 py-2 font-semibold text-white hover:bg-indigo-500"
          >
            Wygeneruj Token Parowania Węzła
          </button>
        </form>
      </Card>

      <div className="space-y-3">
        {devices.map((d) => (
          <Card key={d.id} className="flex flex-wrap items-center justify-between gap-3">
            <div>
              <div className="flex items-center gap-2">
                <span className="font-semibold text-sm text-zinc-100">{d.name}</span>
                <Badge tone={d.status === "REVOKED" ? "rose" : "amber"}>{d.status}</Badge>
              </div>
              <div className="mt-1 text-xs text-zinc-400">
                Node ID: <code>{d.node_id}</code> · Katalogi:{" "}
                {(d.allowed_directories || []).join(", ")} · Uprawnienia: R=
                {String(d.capability_grants?.read)} W={String(d.capability_grants?.write)} X=
                {String(d.capability_grants?.execute)}
              </div>
            </div>
            {d.status !== "REVOKED" && (
              <button
                onClick={() => void handleRevoke(d.id)}
                className="rounded-lg border border-rose-500/40 bg-rose-500/10 px-3 py-1.5 text-xs font-medium text-rose-300 hover:bg-rose-500/20"
              >
                Unieważnij Urządzenie (Revoke)
              </button>
            )}
          </Card>
        ))}
      </div>
    </div>
  );
}

export function ApprovalCenterPage() {
  const { t } = useAuth();
  const [gov, setGov] = useState<any>(null);
  const [note, setNote] = useState("");
  const [err, setErr] = useState<string | null>(null);

  const load = async () => {
    const res = await apiFetch("/api/v1/governance");
    setGov(res);
  };

  useEffect(() => {
    void load();
  }, []);

  const handleDecision = async (id: string, decision: "APPROVED" | "REJECTED") => {
    setErr(null);
    try {
      await apiFetch(`/api/v1/governance/approvals/${id}/decide`, {
        method: "POST",
        body: JSON.stringify({ decision, decision_note: note }),
      });
      setNote("");
      await load();
    } catch (ex: any) {
      setErr(ex.message);
    }
  };

  return (
    <div className="space-y-6">
      <PageHeader
        title={t.nav.approvals}
        subtitle="Centrum Zatwierdzeń R2/R3. Każda zgoda jest kryptograficznie powiązana z sumą SHA-256 parametrów i wygasa automatycznie po ich zmianie."
      />

      {err && (
        <div className="rounded-lg border border-rose-500/40 bg-rose-500/10 p-3 text-xs text-rose-300">
          {err}
        </div>
      )}

      <div className="space-y-3">
        {(gov?.approvals || []).map((ap: any) => (
          <Card key={ap.id}>
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div>
                <div className="flex items-center gap-2">
                  <Badge tone={ap.risk_level === "R3" ? "rose" : "amber"}>
                    {ap.risk_level}
                  </Badge>
                  <span className="font-semibold text-sm text-zinc-100">
                    {ap.operation_type}
                  </span>
                  <Badge
                    tone={
                      ap.status === "APPROVED"
                        ? "emerald"
                        : ap.status === "PENDING"
                        ? "amber"
                        : "rose"
                    }
                  >
                    {ap.status}
                  </Badge>
                </div>
                <div className="mt-1 text-xs text-zinc-400">
                  SHA-256 parametrów: <code>{ap.parameters_sha256.slice(0, 24)}...</code> ·
                  Wygasa: {new Date(ap.expires_at).toLocaleString()}
                </div>
                <pre className="mt-2 rounded bg-zinc-950 p-2 text-[11px] text-zinc-300">
                  {JSON.stringify(ap.parameters_json, null, 2)}
                </pre>
              </div>

              {ap.status === "PENDING" && (
                <div className="flex flex-col gap-2 text-xs">
                  <input
                    value={note}
                    onChange={(e) => setNote(e.target.value)}
                    placeholder="Notatka decyzyjna..."
                    className="rounded border border-zinc-800 bg-zinc-950 px-2.5 py-1.5 text-zinc-100"
                  />
                  <div className="flex gap-2">
                    <button
                      onClick={() => void handleDecision(ap.id, "APPROVED")}
                      className="inline-flex items-center gap-1 rounded bg-emerald-600 px-3 py-1.5 font-semibold text-white hover:bg-emerald-500"
                    >
                      <CheckCircle2 className="h-3.5 w-3.5" />
                      Zatwierdź
                    </button>
                    <button
                      onClick={() => void handleDecision(ap.id, "REJECTED")}
                      className="inline-flex items-center gap-1 rounded bg-rose-600 px-3 py-1.5 font-semibold text-white hover:bg-rose-500"
                    >
                      <XCircle className="h-3.5 w-3.5" />
                      Odrzuć
                    </button>
                  </div>
                </div>
              )}
            </div>
          </Card>
        ))}
      </div>
    </div>
  );
}

export function AuditLogPage() {
  const { t } = useAuth();
  const [gov, setGov] = useState<any>(null);
  const [immutabilityMsg, setImmutabilityMsg] = useState<string | null>(null);

  const load = async () => {
    const res = await apiFetch("/api/v1/governance");
    setGov(res);
  };

  useEffect(() => {
    void load();
  }, []);

  const testImmutability = async (entryId: string) => {
    try {
      await apiFetch(`/api/v1/governance/audit/${entryId}`, { method: "DELETE" });
    } catch (err: any) {
      setImmutabilityMsg(
        `Ochrona Append-Only Potwierdzona (${err.code}): ${err.message}`
      );
    }
  };

  return (
    <div className="space-y-6">
      <PageHeader
        title={t.nav.audit}
        subtitle="Niezmienny (Append-Only) dziennik audytu z łańcuchem skrótów SHA-256 i wyzwalaczem bazodanowym blokującym UPDATE/DELETE."
        actions={
          gov?.audit_chain_integrity && (
            <Badge tone={gov.audit_chain_integrity.valid ? "emerald" : "rose"}>
              <ShieldCheck className="h-3.5 w-3.5" />
              Łańcuch SHA-256:{" "}
              {gov.audit_chain_integrity.valid ? "SPÓJNY" : "NARUSZONY"} (
              {gov.audit_chain_integrity.entries_checked} wpisów)
            </Badge>
          )
        }
      />

      {immutabilityMsg && (
        <div className="rounded-lg border border-indigo-500/40 bg-indigo-500/10 p-3 text-xs text-indigo-200">
          {immutabilityMsg}
        </div>
      )}

      <Card className="overflow-x-auto p-0">
        <table className="w-full text-left text-xs">
          <thead className="border-b border-zinc-800 bg-zinc-900/80 text-zinc-400">
            <tr>
              <th className="px-4 py-3">#</th>
              <th className="px-4 py-3">Akcja</th>
              <th className="px-4 py-3">Zasób</th>
              <th className="px-4 py-3">Ryzyko</th>
              <th className="px-4 py-3">Skrót łańcucha SHA-256</th>
              <th className="px-4 py-3">Ochrona</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-zinc-800/70">
            {(gov?.audit_events || []).map((ev: any) => (
              <tr key={ev.id} className="hover:bg-zinc-900/40">
                <td className="px-4 py-2.5 font-mono text-zinc-400">
                  {ev.sequence_no}
                </td>
                <td className="px-4 py-2.5 font-medium text-zinc-100">
                  {ev.action}
                </td>
                <td className="px-4 py-2.5 text-zinc-300">
                  {ev.resource_type}:{ev.resource_id.slice(0, 8)}
                </td>
                <td className="px-4 py-2.5">
                  <Badge tone={ev.risk_level === "R3" ? "rose" : ev.risk_level === "R2" ? "amber" : "neutral"}>
                    {ev.risk_level}
                  </Badge>
                </td>
                <td className="px-4 py-2.5 font-mono text-[11px] text-indigo-300">
                  {ev.entry_hash.slice(0, 20)}...
                </td>
                <td className="px-4 py-2.5">
                  <button
                    onClick={() => void testImmutability(ev.id)}
                    className="rounded border border-zinc-700 px-2 py-0.5 text-[11px] text-zinc-300 hover:border-indigo-500"
                  >
                    Testuj blokadę usunięcia
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </Card>
    </div>
  );
}

export function AnalyticsPage() {
  const { scope, t } = useAuth();
  const [summary, setSummary] = useState<any>(null);

  useEffect(() => {
    void apiFetch(`/api/v1/analytics/summary?scope=${scope}`).then(setSummary);
  }, [scope]);

  const handleExportCsv = async () => {
    const csv = await apiFetch<string>(
      `/api/v1/analytics/export.csv?scope=${scope}`,
      { rawText: true }
    );
    const blob = new Blob([csv], { type: "text/csv;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `apex-one-usage-${scope}.csv`;
    a.click();
    URL.revokeObjectURL(url);
  };

  if (!summary) return <div className="text-sm text-zinc-400">Ładowanie analityki...</div>;

  const series = (summary.usage_series || []).map((u: any, idx: number) => ({
    idx: `#${idx + 1}`,
    tokens: u.total_tokens,
    tools: u.tool_calls_count,
  }));

  return (
    <div className="space-y-6">
      <PageHeader
        title={t.nav.analytics}
        subtitle="Rzeczywiste zużycie tokenów, koszty USD, czas wykonania, wywołania narzędzi i utylizacja budżetu (0% zmyślonych metryk)."
        actions={
          <button
            onClick={() => void handleExportCsv()}
            className="inline-flex items-center gap-1.5 rounded-lg bg-indigo-600 px-3.5 py-2 text-xs font-semibold text-white hover:bg-indigo-500"
          >
            <Download className="h-3.5 w-3.5" />
            {t.common.exportCsv}
          </button>
        }
      />

      <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
        <Card>
          <div className="text-xs text-zinc-400">Uruchomienia ogółem</div>
          <div className="mt-1 text-2xl font-bold">{summary.agent_execution_count}</div>
        </Card>
        <Card>
          <div className="text-xs text-zinc-400">Wskaźnik błędów</div>
          <div className="mt-1 text-2xl font-bold text-emerald-400">
            {summary.failure_rate_pct}%
          </div>
        </Card>
        <Card>
          <div className="text-xs text-zinc-400">Łączne tokeny</div>
          <div className="mt-1 text-2xl font-bold text-indigo-300">
            {summary.total_tokens_consumed.toLocaleString()}
          </div>
        </Card>
        <Card>
          <div className="text-xs text-zinc-400">Koszt USD / Limit</div>
          <div className="mt-1 text-2xl font-bold">
            ${summary.total_model_cost_usd.toFixed(4)} / ${summary.budget_limit_usd}
          </div>
        </Card>
      </div>

      <Card>
        <h2 className="mb-3 text-sm font-semibold text-zinc-100">
          Profil Zużycia Tokenów w Kolejnych Przebiegach Badawczych
        </h2>
        <div className="h-64 w-full">
          <ResponsiveContainer width="100%" height="100%">
            <AreaChart data={series}>
              <CartesianGrid strokeDasharray="3 3" stroke="#27272a" />
              <XAxis dataKey="idx" tick={{ fill: "#a1a1aa", fontSize: 11 }} />
              <YAxis tick={{ fill: "#a1a1aa", fontSize: 11 }} />
              <Tooltip
                contentStyle={{
                  backgroundColor: "#121620",
                  borderColor: "#3f3f46",
                  fontSize: "12px",
                }}
              />
              <Area
                type="monotone"
                dataKey="tokens"
                stroke="#6366f1"
                fill="#6366f1"
                fillOpacity={0.25}
              />
            </AreaChart>
          </ResponsiveContainer>
        </div>
      </Card>
    </div>
  );
}

export function ExtensionsPage() {
  const { t } = useAuth();
  const [items, setItems] = useState<any[]>([]);

  const load = async () => {
    const res = await apiFetch("/api/v1/extensions");
    setItems(res.items || []);
  };

  useEffect(() => {
    void load();
  }, []);

  const handleToggle = async (slug: string, enabled: boolean) => {
    await apiFetch(`/api/v1/extensions/${slug}`, {
      method: "PATCH",
      body: JSON.stringify({ enabled }),
    });
    await load();
  };

  return (
    <div className="space-y-6">
      <PageHeader
        title={t.nav.extensions}
        subtitle="Wbudowane adaptery badawcze (Dokumenty Lokalne, GitHub, Ollama, Web Search, REST z ochroną SSRF) oraz izolowane gniazda przyszłych rozszerzeń."
      />

      <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
        {items.map((ext) => (
          <Card key={ext.slug} className="flex flex-col justify-between">
            <div>
              <div className="flex items-start justify-between gap-2">
                <div className="flex items-center gap-2">
                  <Puzzle className="h-4 w-4 text-indigo-400" />
                  <span className="font-semibold text-sm text-zinc-100">
                    {ext.name}
                  </span>
                </div>
                <Badge tone={ext.enabled ? "emerald" : "neutral"}>
                  {ext.enabled ? "WŁĄCZONE" : "WYŁĄCZONE"}
                </Badge>
              </div>
              <div className="mt-1 text-xs text-zinc-400">
                Slug: <code>{ext.slug}</code> · Kompatybilność:{" "}
                <code>{ext.compatible_core_range}</code>
              </div>
              {ext.live_health && (
                <div className="mt-2 rounded bg-zinc-900/80 p-2 text-[11px] text-zinc-300">
                  Live Health: <b>{ext.live_health.status}</b> — {ext.live_health.detail}
                </div>
              )}
            </div>

            <div className="mt-3 flex items-center justify-between border-t border-zinc-800 pt-2.5 text-xs">
              <span className="text-zinc-500">
                Uprawnienia: {(ext.permissions || []).join(", ")}
              </span>
              <button
                onClick={() => void handleToggle(ext.slug, !ext.enabled)}
                className="rounded border border-zinc-700 px-2.5 py-1 text-zinc-200 hover:border-indigo-500"
              >
                {ext.enabled ? "Wyłącz" : "Włącz"}
              </button>
            </div>
          </Card>
        ))}
      </div>
    </div>
  );
}

export function SettingsAdminPage() {
  const { activeOrgId, organizations, refreshProfile, t } = useAuth();
  const [orgData, setOrgData] = useState<any>(null);
  const [newOrgName, setNewOrgName] = useState("");
  const [inviteEmail, setInviteEmail] = useState("");
  const [inviteRole, setInviteRole] = useState("Researcher");
  const [apiKeyName, setApiKeyName] = useState("");
  const [createdKeyOnce, setCreatedKeyOnce] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);

  const activeOrg = organizations.find((o) => o.organization_id === activeOrgId);

  const load = async () => {
    const res = await apiFetch("/api/v1/organizations");
    setOrgData(res);
  };

  useEffect(() => {
    void load();
  }, [activeOrgId]);

  const handleCreateOrg = async (e: React.FormEvent) => {
    e.preventDefault();
    await apiFetch("/api/v1/organizations", {
      method: "POST",
      body: JSON.stringify({ name: newOrgName }),
    });
    setNewOrgName("");
    await refreshProfile();
    await load();
  };

  const handleInvite = async (e: React.FormEvent) => {
    e.preventDefault();
    const inv = await apiFetch("/api/v1/organizations/invitations", {
      method: "POST",
      body: JSON.stringify({ email: inviteEmail, role_name: inviteRole }),
    });
    setMsg(`Wysłano zaproszenie dla ${inv.email} (rola: ${inv.role_name}).`);
    setInviteEmail("");
    await load();
  };

  const handleCreateKey = async (e: React.FormEvent) => {
    e.preventDefault();
    const res = await apiFetch("/api/v1/organizations/api-credentials", {
      method: "POST",
      body: JSON.stringify({ name: apiKeyName }),
    });
    setCreatedKeyOnce(res.secret_key_once);
    setApiKeyName("");
    await load();
  };

  const handleEmergencyStop = async (nextActive: boolean) => {
    if (
      nextActive &&
      !window.confirm(
        "AKTYWOWAĆ AWARYJNY STOP (R3)? Wszystkie aktywne przebiegi w organizacji zostaną natychmiast zatrzymane."
      )
    ) {
      return;
    }
    await apiFetch("/api/v1/governance/emergency-stop", {
      method: "POST",
      body: JSON.stringify({
        active: nextActive,
        reason: "Przełączono z panelu Administracji",
      }),
    });
    await refreshProfile();
  };

  return (
    <div className="space-y-6">
      <PageHeader
        title={t.nav.settings}
        subtitle="Zarządzanie organizacjami wielodostępnymi, rolami RBAC (6 ról), kluczami API oraz wyłącznikiem bezpieczeństwa Emergency Stop."
      />

      {msg && (
        <div className="rounded-lg border border-emerald-500/40 bg-emerald-500/10 p-3 text-xs text-emerald-300">
          {msg}
        </div>
      )}

      {createdKeyOnce && (
        <Card className="border-indigo-500/40 bg-indigo-500/10">
          <div className="text-xs text-indigo-200">
            <b>Wygenerowany Klucz API (wyświetlany tylko raz):</b>
            <div className="mt-1 font-mono text-sm text-white">{createdKeyOnce}</div>
          </div>
        </Card>
      )}

      <Card className="border-rose-500/35">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <div className="flex items-center gap-2 text-sm font-semibold text-rose-300">
              <AlertOctagon className="h-4 w-4" />
              Wyłącznik Bezpieczeństwa Organizacji (Emergency Stop — Poziom R3)
            </div>
            <p className="mt-1 text-xs text-zinc-400">
              Natychmiast anuluje trwające przebiegi agentów i blokuje uruchamianie nowych zadań w bieżącej organizacji.
            </p>
          </div>
          <button
            onClick={() =>
              void handleEmergencyStop(!activeOrg?.emergency_stop_active)
            }
            className={`rounded-lg px-4 py-2 text-xs font-semibold text-white ${
              activeOrg?.emergency_stop_active
                ? "bg-emerald-600 hover:bg-emerald-500"
                : "bg-rose-600 hover:bg-rose-500"
            }`}
          >
            {activeOrg?.emergency_stop_active
              ? "Odblokuj Wykonania Organizacji"
              : "AKTYWUJ EMERGENCY STOP"}
          </button>
        </div>
      </Card>

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
        <Card>
          <h3 className="mb-3 flex items-center gap-2 text-sm font-semibold text-zinc-100">
            <UserPlus className="h-4 w-4 text-indigo-400" />
            Zaproś Członka Zespołu (6 Ról RBAC)
          </h3>
          <form onSubmit={handleInvite} className="flex flex-wrap gap-2 text-xs">
            <input
              type="email"
              required
              value={inviteEmail}
              onChange={(e) => setInviteEmail(e.target.value)}
              placeholder="email@instytut.pl"
              className="flex-1 rounded-lg border border-zinc-800 bg-zinc-950 px-3 py-2 text-zinc-100"
            />
            <select
              value={inviteRole}
              onChange={(e) => setInviteRole(e.target.value)}
              className="rounded-lg border border-zinc-800 bg-zinc-950 px-3 py-2 text-zinc-100"
            >
              {[
                "Owner",
                "Administrator",
                "Researcher",
                "Operator",
                "Evaluator",
                "Viewer",
              ].map((r) => (
                <option key={r} value={r}>
                  {r}
                </option>
              ))}
            </select>
            <button
              type="submit"
              className="rounded-lg bg-indigo-600 px-3.5 py-2 font-semibold text-white hover:bg-indigo-500"
            >
              Wyślij Zaproszenie
            </button>
          </form>

          <div className="mt-4 space-y-1.5 text-xs">
            {(orgData?.members || []).map((m: any) => (
              <div
                key={m.id}
                className="flex items-center justify-between rounded border border-zinc-800 bg-zinc-900/60 px-3 py-1.5"
              >
                <span>
                  {m.user?.display_name} ({m.user?.email})
                </span>
                <Badge tone="indigo">{m.role_name}</Badge>
              </div>
            ))}
          </div>
        </Card>

        <Card className="space-y-5">
          <div>
            <h3 className="mb-2 flex items-center gap-2 text-sm font-semibold text-zinc-100">
              <KeyRound className="h-4 w-4 text-indigo-400" />
              Poświadczenia Programistyczne API
            </h3>
            <form onSubmit={handleCreateKey} className="flex gap-2 text-xs">
              <input
                required
                value={apiKeyName}
                onChange={(e) => setApiKeyName(e.target.value)}
                placeholder="Nazwa klucza (np. CI Benchmark Runner)"
                className="flex-1 rounded-lg border border-zinc-800 bg-zinc-950 px-3 py-2 text-zinc-100"
              />
              <button
                type="submit"
                className="rounded-lg bg-indigo-600 px-3.5 py-2 font-semibold text-white hover:bg-indigo-500"
              >
                Utwórz Klucz
              </button>
            </form>
          </div>

          <div className="border-t border-zinc-800 pt-4">
            <h3 className="mb-2 text-sm font-semibold text-zinc-100">
              Utwórz Nową Izolowaną Organizację (Multi-Tenant)
            </h3>
            <form onSubmit={handleCreateOrg} className="flex gap-2 text-xs">
              <input
                required
                value={newOrgName}
                onChange={(e) => setNewOrgName(e.target.value)}
                placeholder="Nazwa nowej organizacji..."
                className="flex-1 rounded-lg border border-zinc-800 bg-zinc-950 px-3 py-2 text-zinc-100"
              />
              <button
                type="submit"
                className="rounded-lg border border-indigo-500/50 bg-indigo-500/15 px-3.5 py-2 font-semibold text-indigo-200"
              >
                Utwórz Organizację
              </button>
            </form>
          </div>
        </Card>
      </div>
    </div>
  );
}
