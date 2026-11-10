"""Reverse HTTP Long-Poll & Browser-Assisted LAN LLM Bridge for Cloud Previews.

Supports TWO simultaneous transport paths from a cloud preview (such as
``https://sbx-*.arena.site`` or ``https://*.e2b.app``) to a private LAN LLM
server (``http://192.168.18.184:1234/v1`` running
``qwen3.6-35b-a3b-uncensored-hauhaucs-aggressive``):

1. **Direct Python-to-Arena HTTPS Long-Poll** (when the preview URL accepts
   direct API-key / bearer requests from Python on the user's machine).
2. **Browser-Assisted Localhost CORS/PNA Relay (`127.0.0.1:18765`)** (when the
   cloud preview edge proxy requires browser-only auth cookies such as
   ``*.arena.site``): ``apex_lan_bridge.py`` exposes ``http://127.0.0.1:18765``
   on the user's PC with full CORS + ``Access-Control-Allow-Private-Network``
   headers, forwarding to ``http://192.168.18.184:1234/v1``, while the user's
   authenticated browser tab relays ``/api/v1/lan-bridge/poll`` <-> ``127.0.0.1:18765``.
"""

from __future__ import annotations

import asyncio
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any


@dataclass
class BridgeInferenceJob:
    job_id: str
    organization_id: str
    model: str
    system_prompt: str
    user_prompt: str
    temperature: float
    max_tokens: int
    created_at_mono: float = field(default_factory=time.monotonic)
    done_event: threading.Event = field(default_factory=threading.Event)
    result: dict[str, Any] | None = None


@dataclass
class _OrgBridgeState:
    endpoint: str = "http://192.168.18.184:1234/v1"
    models: list[str] = field(default_factory=list)
    bridge_host: str = ""
    last_seen_mono: float = 0.0
    last_seen_iso: str | None = None
    completed_jobs: int = 0
    pending: deque[BridgeInferenceJob] = field(default_factory=deque)
    inflight: dict[str, BridgeInferenceJob] = field(default_factory=dict)


