# Dziennik Wykonania Budowy (`BUILD_LOG.md`)

1. **Faza 1 — Inspekcja Repozytorium i Weryfikacja Techniczna:**
   - Zbadano bazę kodu z poprzedniego etapu adaptacji środowiskowej (`6ddca7e`).
   - Przeprowadzono empiryczną ocenę pakietu `eve` w rejestrze npm (`1.0.0-rc.9`, zależność od `nitro@3.0.261103-rc`, architektura plikowa) i udokumentowano decyzję architektoniczną w `docs/adr/0001-agent-runtime-selection.md`.
   - Zainstalowano i zsynchronizowano zależności `argon2-cffi`, `pypdf`, `python-docx`, `ruff` oraz pakiety frontendowe `@tailwindcss/vite`, `tailwindcss`, `@testing-library/react`, `jsdom`.
2. **Faza 2 — Pełny Schemat Bazy Danych i Migracje:**
   - Zaimplementowano 48 nowych modeli domenowych SQLAlchemy 2.0 w `backend/apex_one/db/platform_models.py` (łącznie 57 tabel z rdzeniem `0001`), polityki RLS na `organization_id` oraz wyzwalacz niezmienności `trg_audit_events_immutable`.
   - Utworzono migrację `migrations/versions/0002_apex_one_complete_platform.py` i zweryfikowano `alembic upgrade head --sql` (1 578 linii czystego SQL PostgreSQL).
3. **Faza 3 — Silniki Backendowe, Orkiestracja, Pamięć, Nadzór i Demo:**
   - Utworzono 9 manifestów `AgentDNA` w `agents/*/agent_dna.json`, zbiory danych w `datasets/` oraz adaptery integracji w `integrations/`.
   - Zaimplementowano moduły `security.py`, `governance.py`, `memory_and_docs.py`, `research_engine.py`, `orchestration.py`, `experiments_and_eval.py`, `workspace_service.py` oraz router `/api/v1/*` w `platform_router.py`.
   - Zasiano i wykonano pełne badanie porównawcze Single-Agent vs Multi-Agent w odseparowanej przestrzeni `demo`.
4. **Faza 4 — Budowa APEX WORKBENCH:**
   - Zbudowano 19 sekcji nawigacyjnych i 23 widoki w `apps/web/` (ciemny grafit + akcenty indygo/fiolet, domyślny język polski + przełącznik `PL/EN`, wykresy `Recharts`, topologia SVG Grafu Dowodowego).
5. **Faza 5 — Kompleksowe Testy i Analiza Statyczna:**
   - `ruff check`: 0 błędów.
   - `pytest`: 45 testów zaliczonych (`PASSED`), 0 błędów, 4 testy żywego PostgreSQL rzetelnie pominięte (`SKIPPED`) z powodu braku zewnętrznego `DATABASE_URL`.
   - `npm run typecheck && npm run build && npm test`: 0 błędów TypeScript, produkcyjna paczka Vite zbudowana poprawnie, 3 pliki testowe Vitest (5 testów, w tym E2E UI) zaliczone.
6. **Faza 6 i 7 — Artefakty Wdrożeniowe i Dokumentacja:**
   - Przygotowano `.github/workflows/ci.yml` (z usługą `pgvector/pgvector:pg16`), skrypty `backup_postgres.sh` / `restore_postgres.sh`, `infra/nginx/apex-one.conf` oraz komplet 19 dokumentów w `docs/` i `README.md`.
