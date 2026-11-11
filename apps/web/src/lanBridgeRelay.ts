/**
 * Opt-in Browser-Assisted Local LLM Bridge Relay for Cloud Previews (*.arena.site).
 *
 * Because cloud preview proxies such as `*.arena.site` authenticate viewers via
 * browser cookies, external CLI tools outside the browser may be blocked from
 * reaching `/api/v1/lan-bridge/poll` directly. When the user clicks "Połącz
 * przeglądarkę z lokalnym mostem / LM Studio", this worker runs inside the
 * authenticated Arena Preview browser tab:
 *   1. Probes the user's local `apex_lan_bridge.py` CORS/PNA relay
 *      (`http://127.0.0.1:18765`) or direct local LM Studio (`:1234`).
 *   2. Imports the live 90D/30D BTC/USD market snapshot into `/api/v1/sources`.
 *   3. Relays `/api/v1/lan-bridge/poll` -> local Qwen 3.6 -> `/api/v1/lan-bridge/complete`.
 */

import { apiFetch } from "./api";

export interface BrowserRelayState {
  active: boolean;
  mode: "idle" | "proxy_18765" | "direct_lmstudio" | "error";
  localTarget: string | null;
  models: string[];
  btcSynced: boolean;
  completedJobs: number;
  lastError: string | null;
}

type Listener = (state: BrowserRelayState) => void;

class BrowserLanBridgeController {
  private state: BrowserRelayState = {
    active: false,
    mode: "idle",
    localTarget: null,
    models: [],
    btcSynced: false,
    completedJobs: 0,
    lastError: null,
  };
  private listeners = new Set<Listener>();
  private stopFlag = false;
  private loopRunning = false;

  getState(): BrowserRelayState {
    return { ...this.state };
  }

  subscribe(fn: Listener): () => void {
    this.listeners.add(fn);
    fn(this.getState());
    return () => {
      this.listeners.delete(fn);
    };
  }

  private update(patch: Partial<BrowserRelayState>): void {
    this.state = { ...this.state, ...patch };
    const snap = this.getState();
    for (const fn of this.listeners) {
      fn(snap);
    }
  }

  stop(): void {
    this.stopFlag = true;
    this.update({ active: false, mode: "idle" });
  }

  async connectAndStart(customDirectUrl?: string): Promise<BrowserRelayState> {
    this.stopFlag = false;
    this.update({ lastError: null });

    // 1. Try local `apex_lan_bridge.py` CORS/PNA relay on port 18765 first
    const proxyOrigin = ["http:", "", "127.0.0.1:18765"].join("/");
    try {
      const resp = await fetch(`${proxyOrigin}/health`, {
        method: "GET",
        mode: "cors",
      });
      if (resp.ok) {
        const info = await resp.json();
        this.update({
          active: true,
          mode: "proxy_18765",
          localTarget: info.endpoint || proxyOrigin,
          models: info.models || ["qwen3.6-35b-a3b-uncensored-hauhaucs-aggressive"],
          lastError: null,
        });
        await this.syncBtcFromLocalProxy(proxyOrigin);
        void this.runLoop(proxyOrigin, "proxy_18765");
        return this.getState();
      }
    } catch {
      // Fall through to direct LM Studio check
    }

    // 2. Try direct LM Studio OpenAI-compatible server (if CORS enabled in LM Studio)
    const candidates = [
      ...(customDirectUrl ? [customDirectUrl.replace(/\/+$/, "")] : []),
      ["http:", "", "127.0.0.1:1234", "v1"].join("/"),
      "http://192.168.18.184:1234/v1",
    ];
    for (const base of candidates) {
      try {
        const modelsUrl = base.endsWith("/v1") ? `${base}/models` : `${base}/v1/models`;
        const resp = await fetch(modelsUrl, { method: "GET", mode: "cors" });
        if (resp.ok) {
          const data = await resp.json();
          const ids = (data.data || [])
            .map((m: any) => String(m?.id || ""))
            .filter(Boolean);
          this.update({
            active: true,
            mode: "direct_lmstudio",
            localTarget: base,
            models: ids.length
              ? ids
              : ["qwen3.6-35b-a3b-uncensored-hauhaucs-aggressive"],
            lastError: null,
          });
          void this.runLoop(base, "direct_lmstudio");
          return this.getState();
        }
      } catch {
        // Continue to next candidate
      }
    }

    const inIframe = typeof window !== "undefined" && window.self !== window.top;
    const currentUrl = typeof window !== "undefined" ? window.location.href : "";
    const hint = inIframe
      ? `Ramka podglądu Arena (<iframe sandbox>) blokuje w przeglądarce zarówno dostęp do sieci lokalnej (127.0.0.1 / 192.168.x.x), jak i automatyczne otwieranie wyskakujących okien. Rozwiązanie: (A) Uruchom na komputerze nowy skrypt 'python apex_lan_bridge.py' (łączy się sam bezpośrednio z portem chmurowym E2B — odznaka na górze zmieni się na ZIELONĄ automatycznie!), LUB (B) zaznacz i skopiuj ręcznie (Ctrl+C) adres podglądu: ${currentUrl} , otwórz nową kartę w przeglądarce (Ctrl+T), wklej adres (Ctrl+V) i tam kliknij zielony przycisk.`
      : "Nie udało się połączyć z mostem na 127.0.0.1:18765 ani z LM Studio na porcie 1234. Upewnij się, że w terminalu na Twoim komputerze działa `python apex_lan_bridge.py` (lub włącz 'Enable CORS' w LM Studio).";

    this.update({
      active: false,
      mode: "error",
      lastError: hint,
    });
    return this.getState();
  }

