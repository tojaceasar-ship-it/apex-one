# Znane Ograniczenia Środowiskowe i Kwestie Operacyjne (`KNOWN_ISSUES.md`)

1. **Brak Zewnętrznego Serwera PostgreSQL w Odizolowanym Kontenerze Arena (`CONDITIONAL`):**
   - W środowisku Arena zmienne `DATABASE_URL` oraz `APEX_DATABASE_URL` nie są skonfigurowane, a lokalny demon PostgreSQL / Docker nie jest dostępny.
   - Zgodnie z zakazem zastępowania produkcyjnego PostgreSQL bazą SQLite, schemat PostgreSQL 16 + `pgvector` + RLS jest w 100% zdefiniowany i zweryfikowany kompilacją DDL (`alembic upgrade head --sql`), natomiast testy klas C i D (`tests/postgres_integration/`, `tests/postgres_security/`) są rzetelnie oznaczane jako `SKIPPED` w tym sandboxie i uruchamiane automatycznie na żywym `pgvector/pgvector:pg16` w potoku CI (`.github/workflows/ci.yml`) oraz po podaniu `APEX_DATABASE_URL`.
2. **Blokada Wykonywania Dowolnego Niezaufanego Kodu w Sandboxie Bez Zewnętrznego Dostawcy microVM:**
   - Bez skonfigurowanego i poświadczonego zewnętrznego dostawcy izolacji (`APEX_SANDBOX_ISOLATION_ATTESTED=true`) wywołanie `/api/v1/sandbox/jobs/execute` celowo zwraca `503 UNTRUSTED_EXECUTION_BLOCKED`. Deklaratywne eksperymenty i benchmarki działają w pełni.
3. **Izolacja Fizycznego Komputera Użytkownika od Chmury Arena (`APEX HYBRID`):**
   - W chmurze Arena endpoint `/api/v1/local-node/status` rzetelnie zwraca `user_machine_connected: false`. Połączenie z fizyczną stacją roboczą następuje po uruchomieniu klienta `local_node/` przez użytkownika z wygenerowanym tokenem parowania.
4. **Zewnętrzne Płatne Klucze API (LLM / Web Search):**
   - Gdy klucze dostawców komercyjnych (`OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `APEX_WEB_SEARCH_API_KEY`) nie są ustawione w środowisku, system rzetelnie raportuje ich stan jako `UNCONFIGURED_MISSING_KEY` / `UNCONFIGURED` i korzysta z lokalnego deterministycznego silnika analitycznego oraz lokalnego przetwarzania dokumentów (`PDF`/`DOCX`/`MD`/`TXT`).
