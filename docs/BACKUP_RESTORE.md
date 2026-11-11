# Procedury Kopii Zapasowych i Odtwarzania (`BACKUP_RESTORE.md`)

## 1. Tworzenie Kopii Zapasowej PostgreSQL

Skrypt `infra/scripts/backup_postgres.sh` tworzy skompresowany zrzut logiczny `pg_dump --format=custom` wraz z plikiem sumy kontrolnej `.sha256`:

```bash
APEX_BACKUP_DSN="postgresql://apex_backup:***@db.example.org:5432/apex_one?sslmode=require" \
  ./infra/scripts/backup_postgres.sh /var/backups/apex-one
```

## 2. Weryfikacja Integralności i Odtwarzanie

Skrypt `infra/scripts/restore_postgres.sh` przed odtworzeniem weryfikuje skrót `SHA-256` archiwum, a następnie wykonuje `pg_restore`:

```bash
APEX_RESTORE_DSN="postgresql://apex_admin:***@staging-db.example.org:5432/apex_one_dr?sslmode=require" \
  ./infra/scripts/restore_postgres.sh /var/backups/apex-one/apex_one_20261110T000000Z.dump
```

## 3. Harmonogram Zalecany dla Produkcji
- Ciągły zapis WAL / Point-in-Time Recovery (PITR) u dostawcy zarządzanego PostgreSQL (np. Neon, RDS, CloudSQL).
- Codzienny zrzut logiczny `backup_postgres.sh` przechowywany w szyfrowanym magazynie obiektowym w osobnym regionie.
- Comiesięczny test odtwarzania DR na środowisku `staging`.
