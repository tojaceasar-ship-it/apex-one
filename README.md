# APEX ONE 1.0 (Full Research Edition)

**APEX ONE** to wieloagentowa platforma badań AI, inżynierii dowodowej, eksperymentowania, benchmarkingu oraz nadzoru operacyjnego (Governance R0–R3).

> **Domyślny język interfejsu:** Polski (`pl`) z pełną obsługą przełączania na język angielski (`en`).

---

## 1. Zintegrowane Moduły Platformy (18 Podsystemów)

1. **APEX CORE** — jądro konfiguracyjne (`development` / `staging` / `production`), redakcja sekretów, logi strukturalne, Request ID (`X-Request-ID`), limity zapytań i diagnostyka gotowości.
2. **APEX WORKBENCH** — 19 sekcji nawigacyjnych i 23 funkcjonalne widoki React 19 + Tailwind CSS v4 (ciemny motyw grafitowy z akcentami indygo/fioletu), brak martwych przycisków, separacja przestrzeni `demo` od `research`.
3. **APEX IDENTITY** — rejestracja, logowanie Argon2id, sesje HttpOnly + tokeny CSRF, organizacje wielodostępne, zaproszenia, poświadczenia API i 6 ról RBAC (`Owner`, `Administrator`, `Researcher`, `Operator`, `Evaluator`, `Viewer`).
4. **APEX AGENT FACTORY** — graficzny Kreator Agentów, niezmienne wersjonowanie `AgentDNA` (`v1 -> v2`), porównywanie wersji (diff), duplikowanie i archiwizacja.
5. **APEX MULTI-AGENT ORCHESTRATION** — interfejs `AgentRuntimeAdapter` (`DurableCheckpointRuntimeAdapter`, zob. [ADR 0001](docs/adr/0001-agent-runtime-selection.md)), wykonywanie DAG, punkty kontrolne niezależne od kontekstu LLM, idempotentny rejestr narzędzi i strumieniowanie SSE.
6. **APEX AGENT TEAM** — 8 połączonych agentów specjalistycznych (`Supervisor`, `Research Planner`, `Source Scout`, `Evidence Analyst`, `Data Analyst`, `Experiment Engineer`, `Critic / Replicator`, `Research Writer`) oraz `Universal Baseline`.
7. **APEX RESEARCH ENGINE** — projekty badawcze z walidowaną 13-stanową maszyną stanów (`CREATED` → `COMPLETED`), wersjonowane Briefy, pytania, hipotezy, raporty i eksport Markdown.
8. **APEX SOURCE REGISTRY** — obsługa `URL`, `PDF`, `DOCX`, `TXT`, `MD`, sumy kontrolne SHA-256, wykrywanie duplikatów oraz skaner anty-Prompt-Injection.
9. **APEX EVIDENCE ENGINE** — ustrukturyzowane twierdzenia (`ResearchClaim`), dowody (`Evidence`), wizualizacja topologii Grafu Dowodowego, detekcja sprzeczności i zasada nietraktowania samooceny LLM jako skalibrowanego prawdopodobieństwa.
10. **APEX MEMORY** — 7 typów pamięci współdzielonej, wektory `1536-d` (`pgvector` HNSW), chunking dokumentów, wyszukiwanie hybrydowe (semantyczne + leksykalne) z cytowaniami oraz retencja/reindeksacja.
11. **APEX EXPERIMENT ENGINE** — wersjonowane zbiory danych, izolowany kontrakt `ExperimentSandboxAdapter` (blokujący niezweryfikowany kod obcy kodem `503`), artefakty SHA-256 i **Silnik Reprodukcji**.
12. **APEX EVALUATION ENGINE** — 9 kategorii oceny, benchmark porównawczy Single-Agent vs Multi-Agent, oceny eksperckie bez auto-promocji na produkcji oraz alerty regresji.
13. **APEX MODELS** — rejestr dostawców chmurowych, lokalnych (`Ollama`) i HTTP z łańcuchami fallback oraz śledzeniem tokenów i kosztów USD.
14. **APEX HYBRID** — samodzielna aplikacja Python `apex-local-node` z parowaniem HMAC/tokenowym, rozdzielnymi uprawnieniami `read` / `write` / `execute`, ograniczeniem katalogów i unieważnianiem urządzeń (`Revoke`).
15. **APEX WORKFLOWS** — trwałe przepływy pracy z wzorcem PostgreSQL Transactional Outbox (`FOR UPDATE SKIP LOCKED`), kluczami idempotencji i wstrzymaniem do zatwierdzenia.
16. **APEX GOVERNANCE** — poziomy ryzyka `R0`–`R3`, zatwierdzenia powiązane ze skrótem SHA-256 parametrów, niezmienny (append-only) Dziennik Audytu i wyłącznik `Emergency Stop`.
17. **APEX ANALYTICS** — rzeczywiste wskaźniki uruchomień, tokenów, kosztów, narzędzi i budżetów oraz eksport CSV.
18. **APEX PLUGINS & EXTENSIONS** — adaptery dla Dokumentów Lokalnych, GitHub, Ollama, Web Search i REST (z ochroną SSRF) oraz izolowane interfejsy przyszłych rozszerzeń (`FreshStock`, `KSeF`, `Personal AI`, `Dev Tools`, `Business`, `Creative`, `Simulation`).

