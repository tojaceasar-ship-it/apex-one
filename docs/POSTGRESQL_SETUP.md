# Konfiguracja PostgreSQL, `pgvector` oraz RLS (`POSTGRESQL_SETUP.md`)

## 1. Wymagania architektoniczne

- **Wersja serwera:** PostgreSQL `16+` (lub `15+` z `pgvector >= 0.6.0`).
- **Wymagane rozszerzenia:** `vector` (`pgvector` z obsługą indeksu `HNSW` i operatora odległości cosinusowej `<=>`).
- **Row-Level Security (RLS):** Wszystkie tabele wielodostępne (`research_projects`, `knowledge_chunks`, `workflow_runs`, `workflow_steps`, `outbox_events`, `sandbox_job_records`, `local_node_sessions`) mają włączone `ENABLE ROW LEVEL SECURITY` oraz `FORCE ROW LEVEL SECURITY`.
- **Zakaz zamienników:** Produkcyjny PostgreSQL nie może być zastępowany przez SQLite ani pliki JSON.

## 2. Tworzenie bazy i ról (np. Neon / Managed PostgreSQL)

> **Kluczowe dla RLS:** Domyślna rola właściciela bazy w usługach chmurowych (np. `neondb_owner` w Neon lub `postgres`) często posiada atrybut `BYPASSRLS` lub `SUPERUSER`, który omija polityki RLS! Aplikacja oraz testy bezpieczeństwa **Suite D (`pg_security`)** muszą łączyć się przez dedykowaną rolę z `NOBYPASSRLS`.

Wykonaj jako właściciel bazy (administrator migracji):

```sql
-- 1. Włączenie rozszerzenia wektorowego
CREATE EXTENSION IF NOT EXISTS vector;

-- 2. Utworzenie dedykowanej roli aplikacyjnej podlegającej RLS
CREATE ROLE apex_app LOGIN PASSWORD 'ZMIEN_NA_SILNE_HASLO'
    NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS;

GRANT CONNECT ON DATABASE apex_one TO apex_app;
GRANT USAGE, CREATE ON SCHEMA public TO apex_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO apex_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO apex_app;
```

## 3. Migracje Alembic

Konfiguracja znajduje się w `alembic.ini` oraz `migrations/env.py`. Adres połączenia pobierany jest dynamicznie z `APEX_DATABASE_URL` lub `DATABASE_URL` (normalizowany do `postgresql+psycopg://`, z automatyczną obsługą `sslmode=require` dla Neon).

```bash
# Podgląd wygenerowanego DDL offline (działa bez aktywnego połączenia z bazą):
.venv/bin/alembic upgrade head --sql

# Wykonanie migracji online na skonfigurowanej bazie PostgreSQL:
.venv/bin/alembic upgrade head

# Wycofanie migracji (wyłącznie środowiska testowe):
.venv/bin/alembic downgrade base
```

## 4. Mechanizm działania RLS w APEX ONE

Każda transakcja domenowa otwierana przez `PostgresConnectionAdapter.session_with_rls(tenant_id)` ustawia zmienną sesyjną o zasięgu ściśle transakcyjnym (`is_local = true`):

```sql
SELECT set_config('app.current_tenant_id', '<tenant-uuid>', true);
```

Polityki RLS weryfikują warunek:

```sql
tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid
```

Dzięki `NULLIF(..., '')` brak ustawionego kontekstu najemcy bezpiecznie zwraca `0` wierszy zamiast błędu rzutowania pustego ciągu na `UUID`, natomiast próba zapisu wiersza innego najemcy jest odrzucana przez klauzulę `WITH CHECK`.

## 5. Podział testów i rola PGlite

1. **Suite A (`pytest -m unit`)** — testy jednostkowe schematu, kompilacji DDL, zapytań `pgvector` `<=>` oraz offline Alembic (nie wymagają działającego serwera PostgreSQL).
2. **Suite B (`pytest -m api`)** — testy kontraktów HTTP/WebSocket z podwójnymi obiektami odizolowanymi wyłącznie w `tests/fakes/`.
3. **Suite C (`pytest -m pg_integration`)** — testy integracyjne wymagające prawdziwego PostgreSQL + `pgvector`. Przy braku `DATABASE_URL` / `APEX_DATABASE_URL` są jawnie oznaczane jako `SKIPPED` (nigdy fałszywie jako `PASS`).
4. **Suite D (`pytest -m pg_security`)** — testy bezpieczeństwa RLS na prawdziwym PostgreSQL z rolą `NOBYPASSRLS`. Przy braku bazy oznaczane jako `SKIPPED`.
5. **PGlite (`@electric-sql/pglite` + `@electric-sql/pglite-pgvector`)** — używany wyłącznie jako pomocnicze środowisko testowe po stronie JavaScript/TypeScript w `packages/agent-runtime`. Nie jest traktowany jako zamiennik PostgreSQL dla backendu FastAPI.
