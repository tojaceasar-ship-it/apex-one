# Środowisko Developerskie bez Dockera (`LOCAL_DEVELOPMENT.md`)

`APEX ONE Research Edition` działa w pełni bez Dockera, `dockerd`, pakietów systemowych `APT`, lokalnego PostgreSQL oraz lokalnego Redis. Jednocześnie w repozytorium znajduje się kompletny `docker-compose.yml` dla środowisk lokalnych dysponujących Dockerem.

## 1. Struktura projektu

- `backend/apex_one/` — API FastAPI, adapter PostgreSQL (`asyncpg`/`psycopg`), modele SQLAlchemy 2.0 + `pgvector`, polityki RLS, kolejka Outbox, Worker, Sandbox i brama Hybrid Local Node.
- `migrations/` — migracje Alembic (`0001_apex_one_initial_schema.py`).
- `local_node/` — niezależna aplikacja Python (`apex-local-node`) dla węzła hybrydowego.
- `packages/agent-runtime/` — pakiet TypeScript SDK i kontrakty uruchomieniowe agentów.
- `apps/web/` — konsola badawcza React + TypeScript + Vite (skonfigurowana pod Arena Live Preview).
- `tests/` — podzielone pakiety testowe `unit` (A), `api` (B), `postgres_integration` (C), `postgres_security` (D).

## 2. Instalacja zależności (bez APT i bez Dockera)

### Python (`venv` + `pip`)

```bash
python3 -m venv .venv
.venv/bin/pip install --upgrade pip setuptools wheel
.venv/bin/pip install -r requirements.txt
.venv/bin/pip install --no-build-isolation --no-deps -e . -e ./local_node
```

### Frontend & Agent Runtime (`npm`)

```bash
npm ci   # lub npm install
npm run build
```

## 3. Uruchamianie procesów developerskich (Arena `start_process` lub terminal)

### A. Backend API (FastAPI na `0.0.0.0:8000`)

```bash
.venv/bin/uvicorn apex_one.api.main:app --host 0.0.0.0 --port 8000
```

### B. Web Console (Vite na `0.0.0.0:5173` z proxy `/api` → `127.0.0.1:8000`)

```bash
npm run dev:web
```

### C. Background Outbox Worker (bez demona systemowego i bez Dockera)

```bash
# Walidacja konfiguracji workera (dry check):
.venv/bin/python -m apex_one.worker.cli --dry-check

# Pojedynczy cykl odzyskiwania dzierżaw i wykonania zadań (tryb bezdemona / cron):
.venv/bin/python -m apex_one.worker.cli --mode once

# Pętla ciągła (gdy skonfigurowano APEX_DATABASE_URL):
.venv/bin/python -m apex_one.worker.cli --mode loop
```

### D. Hybrid Local Node CLI

```bash
.venv/bin/apex-local-node --inspect-capabilities
```

## 4. Uruchamianie testów

```bash
# Wszystkie testy Python (A + B przechodzą; C + D są jawnie SKIPPED przy braku DATABASE_URL):
.venv/bin/pytest -v -rs

# Wyłącznie wybrane kategorie:
.venv/bin/pytest -m unit
.venv/bin/pytest -m api
.venv/bin/pytest -m pg_integration -rs
.venv/bin/pytest -m pg_security -rs

# Testy TypeScript / Frontend / Agent Runtime:
npm run typecheck
npm test
```

## 5. Opcjonalne uruchomienie przez Docker Compose (poza Arena)

W środowisku zewnętrznym posiadającym Docker Engine oraz Docker Compose v2:

```bash
docker compose up --build
# Opcjonalnie z kontenerem Redis:
docker compose --profile with-redis up --build
```