---

## 2. Szybki Start (Lokalnie bez Dockera lub przez Docker Compose)

### Wariant A — Natywne uruchomienie deweloperskie (kompatybilne z Arena)

```bash
# 1. Środowisko Python
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/pip install -e ".[dev]" -e ./local_node

# 2. Zależności Node.js / TypeScript
npm install

# 3. Uruchomienie API (0.0.0.0:8000) oraz Workbench UI (0.0.0.0:5173)
# Domyślnie aplikacja startuje bez kont demonstracyjnych (APEX_ENABLE_DEMO_SEED=false).
# Pierwszą organizację i konto właściciela utworzysz w formularzu /register.
.venv/bin/uvicorn apex_one.api.main:app --host 0.0.0.0 --port 8000 &
npm run dev:web
```

> **Uwaga bezpieczeństwa (HARDENING 1.0):** Konta demonstracyjne **nie są** tworzone podczas domyślnego uruchomienia serwera i są bezwarunkowo zablokowane w środowiskach `staging` oraz `production`. Opcjonalny zbiór demonstracyjny można włączyć wyłącznie lokalnie w trybie `APEX_ENV=development` z jawną flagą `APEX_ENABLE_DEMO_SEED=true` oraz własnym hasłem testowym `APEX_DEV_DEMO_PASSWORD`.

### Wariant B — Wdrożenie kontenerowe (PostgreSQL 16 + pgvector + Backend + Worker + Web)

```bash
cp .env.example .env
docker compose up --build -d
```

---

## 3. Weryfikacja i Testy

```bash
.venv/bin/ruff check backend local_node tests migrations
.venv/bin/alembic upgrade head --sql > /dev/null
.venv/bin/pytest -v
npm run typecheck && npm run build && npm test
```

## 4. Spis Dokumentacji (`docs/`)

- [Specyfikacja Produktowa](docs/PRODUCT_SPEC.md) · [Architektura](docs/ARCHITECTURE.md) · [Schemat Bazy Danych](docs/DATABASE_SCHEMA.md) · [Referencja API](docs/API_REFERENCE.md)
- [Rozwój Agentów (AgentDNA)](docs/AGENT_DEVELOPMENT.md) · [Przewodnik Badawczy](docs/RESEARCH_GUIDE.md) · [Przewodnik Eksperymentów](docs/EXPERIMENT_GUIDE.md) · [Przewodnik Ewaluacji](docs/EVALUATION_GUIDE.md)
- [Konfiguracja Węzła Lokalnego](docs/LOCAL_NODE_SETUP.md) · [Model Bezpieczeństwa](docs/SECURITY_MODEL.md) · [Wdrożenie Produkcyjne](docs/DEPLOYMENT.md) · [Kopie Zapasowe i Odtwarzanie](docs/BACKUP_RESTORE.md)
- [Podręcznik Administratora](docs/ADMIN_GUIDE.md) · [Podręcznik Użytkownika](docs/USER_GUIDE.md)
- [Status Budowy](docs/BUILD_STATUS.md) · [Dziennik Budowy](docs/BUILD_LOG.md) · [Znane Ograniczenia](docs/KNOWN_ISSUES.md) · [Raport Wydania 1.0](docs/RELEASE_REPORT.md)
- [ADR 0001: Wybór Runtime'u Wieloagentowego](docs/adr/0001-agent-runtime-selection.md)
