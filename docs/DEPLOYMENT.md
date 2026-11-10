# Przewodnik Wdrożenia Produkcyjnego (`DEPLOYMENT.md`)

## 1. Wymagania Produkcyjne
- Linux x86_64 / ARM64 z Docker Engine 26+ i Docker Compose v2 (lub klaster Kubernetes).
- **PostgreSQL 16+** z rozszerzeniem `pgvector` (`CREATE EXTENSION vector`) oraz dedykowaną rolą aplikacyjną **bez** uprawnień `SUPERUSER` i `BYPASSRLS` (aby polityki RLS były bezwzględnie egzekwowane).
- Opcjonalnie Redis 7+ dla rozproszonego cache i limitów zapytań.
- Odwrotne proxy TLS 1.3 (np. `infra/nginx/apex-one.conf`).

## 2. Zmienne Środowiskowe Produkcji
W profilu `APEX_ENV=production` walidator konfiguracyjny wymusza:
- `APEX_SECRET_KEY` o długości min. 32 znaków (inny niż wartość deweloperska),
- skonfigurowany `APEX_DATABASE_URL` z `APEX_DB_SSL_REQUIRED=true`,
- jawną listę domen w `APEX_ALLOWED_ORIGINS` (symbol `*` jest odrzucany).

## 3. Procedura Wdrożenia

```bash
cp config/environments/production.env.example .env
# Uzupełnij sekrety w bezpiecznym menedżerze sekretów / .env
docker compose up --build -d
docker compose exec backend alembic upgrade head
```

> **Uwaga:** Zgodnie z dyrektywą projektową automatyczne wdrożenie na zewnętrzną infrastrukturę produkcyjną nie zostało uruchomione bez zatwierdzenia użytkownika.