  private async syncBtcFromLocalProxy(proxyOrigin: string): Promise<void> {
    if (this.state.btcSynced) return;
    try {
      const resp = await fetch(`${proxyOrigin}/btc-snapshot`, { mode: "cors" });
      if (!resp.ok) return;
      const snap = await resp.json();
      if (snap?.markdown && snap?.title) {
        await apiFetch("/api/v1/sources", {
          method: "POST",
          body: JSON.stringify({
            source_type: "MD",
            title: snap.title,
            uri_or_filename: "btc_usd_live_30d_telemetry.md",
            raw_text: snap.markdown,
          }),
        });
        this.update({ btcSynced: true });
      }
    } catch {
      // Non-fatal
    }
  }

  private async runLoop(
    targetBase: string,
    mode: "proxy_18765" | "direct_lmstudio"
  ): Promise<void> {
    if (this.loopRunning) return;
    this.loopRunning = true;
    try {
      while (!this.stopFlag) {
        try {
          const poll = await apiFetch("/api/v1/lan-bridge/poll", {
            method: "POST",
            body: JSON.stringify({
              endpoint: this.state.localTarget || "http://192.168.18.184:1234/v1",
              models: this.state.models,
              bridge_host:
                mode === "proxy_18765" ? "Browser+LocalProxy" : "BrowserDirect",
              wait_seconds: 8.0,
            }),
          });
          const job = poll?.job;
          if (!job || this.stopFlag) continue;

          let resultPayload: any;
          const t0 = performance.now();
          try {
            if (mode === "proxy_18765") {
              const r = await fetch(`${targetBase}/infer`, {
                method: "POST",
                mode: "cors",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(job),
              });
              resultPayload = await r.json();
            } else {
              const chatUrl = targetBase.endsWith("/v1")
                ? `${targetBase}/chat/completions`
                : `${targetBase}/v1/chat/completions`;
              const r = await fetch(chatUrl, {
                method: "POST",
                mode: "cors",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                  model:
                    job.model || "qwen3.6-35b-a3b-uncensored-hauhaucs-aggressive",
                  temperature: job.temperature ?? 0.2,
                  max_tokens: job.max_tokens ?? 1600,
                  messages: [
                    { role: "system", content: job.system_prompt || "" },
                    { role: "user", content: job.user_prompt || "" },
                  ],
                }),
              });
              const data = await r.json();
              const msg = data?.choices?.[0]?.message || {};
              const content = String(
                msg.content || msg.reasoning_content || ""
              );
              const usage = data?.usage || {};
              resultPayload = {
                content,
                model: data?.model || job.model,
                prompt_tokens: usage.prompt_tokens || 0,
                completion_tokens: usage.completion_tokens || 0,
                total_tokens: usage.total_tokens || 0,
                latency_ms: Math.max(1, Math.round(performance.now() - t0)),
              };
            }
          } catch (err: any) {
            resultPayload = { error: String(err?.message || err) };
          }

          await apiFetch("/api/v1/lan-bridge/complete", {
            method: "POST",
            body: JSON.stringify({
              job_id: job.job_id,
              result: resultPayload,
            }),
          });
          this.update({ completedJobs: this.state.completedJobs + 1 });
        } catch (err: any) {
          if (err?.status === 401) {
            this.stop();
            break;
          }
          await new Promise((r) => setTimeout(r, 2000));
        }
      }
    } finally {
      this.loopRunning = false;
    }
  }
}

export const browserLanBridge = new BrowserLanBridgeController();
