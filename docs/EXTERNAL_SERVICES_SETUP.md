# Konfiguracja Usług Zewnętrznych (`EXTERNAL_SERVICES_SETUP.md`)

> **Zasada bezpieczeństwa:** Nigdy nie zapisuj haseł, kluczy API ani pełnych adresów `DATABASE_URL` w plikach śledzonych przez Git ani nie wypisuj ich w logach. Wszystkie sekrety w `APEX ONE` są opakowane w `SecretStr` i redagowane przez `redact_dsn()`.

## 1. Zewnętrzny PostgreSQL 16+ (wymagany do pełnej trwałości oraz testów C i D)

Rekomendowani dostawcy kompatybilni z bezserwerowym profilem i `pgvector`:
- **Neon Serverless Postgres** (PostgreSQL 16/17 z natywnym rozszerzeniem `vector`)
- **Supabase Postgres** lub **AWS RDS / Google Cloud SQL for PostgreSQL**

> **Uwaga dotycząca sieci w Arena:** Sandbox Arena ma ograniczoną listę dozwolonych hostów wychodzących (`github.com`, `registry.npmjs.org`, `pypi.org`, `files.pythonhosted.org`). Jeżeli host zewnętrznej bazy (np. `*.neon.tech`) nie znajduje się na liście dozwolonych połączeń wychodzących bieżącej sesji sandboxa, testy `C` i `D` należy uruchamiać w pipeline GitHub Actions lub w środowisku z odblokowanym ruchem wychodzącym TCP `5432`/`443` do wybranego dostawcy PostgreSQL.

### Wymagane zmienne środowiskowe

```bash
export APEX_DATABASE_URL="postgresql+asyncpg://<rola_nieluprzywilejowana>:<haslo>@<host>:5432/<baza>?sslmode=require"
# Dla środowisk serverless / Neon Pooler:
export APEX_DB_POOL_MODE="nullpool"
export APEX_DB_SSL_REQUIRED="true"
```

### Weryfikacja gotowości bazy (bez ujawniania sekretów)

```bash
.venv/bin/python -m apex_one.cli.db_readiness
```

Skrypt sprawdza wyłącznie obecność zmiennych, wykonuje `SELECT 1`, odczytuje wersję serwera, weryfikuje rozszerzenie `pgvector`, przeprowadza próbną migrację Alembic na tymczasowym schemacie oraz testuje izolację RLS.

## 2. Opcjonalny Redis (`APEX_REDIS_URL`)

Redis służy wyłącznie jako opcjonalny akcelerator cache i pub/sub (np. Upstash Redis TLS lub Managed Redis). Trwałość zadań i workflowów realizuje PostgreSQL Outbox.

```bash
export APEX_REDIS_URL="rediss://default:<token>@<host>:6379/0"
export APEX_REDIS_REQUIRED="false"
```

## 3. Zewnętrzny dostawca Sandboxa Eksperymentów (`ExperimentSandboxAdapter`)

Wykonywanie niezaufanego kodu wygenerowanego przez agentów badawczych wymaga zewnętrznego dostawcy izolowanych mikro-VM (Firecracker / gVisor / E2B / Modal). Szczegóły konfiguracji znajdują się w `docs/SANDBOX_PROVIDER.md`.

```bash
export APEX_SANDBOX_PROVIDER="external_http"
export APEX_SANDBOX_ENDPOINT="https://<adres-bramki-sandboxa>"
export APEX_SANDBOX_API_KEY="<sekretny-klucz>"
export APEX_SANDBOX_ISOLATION_ATTESTED="true"
```

## 4. Hybrid Local Node (na fizycznym komputerze badacza)

Aby połączyć stację roboczą użytkownika z wdrożonym w chmurze `APEX ONE` (poza izolowanym sandboxem Arena):

```bash
export APEX_ARENA_ISOLATED_MODE="false"
export APEX_LOCAL_NODE_SHARED_SECRET="<minimum-32-znakowy-sekret-hmac>"
```
Na komputerze lokalnym zainstaluj pakiet z katalogu `local_node/` zgodnie z `local_node/README.md`.
