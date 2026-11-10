# Referencja API `/api/v1` (`API_REFERENCE.md`)

Wszystkie punkty końcowe są wersjonowane pod prefiksem `/api/v1`, zwracają nagłówek `X-Request-ID`, stosują limity zapytań (`X-RateLimit-Remaining`, `429 RATE_LIMITED`) oraz ujednoliconą kopertę błędów `{"error": {"code", "message", "request_id"}}`. Interaktywna specyfikacja OpenAPI jest dostępna pod `/docs` oraz `/openapi.json`.

## 1. System i Diagnostyka
- `GET /api/v1/health` — stan usługi API.
- `GET /api/v1/environment/status` — rzetelny raport gotowości PostgreSQL, Redis, Sandboxa i Węzła Lokalnego.

## 2. Uwierzytelnianie i Organizacje
- `POST /api/v1/auth/register` — rejestracja użytkownika (Argon2id) i organizacji.
- `POST /api/v1/auth/login` — logowanie zwracające token sesyjny, ciasteczko `HttpOnly` `apx_session` oraz `csrf_token`.
- `POST /api/v1/auth/logout` — unieważnienie sesji.
- `GET /api/v1/auth/me` — profil, aktywna organizacja i rola RBAC.
- `PATCH /api/v1/auth/preferences` — zmiana języka (`pl`/`en`) i preferencji.
- `GET / POST /api/v1/organizations` — lista i tworzenie organizacji.
- `POST /api/v1/organizations/invitations` — zaproszenie członka do roli RBAC.
- `POST /api/v1/organizations/api-credentials` — generowanie klucza API.

## 3. Fabryka Agentów, Zespoły i Orkiestracja
- `GET / POST /api/v1/agents` — lista i tworzenie agentów (`AgentDNA v1`).
- `GET / PUT /api/v1/agents/{agent_id}` — szczegóły oraz zapis nowej wersji `AgentDNA v+1`.
- `POST /api/v1/agents/{agent_id}/duplicate` — duplikacja agenta.
- `PATCH /api/v1/agents/{agent_id}/status` — zmiana statusu (`active`, `inactive`, `archived`).
- `GET /api/v1/agents/{agent_id}/compare?version_a=1&version_b=2` — porównanie wersji `AgentDNA`.
- `GET / POST /api/v1/teams` — zespoły wieloagentowe.
- `GET / POST /api/v1/runs` — uruchomienie przebiegu `single_agent` lub `multi_agent_team`.
- `POST /api/v1/runs/{run_id}/resume` — idempotentne wznowienie przerwanego przebiegu z punktu kontrolnego.
- `POST /api/v1/runs/{run_id}/cancel` — anulowanie przebiegu.
- `GET /api/v1/runs/{run_id}/stream` — strumień postępu SSE (`text/event-stream`).

## 4. Badania, Źródła, Graf Dowodowy i Pamięć
- `GET / POST /api/v1/research/projects` — rejestr projektów badawczych.
- `GET /api/v1/research/projects/{id}` — pełny stan projektu.
- `POST /api/v1/research/projects/{id}/transition` — walidowane przejście między 13 stanami.
- `POST /api/v1/research/projects/{id}/brief` — nowa wersja Research Brief i pytań.
- `GET /api/v1/research/projects/{id}/export` — eksport raportu Markdown.
- `GET / POST /api/v1/sources` — rejestracja źródeł (`URL`, `PDF`, `DOCX`, `TXT`, `MD`) z deduplikacją SHA-256.
- `POST / PATCH /api/v1/evidence/claims` — tworzenie i zmiana statusu twierdzeń.
- `GET /api/v1/evidence/graph` — topologia Grafu Dowodowego, sprzeczności i alerty luk.
- `GET / POST /api/v1/memories`, `GET /api/v1/memories/search`, `POST /api/v1/memories/{id}/action` — Pamięć Współdzielona.

## 5. Eksperymenty, Zbiory, Ewaluacje, Nadzór i Węzły Lokalne
- `GET / POST /api/v1/datasets` — zbiory danych i sumy kontrolne.
- `GET / POST /api/v1/experiments` oraz `POST /api/v1/experiments/runs/{id}/reproduce` — eksperymenty i Silnik Reprodukcji.
- `POST /api/v1/sandbox/jobs/validate` i `POST /api/v1/sandbox/jobs/execute` — bramka izolacji kodu obcego.
- `GET /api/v1/evaluations`, `POST /api/v1/evaluations/run`, `POST /api/v1/evaluations/runs/{id}/human-review` — 9-kategorialny silnik ewaluacji.
- `GET / POST /api/v1/models/providers` — rejestr dostawców modeli.
- `GET / POST /api/v1/local-nodes/devices`, `POST /api/v1/local-nodes/devices/{id}/revoke`, `/api/v1/local-node/*` — parowanie, unieważnianie i protokół Węzła Lokalnego.
- `GET / POST /api/v1/automation/workflows` oraz `/api/v1/workflows` — przepływy pracy i Outbox.
- `GET /api/v1/governance`, `POST /api/v1/governance/approvals/{id}/decide`, `POST /api/v1/governance/emergency-stop` — nadzór R0–R3 i Emergency Stop.
- `GET /api/v1/analytics/summary`, `GET /api/v1/analytics/export.csv` — analityka i eksport CSV.
- `GET / PATCH /api/v1/extensions` — rejestr wtyczek i adapterów.
