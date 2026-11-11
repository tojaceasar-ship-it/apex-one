# Status Budowy Komponentów (`BUILD_STATUS.md`)

| Moduł / Wymaganie | Status | Uwagi Weryfikacyjne |
| :--- | :---: | :--- |
| 1. APEX CORE (Konfiguracja, Logi, Request ID, Limity) | **PASS** | Walidowane profile `dev`/`staging`/`prod`, redakcja DSN |
| 2. APEX WORKBENCH (19 sekcji / 23 widoki, PL/EN) | **PASS** | Kompilacja `tsc` + `vite build` bez błędów, brak martwych kontrolek |
| 3. APEX IDENTITY (Argon2id, Sesje, CSRF, 6 ról RBAC) | **PASS** | Zweryfikowane testami integracyjnymi i bezpieczeństwa |
| 4. APEX AGENT FACTORY (Kreator, Wersjonowanie AgentDNA) | **PASS** | Niezmienne wersje `v1 -> v2`, porównywanie różnic (diff) |
| 5. APEX MULTI-AGENT ORCHESTRATION (`AgentRuntimeAdapter`) | **PASS** | ADR 0001 + trwałe punkty kontrolne + SSE + idempotentne wznawianie |
| 6. APEX AGENT TEAM (8 wyspecjalizowanych agentów) | **PASS** | Manifesty `agents/*/agent_dna.json` + potok 8-etapowy |
| 7. APEX RESEARCH ENGINE (13-stanowa maszyna, eksport MD) | **PASS** | Walidacja przejść stanów (`422` dla niedozwolonych skoków) |
| 8. APEX SOURCE REGISTRY (`URL`,`PDF`,`DOCX`,`TXT`,`MD`) | **PASS** | Deduplikacja SHA-256 + skaner anty-Prompt-Injection |
| 9. APEX EVIDENCE ENGINE (Twierdzenia, Graf, Sprzeczności) | **PASS** | Topologia SVG + brak fałszywej kalibracji pewności LLM |
| 10. APEX MEMORY (7 typów, 1536-d, wyszukiwanie hybrydowe) | **PASS** | Chunking, cytowania źródłowe, reindeksacja i usuwanie |
| 11. APEX EXPERIMENT ENGINE (Zbiory, Artefakty, Reprodukcja)| **PASS** | Silnik Reprodukcji (`reproduction_verified: true`) + blokada obcego kodu |
| 12. APEX EVALUATION ENGINE (9 kategorii, Human Review) | **PASS** | Porównanie Config A vs Config B + brak auto-promocji na produkcji |
| 13. APEX MODELS (Rejestr dostawców, Fallback, Koszty) | **PASS** | Katalog dynamiczny bez zaszytych wycofanych nazw modeli |
| 14. APEX HYBRID (`apex-local-node`, prawa R/W/X, Revoke) | **PASS** | Rozdzielne prawa `read`/`write`/`execute`, rzetelny status Arena |
| 15. APEX WORKFLOWS (Outbox, Idempotencja, Zgody) | **PASS** | `FOR UPDATE SKIP LOCKED` + wstrzymanie `AWAITING_APPROVAL` |
| 16. APEX GOVERNANCE (`R0`–`R3`, SHA-256, Niezmienny Audyt) | **PASS** | Unieważnienie przy zmianie parametrów + łańcuch SHA-256 |
| 17. APEX ANALYTICS (Metryki rzeczywiste + Eksport CSV) | **PASS** | 100% metryk z realnych przebiegów |
| 18. APEX PLUGINS & EXTENSIONS (Adaptery + Gniazda) | **PASS** | Dokumenty Lokalne, GitHub, Ollama, Web Search, KSeF, FreshStock itd. |
| 19. Schemat PostgreSQL + Migracje Alembic (`0001`+`0002`) | **CONDITIONAL** | DDL (57 tabel + RLS + pgvector) kompiluje się w 100%; brak żywego `DATABASE_URL` w kontenerze Arena |