class LanLLMBridgeBroker:
    """Thread-safe & async-safe coordinator for reverse-tunneled LAN LLM calls."""

    ONLINE_TTL_SECONDS = 35.0

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._orgs: dict[str, _OrgBridgeState] = {}

    def _get_or_create_unlocked(self, organization_id: str) -> _OrgBridgeState:
        st = self._orgs.get(organization_id)
        if st is None:
            st = _OrgBridgeState()
            self._orgs[organization_id] = st
        return st

    def is_online(self, organization_id: str) -> bool:
        with self._lock:
            st = self._orgs.get(organization_id)
            if st is None or st.last_seen_mono <= 0.0:
                return False
            return (time.monotonic() - st.last_seen_mono) <= self.ONLINE_TTL_SECONDS

    def status_snapshot(self, organization_id: str) -> dict[str, Any]:
        with self._lock:
            st = self._orgs.get(organization_id)
            if st is None or st.last_seen_mono <= 0.0:
                return {
                    "online": False,
                    "endpoint": "http://192.168.18.184:1234/v1",
                    "models": [],
                    "bridge_host": None,
                    "last_seen_at": None,
                    "completed_jobs": 0,
                    "pending_jobs": 0,
                }
            online = (time.monotonic() - st.last_seen_mono) <= self.ONLINE_TTL_SECONDS
            return {
                "online": online,
                "endpoint": st.endpoint,
                "models": list(st.models),
                "bridge_host": st.bridge_host or None,
                "last_seen_at": st.last_seen_iso,
                "completed_jobs": st.completed_jobs,
                "pending_jobs": len(st.pending) + len(st.inflight),
            }

    def submit_and_wait_sync(
        self,
        *,
        organization_id: str,
        model: str,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.2,
        max_tokens: int = 1600,
        timeout_seconds: float = 120.0,
    ) -> dict[str, Any]:
        job = BridgeInferenceJob(
            job_id=f"brjob-{uuid.uuid4().hex[:16]}",
            organization_id=organization_id,
            model=model,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        with self._lock:
            st = self._get_or_create_unlocked(organization_id)
            st.pending.append(job)

        finished = job.done_event.wait(timeout=timeout_seconds)
        if not finished or job.result is None:
            with self._lock:
                st = self._orgs.get(organization_id)
                if st is not None:
                    st.inflight.pop(job.job_id, None)
                    try:
                        st.pending.remove(job)
                    except ValueError:
                        pass
            raise TimeoutError(
                f"LAN Bridge timeout after {timeout_seconds:.0f}s waiting for local model '{model}'."
            )

        if job.result.get("error"):
            raise RuntimeError(str(job.result["error"]))
        return job.result

    async def poll_next_job_async(
        self,
        *,
        organization_id: str,
        endpoint: str,
        models: list[str],
        bridge_host: str = "",
        wait_seconds: float = 10.0,
    ) -> dict[str, Any] | None:
        now_iso = datetime.now(UTC).isoformat()
        with self._lock:
            st = self._get_or_create_unlocked(organization_id)
            if endpoint:
                st.endpoint = endpoint
            if models:
                st.models = list(models)[:25]
            if bridge_host:
                st.bridge_host = bridge_host
            st.last_seen_mono = time.monotonic()
            st.last_seen_iso = now_iso

        ticks = max(1, int(wait_seconds / 0.12))
        for _ in range(ticks):
            with self._lock:
                st = self._get_or_create_unlocked(organization_id)
                st.last_seen_mono = time.monotonic()
                if st.pending:
                    job = st.pending.popleft()
                    st.inflight[job.job_id] = job
                    return {
                        "job_id": job.job_id,
                        "model": job.model,
                        "system_prompt": job.system_prompt,
                        "user_prompt": job.user_prompt,
                        "temperature": job.temperature,
                        "max_tokens": job.max_tokens,
                    }
            await asyncio.sleep(0.12)
        return None

    def complete_job(
        self,
        *,
        organization_id: str,
        job_id: str,
        result_payload: dict[str, Any],
    ) -> bool:
        with self._lock:
            st = self._orgs.get(organization_id)
            if st is None:
                return False
            st.last_seen_mono = time.monotonic()
            st.last_seen_iso = datetime.now(UTC).isoformat()
            job = st.inflight.pop(job_id, None)
            if job is None:
                return False
            job.result = dict(result_payload)
            st.completed_jobs += 1
            job.done_event.set()
            return True


def build_lan_bridge_python_script(
    *,
    arena_base_url: str,
    auth_token: str,
    organization_id: str,
    lmstudio_url: str = "http://192.168.18.184:1234/v1",
    default_model: str = "qwen3.6-35b-a3b-uncensored-hauhaucs-aggressive",
) -> str:
    """Return a Dual-Mode zero-dependency Python 3 bridge script for the user's PC."""
    clean_arena = arena_base_url.rstrip("/")
    clean_lms = lmstudio_url.rstrip("/")
    return f'''#!/usr/bin/env python3
"""APEX ONE — Dual-Mode Most LAN dla Arena Preview (Zero-Dependency Python 3).

Działa w dwóch trybach jednocześnie, aby zagwarantować 100% łączności z Arena Preview:
  1. Lokalny przekaźnik CORS/PNA na http://127.0.0.1:18765 -> {clean_lms}
     (dla Twojej zalogowanej przeglądarki z otwartym Arena Preview — omija blokady cookie *.arena.site!)
  2. Bezpośredni poller HTTPS do {clean_arena} (jeśli proxy chmurowe wpuszcza ruch CLI).

Dodatkowo pobiera świeże 90-dniowe / 30-dniowe dane rynkowe Bitcoin (BTC/USD).

Uruchomienie na Twoim komputerze:
    python apex_lan_bridge.py
"""

from __future__ import annotations

import argparse
import json
import math
import socket
import ssl
import statistics
import sys
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

ARENA_URL = {clean_arena!r}
AUTH_TOKEN = {auth_token!r}
ORG_ID = {organization_id!r}
DEFAULT_LMS_URL = {clean_lms!r}
DEFAULT_MODEL = {default_model!r}
LOCAL_PROXY_PORT = 18765

SSL_CTX = ssl.create_default_context()
CACHED_BTC_MARKDOWN: dict[str, str] = {{}}


def _http_json(url: str, method: str = "GET", payload: dict | None = None, headers: dict | None = None, timeout: float = 30.0) -> dict:
    hdrs = {{"Accept": "application/json", **(headers or {{}})}}
    data = None
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        hdrs["Content-Type"] = "application/json"
    req = Request(url=url, data=data, headers=hdrs, method=method)
    ctx = SSL_CTX if url.startswith("https://") else None
    with urlopen(req, timeout=timeout, context=ctx) as resp:
        ctype = (resp.headers.get("Content-Type") or "").lower()
        raw = resp.read().decode("utf-8", errors="replace")
        if "text/html" in ctype or raw.lstrip().startswith("<!DOCTYPE"):
            raise PermissionError("Arena proxy wymaga sesji przeglądarki (HTML auth gate)")
        return json.loads(raw) if raw.strip() else {{}}


def probe_lmstudio(lms_url: str) -> list[str]:
    base = lms_url.rstrip("/")
    models_url = f"{{base}}/models" if base.endswith("/v1") else f"{{base}}/v1/models"
    try:
        res = _http_json(models_url, timeout=4.0)
        return [str(m.get("id", "")) for m in (res.get("data") or []) if isinstance(m, dict) and m.get("id")]
    except Exception as exc:
        print(f"[OSTRZEŻENIE] Nie udało się odpytać {{models_url}}: {{exc}}")
        return []


def build_live_btc_markdown() -> dict[str, str] | None:
    if CACHED_BTC_MARKDOWN:
        return CACHED_BTC_MARKDOWN
    closes: list[float] = []
    highs: list[float] = []
    lows: list[float] = []
    source_label = "Binance Spot BTCUSDT (1D)"
    try:
        req = Request(
            "https://api.binance.com/api/v3/klines?symbol=BTCUSDT&interval=1d&limit=90",
            headers={{"Accept": "application/json", "User-Agent": "APEX-ONE-LAN-Bridge/1.0"}},
        )
        with urlopen(req, timeout=8.0, context=SSL_CTX) as resp:
            rows = json.loads(resp.read().decode("utf-8"))
        for r in rows:
            highs.append(float(r[2]))
            lows.append(float(r[3]))
            closes.append(float(r[4]))
    except Exception:
        try:
            source_label = "CoinGecko BTC/USD (90D)"
            cg = _http_json(
                "https://api.coingecko.com/api/v3/coins/bitcoin/market_chart?vs_currency=usd&days=90&interval=daily",
                headers={{"User-Agent": "APEX-ONE-LAN-Bridge/1.0"}},
                timeout=8.0,
            )
            closes = [float(p[1]) for p in (cg.get("prices") or [])]
            highs = list(closes)
            lows = list(closes)
        except Exception as exc:
            print(f"[INFO] Pominięto pobranie kursu BTC/USD ({{exc}}).")
            return None

    if len(closes) < 32:
        return None

    spot = closes[-1]
    chg_7d = ((spot / closes[-8]) - 1.0) * 100.0 if len(closes) >= 8 else 0.0
    chg_30d = ((spot / closes[-31]) - 1.0) * 100.0
    log_rets = [math.log(closes[i] / closes[i - 1]) for i in range(1, len(closes)) if closes[i - 1] > 0]
    daily_vol = statistics.stdev(log_rets[-30:]) if len(log_rets) >= 30 else statistics.stdev(log_rets)
    ann_vol_pct = daily_vol * math.sqrt(365.0) * 100.0
    vol_30d = daily_vol * math.sqrt(30.0)

    windows = 0
    dd_5 = dd_10 = dd_20 = 0
    for start in range(0, len(closes) - 30):
        entry = closes[start]
        min_low = min(lows[start + 1 : start + 31])
        dd = (min_low - entry) / entry
        windows += 1
        if dd <= -0.05:
            dd_5 += 1
        if dd <= -0.10:
            dd_10 += 1
        if dd <= -0.20:
            dd_20 += 1

    def _norm_cdf(x: float) -> float:
        return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))

    p_close_5 = _norm_cdf((math.log(0.95) + 0.5 * vol_30d * vol_30d) / max(vol_30d, 1e-6)) * 100.0
    p_close_10 = _norm_cdf((math.log(0.90) + 0.5 * vol_30d * vol_30d) / max(vol_30d, 1e-6)) * 100.0
    p_close_20 = _norm_cdf((math.log(0.80) + 0.5 * vol_30d * vol_30d) / max(vol_30d, 1e-6)) * 100.0

    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    md = (
        f"# Empiryczny Snapshot Rynkowy Bitcoin (BTC/USD) — {{now_str}}\\n"
        f"- Źródło kwotowań: {{source_label}} (próba 90 dni)\\n"
        f"- Aktualny kurs spot BTC/USD: ${{spot:,.2f}}\\n"
        f"- Zmiana 7-dniowa: {{chg_7d:+.2f}}% | Zmiana 30-dniowa: {{chg_30d:+.2f}}%\\n"
        f"- Zmienność zrealizowana 30D (annualizowana): {{ann_vol_pct:.2f}}% (dzienna sigma={{daily_vol*100:.2f}}%, 30D sigma={{vol_30d*100:.2f}}%)\\n"
        f"- Empiryczna częstość dotknięcia korekty w kroczących oknach 30D (ostatnie 90 dni, n={{windows}} okien):\\n"
        f"  * Spadek wewnątrzokresowy >= 5%: {{(dd_5/max(windows,1))*100:.1f}}% okien\\n"
        f"  * Spadek wewnątrzokresowy >= 10%: {{(dd_10/max(windows,1))*100:.1f}}% okien\\n"
        f"  * Spadek wewnątrzokresowy >= 20%: {{(dd_20/max(windows,1))*100:.1f}}% okien\\n"
        f"- Bazowe prawdopodobieństwo log-normalne na zamknięcie za 30 dni (neutralny dryf):\\n"
        f"  * Kurs za 30D <= -5% (${{spot*0.95:,.0f}}): {{p_close_5:.1f}}%\\n"
        f"  * Kurs za 30D <= -10% (${{spot*0.90:,.0f}}): {{p_close_10:.1f}}%\\n"
        f"  * Kurs za 30D <= -20% (${{spot*0.80:,.0f}}): {{p_close_20:.1f}}%\\n"
    )
    CACHED_BTC_MARKDOWN.update({{
        "title": f"Live BTC/USD Market Telemetry Snapshot ({{now_str}})",
        "markdown": md,
        "spot_usd": f"{{spot:,.2f}}",
    }})
    print(f"[OK] Pobrano świeży kurs BTC/USD: ${{spot:,.2f}} (zmienność 30D={{ann_vol_pct:.1f}}%)")
    return CACHED_BTC_MARKDOWN


def call_lmstudio(lms_url: str, job: dict) -> dict:
    base = lms_url.rstrip("/")
    chat_url = f"{{base}}/chat/completions" if base.endswith("/v1") else f"{{base}}/v1/chat/completions"
    model = job.get("model") or DEFAULT_MODEL
    t0 = time.perf_counter()
    res = _http_json(
        chat_url,
        method="POST",
        payload={{
            "model": model,
            "temperature": float(job.get("temperature", 0.2)),
            "max_tokens": int(job.get("max_tokens", 1600)),
            "messages": [
                {{"role": "system", "content": str(job.get("system_prompt", ""))}},
                {{"role": "user", "content": str(job.get("user_prompt", ""))}},
            ],
        }},
        headers={{"Authorization": "Bearer lm-studio"}},
        timeout=120.0,
    )
    elapsed_ms = max(1, int((time.perf_counter() - t0) * 1000))
    choices = res.get("choices") or []
    msg = (choices[0].get("message") or {{}}) if choices else {{}}
    content = str(msg.get("content") or msg.get("reasoning_content") or "")
    usage = res.get("usage") or {{}}
    pt = int(usage.get("prompt_tokens") or 0)
    ct = int(usage.get("completion_tokens") or 0)
    tt = int(usage.get("total_tokens") or (pt + ct))
    return {{
        "content": content,
        "model": str(res.get("model") or model),
        "prompt_tokens": pt,
        "completion_tokens": ct,
        "total_tokens": tt,
        "latency_ms": elapsed_ms,
    }}


def make_local_cors_handler(lms_url: str):
    host_name = socket.gethostname()

    class _LocalRelayHandler(BaseHTTPRequestHandler):
        def _send_cors(self, status: int = 200) -> None:
            origin = self.headers.get("Origin") or "*"
            self.send_response(status)
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "*")
            self.send_header("Access-Control-Allow-Private-Network", "true")
            self.send_header("Content-Type", "application/json; charset=utf-8")

        def do_OPTIONS(self) -> None:  # noqa: N802
            self._send_cors(204)
            self.end_headers()

        def do_GET(self) -> None:  # noqa: N802
            if self.path.startswith("/health"):
                models = probe_lmstudio(lms_url)
                body = json.dumps({{
                    "ok": True,
                    "endpoint": lms_url,
                    "host": host_name,
                    "models": models or [DEFAULT_MODEL],
                }}).encode("utf-8")
                self._send_cors(200)
                self.end_headers()
                self.wfile.write(body)
                return
            if self.path.startswith("/btc-snapshot"):
                snap = build_live_btc_markdown() or {{}}
                body = json.dumps(snap).encode("utf-8")
                self._send_cors(200)
                self.end_headers()
                self.wfile.write(body)
                return
            self._send_cors(404)
            self.end_headers()
            self.wfile.write(b'{{"error":"not_found"}}')

        def do_POST(self) -> None:  # noqa: N802
            if self.path.startswith("/infer"):
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length).decode("utf-8", errors="replace")
                job = json.loads(raw) if raw else {{}}
                jid = job.get("job_id", "browser")
                req_model = job.get("model") or DEFAULT_MODEL
                print(f"[PRZEGLĄDARKA -> LM STUDIO {{jid}}] {{req_model}} ...", end=" ", flush=True)
                try:
                    result = call_lmstudio(lms_url, job)
                    print(f"OK ({{result['latency_ms']}} ms, {{result['total_tokens']}} tok)")
                except Exception as exc:
                    print(f"BŁĄD: {{exc}}")
                    result = {{"error": str(exc)}}
                body = json.dumps(result).encode("utf-8")
                self._send_cors(200)
                self.end_headers()
                self.wfile.write(body)
                return
            self._send_cors(404)
            self.end_headers()
            self.wfile.write(b'{{"error":"not_found"}}')

        def log_message(self, fmt: str, *args) -> None:
            return

    return _LocalRelayHandler


def direct_https_poller(arena_url: str, lms_url: str, headers: dict, models: list[str], sync_btc: bool) -> None:
    host_name = socket.gethostname()
    if sync_btc:
        snap = build_live_btc_markdown()
        if snap:
            try:
                _http_json(
                    f"{{arena_url.rstrip('/')}}/api/v1/sources",
                    method="POST",
                    payload={{
                        "source_type": "MD",
                        "title": snap["title"],
                        "uri_or_filename": "btc_usd_live_30d_telemetry.md",
                        "raw_text": snap["markdown"],
                    }},
                    headers=headers,
                    timeout=10.0,
                )
                print("[OK] Wgrano snapshot BTC/USD bezpośrednio do Arena Preview!")
            except Exception:
                pass

    while True:
        try:
            poll_resp = _http_json(
                f"{{arena_url.rstrip('/')}}/api/v1/lan-bridge/poll",
                method="POST",
                payload={{
                    "endpoint": lms_url,
                    "models": models or [DEFAULT_MODEL],
                    "bridge_host": host_name,
                }},
                headers=headers,
                timeout=22.0,
            )
            job = poll_resp.get("job")
            if not job:
                continue
            jid = job["job_id"]
            req_model = job.get("model") or DEFAULT_MODEL
            print(f"[ZADANIE HTTPS {{jid}}] {{req_model}} ...", end=" ", flush=True)
            try:
                result = call_lmstudio(lms_url, job)
                print(f"OK ({{result['latency_ms']}} ms, {{result['total_tokens']}} tok)")
            except Exception as exc:
                print(f"BŁĄD: {{exc}}")
                result = {{"error": str(exc)}}
            _http_json(
                f"{{arena_url.rstrip('/')}}/api/v1/lan-bridge/complete",
                method="POST",
                payload={{"job_id": jid, "result": result}},
                headers=headers,
                timeout=15.0,
            )
        except PermissionError:
            print("[TRYB PRZEGLĄDARKOWY] Proxy *.arena.site wymaga cookie przeglądarki — użyj przycisku 'Połącz z lokalnym mostem (127.0.0.1:18765)' w karcie Modele i Dostawcy!")
            return
        except HTTPError as exc:
            if exc.code in (401, 403):
                print(f"[TRYB PRZEGLĄDARKOWY] HTTP {{exc.code}} na połączeniu bezpośrednim — używam lokalnego portu http://127.0.0.1:{{LOCAL_PROXY_PORT}} dla Twojej przeglądarki!")
                return
            time.sleep(3.0)
        except Exception:
            time.sleep(3.0)


def main() -> None:
    parser = argparse.ArgumentParser(description="APEX ONE Dual-Mode LAN Bridge")
    parser.add_argument("--arena-url", default=ARENA_URL)
    parser.add_argument("--lms-url", default=DEFAULT_LMS_URL)
    parser.add_argument("--port", type=int, default=LOCAL_PROXY_PORT)
    parser.add_argument("--no-btc", action="store_true")
    args = parser.parse_args()

    print("=" * 74)
    print(" APEX ONE — Dual-Mode Most LAN dla Arena Preview")
    print(f" Lokalny przekaźnik dla przeglądarki : http://127.0.0.1:{{args.port}}")
    print(f" Docelowy serwer LM Studio w LAN     : {{args.lms_url}}")
    print(f" Model docelowy                      : {{DEFAULT_MODEL}}")
    print("=" * 74)

    models = probe_lmstudio(args.lms_url)
    if models:
        print(f"[OK] Połączono z LM Studio! Dostępne modele: {{', '.join(models)}}")
    else:
        print(f"[!] Nie wykryto jeszcze odpowiedzi spod {{args.lms_url}} — upewnij się, że serwer LM Studio działa na porcie 1234.")

    if not args.no_btc:
        build_live_btc_markdown()

    headers = {{
        "X-Apex-Session-Token": AUTH_TOKEN,
        "Authorization": f"Bearer {{AUTH_TOKEN}}",
        "X-Organization-Id": ORG_ID,
    }}
    t = threading.Thread(
        target=direct_https_poller,
        args=(args.arena_url, args.lms_url, headers, models, not args.no_btc),
        daemon=True,
    )
    t.start()

    srv = ThreadingHTTPServer(("127.0.0.1", args.port), make_local_cors_handler(args.lms_url))
    print(f"[GOTOWY] Nasłuchuję na http://127.0.0.1:{{args.port}} — kliknij 'Połącz przeglądarkę z mostem lokalnym' w Arena Preview (Ctrl+C aby zakończyć).")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\\n[STOP] Zatrzymano most LAN.")
        sys.exit(0)


if __name__ == "__main__":
    main()
'''
