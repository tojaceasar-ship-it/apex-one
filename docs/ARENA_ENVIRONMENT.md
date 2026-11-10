# APEX ONE — Adaptacja Środowiska Arena (`ARENA_ENVIRONMENT.md`)

## 1. Charakterystyka środowiska Arena

Środowisko Arena działa w odizolowanym mikro-sandboxie Linux (`Debian GNU/Linux 12 Bookworm`, `x86_64`, 2 vCPU, 3.8 GiB RAM, 20 GiB dysku) powiązanym z gałęzią roboczą `arena/e7fc39dd-apex-one` repozytorium `https://github.com/tojaceasar-ship-it/apex-one`.

### Wyniki preflightu i macierz dostępności

| Zasób / Usługa | Status w Arena | Wpływ na architekturę APEX ONE |
| :--- | :---: | :--- |
| GitHub (`git` + `gh`) | **PASS** | Pełny odczyt/zapis na gałęzi `arena/e7fc39dd-apex-one` |
| Node.js `22.22.3` + `npm 10.9.8` | **PASS** | Natywne budowanie `@apex-one/web` oraz `@apex-one/agent-runtime` z `package-lock.json` |
| Python `3.11.2` + `pip` + `venv` | **PASS** | Izolowane środowisko `.venv` z kołami binarnymi (`manylinux`), bez kompilacji C i bez APT |
| Arena Live Preview (`0.0.0.0`) | **PASS** | Vite (`0.0.0.0:5173`, `allowedHosts: true`) z proxy `/api` → FastAPI (`127.0.0.1:8000`) |
| Docker / `dockerd` | **BLOCKED** | Wszystkie procesy (`api`, `worker`, `web`, `local_node`) działają bez Dockera; `docker-compose.yml` zachowany dla środowisk zewnętrznych |
| Lokalny PostgreSQL | **BLOCKED** | Docelową bazą pozostaje **zewnętrzny PostgreSQL 16+ z `pgvector` i RLS** (np. Neon); brak degradacji do SQLite/JSON |
| APT (`deb.debian.org`) i Docker Hub | **BLOCKED** | Zarządzanie zależnościami oparte wyłącznie o `registry.npmjs.org` oraz `pypi.org` / `files.pythonhosted.org` |

## 2. Kluczowe decyzje adaptacyjne

1. **Brak obniżenia jakości architektury:** Główna warstwa trwałości (`backend/apex_one/db/`) używa wyłącznie `SQLAlchemy 2.0` (`asyncpg` + `psycopg`), `Alembic`, `pgvector` (`Vector(1536)` + indeks `HNSW`) oraz polityk `ENABLE / FORCE ROW LEVEL SECURITY`. Próba podania `sqlite://` lub ścieżki plikowej kończy się wyjątkiem `UnsupportedDatabaseBackendError`.
2. **Kolejka zadań bez trwałego demona i bez Redis jako SPOF:** Zaimplementowano transakcyjny **PostgreSQL Outbox** (`FOR UPDATE SKIP LOCKED`), idempotencję, ponawianie z wykładniczym backoffem, odzyskiwanie wygasłych dzierżaw (`recover_stale_leases`), `heartbeat` oraz kooperatywne anulowanie (`cancellation`).
3. **Redis jako adapter opcjonalny:** `RedisCacheAdapter` aktywuje się automatycznie po ustawieniu `APEX_REDIS_URL` / `REDIS_URL`. Przy jego braku działa `DisabledCacheAdapter` bez wpływu na trwałość workflowów.
4. **Profile środowiskowe:** Wyodrębniono konfiguracje `development`, `staging` oraz `production` (`config/environments/*.env.example` + walidator Pydantic w `backend/apex_one/config.py`).
5. **Bezpieczeństwo Sandboxa Eksperymentów:** Domyślny `GuardedUnverifiedSandboxAdapter` waliduje kontrakty `SandboxJob`, limity zasobów, timeouty i artefakty, lecz blokuje wykonywanie niezaufanego kodu (`HTTP 503 UNTRUSTED_EXECUTION_BLOCKED`), dopóki nie zostanie skonfigurowany zewnętrzny dostawca z potwierdzoną izolacją (`APEX_SANDBOX_ISOLATION_ATTESTED=true`).
6. **Hybrid Local Node:** Wydzielona aplikacja Python (`local_node/`) komunikuje się z Cloud Gateway po protokole `v1.0` (podpisy `HMAC-SHA256`, ochrona przed replay, `WebSocket JSON-RPC 2.0`). W Arena jawnie raportujemy `user_machine_connected = false`.

## 3. Reguły Arena Live Preview

- Serwery deweloperskie wiążą adres `0.0.0.0`.
- Kod przeglądarkowy (`@apex-one/web`, `@apex-one/agent-runtime`) nigdy nie wywołuje `localhost` ani `127.0.0.1` — używa wyłącznie względnych ścieżek `/api/v1/...`, które serwer Vite przekazuje wewnętrznie do backendu FastAPI.
